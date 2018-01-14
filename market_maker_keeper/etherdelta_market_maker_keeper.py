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
import logging
import operator
import sys
from functools import reduce

import itertools
from typing import Iterable

from retry import retry
from web3 import Web3, HTTPProvider

from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.gas import GasPriceFactory
from pymaker import Address, synchronize
from pymaker.approval import directly
from pymaker.etherdelta import EtherDelta, EtherDeltaApi, Order
from pymaker.lifecycle import Web3Lifecycle
from pymaker.numeric import Wad
from market_maker_keeper.band import Bands
from market_maker_keeper.price import PriceFeedFactory
from pymaker.sai import Tub, Vox
from pymaker.token import ERC20Token
from pymaker.util import eth_balance


class EtherDeltaMarketMakerKeeper:
    """Keeper acting as a market maker on EtherDelta, on the ETH/SAI pair."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='etherdelta-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--tub-address", type=str, required=True,
                            help="Ethereum address of the Tub contract")

        parser.add_argument("--etherdelta-address", type=str, required=True,
                            help="Ethereum address of the EtherDelta contract")

        parser.add_argument("--etherdelta-socket", type=str, required=True,
                            help="Ethereum address of the EtherDelta API socket")

        parser.add_argument("--etherdelta-number-of-attempts", type=int, default=3,
                            help="Number of attempts of running the tool to talk to the EtherDelta API socket")

        parser.add_argument("--etherdelta-retry-interval", type=int, default=10,
                            help="Retry interval for sending orders over the EtherDelta API socket")

        parser.add_argument("--etherdelta-timeout", type=int, default=120,
                            help="Timeout for sending orders over the EtherDelta API socket")

        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str,
                            help="Source of price feed. Tub price feed will be used if not specified")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of non-Tub price feed (in seconds, default: 120)")

        parser.add_argument("--order-age", type=int, required=True,
                            help="Age of created orders (in blocks)")

        parser.add_argument("--order-expiry-threshold", type=int, default=0,
                            help="Remaining order age (in blocks) at which order is considered already expired, which"
                                 " means the keeper will send a new replacement order slightly ahead")

        parser.add_argument("--order-no-cancel-threshold", type=int, default=0,
                            help="Remaining order age (in blocks) below which keeper does not try to cancel orders,"
                                 " assuming that they will probably expire before the cancel transaction gets mined")

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
                                 " `--gas-price-increase-every` seconds")

        parser.add_argument("--gas-price-increase-every", type=int, default=120,
                            help="Gas price increase frequency (in seconds, default: 120)")

        parser.add_argument("--gas-price-max", type=int,
                            help="Maximum gas price (in Wei)")

        parser.add_argument("--gas-price-file", type=str,
                            help="Gas price configuration file")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        parser.set_defaults(cancel_on_shutdown=False, withdraw_on_shutdown=False)

        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        self.tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address))
        self.vox = Vox(web3=self.web3, address=self.tub.vox())
        self.sai = ERC20Token(web3=self.web3, address=self.tub.sai())
        self.gem = ERC20Token(web3=self.web3, address=self.tub.gem())

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))
        logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
        logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.INFO)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.eth_reserve = Wad.from_number(self.arguments.eth_reserve)
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.min_eth_deposit = Wad.from_number(self.arguments.min_eth_deposit)
        self.min_sai_deposit = Wad.from_number(self.arguments.min_sai_deposit)
        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed,
                                                               self.arguments.price_feed_expiry, self.tub, self.vox)

        if self.eth_reserve <= self.min_eth_balance:
            raise Exception("--eth-reserve must be higher than --min-eth-balance")

        assert(self.arguments.order_expiry_threshold >= 0)
        assert(self.arguments.order_no_cancel_threshold >= self.arguments.order_expiry_threshold)

        self.etherdelta = EtherDelta(web3=self.web3, address=Address(self.arguments.etherdelta_address))
        self.etherdelta_api = EtherDeltaApi(client_tool_directory="lib/pymaker/utils/etherdelta-client",
                                            client_tool_command="node main.js",
                                            api_server=self.arguments.etherdelta_socket,
                                            number_of_attempts=self.arguments.etherdelta_number_of_attempts,
                                            retry_interval=self.arguments.etherdelta_retry_interval,
                                            timeout=self.arguments.etherdelta_timeout)

        self.our_orders = list()

    def main(self):
        with Web3Lifecycle(self.web3) as lifecycle:
            self.lifecycle = lifecycle
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.on_block(self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

    @retry(delay=5, logger=logger)
    def shutdown(self):
        if self.arguments.cancel_on_shutdown:
            self.cancel_all_orders()

        if self.arguments.withdraw_on_shutdown:
            self.withdraw_everything()

    def approve(self):
        """Approve EtherDelta to access our SAI, so we can deposit it with the exchange"""
        self.etherdelta.approve([self.sai], directly(gas_price=self.gas_price))

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
        # If keeper balance is below `--min-eth-balance`, cancel all orders but do not terminate
        # the keeper, keep processing blocks as the moment the keeper gets a top-up it should
        # resume activity straight away, without the need to restart it.
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.logger.warning("Keeper ETH balance below minimum. Cancelling all orders.")
            self.cancel_all_orders()
            return

        bands = Bands(self.bands_config)
        block_number = self.web3.eth.blockNumber
        target_price = self.price_feed.get_price()

        # If the is no target price feed, cancel all orders but do not terminate the keeper.
        # The moment the price feed comes back, the keeper will resume placing orders.
        if target_price is None:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_all_orders()
            return

        self.remove_expired_orders(block_number)
        self.cancel_orders(itertools.chain(bands.excessive_buy_orders(self.our_buy_orders(), target_price),
                                           bands.excessive_sell_orders(self.our_sell_orders(), target_price),
                                           bands.outside_orders(self.our_buy_orders(), self.our_sell_orders(), target_price)), block_number)
        self.top_up_bands(bands.buy_bands, bands.sell_bands, target_price)

    @staticmethod
    def is_order_age_above_threshold(order: Order, block_number: int, threshold: int):
        return block_number >= order.expires-threshold  # we do >= 0, which makes us effectively detect an order
                                                        # as expired one block earlier than the contract, but
                                                        # this is desirable from the keeper point of view

    def is_expired(self, order: Order, block_number: int):
        return self.is_order_age_above_threshold(order, block_number, self.arguments.order_expiry_threshold)

    def is_non_cancellable(self, order: Order, block_number: int):
        return self.is_order_age_above_threshold(order, block_number, self.arguments.order_no_cancel_threshold)

    def remove_expired_orders(self, block_number: int):
        self.our_orders = list(filter(lambda order: not self.is_expired(order, block_number), self.our_orders))

    def cancel_orders(self, orders: Iterable, block_number: int):
        """Cancel orders asynchronously."""
        cancellable_orders = list(filter(lambda order: not self.is_non_cancellable(order, block_number), orders))
        synchronize([self.etherdelta.cancel_order(order).transact_async(gas_price=self.gas_price) for order in cancellable_orders])
        self.our_orders = list(set(self.our_orders) - set(cancellable_orders))

    def cancel_all_orders(self):
        """Cancel all our orders."""
        self.cancel_orders(self.our_orders, self.web3.eth.blockNumber)

    def withdraw_everything(self):
        eth_balance = self.etherdelta.balance_of(self.our_address)
        if eth_balance > Wad(0):
            self.etherdelta.withdraw(eth_balance).transact(gas_price=self.gas_price)

        sai_balance = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        if sai_balance > Wad(0):
            self.etherdelta.withdraw_token(self.sai.address, sai_balance).transact()

    def top_up_bands(self, buy_bands: list, sell_bands: list, target_price: Wad):
        """Create new buy and sell orders in all send and buy bands if necessary."""
        self.top_up_buy_bands(buy_bands, target_price)
        self.top_up_sell_bands(sell_bands, target_price)

    def top_up_sell_bands(self, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement is not below minimum in all sell bands. Place new orders if necessary."""
        our_balance = self.etherdelta.balance_of(self.our_address)
        for band in sell_bands:
            orders = [order for order in self.our_sell_orders() if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                if self.deposit_for_sell_order_if_needed(band.avg_amount - total_amount):
                    return

                price = band.avg_price(target_price)
                pay_amount = self.fix_amount(Wad.min(band.avg_amount - total_amount, our_balance - self.total_amount(self.our_sell_orders())))
                buy_amount = self.fix_amount(pay_amount * price)
                if (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new sell order")

                    order = self.etherdelta.create_order(pay_token=EtherDelta.ETH_TOKEN,
                                                         pay_amount=pay_amount,
                                                         buy_token=self.sai.address,
                                                         buy_amount=buy_amount,
                                                         expires=self.web3.eth.blockNumber + self.arguments.order_age)
                    self.place_order(order)

    def top_up_buy_bands(self, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement is not below minimum in all buy bands. Place new orders if necessary."""
        our_balance = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        for band in buy_bands:
            orders = [order for order in self.our_buy_orders() if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                if self.deposit_for_buy_order_if_needed(band.avg_amount - total_amount):
                    return

                price = band.avg_price(target_price)
                pay_amount = self.fix_amount(Wad.min(band.avg_amount - total_amount, our_balance - self.total_amount(self.our_buy_orders())))
                buy_amount = self.fix_amount(pay_amount / price)
                if (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new buy order")

                    order = self.etherdelta.create_order(pay_token=self.sai.address,
                                                         pay_amount=pay_amount,
                                                         buy_token=EtherDelta.ETH_TOKEN,
                                                         buy_amount=buy_amount,
                                                         expires=self.web3.eth.blockNumber + self.arguments.order_age)
                    self.place_order(order)

    def deposit_for_sell_order_if_needed(self, desired_order_pay_amount: Wad):
        currently_deposited = self.etherdelta.balance_of(self.our_address)
        if currently_deposited < desired_order_pay_amount:
            return self.deposit_for_sell_order()
        else:
            return False

    def deposit_for_sell_order(self):
        depositable_eth = Wad.max(eth_balance(self.web3, self.our_address) - self.eth_reserve, Wad(0))
        if depositable_eth > self.min_eth_deposit:
            return self.etherdelta.deposit(depositable_eth).transact(gas_price=self.gas_price).successful
        else:
            return False

    def deposit_for_buy_order_if_needed(self, desired_order_pay_amount: Wad):
        currently_deposited = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        if currently_deposited < desired_order_pay_amount:
            return self.deposit_for_buy_order()
        else:
            return False

    def deposit_for_buy_order(self):
        depositable_sai = self.sai.balance_of(self.our_address)
        if depositable_sai > self.min_sai_deposit:
            return self.etherdelta.deposit_token(self.sai.address, depositable_sai).transact(gas_price=self.gas_price).successful
        else:
            return False

    def total_amount(self, orders):
        return reduce(operator.add, map(lambda order: order.remaining_sell_amount, orders), Wad(0))

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
    EtherDeltaMarketMakerKeeper(sys.argv[1:]).main()
