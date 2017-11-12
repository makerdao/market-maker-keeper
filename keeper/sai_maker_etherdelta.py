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
from typing import List

from keeper.api import Address, synchronize
from keeper.api.approval import directly
from keeper.api.etherdelta import EtherDelta, Order, EtherDeltaApi, OffChainOrder
from keeper.api.feed import DSValue
from keeper.api.numeric import Wad
from keeper.band import BuyBand, SellBand
from keeper.sai import SaiKeeper


class SaiMakerEtherDelta(SaiKeeper):
    """SAI keeper to act as a market maker on EtherDelta, on the ETH/SAI pair.

    Due to limitations of EtherDelta, the development of this keeper has been
    discontinued. It works most of the time, but due to the fact that EtherDelta
    was a bit unpredictable in terms of placing orders at the time this keeper
    was developed, we abandoned it and decided to stick to SaiMakerOtc for now.
    """
    def __init__(self, args: list, **kwargs):
        super().__init__(args, **kwargs)
        self.order_age = self.arguments.order_age
        self.eth_reserve = Wad.from_number(self.arguments.eth_reserve)

        # We need to have the API server in order for this keeper to run.
        # Unfortunately it means it can be run only on Mainnet as there is no test server.
        if "apiServer" not in self.config.get_config()["etherDelta"]:
            raise Exception("Off-chain EtherDelta orders not supported on this chain")

        self.etherdelta = EtherDelta(web3=self.web3,
                                     address=Address(self.config.get_config()["etherDelta"]["contract"]))

        self.etherdelta_api = EtherDeltaApi(contract_address=self.etherdelta.address,
                                            api_server=self.config.get_config()["etherDelta"]["apiServer"],
                                            logger=self.logger)

        self.our_orders = set()

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--config", help="Buy/sell bands configuration file", type=str, required=True)
        parser.add_argument("--order-age", help="Age of created orders (in blocks)", type=int, required=True)
        parser.add_argument("--eth-reserve", help="Minimum amount of ETH to keep in order to cover gas", type=float, required=True)

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
            self.cancel_all_offers()

        if self.arguments.withdraw_on_shutdown:
            self.withdraw_everything()

    def print_balances(self):
        sai_owned = self.sai.balance_of(self.our_address)
        sai_deposited = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        eth_owned = self.eth_balance(self.our_address)
        eth_deposited = self.etherdelta.balance_of(self.our_address)

        self.logger.info(f"Keeper balances are {sai_owned} + {sai_deposited} SAI, {eth_owned} + {eth_deposited} ETH")

    def approve(self):
        """Approve EtherDelta to access our SAI, so we can deposit it with the exchange"""
        self.etherdelta.approve([self.sai], directly())

    def band_configuration(self):
        config = self.get_config(self.arguments.config)
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

    def place_order(self, order: OffChainOrder):
        self.our_orders.add(order)
        self.etherdelta_api.publish_offchain_order(order)

    def our_sell_offers(self):
        return list(filter(lambda order: order.token_get == self.sai.address and
                                         order.token_give == EtherDelta.ETH_TOKEN, self.our_orders))

    def our_buy_offers(self):
        return list(filter(lambda order: order.token_get == EtherDelta.ETH_TOKEN and
                                         order.token_give == self.sai.address, self.our_orders))

    def synchronize_orders(self):
        """Update our positions in the order book to reflect keeper parameters."""
        buy_bands, sell_bands = self.band_configuration()
        target_price = self.tub_target_price()
        self.cancel_offers(chain(self.excessive_buy_offers(buy_bands, target_price),
                                 self.excessive_sell_offers(sell_bands, target_price),
                                 self.outside_offers(buy_bands, sell_bands, target_price)))
        self.top_up_bands(buy_bands, sell_bands, target_price)

        # TODO apparently deposits have to be made before we place orders, otherwise the EtherDelta backend
        # TODO seems to ignore new offchain orders. even if we deposit the tokens shortly afterwards, the orders
        # TODO will not reappear
        # self.deposit_for_buy_orders()
        # self.deposit_for_sell_orders()


        # """Update our positions in the order book to reflect settings."""
        # self.cancel_excessive_buy_orders()
        # self.cancel_excessive_sell_orders()
        # self.create_new_buy_order()
        # # self.create_new_sell_order()

    def outside_offers(self, buy_bands: list, sell_bands: list, target_price: Wad):
        """Return offers which do not fall into any buy or sell band."""
        def outside_any_band_offers(offers: list, bands: list, target_price: Wad):
            for offer in offers:
                if not any(band.includes(offer, target_price) for band in bands):
                    yield offer

        return chain(outside_any_band_offers(self.our_buy_offers(), buy_bands, target_price),
                     outside_any_band_offers(self.our_sell_offers(), sell_bands, target_price))

    def cancel_offers(self, offers):
        """Cancel offers asynchronously."""
        synchronize([self.etherdelta.cancel_order(offer).transact_async(gas_price=self.gas_price) for offer in offers])

    def excessive_sell_offers(self, sell_bands: list, target_price: Wad):
        """Return sell offers which need to be cancelled to bring total amounts within all sell bands below maximums."""
        for band in sell_bands:
            for offer in self.excessive_offers_in_band(band, self.our_sell_offers(), target_price):
                yield offer

    def excessive_buy_offers(self, buy_bands: list, target_price: Wad):
        """Return buy offers which need to be cancelled to bring total amounts within all buy bands below maximums."""
        for band in buy_bands:
            for offer in self.excessive_offers_in_band(band, self.our_buy_offers(), target_price):
                yield offer

    def excessive_offers_in_band(self, band, offers: list, target_price: Wad):
        """Return offers which need to be cancelled to bring the total offer amount in the band below maximum."""
        # if total amount of orders in this band is greater than the maximum, we cancel them all
        #
        # if may not be the best solution as cancelling only some of them could bring us below
        # the maximum, but let's stick to it for now
        offers_in_band = [offer for offer in offers if band.includes(offer, target_price)]
        return offers_in_band if self.total_amount(offers_in_band) > band.max_amount else []

    def cancel_all_offers(self):
        """Cancel all our offers."""
        self.cancel_offers(self.our_orders)

    def withdraw_everything(self):
        eth_balance = self.etherdelta.balance_of(self.our_address)
        if eth_balance > Wad(0):
            self.etherdelta.withdraw(eth_balance).transact()

        sai_balance = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        if sai_balance > Wad(0):
            self.etherdelta.withdraw_token(self.sai.address, sai_balance).transact()

    def top_up_bands(self, buy_bands: list, sell_bands: list, target_price: Wad):
        """Asynchronously create new buy and sell offers in all send and buy bands if necessary."""
        self.top_up_buy_bands(buy_bands, target_price)
        self.top_up_sell_bands(sell_bands, target_price)

    def top_up_sell_bands(self, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement is not below minimum in all sell bands. Place new offers if necessary."""
        our_balance = self.etherdelta.balance_of(self.our_address)
        for band in sell_bands:
            offers = [offer for offer in self.our_sell_offers() if band.includes(offer, target_price)]
            total_amount = self.total_amount(offers)
            if total_amount < band.min_amount:
                have_amount = self.fix_amount(Wad.min(band.avg_amount - total_amount, our_balance))
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    our_balance = our_balance - have_amount
                    want_amount = self.fix_amount(have_amount * round(band.avg_price(target_price)))
                    if want_amount > Wad(0):
                        self.place_order(
                            self.etherdelta.create_offchain_order(token_get=self.sai.address, amount_get=want_amount,
                                                                  token_give=EtherDelta.ETH_TOKEN, amount_give=have_amount,
                                                                  expires=self.web3.eth.blockNumber+self.order_age))

    def top_up_buy_bands(self, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement is not below minimum in all buy bands. Place new offers if necessary."""
        our_balance = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        for band in buy_bands:
            offers = [offer for offer in self.our_buy_offers() if band.includes(offer, target_price)]
            total_amount = self.total_amount(offers)
            if total_amount < band.min_amount:
                have_amount = self.fix_amount(Wad.min(band.avg_amount - total_amount, our_balance))
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    our_balance = our_balance - have_amount
                    want_amount = self.fix_amount(have_amount / round(band.avg_price(target_price)))
                    if want_amount > Wad(0):
                        self.place_order(
                            self.etherdelta.create_offchain_order(token_get=EtherDelta.ETH_TOKEN, amount_get=want_amount,
                                                                  token_give=self.sai.address, amount_give=have_amount,
                                                                  expires=self.web3.eth.blockNumber+self.order_age))

    def deposit_for_buy_orders(self):
        order_total = self.total_amount(self.our_buy_offers())
        currently_deposited = self.etherdelta.balance_of(self.our_address)
        if order_total > currently_deposited:
            depositable_eth = Wad.max(self.eth_balance(self.our_address) - self.eth_reserve, Wad(0))
            additional_deposit = Wad.min(order_total - currently_deposited, depositable_eth)
            if additional_deposit > Wad(0):
                self.etherdelta.deposit(additional_deposit).transact()

    def deposit_for_sell_orders(self):
        order_total = self.total_amount(self.our_sell_offers())
        currently_deposited = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        if order_total > currently_deposited:
            additional_deposit = Wad.min(order_total - currently_deposited, self.sai.balance_of(self.our_address))
            if additional_deposit > Wad(0):
                self.etherdelta.deposit_token(self.sai.address, additional_deposit).transact()

    def tub_target_price(self) -> Wad:
        ref_per_gem = Wad(DSValue(web3=self.web3, address=self.tub.pip()).read_as_int())
        return ref_per_gem / self.tub.par()

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
        return Wad(int(amount.value / 10**9) * 10**9) - Wad(1000000000)


if __name__ == '__main__':
    SaiMakerEtherDelta(sys.argv[1:]).start()
