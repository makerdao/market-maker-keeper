# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2019 grandizzy
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
import sys

from retry import retry
from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pyexchange.tethfinex import TEthfinexToken, TEthfinexApi
from pymaker import Address
from pymaker.zrx import ZrxExchange
from pymaker.keys import register_keys
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.sai import Tub
from pymaker.util import eth_balance
from pymaker.token import ERC20Token


class TethfinexMarketMakerKeeper:
    """Keeper acting as a market maker on Trustless Ethfinex."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='tethfinex-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--eth-key", type=str, nargs='*',
                            help="Ethereum private key(s) to use (e.g. 'key_file=aaa.json,pass_file=aaa.pass')")

        parser.add_argument("--exchange-address", type=str, required=True,
                            help="Ethereum address of the 0x Exchange contract")

        parser.add_argument("--tub-address", type=str, required=False,
                            help="Ethereum address of the Tub contract")

        parser.add_argument("--tethfinex-api-server", type=str, default='https://api.ethfinex.com',
                            help="Address of the Trustless Ethfinex API server (default: 'https://api.ethfinex.com')")

        parser.add_argument("--tethfinex-timeout", type=float, default=9.5,
                            help="Timeout for accessing the IDEX API (in seconds, default: 9.5)")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--config", type=str, required=True,
                            help="Bands configuration file")

        parser.add_argument("--price-feed", type=str, required=True,
                            help="Source of price feed")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of the price feed (in seconds, default: 120)")

        parser.add_argument("--spread-feed", type=str,
                            help="Source of spread feed")

        parser.add_argument("--spread-feed-expiry", type=int, default=3600,
                            help="Maximum age of the spread feed (in seconds, default: 3600)")

        parser.add_argument("--control-feed", type=str,
                            help="Source of control feed")

        parser.add_argument("--control-feed-expiry", type=int, default=86400,
                            help="Maximum age of the control feed (in seconds, default: 86400)")

        parser.add_argument("--order-history", type=str,
                            help="Endpoint to report active orders to")

        parser.add_argument("--order-history-every", type=int, default=30,
                            help="Frequency of reporting active orders (in seconds, default: 30)")

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--ethgasstation-api-key", type=str, default=None, help="ethgasstation API key")

        parser.add_argument("--refresh-frequency", type=int, default=3,
                            help="Order book refresh frequency (in seconds, default: 3)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        parser.set_defaults(cancel_on_shutdown=False, withdraw_on_shutdown=False)

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        register_keys(self.web3, self.arguments.eth_key)

        tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address)) \
            if self.arguments.tub_address is not None else None
        self.sai = ERC20Token(web3=self.web3, address=tub.sai())
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments, tub)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.gas_price = GasPriceFactory().create_gas_price(self.web3, self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)

        self.history = History()
        self.tethfinex_exchange = ZrxExchange(web3=self.web3, address=Address(self.arguments.exchange_address))
        self.tethfinex_api = TEthfinexApi(self.tethfinex_exchange,
                                          self.arguments.tethfinex_api_server,
                                          timeout=self.arguments.tethfinex_timeout)

        config = self.tethfinex_api.get_config()['0x']
        self.fee_address = Address(config['ethfinexAddress'])

        token_registry = config['tokenRegistry']
        token_sell = self.token_sell()
        token_buy = self.token_buy()
        self.token_sell_wrapper = TEthfinexToken(self.web3, Address(token_registry[token_sell]['wrapperAddress']), token_sell)
        self.token_buy_wrapper = TEthfinexToken(self.web3, Address(token_registry[token_buy]['wrapperAddress']), token_buy)

        pair=self.pair()

        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency, max_workers=1)
        self.order_book_manager.get_orders_with(lambda: self.tethfinex_api.get_orders(pair))
        self.order_book_manager.cancel_orders_with(lambda order: self.tethfinex_api.cancel_order(order.order_id))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders, self.our_sell_orders)
        self.order_book_manager.start()

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_block(self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def pair(self):
        # Trustless Ethfinex is inconsistent here. They call the pair `DAIETH`, but in reality all prices are
        # calculated like it was an `ETH/DAI` pair.
        return 'DAIETH'

    def token_sell(self) -> str:
        return self.arguments.pair[:3]

    def token_buy(self) -> str:
        return self.arguments.pair[3:]

    @retry(delay=5, logger=logger)
    def shutdown(self):
        self.order_book_manager.cancel_all_orders()

    def our_available_balance(self, token: TEthfinexToken) -> Wad:
        return Wad.from_number(token.balance_of(self.our_address))

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        bands = Bands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)
        order_book = self.order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()

        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                                      our_sell_orders=self.our_sell_orders(order_book.orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.cancel_orders(cancellable_orders)
            return

        # Do not place new orders if order book state is not confirmed
        if order_book.orders_being_placed or order_book.orders_being_cancelled:
            self.logger.debug("Order book is in progress, not placing new orders")
            return

        # Evaluate if we need to create new orders, and how much do we need to deposit
        new_orders, missing_buy_amount, missing_sell_amount = bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                                                               our_sell_orders=self.our_sell_orders(order_book.orders),
                                                                               our_buy_balance=self.our_available_balance(self.token_buy_wrapper),
                                                                               our_sell_balance=self.our_available_balance(self.token_sell_wrapper),
                                                                               target_price=target_price)

        # If deposited amount too low for placing buy orders, try to deposit.
        # If deposited amount too low for placing sell orders, try to deposit.
        made_deposit = False

        if missing_buy_amount > Wad(0):
            if self.deposit_for_buy_order(missing_buy_amount):
                made_deposit = True

        if missing_sell_amount > Wad(0):
            if missing_sell_amount > Wad(0):
                if self.deposit_for_sell_order(missing_sell_amount):
                    made_deposit = True

        # If we managed to deposit something, do not do anything so we can reevaluate new orders to be placed.
        # Otherwise, place new orders.
        if not made_deposit:
            self.place_orders(new_orders)

    def cancel_orders(self, orders: list):
        for order in orders:
            self.tethfinex_api.cancel_order(order.order_id)

    def place_orders(self, new_orders):
        for new_order in new_orders:
            if new_order.is_sell:
                self.logger.info(f"Sell amount {float(new_order.pay_amount)} of ETH with {float(new_order.buy_amount)} DAI")
                self.tethfinex_api.place_order(True,
                                               pay_token=self.token_sell_wrapper.address,
                                               pay_amount=new_order.pay_amount,
                                               buy_token=self.token_buy_wrapper.address,
                                               buy_amount=new_order.buy_amount,
                                               fee_address=self.fee_address,
                                               pair=self.pair())
            else:
                self.logger.info(f"Buy amount {float(new_order.buy_amount)} of ETH with {float(new_order.pay_amount)} DAI")
                self.tethfinex_api.place_order(False,
                                               pay_token=self.token_buy_wrapper.address,
                                               pay_amount=new_order.pay_amount,
                                               buy_token=self.token_sell_wrapper.address,
                                               buy_amount=new_order.buy_amount,
                                               fee_address=self.fee_address,
                                               pair=self.pair())

    def deposit_for_sell_order(self, missing_sell_amount: Wad):

        # We can never lock more than our available ETH balance.
        depositable_eth = eth_balance(self.web3, self.our_address)
        missing_sell_amount = Wad.min(missing_sell_amount, depositable_eth)

        # If we still can deposit something, and it's at least `min_eth_deposit`, then we do deposit.
        if missing_sell_amount > Wad(0):
            receipt = self.token_sell_wrapper.deposit(missing_sell_amount).transact(gas_price=self.gas_price)
            return receipt is not None and receipt.successful
        else:
            return False

    def deposit_for_buy_order(self, missing_buy_amount: Wad):

        # We can never lock more than our available SAI balance.
        depositable_sai = self.sai.balance_of(self.our_address)
        missing_buy_amount = Wad.min(missing_buy_amount, depositable_sai)

        # If we still can deposit something, and it's at least `min_sai_deposit`, then we do deposit.
        if missing_buy_amount > Wad(0):
            receipt = self.token_buy_wrapper.deposit(missing_buy_amount).transact(gas_price=self.gas_price)
            return receipt is not None and receipt.successful
        else:
            return False


if __name__ == '__main__':
    TethfinexMarketMakerKeeper(sys.argv[1:]).main()
