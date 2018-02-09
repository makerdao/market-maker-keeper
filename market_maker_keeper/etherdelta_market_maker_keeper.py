# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017-2018 reverendus
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
from market_maker_keeper.util import setup_logging
from pymaker import Address, synchronize
from pymaker.approval import directly
from pymaker.etherdelta import EtherDelta, EtherDeltaApi, Order
from pymaker.lifecycle import Lifecycle
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
                            help="Bands configuration file")

        parser.add_argument("--price-feed", type=str, required=True,
                            help="Source of price feed")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of the price feed (in seconds, default: 120)")

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
                            help="Minimum ETH balance below which keeper will cease operation")

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

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        parser.set_defaults(cancel_on_shutdown=False, withdraw_on_shutdown=False)

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        self.tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address))
        self.sai = ERC20Token(web3=self.web3, address=self.tub.sai())
        self.gem = ERC20Token(web3=self.web3, address=self.tub.gem())

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.eth_reserve = Wad.from_number(self.arguments.eth_reserve)
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.min_eth_deposit = Wad.from_number(self.arguments.min_eth_deposit)
        self.min_sai_deposit = Wad.from_number(self.arguments.min_sai_deposit)
        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed,
                                                               self.arguments.price_feed_expiry, self.tub)

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
        with Lifecycle(self.web3) as lifecycle:
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
        """Approve EtherDelta to access our tokens, so we can deposit them with the exchange"""
        token_addresses = filter(lambda address: address != EtherDelta.ETH_TOKEN, [self.token_sell(), self.token_buy()])
        tokens = list(map(lambda address: ERC20Token(web3=self.web3, address=address), token_addresses))

        self.etherdelta.approve(tokens, directly(gas_price=self.gas_price))

    def place_order(self, order: Order):
        self.our_orders.append(order)
        self.etherdelta_api.publish_order(order)

    def price(self) -> Wad:
        return self.price_feed.get_price()

    def token_sell(self) -> Address:
        return EtherDelta.ETH_TOKEN

    def token_buy(self) -> Address:
        return self.sai.address

    def our_total_balance(self, token: Address) -> Wad:
        if token == EtherDelta.ETH_TOKEN:
            return self.etherdelta.balance_of(self.our_address)
        else:
            return self.etherdelta.balance_of_token(token, self.our_address)

    def our_sell_orders(self):
        return list(filter(lambda order: order.buy_token == self.token_buy() and
                                         order.pay_token == self.token_sell(), self.our_orders))

    def our_buy_orders(self):
        return list(filter(lambda order: order.buy_token == self.token_sell() and
                                         order.pay_token == self.token_buy(), self.our_orders))

    def synchronize_orders(self):
        # If keeper balance is below `--min-eth-balance`, cancel all orders but do not terminate
        # the keeper, keep processing blocks as the moment the keeper gets a top-up it should
        # resume activity straight away, without the need to restart it.
        #
        # The exception is when we can withdraw some ETH from EtherDelta. Then we do it and carry on.
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            if self.etherdelta.balance_of(self.our_address) > self.eth_reserve:
                self.logger.warning(f"Keeper ETH balance below minimum, withdrawing {self.eth_reserve}.")
                self.etherdelta.withdraw(self.eth_reserve).transact()
            else:
                self.logger.warning(f"Keeper ETH balance below minimum, cannot withdraw. Cancelling all orders.")
                self.cancel_all_orders()

            return

        bands = Bands(self.bands_config)
        block_number = self.web3.eth.blockNumber
        target_price = self.price()

        # If the is no target price feed, cancel all orders but do not terminate the keeper.
        # The moment the price feed comes back, the keeper will resume placing orders.
        if target_price is None:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_all_orders()
            return

        # Remove expired orders from the local order list
        self.remove_expired_orders(block_number)

        # Cancel orders
        cancellable_orders = bands.cancellable_orders(self.our_buy_orders(), self.our_sell_orders(), target_price)
        if len(cancellable_orders) > 0:
            self.cancel_orders(cancellable_orders, block_number)
            return

        # In case of EtherDelta, balances returned by `our_total_balance` still contain amounts "locked"
        # by currently open orders, so we need to explicitly subtract these amounts.
        our_buy_balance = self.our_total_balance(self.token_buy()) - Bands.total_amount(self.our_buy_orders())
        our_sell_balance = self.our_total_balance(self.token_sell()) - Bands.total_amount(self.our_sell_orders())

        # Evaluate if we need to create new orders, and how much do we need to deposit
        new_orders, missing_buy_amount, missing_sell_amount = bands.new_orders(our_buy_orders=self.our_buy_orders(),
                                                                               our_sell_orders=self.our_sell_orders(),
                                                                               our_buy_balance=our_buy_balance,
                                                                               our_sell_balance=our_sell_balance,
                                                                               target_price=target_price)

        # If deposited amount too low for placing buy orders, try to deposit.
        # If deposited amount too low for placing sell orders, try to deposit.
        made_deposit = False

        if missing_buy_amount > Wad(0):
            if self.deposit_for_buy_order():
                made_deposit = True

        if missing_sell_amount > Wad(0):
            if self.deposit_for_sell_order():
                made_deposit = True

        # If we managed to deposit something, do not do anything so we can reevaluate new orders to be created.
        # Otherwise, create new orders.
        if not made_deposit:
            self.create_orders(new_orders)

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

    def create_orders(self, new_orders):
        for new_order in new_orders:
            if new_order.is_sell:
                order = self.etherdelta.create_order(pay_token=self.token_sell(),
                                                     pay_amount=self.fix_amount(new_order.pay_amount),
                                                     buy_token=self.token_buy(),
                                                     buy_amount=self.fix_amount(new_order.buy_amount),
                                                     expires=self.web3.eth.blockNumber + self.arguments.order_age)
            else:
                order = self.etherdelta.create_order(pay_token=self.token_buy(),
                                                     pay_amount=self.fix_amount(new_order.pay_amount),
                                                     buy_token=self.token_sell(),
                                                     buy_amount=self.fix_amount(new_order.buy_amount),
                                                     expires=self.web3.eth.blockNumber + self.arguments.order_age)

            self.place_order(order)

    def withdraw_everything(self):
        eth_balance = self.etherdelta.balance_of(self.our_address)
        if eth_balance > Wad(0):
            self.etherdelta.withdraw(eth_balance).transact(gas_price=self.gas_price)

        sai_balance = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        if sai_balance > Wad(0):
            self.etherdelta.withdraw_token(self.sai.address, sai_balance).transact()

    def depositable_balance(self, token: Address) -> Wad:
        if token == EtherDelta.ETH_TOKEN:
            return Wad.max(eth_balance(self.web3, self.our_address) - self.eth_reserve, Wad(0))
        else:
            return ERC20Token(web3=self.web3, address=token).balance_of(self.our_address)

    def deposit_for_sell_order(self):
        depositable_eth = self.depositable_balance(self.token_sell())
        if depositable_eth > self.min_eth_deposit:
            return self.etherdelta.deposit(depositable_eth).transact(gas_price=self.gas_price).successful
        else:
            return False

    def deposit_for_buy_order(self):
        depositable_sai = self.depositable_balance(self.token_buy())
        if depositable_sai > self.min_sai_deposit:
            return self.etherdelta.deposit_token(self.token_buy(), depositable_sai).transact(gas_price=self.gas_price).successful
        else:
            return False

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
