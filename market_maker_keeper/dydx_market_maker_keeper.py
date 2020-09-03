# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2020 MikeHathaway
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
import time

from functools import reduce

from pyexchange.dydx import DydxApi, Order
from pymaker.numeric import Wad

from market_maker_keeper.cex_api import CEXKeeperAPI
from market_maker_keeper.band import Bands
from market_maker_keeper.order_book import OrderBookManager

def total_amount(orders: list) -> Wad:
    return reduce(operator.add, map(lambda order: order.remaining_sell_amount, orders), Wad(0))


class DyDxMarketMakerKeeper(CEXKeeperAPI):
    """
    Keeper acting as a market maker on DyDx.
    Although portions of DyDx are onchain, 
    full order book functionality requires offchain components.
    """
    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='dydx-market-maker-keeper')

        parser.add_argument("--dydx-api-server", type=str, required=True,
                            help="Address of the Eth RPC node used for Dydx connection")

        parser.add_argument("--dydx-private-key", type=str, required=True,
                            help="API key for the DyDx API")

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

        parser.add_argument("--refresh-frequency", type=int, default=3,
                            help="Order book refresh frequency (in seconds, default: 3)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)

        self.dydx_api = DydxApi(node=self.arguments.dydx_api_server,
                                private_key=self.arguments.dydx_private_key)

        self.market_info = self.dydx_api.get_markets()

        super().__init__(self.arguments, self.dydx_api)

    def pair(self):
        return self.arguments.pair

    def init_order_book_manager(self, arguments, pyex_api):
        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: pyex_api.get_orders(self.pair()))
        self.order_book_manager.get_balances_with(lambda: pyex_api.get_balances())
        self.order_book_manager.cancel_orders_with(lambda order: pyex_api.cancel_order(order.order_id))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders,
                                                        self.our_sell_orders)
        self.order_book_manager.start()

    def token_sell(self) -> str:
        return self.arguments.pair.split('-')[0].lower()

    def token_buy(self) -> str:
        return self.arguments.pair.split('-')[1].lower()

    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        if token == 'weth':
            token = 'eth'

        return list(filter(lambda x: x['currency'] == token.upper(), our_balances))[0]['wad']

    def _should_place_order(self, new_order: dict, minimum_order_size: Wad) -> bool:
        amount = new_order.pay_amount if new_order.is_sell else new_order.buy_amount
        return amount > minimum_order_size

    def place_orders(self, new_orders):
        def place_order_function(new_order_to_be_placed):
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            order_id = self.dydx_api.place_order(pair=self.pair().upper(),
                                                 is_sell=new_order_to_be_placed.is_sell,
                                                 price=Wad.__float__(new_order_to_be_placed.price),
                                                 amount=Wad.__float__(amount))

            return Order(str(order_id), int(time.time()), self.pair(), new_order_to_be_placed.is_sell,
                         new_order_to_be_placed.price, amount)

        for new_order in new_orders:
            amount = new_order.pay_amount if new_order.is_sell else new_order.buy_amount
            side = 'Sell' if new_order.is_sell else 'Buy'
            minimum_order_size = Wad(int(self.market_info[self.pair().upper()]['smallOrderThreshold']))
            if self._should_place_order(new_order, minimum_order_size):
                self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))
            else:
                logging.info(f"New {side} Order below size minimum of {minimum_order_size}. Order of amount {amount} ignored.")

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

        # Place new orders
        self.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                           our_sell_orders=self.our_sell_orders(order_book.orders),
                                           our_buy_balance=self.our_available_balance(order_book.balances, self.token_buy()),
                                           our_sell_balance=self.our_available_balance(order_book.balances, self.token_sell()),
                                           target_price=target_price)[0])


if __name__ == '__main__':
    DyDxMarketMakerKeeper(sys.argv[1:]).main()
