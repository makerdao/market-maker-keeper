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
from pyexchange.paradex import ParadexApi, Order
from pymaker import Address
from pymaker.approval import directly
from pymaker.keys import register_keys
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.token import ERC20Token
from pymaker.util import eth_balance
from pymaker.zrxv2 import ZrxExchangeV2


class ParadexMarketMakerKeeper:
    """Keeper acting as a market maker on Paradex."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='paradex-market-maker-keeper')

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

        parser.add_argument("--paradex-api-server", type=str, default='https://api.paradex.io/consumer',
                            help="Address of the Paradex API (default: 'https://api.paradex.io/consumer')")

        parser.add_argument("--paradex-api-key", type=str, required=True,
                            help="API key for the Paradex API")

        parser.add_argument("--paradex-api-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Paradex API (in seconds, default: 9.5)")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--buy-token-address", type=str, required=True,
                            help="Ethereum address of the buy token")

        parser.add_argument("--sell-token-address", type=str, required=True,
                            help="Ethereum address of the sell token")

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

        parser.add_argument("--order-expiry", type=int, required=True,
                            help="Expiration time of created orders (in seconds)")

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--ethgasstation-api-key", type=str, default=None, help="ethgasstation API key")

        parser.add_argument("--refresh-frequency", type=int, default=3,
                            help="Order book refresh frequency (in seconds, default: 3)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        register_keys(self.web3, self.arguments.eth_key)

        self.pair = self.arguments.pair.upper()
        self.token_buy = ERC20Token(web3=self.web3, address=Address(self.arguments.buy_token_address))
        self.token_sell = ERC20Token(web3=self.web3, address=Address(self.arguments.sell_token_address))
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_max_decimals = None
        self.amount_max_decimals = None
        self.gas_price = GasPriceFactory().create_gas_price(self.web3, self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)

        self.history = History()
        self.zrx_exchange = ZrxExchangeV2(web3=self.web3, address=Address(self.arguments.exchange_address))
        self.paradex_api = ParadexApi(self.zrx_exchange,
                                      self.arguments.paradex_api_server,
                                      self.arguments.paradex_api_key,
                                      self.arguments.paradex_api_timeout)

        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency, max_workers=1)
        self.order_book_manager.get_orders_with(lambda: self.paradex_api.get_orders(self.pair))
        self.order_book_manager.get_balances_with(lambda: self.get_balances())
        self.order_book_manager.cancel_orders_with(lambda order: self.paradex_api.cancel_order(order.order_id))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders, self.our_sell_orders)
        self.order_book_manager.start()

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

        # Get maximum number of decimals for prices and amounts.
        # Paradex API enforces it.
        markets = self.paradex_api.get_markets()
        market = next(filter(lambda item: item['symbol'] == self.pair, markets))

        self.price_max_decimals = market['priceMaxDecimals']
        self.amount_max_decimals = market['amountMaxDecimals']

    def shutdown(self):
        self.order_book_manager.cancel_all_orders()

    def approve(self):
        self.zrx_exchange.approve([self.token_sell, self.token_buy], directly(gas_price=self.gas_price))

    def get_balances(self):
        return self.token_sell.balance_of(self.our_address), self.token_buy.balance_of(self.our_address)

    def our_total_sell_balance(self, balances) -> Wad:
        return balances[0]

    def our_total_buy_balance(self, balances) -> Wad:
        return balances[1]

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
            self.order_book_manager.cancel_orders(cancellable_orders)
            return

        # Do not place new orders if order book state is not confirmed
        if order_book.orders_being_placed or order_book.orders_being_cancelled:
            self.logger.debug("Order book is in progress, not placing new orders")
            return

        # In case of Paradex, balances returned by `our_total_balance` still contain amounts "locked"
        # by currently open orders, so we need to explicitly subtract these amounts.
        our_buy_balance = self.our_total_buy_balance(order_book.balances) - Bands.total_amount(self.our_buy_orders(order_book.orders))
        our_sell_balance = self.our_total_sell_balance(order_book.balances) - Bands.total_amount(self.our_sell_orders(order_book.orders))

        # Place new orders
        self.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                           our_sell_orders=self.our_sell_orders(order_book.orders),
                                           our_buy_balance=our_buy_balance,
                                           our_sell_balance=our_sell_balance,
                                           target_price=target_price)[0])

    def place_orders(self, new_orders):
        def place_order_function(new_order_to_be_placed):
            price = round(new_order_to_be_placed.price, self.price_max_decimals)
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            amount = round(amount, self.amount_max_decimals)
            order_id = self.paradex_api.place_order(pair=self.pair,
                                                    is_sell=new_order_to_be_placed.is_sell,
                                                    price=price,
                                                    amount=amount,
                                                    expiry=self.arguments.order_expiry)

            return Order(order_id, self.pair, new_order_to_be_placed.is_sell, price, amount, amount)

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))


if __name__ == '__main__':
    ParadexMarketMakerKeeper(sys.argv[1:]).main()
