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

import os

import itertools
import pkg_resources
from web3 import Web3, HTTPProvider

from pymaker import Address, synchronize, Logger, Contract
from pymaker.approval import directly
from pymaker.config import ReloadableConfig
from pymaker.etherdelta import EtherDelta, EtherDeltaApi, Order
from pymaker.gas import FixedGasPrice, DefaultGasPrice, GasPrice, IncreasingGasPrice, GasPriceFile
from pymaker.lifecycle import Web3Lifecycle
from pymaker.numeric import Wad
from market_maker_keeper.band import BuyBand, SellBand
from market_maker_keeper.price import TubPriceFeed, SetzerPriceFeed
from pymaker.sai import Tub
from pymaker.token import ERC20Token
from pymaker.util import eth_balance, chain


class EtherDeltaMarketMakerKeeper:
    """Keeper acting as a market maker on EtherDelta, on the ETH/SAI pair."""

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='etherdelta-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--tub-address", type=str, required=True,
                            help="Ethereum address of the Tub contract")

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

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--gas-price-increase", type=int,
                            help="Gas price increase (in Wei) if no confirmation within"
                                 " --gas-price-increase-every seconds")

        parser.add_argument("--gas-price-increase-every", type=int, default=120,
                            help="Gas price increase frequency (in seconds, default: 120)")

        parser.add_argument("--gas-price-max", type=int,
                            help="Maximum gas price (in Wei)")

        parser.add_argument("--gas-price-file", type=str,
                            help="Gas price configuration file")

        parser.add_argument("--cancel-gas-price", type=int, default=0,
                            help="Gas price (in Wei) for order cancellation")

        parser.add_argument("--cancel-gas-price-increase", type=int,
                            help="Gas price increase (in Wei) for order cancellation if no confirmation within"
                                 " --cancel-gas-price-increase-every seconds")

        parser.add_argument("--cancel-gas-price-increase-every", type=int, default=120,
                            help="Gas price increase frequency for order cancellation (in seconds, default: 120)")

        parser.add_argument("--cancel-gas-price-max", type=int,
                            help="Maximum gas price (in Wei) for order cancellation")

        parser.add_argument("--cancel-gas-price-file", type=str,
                            help="Gas price configuration file for order cancellation")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        parser.add_argument("--trace", dest='trace', action='store_true',
                            help="Enable trace output")

        parser.set_defaults(cancel_on_shutdown=False, withdraw_on_shutdown=False)

        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}"))
        self.web3.eth.defaultAccount = self.arguments.eth_from

        self.chain = chain(self.web3)
        self.our_address = Address(self.arguments.eth_from)
        self.tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address))
        self.sai = ERC20Token(web3=self.web3, address=self.tub.sai())
        self.gem = ERC20Token(web3=self.web3, address=self.tub.gem())

        _json_log = os.path.abspath(pkg_resources.resource_filename(__name__, f"../logs/etherdelta-market-maker-keeper_{self.chain}_{self.our_address}.json.log".lower()))
        self.logger = Logger('etherdelta-market-maker-keeper', self.chain, _json_log, self.arguments.debug, self.arguments.trace)
        Contract.logger = self.logger

        self.bands_config = ReloadableConfig(self.arguments.config, self.logger)
        self.eth_reserve = Wad.from_number(self.arguments.eth_reserve)
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.min_eth_deposit = Wad.from_number(self.arguments.min_eth_deposit)
        self.min_sai_deposit = Wad.from_number(self.arguments.min_sai_deposit)
        self.gas_price_for_deposits = self.get_gas_price_for_deposits()
        self.gas_price_for_order_cancellation = self.get_gas_price_for_order_cancellation()

        if self.eth_reserve <= self.min_eth_balance:
            raise Exception("--eth-reserve must be higher than --min-eth-balance")

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

    def main(self):
        with Web3Lifecycle(self.web3, self.logger) as lifecycle:
            self.lifecycle = lifecycle
            lifecycle.on_startup(self.startup)
            lifecycle.on_block(self.synchronize_orders)
            lifecycle.every(60*60, self.print_balances)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

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
            self.lifecycle.terminate(f"Bands in the config file overlap. Terminating the keeper.")
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
        return list(filter(lambda order: order.buy_token == self.sai.address and
                                         order.pay_token == EtherDelta.ETH_TOKEN, self.our_orders))

    def our_buy_orders(self):
        return list(filter(lambda order: order.buy_token == EtherDelta.ETH_TOKEN and
                                         order.pay_token == self.sai.address, self.our_orders))

    def synchronize_orders(self):
        """Update our positions in the order book to reflect keeper parameters."""
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.lifecycle.terminate("Keeper balance is below the minimum, terminating.")
            self.cancel_all_orders()
            return

        block_number = self.web3.eth.blockNumber
        target_price = self.price_feed.get_price()
        buy_bands, sell_bands = self.band_configuration()

        if target_price is not None:
            self.remove_expired_orders(block_number)
            self.cancel_orders(list(itertools.chain(self.excessive_buy_orders(buy_bands, target_price),
                                                    self.excessive_sell_orders(sell_bands, target_price),
                                                    self.outside_orders(buy_bands, sell_bands, target_price))))
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

        return itertools.chain(outside_any_band_orders(self.our_buy_orders(), buy_bands, target_price),
                               outside_any_band_orders(self.our_sell_orders(), sell_bands, target_price))

    def cancel_orders(self, orders: list):
        """Cancel orders asynchronously."""
        assert(isinstance(orders, list))  # so we can read the list twice - once to cancel,
                                          # second time to remove from 'self.our_orders'
        synchronize([self.etherdelta.cancel_order(order).transact_async(gas_price=self.gas_price_for_order_cancellation) for order in orders])
        self.our_orders = list(set(self.our_orders) - set(orders))

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
                want_amount = self.fix_amount(have_amount * band.avg_price(target_price))
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)) and (want_amount > Wad(0)):
                    order = self.etherdelta.create_order(pay_token=EtherDelta.ETH_TOKEN,
                                                         pay_amount=have_amount,
                                                         buy_token=self.sai.address,
                                                         buy_amount=want_amount,
                                                         expires=self.web3.eth.blockNumber + self.arguments.order_age)
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
                want_amount = self.fix_amount(have_amount / band.avg_price(target_price))
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)) and (want_amount > Wad(0)):
                    order = self.etherdelta.create_order(pay_token=self.sai.address,
                                                         pay_amount=have_amount,
                                                         buy_token=EtherDelta.ETH_TOKEN,
                                                         buy_amount=want_amount,
                                                         expires=self.web3.eth.blockNumber + self.arguments.order_age)
                    if self.deposit_for_buy_order_if_needed(order):
                        return
                    self.place_order(order)

    def deposit_for_sell_order_if_needed(self, order: Order):
        currently_deposited = self.etherdelta.balance_of(self.our_address)
        currently_reserved_by_open_buy_orders = self.total_amount(self.our_sell_orders())
        if currently_deposited - currently_reserved_by_open_buy_orders < order.pay_amount:
            return self.deposit_for_sell_order()
        else:
            return False

    def deposit_for_sell_order(self):
        depositable_eth = Wad.max(eth_balance(self.web3, self.our_address) - self.eth_reserve, Wad(0))
        if depositable_eth > self.min_eth_deposit:
            return self.etherdelta.deposit(depositable_eth).transact(gas_price=self.gas_price_for_deposits).successful
        else:
            return False

    def deposit_for_buy_order_if_needed(self, order: Order):
        currently_deposited = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        currently_reserved_by_open_sell_orders = self.total_amount(self.our_buy_orders())
        if currently_deposited - currently_reserved_by_open_sell_orders < order.pay_amount:
            return self.deposit_for_buy_order()
        else:
            return False

    def deposit_for_buy_order(self):
        sai_balance = self.sai.balance_of(self.our_address)
        if sai_balance > self.min_sai_deposit:
            return self.etherdelta.deposit_token(self.sai.address, sai_balance).transact(gas_price=self.gas_price_for_deposits).successful
        else:
            return False

    def total_amount(self, orders):
        pay_available = lambda order: order.pay_amount - (self.etherdelta.amount_filled(order) * order.pay_amount / order.buy_amount)
        return reduce(operator.add, map(pay_available, orders), Wad(0))

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

    def get_gas_price_for_deposits(self) -> GasPrice:
        if self.arguments.gas_price_file:
            return GasPriceFile(self.arguments.gas_price_file, self.logger)
        elif self.arguments.gas_price > 0:
            if self.arguments.gas_price_increase is not None:
                return IncreasingGasPrice(initial_price=self.arguments.gas_price,
                                          increase_by=self.arguments.gas_price_increase,
                                          every_secs=self.arguments.gas_price_increase_every,
                                          max_price=self.arguments.gas_price_max)
            else:
                return FixedGasPrice(self.arguments.gas_price)
        else:
            return DefaultGasPrice()

    def get_gas_price_for_order_cancellation(self) -> GasPrice:
        if self.arguments.cancel_gas_price_file:
            return GasPriceFile(self.arguments.cancel_gas_price_file, self.logger)
        elif self.arguments.cancel_gas_price > 0:
            if self.arguments.cancel_gas_price_increase is not None:
                return IncreasingGasPrice(initial_price=self.arguments.cancel_gas_price,
                                          increase_by=self.arguments.cancel_gas_price_increase,
                                          every_secs=self.arguments.cancel_gas_price_increase_every,
                                          max_price=self.arguments.cancel_gas_price_max)
            else:
                return FixedGasPrice(self.arguments.cancel_gas_price)
        else:
            return self.get_gas_price_for_deposits()


if __name__ == '__main__':
    EtherDeltaMarketMakerKeeper(sys.argv[1:]).main()
