# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2020 mitakash
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
from typing import List
from math import log10
from market_maker_keeper.band import Bands, NewOrder
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pyexchange.leverjfutures import LeverjFuturesAPI, Order
from web3 import Web3, HTTPProvider
from pymaker.keys import register_keys
from decimal import *

_context = Context(prec=1000, rounding=ROUND_DOWN)


class LeverjMarketMakerKeeper:
    """Keeper acting as a market maker on leverj."""

    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='leverj-market-maker-keeper')

        parser.add_argument("--leverj-api-server", type=str, default="https://test.leverj.io",
                            help="Address of the leverj API server (default: 'https://test.leverj.io')")

        parser.add_argument("--account-id", type=str, default="",
                            help="Address of leverj api account id")

        parser.add_argument("--api-key", type=str, default="",
                            help="Address of leverj api key")

        parser.add_argument("--api-secret", type=str, default="",
                            help="Address of leverj api secret")

        parser.add_argument("--leverj-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Leverj API (in seconds, default: 9.5)")

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to watch our trades")

        parser.add_argument("--eth-key", type=str, nargs='*',
                            help="Ethereum private key(s) to use (e.g. 'key_file=aaa.json,pass_file=aaa.pass')")

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

        parser.add_argument("--refresh-frequency", type=int, default=3,
                            help="Order book refresh frequency (in seconds, default: 3)")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
    
        if "infura" in self.arguments.rpc_host:
            self.web3 = Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}",
                                      request_kwargs={"timeout": self.arguments.rpc_timeout}))
        else:
            self.web3 = Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                      request_kwargs={"timeout": self.arguments.rpc_timeout}))

        self.web3.eth.defaultAccount = self.arguments.eth_from
        register_keys(self.web3, self.arguments.eth_key)

        setup_logging(self.arguments)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)

        self.history = History()

        self.leverj_api = LeverjFuturesAPI(web3=self.web3,
                                    api_server=self.arguments.leverj_api_server,
                                    account_id=self.arguments.account_id,
                                    api_key=self.arguments.api_key,
                                    api_secret=self.arguments.api_secret,
                                    timeout=self.arguments.leverj_timeout)


        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: self.leverj_api.get_orders(self.pair()))
        self.order_book_manager.get_balances_with(lambda: self.leverj_api.get_balances())
        self.order_book_manager.cancel_orders_with(lambda order: self.leverj_api.cancel_order(order.order_id))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders,
                                                         self.our_sell_orders)
        self.order_book_manager.start()

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.initial_delay(1)
            lifecycle.on_startup(self.startup)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        #quote_increment = 1/(self.leverj_api.get_product(self.arguments.pair)["ticksperpoint"])
        quote_increment = self.leverj_api.get_product(self.pair())["tickSize"]
        self.precision = -(int(log10(float(quote_increment)))+1)

    def shutdown(self):
        self.order_book_manager.cancel_all_orders()

    def pair(self):
        name_to_id_map = {'BTCMCD': '1', 'ETHMCD': '2'}
        return name_to_id_map[self.arguments.pair.upper()]

    def token_sell(self) -> str:
        return self.arguments.pair.upper()[:3]

    def token_buy(self) -> str:
        return self.arguments.pair.upper()[3:]


    def allocated_balance(self, our_balances: dict, token: str) -> Wad:
        quote_asset_address = self.leverj_api.get_product(self.pair())["quote"]["address"]
        total_available = our_balances[quote_asset_address]['available']
        self.logger.info(f'total_available: {total_available}')
        return self._allocate_to_pair(total_available).get(token)

    def _allocate_to_pair(self, total_available):
        # total number of instruments across which the total_available balance is distributed
        # total_available is denominated in quote units
        total_number_of_instruments = 2
        buffer_adjustment_factor = 1.1
        base = self.arguments.pair.upper()[:3]
        quote = self.arguments.pair.upper()[3:]
        target_price = self.price_feed.get_price()
        product = self.leverj_api.get_product(self.pair())
        if (base == product['baseSymbol']):
            if ((target_price is None) or (target_price.buy_price is None) or (target_price.sell_price is None)):
                net_base_allocation = Wad(0)
            else:
                average_price = (int(target_price.buy_price) + int(target_price.sell_price))/2
                self.logger.info(f'target_price, average_price: {average_price}')
                conversion_divisor = int(average_price*total_number_of_instruments*2*buffer_adjustment_factor)
                exponent = self._exponent_for_lowest_denomination(base)
                base_allocation = (float(total_available)/(conversion_divisor*10**(18 - exponent)))
                base_allocation_wad = Wad(int(base_allocation))
                open_position_for_base = self.current_open_position(base)
                calculated_net_allocation = base_allocation_wad.value + open_position_for_base.value
                net_base_allocation = Wad(calculated_net_allocation) if calculated_net_allocation > 0 else Wad(0)
            self.logger.info(f'net_base_allocation: {net_base_allocation}')
        if (quote == product['quoteSymbol']):
            if ((target_price is None) or (target_price.buy_price is None) or (target_price.sell_price is None)):
                quote_allocation = Wad(0)
            else:
                quote_allocation = Wad(int(float(total_available)/(total_number_of_instruments*2)))
            self.logger.info(f'quote_allocation: {quote_allocation}')
        allocation = {base: net_base_allocation, quote: quote_allocation}
        return allocation

    def _exponent_for_lowest_denomination(self, coin: str) -> int:
        if (coin == 'BTC'):
            exponent_for_lowest_denomination = 18
        else:
            exponent_for_lowest_denomination = 18
        return exponent_for_lowest_denomination

    def current_open_position(self, token: str) -> Wad:
        open_position_for_token = self.leverj_api.get_position_in_wad(token)
        return open_position_for_token

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

        self.logger.info(f'token_buy: {self.token_buy()}, token_sell: {self.token_sell()}')
        # Place new orders
        new_orders = bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                      our_sell_orders=self.our_sell_orders(order_book.orders),
                                      our_buy_balance=self.allocated_balance(order_book.balances, self.token_buy()),
                                      our_sell_balance=self.allocated_balance(order_book.balances, self.token_sell()),
                                      target_price=target_price)[0]
        self.place_orders(new_orders)

    def place_orders(self, new_orders: List[NewOrder]):
        def place_order_function(new_order_to_be_placed):
            price = round(new_order_to_be_placed.price, self.precision + 2)
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            order_id = str(self.leverj_api.place_order(self.pair(), price, 'LMT', new_order_to_be_placed.is_sell, price, amount))
            return Order(order_id=order_id,
                         pair=self.pair(),
                         is_sell=new_order_to_be_placed.is_sell,
                         price=price,
                         amount=amount)

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))


if __name__ == '__main__':
    LeverjMarketMakerKeeper(sys.argv[1:]).main()
