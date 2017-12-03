# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017 reverendus
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import operator
import sys
from functools import reduce
from itertools import chain

from pymaker import Address, synchronize
from pymaker.approval import directly
from pymaker.config import ReloadableConfig
from pymaker.etherdelta import EtherDelta, EtherDeltaApi, Order
from pymaker.numeric import Wad
from keeper.band import BuyBand, SellBand
from keeper.price import TubPriceFeed, SetzerPriceFeed
from keeper.sai import SaiKeeper
from pymaker.util import eth_balance


class SaiMakerEtherDelta(SaiKeeper):
    """SAI keeper to act as a market maker on EtherDelta, on the ETH/SAI pair.

    Due to limitations of EtherDelta, the development of this keeper has been
    discontinued. It works most of the time, but due to the fact that EtherDelta
    was a bit unpredictable in terms of placing orders at the time this keeper
    was developed, we abandoned it and decided to stick to SaiMakerOtc for now.
    """
    def __init__(self, args: list, **kwargs):
        super().__init__(args, **kwargs)
        self.bands_config = ReloadableConfig(self.arguments.config, self.logger)
        self.order_age = self.arguments.order_age
        self.eth_reserve = Wad.from_number(self.arguments.eth_reserve)
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.min_eth_deposit = Wad.from_number(self.arguments.min_eth_deposit)
        self.min_sai_deposit = Wad.from_number(self.arguments.min_sai_deposit)

        # Choose the price feed
        if self.arguments.price_feed is not None:
            self.price_feed = SetzerPriceFeed(self.tub, self.arguments.price_feed, self.logger)
        else:
            self.price_feed = TubPriceFeed(self.tub)

        self.etherdelta = EtherDelta(web3=self.web3, address=Address(self.arguments.etherdelta_address))
        self.etherdelta_api = EtherDeltaApi(contract_address=self.etherdelta.address,
                                            api_server=self.arguments.etherdelta_socket,
                                            logger=self.logger)

        self.our_orders = list()

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--etherdelta-address", type=str, required=True,
                            help="Ethereum address of the EtherDelta contract")

        parser.add_argument("--etherdelta-socket", type=str, required=True,
                            help="Ethereum address of the EtherDelta API socket")

        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str,
                            help="Source of price feed. Tub price feed will be used if not specified")

        parser.add_argument("--order-age", type=int, required=True,
                            help="Age of created orders (in blocks)")

        parser.add_argument("--order-expiry-threshold", type=int, default=0,
                            help="Order age at which order is considered already expired (in blocks)")

        parser.add_argument("--eth-reserve", type=float, required=True,
                            help="Amount of ETH which will never be deposited so the keeper can cover gas")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper with either terminate or not start at all")

        parser.add_argument("--min-eth-deposit", type=float, required=True,
                            help="Minimum amount of ETH that can be deposited in one transaction")

        parser.add_argument("--min-sai-deposit", type=float, required=True,
                            help="Minimum amount of SAI that can be deposited in one transaction")

        parser.add_argument('--cancel-on-shutdown', dest='cancel_on_shutdown', action='store_true',
                            help="Whether should cancel all open orders on EtherDelta on keeper shutdown")

        parser.add_argument('--withdraw-on-shutdown', dest='withdraw_on_shutdown', action='store_true',
                            help="Whether should withdraw all tokens from EtherDelta on keeper shutdown")

        parser.set_defaults(cancel_on_shutdown=False, withdraw_on_shutdown=False)

    def startup(self):
        self.approve()
        self.on_block(self.synchronize_orders)
        self.every(60*60, self.print_balances)

    def shutdown(self):
        if self.arguments.cancel_on_shutdown:
            self.cancel_all_orders()

        if self.arguments.withdraw_on_shutdown:
            self.withdraw_everything()

    def print_balances(self):
        sai_owned = self.sai.balance_of(self.our_address)
        sai_deposited = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        eth_owned = eth_balance(self.web3, self.our_address)
        eth_deposited = self.etherdelta.balance_of(self.our_address)

        self.logger.info(f"Keeper balances are {sai_owned} + {sai_deposited} SAI, {eth_owned} + {eth_deposited} ETH")

    def approve(self):
        """Approve EtherDelta to access our SAI, so we can deposit it with the exchange"""
        self.etherdelta.approve([self.sai], directly())

    def band_configuration(self):
        config = self.bands_config.get_config()
        buy_bands = list(map(BuyBand, config['buyBands']))
        sell_bands = list(map(SellBand, config['sellBands']))

        if self.bands_overlap(buy_bands) or self.bands_overlap(sell_bands):
            self.terminate(f"Bands in the config file overlap. Terminating the keeper.")
            return [], []
        else:
            return buy_bands, sell_bands

    def bands_overlap(self, bands: list):
        def two_bands_overlap(band1, band2):
            return band1.min_margin < band2.max_margin and band2.min_margin < band1.max_margin

        for band1 in bands:
            if len(list(filter(lambda band2: two_bands_overlap(band1, band2), bands))) > 1:
                return True

        return False

    def place_order(self, order: Order):
        self.our_orders.append(order)
        self.etherdelta_api.publish_order(order)

    def our_sell_orders(self):
        return list(filter(lambda order: order.token_get == self.sai.address and
                                         order.token_give == EtherDelta.ETH_TOKEN, self.our_orders))

    def our_buy_orders(self):
        return list(filter(lambda order: order.token_get == EtherDelta.ETH_TOKEN and
                                         order.token_give == self.sai.address, self.our_orders))

    def synchronize_orders(self):
        """Update our positions in the order book to reflect keeper parameters."""
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.terminate("Keeper balance is below the minimum, terminating.")
            self.cancel_all_orders()
            return

        block_number = self.web3.eth.blockNumber
        target_price = self.price_feed.get_price()
        buy_bands, sell_bands = self.band_configuration()

        if target_price is not None:
            self.remove_expired_orders(block_number)
            self.cancel_orders(chain(self.excessive_buy_orders(buy_bands, target_price),
                                     self.excessive_sell_orders(sell_bands, target_price),
                                     self.outside_orders(buy_bands, sell_bands, target_price)))
            self.top_up_bands(buy_bands, sell_bands, target_price)
        else:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_all_orders()

    def remove_expired_orders(self, block_number: int):
        self.our_orders = list(filter(lambda order: order.expires - block_number > self.arguments.order_expiry_threshold-1,
                                      self.our_orders))

    def outside_orders(self, buy_bands: list, sell_bands: list, target_price: Wad):
        """Return orders which do not fall into any buy or sell band."""
        def outside_any_band_orders(orders: list, bands: list, target_price: Wad):
            for order in orders:
                if not any(band.includes(order, target_price) for band in bands):
                    yield order

        return chain(outside_any_band_orders(self.our_buy_orders(), buy_bands, target_price),
                     outside_any_band_orders(self.our_sell_orders(), sell_bands, target_price))

    def cancel_orders(self, orders):
        """Cancel orders asynchronously."""
        synchronize([self.etherdelta.cancel_order(order).transact_async(gas_price=self.gas_price) for order in orders])

    def excessive_sell_orders(self, sell_bands: list, target_price: Wad):
        """Return sell orders which need to be cancelled to bring total amounts within all sell bands below maximums."""
        for band in sell_bands:
            for order in band.excessive_orders(self.our_sell_orders(), target_price):
                yield order

    def excessive_buy_orders(self, buy_bands: list, target_price: Wad):
        """Return buy orders which need to be cancelled to bring total amounts within all buy bands below maximums."""
        for band in buy_bands:
            for order in band.excessive_orders(self.our_buy_orders(), target_price):
                yield order

    def cancel_all_orders(self):
        """Cancel all our orders."""
        self.cancel_orders(self.our_orders)

    def withdraw_everything(self):
        eth_balance = self.etherdelta.balance_of(self.our_address)
        if eth_balance > Wad(0):
            self.etherdelta.withdraw(eth_balance).transact()

        sai_balance = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        if sai_balance > Wad(0):
            self.etherdelta.withdraw_token(self.sai.address, sai_balance).transact()

    def top_up_bands(self, buy_bands: list, sell_bands: list, target_price: Wad):
        """Create new buy and sell orders in all send and buy bands if necessary."""
        self.top_up_buy_bands(buy_bands, target_price)
        self.top_up_sell_bands(sell_bands, target_price)

    def top_up_sell_bands(self, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement is not below minimum in all sell bands. Place new orders if necessary."""
        our_balance = eth_balance(self.web3, self.our_address) + self.etherdelta.balance_of(self.our_address)
        for band in sell_bands:
            orders = [order for order in self.our_sell_orders() if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                have_amount = self.fix_amount(Wad.min(band.avg_amount - total_amount, our_balance))
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    want_amount = self.fix_amount(have_amount * band.avg_price(target_price))
                    if want_amount > Wad(0):
                        order = self.etherdelta.create_order(token_give=EtherDelta.ETH_TOKEN,
                                                             amount_give=have_amount,
                                                             token_get=self.sai.address,
                                                             amount_get=want_amount,
                                                             expires=self.web3.eth.blockNumber + self.order_age)
                        if self.deposit_for_sell_order_if_needed(order):
                            return
                        self.place_order(order)

    def top_up_buy_bands(self, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement is not below minimum in all buy bands. Place new orders if necessary."""
        our_balance = self.sai.balance_of(self.our_address) + self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        for band in buy_bands:
            orders = [order for order in self.our_buy_orders() if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                have_amount = self.fix_amount(Wad.min(band.avg_amount - total_amount, our_balance))
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    want_amount = self.fix_amount(have_amount / band.avg_price(target_price))
                    if want_amount > Wad(0):
                        order = self.etherdelta.create_order(token_give=self.sai.address,
                                                             amount_give=have_amount,
                                                             token_get=EtherDelta.ETH_TOKEN,
                                                             amount_get=want_amount,
                                                             expires=self.web3.eth.blockNumber + self.order_age)
                        if self.deposit_for_buy_order_if_needed(order):
                            return
                        self.place_order(order)

    def deposit_for_sell_order_if_needed(self, order: Order):
        currently_deposited = self.etherdelta.balance_of(self.our_address)
        currently_reserved_by_open_buy_orders = self.total_amount(self.our_sell_orders())
        if currently_deposited - currently_reserved_by_open_buy_orders < order.amount_give:
            return self.deposit_for_sell_order()
        else:
            return False

    def deposit_for_sell_order(self):
        depositable_eth = Wad.max(eth_balance(self.web3, self.our_address) - self.eth_reserve, Wad(0))
        if depositable_eth > self.min_eth_deposit:
            return self.etherdelta.deposit(depositable_eth).transact().successful
        else:
            return False

    def deposit_for_buy_order_if_needed(self, order: Order):
        currently_deposited = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        currently_reserved_by_open_sell_orders = self.total_amount(self.our_buy_orders())
        if currently_deposited - currently_reserved_by_open_sell_orders < order.amount_give:
            return self.deposit_for_buy_order()
        else:
            return False

    def deposit_for_buy_order(self):
        sai_balance = self.sai.balance_of(self.our_address)
        if sai_balance > self.min_sai_deposit:
            return self.etherdelta.deposit_token(self.sai.address, sai_balance).transact().successful
        else:
            return False

    def total_amount(self, orders):
        give_available = lambda order: order.amount_give - (self.etherdelta.amount_filled(order) * order.amount_give / order.amount_get)
        return reduce(operator.add, map(give_available, orders), Wad(0))

    @staticmethod
    def fix_amount(amount: Wad) -> Wad:
        # for some reason, the EtherDelta backend rejects offchain orders with some amounts
        # for example, the following order:
        #       self.etherdelta.place_order_offchain(self.sai.address, Wad(93033469375510291122),
        #                                                 EtherDelta.ETH_TOKEN, Wad(400000000000000000),
        #                                                 self.web3.eth.blockNumber + 50)
        # will get placed correctly, but if we substitute 93033469375510291122 for 93033469375510237227
        # the backend will not accept it. this is 100% reproductible with above amounts,
        # although I wasn't able to figure out the actual reason
        #
        # what I have noticed is that rounding the amount seems to help,
        # so this is what this particular method does
        return Wad(int(amount.value / 10**9) * 10**9)


if __name__ == '__main__':
    SaiMakerEtherDelta(sys.argv[1:]).start()
