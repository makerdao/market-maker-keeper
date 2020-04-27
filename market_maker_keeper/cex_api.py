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

from argparse import Namespace

from market_maker_keeper.band import Bands
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
from pyexchange.api import PyexAPI

class CEXKeeperAPI:
    """
    Define a common abstract API for keepers on centralized and hybrid exchanges
    """

    def __init__(self, arguments: Namespace, pyex_api: PyexAPI, standard_pyex: bool):

        setup_logging(arguments)

        self.bands_config = ReloadableConfig(arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(arguments)
        self.spread_feed = create_spread_feed(arguments)
        self.control_feed = create_control_feed(arguments)

        # Check to see if exchange is using a standard PyEx interface or needs to be overriden
        if standard_pyex == True:
            self.order_history_reporter = create_order_history_reporter(arguments)

            self.history = History()

            self.order_book_manager = OrderBookManager(refresh_frequency=arguments.refresh_frequency)
            self.order_book_manager.get_orders_with(lambda: pyex_api.get_orders(self.pair()))
            self.order_book_manager.get_balances_with(lambda: pyex_api.get_balances())
            self.order_book_manager.cancel_orders_with(lambda order: pyex_api.cancel_order(order.order_id))
            self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders,
                                                            self.our_sell_orders)
            self.order_book_manager.start()

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def shutdown(self):
        self.order_book_manager.cancel_all_orders()

    # Each exchange takes pair input as a different format
    def pair(self):
        raise NotImplementedError()

    def token_sell(self) -> str:
        raise NotImplementedError()

    def token_buy(self) -> str:
        raise NotImplementedError()

    # Different keys are used to access balance object for different exchanges
    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        raise NotImplementedError()

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

        # Place new orders
        self.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                           our_sell_orders=self.our_sell_orders(order_book.orders),
                                           our_buy_balance=self.our_available_balance(order_book.balances,
                                                                                      self.token_buy()),
                                           our_sell_balance=self.our_available_balance(order_book.balances,
                                                                                       self.token_sell()),
                                           target_price=target_price)[0])

    def place_orders(self, new_orders: list):
        raise NotImplementedError()
