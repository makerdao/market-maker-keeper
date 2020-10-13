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
import sys
import threading
import os

import time
from decimal import Decimal
from typing import List

from pyexchange.erisx import ErisxApi
from pyexchange.fix import FixConnectionState
from pyexchange.model import Order

from pymaker.numeric import Wad
from pymaker.lifecycle import Lifecycle

from market_maker_keeper.band import Bands
from market_maker_keeper.cex_api import CEXKeeperAPI
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.util import setup_logging

class ErisXLifecycle(Lifecycle):

    def _start_every_timer(self, idx: int, frequency_in_seconds: int, callback):
        self.count = 0
        self._socket_closed = False

        def setup_timer(delay):
            timer = threading.Timer(delay, func)
            timer.daemon = True

            self._start_thread_safely(timer)

        def func():
            try:
                if not self.terminated_internally and not self.terminated_externally and not self.fatal_termination:
                    def on_start():
                        self.logger.debug(f"Processing the timer #{idx}")

                    def on_finish():
                        self.logger.debug(f"Finished processing the timer #{idx}")
                        self.count = 0

                    if not callback.trigger(on_start, on_finish):
                        self.count += 1
                        if self.count >= 20:
                            self.logger.debug(f"killing lifecycle as parent is no longer running")
                            self.terminated_externally = True
                            self._socket_closed = True
                        self.logger.debug(f"Ignoring timer #{idx} as previous one is already running")
                else:
                    self.logger.debug(f"Ignoring timer #{idx} as keeper is already terminating")
                    if self._socket_closed:
                        time.sleep(10)
                        os._exit(1)
            except:
                setup_timer(frequency_in_seconds)
                raise
            setup_timer(frequency_in_seconds)

        setup_timer(1)
        self._at_least_one_every = True


# Subclass orderboook to enable support for different order schema required by ErisX
class ErisXOrderBookManager(OrderBookManager):

    """
    Due to nature of FIX engine, there is a single socket connection controlled by a single event loop.
    ThreadExecutor as used in the standard OrderBookManager thereby doesn't work.
    Subclassing enables order actions to be made synchronously without interfering with the FIX event loop.
    """
    def place_order(self, place_order_function):
        """Places new order. Order placement will happen in a background thread.

        Args:
            place_order_function: Function used to place the order.
        """
        assert (callable(place_order_function))

        with self._lock:
            self._currently_placing_orders += 1

        self._report_order_book_updated()

        try:
            with self._lock:
                new_order = place_order_function()

                if new_order is not None:
                    self._orders_placed.append(new_order)
        except BaseException as exception:
            self.logger.exception(exception)
        finally:
            with self._lock:
                self._currently_placing_orders -= 1
            self._report_order_book_updated()

    def cancel_orders(self, orders: list):
        """Cancels existing orders. Order cancellation will happen in a background thread.

        Args:
            orders: List of orders to cancel.
        """
        assert (isinstance(orders, list))
        assert (callable(self.cancel_order_function))

        with self._lock:
            for order in orders:
                self._order_ids_cancelling.add(order.order_id)

        self._report_order_book_updated()

        for order in orders:
            order_id = order.order_id
            try:
                with self._lock:
                    cancel_result, order_unknown = self.cancel_order_function(order)

                    if cancel_result:
                        self._order_ids_cancelled.add(order_id)
                        self._order_ids_cancelling.remove(order_id)
                        self.logger.info(f"Succesfully canceled order: {order_id}")
                    else:
                        if order_unknown:
                            self._order_ids_cancelled.add(order_id)
                            self._order_ids_cancelling.remove(order_id)
                            self.logger.warning(f"Order unknown, removing from local orderbook: {order_id}")
            except BaseException as exception:
                self.logger.exception(f"Failed to cancel {order_id}")
            finally:
                try:
                    self._order_ids_cancelling.remove(order_id)
                except KeyError:
                    self.logger.info(f"Failed to remove {order_id}")
                    pass
                self._report_order_book_updated()

    def _thread_refresh_order_book(self):
        while True:
            try:
                with self._lock:
                    orders_already_cancelled_before = set(self._order_ids_cancelled)
                    orders_already_placed_before = set(self._orders_placed)

                # get orders, get balances
                with self._lock:
                    orders = self.get_orders_function()

                # remove canceled orders from local orders instance
                with self._lock:
                    for order in orders:
                        if order.order_id in orders_already_cancelled_before:
                            orders.remove(order)

                balances = self.get_balances_function() if self.get_balances_function is not None else None

                if self.order_history_reporter:
                    orders_buy = self.buy_filter_function(orders)
                    orders_sell = self.sell_filter_function(orders)

                    self.order_history_reporter.report_orders(orders_buy, orders_sell)

                with self._lock:
                    self._order_ids_cancelled = self._order_ids_cancelled - orders_already_cancelled_before
                    for order in orders_already_placed_before:
                        self._orders_placed.remove(order)

                    if self._state is None:
                        self.logger.info("Order book became available")

                    self._state = {'orders': orders, 'balances': balances}
                    self._refresh_count += 1

                self._report_order_book_updated()

                self.logger.debug(f"Fetched the order book"
                                  f" (orders: {[order.order_id for order in orders]})")
            except Exception as e:
                self.logger.info(f"Failed to fetch the order book ({e})")

            time.sleep(self.refresh_frequency)


class ErisXMarketMakerKeeper(CEXKeeperAPI):
    """
    Keeper acting as a market maker on ErisX.
    """

    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='erisx-market-maker-keeper')

        parser.add_argument("--erisx-clearing-url", type=str, required=True,
                            help="Address of the ErisX clearing server")

        parser.add_argument("--fix-trading-endpoint", type=str, required=True,
                            help="FIX endpoint for ErisX trading")

        parser.add_argument("--fix-trading-user", type=str, required=True,
                            help="Account ID for ErisX trading")

        parser.add_argument("--fix-marketdata-endpoint", type=str, required=True,
                            help="FIX endpoint for ErisX market data")

        parser.add_argument("--fix-marketdata-user", type=str, required=True,
                            help="Account ID for ErisX market data")

        parser.add_argument("--erisx-password", type=str, required=True,
                            help="password for FIX account")

        parser.add_argument("--erisx-api-key", type=str, required=True,
                            help="API key for ErisX REST API")

        parser.add_argument("--erisx-api-secret", type=str, required=True,
                            help="API secret for ErisX REST API")

        parser.add_argument("--erisx-certs", type=str, default=None,
                            help="Client key pair used to authenticate to Production FIX endpoints")

        parser.add_argument("--account-id", type=int, default=0,
                            help="ErisX account ID index")

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

        setup_logging(self.arguments)

        self.erisx_api = ErisxApi(fix_trading_endpoint=self.arguments.fix_trading_endpoint,
                                  fix_trading_user=self.arguments.fix_trading_user,
                                  fix_marketdata_endpoint=self.arguments.fix_marketdata_endpoint,
                                  fix_marketdata_user=self.arguments.fix_marketdata_user,
                                  password=self.arguments.erisx_password,
                                  clearing_url=self.arguments.erisx_clearing_url,
                                  api_key=self.arguments.erisx_api_key,
                                  api_secret=self.arguments.erisx_api_secret,
                                  certs=self.arguments.erisx_certs,
                                  account_id=self.arguments.account_id)

        termination_time = time.time() + 15
        while self.erisx_api.fix_trading.connection_state != FixConnectionState.LOGGED_IN and self.erisx_api.fix_marketdata.connection_state != FixConnectionState.LOGGED_IN:
            time.sleep(0.3)
            if time.time() > termination_time:
                raise RuntimeError("Timed out while waiting to log in")

        self.market_info = self.erisx_api.get_markets()

        self.orders = self.erisx_api.get_orders(self.pair())
        
        super().__init__(self.arguments, self.erisx_api)

    def init_order_book_manager(self, arguments, erisx_api):
        self.order_book_manager = ErisXOrderBookManager(refresh_frequency=self.arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: self.get_orders())
        self.order_book_manager.get_balances_with(lambda: self.erisx_api.get_balances())
        self.order_book_manager.cancel_orders_with(
            lambda order: self.erisx_api.cancel_order(order.order_id, self.pair(), order.is_sell))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders,
                                                         self.our_sell_orders)

        self.order_book_manager.pair = self.pair()
        self.order_book_manager.start()

    def main(self):
        with ErisXLifecycle() as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def pair(self):
        return self.arguments.pair

    def token_sell(self) -> str:
        return self.arguments.pair.split('/')[0].upper()

    def token_buy(self) -> str:
        return self.arguments.pair.split('/')[1].upper()

    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        if 'newrelease' in self.arguments.erisx_clearing_url:
            if token == 'ETH':
                token = 'TETH'
            if token == 'BTC':
                token = 'TBTC'

        token_balances = list(filter(lambda asset: asset['asset_type'].upper() == token, our_balances))
        if token_balances:
            return Wad.from_number(float(token_balances[0]['available_to_trade']))
        else:
            return Wad(0)

    def get_orders(self) -> List:
        return self.orders

    def place_orders(self, new_orders):
        def place_order_function(new_order_to_be_placed):
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            # automatically retrive qty precision
            round_lot = str(self.market_info[self.pair()]["RoundLot"])
            price_increment = str(self.market_info[self.pair()]["MinPriceIncrement"])
            price_precision = abs(Decimal(price_increment).as_tuple().exponent)
            order_qty_precision = abs(Decimal(round_lot).as_tuple().exponent)
            rounded_amount = round(Wad.__float__(amount), order_qty_precision)
            rounded_price = round(Wad.__float__(new_order_to_be_placed.price), price_precision)

            order_id = self.erisx_api.place_order(pair=self.pair().upper(),
                                                  is_sell=new_order_to_be_placed.is_sell,
                                                  price=rounded_price,
                                                  amount=rounded_amount)

            placed_order = Order(str(order_id), int(time.time()), self.pair(), new_order_to_be_placed.is_sell,
                         new_order_to_be_placed.price, amount)

            self.orders.append(placed_order)
            return placed_order

        for new_order in new_orders:
            # check if new order is greater than exchange minimums
            amount = new_order.pay_amount if new_order.is_sell else new_order.buy_amount
            side = 'Sell' if new_order.is_sell else 'Buy'
            round_lot = str(self.market_info[self.pair()]["RoundLot"])
            order_qty_precision = abs(Decimal(round_lot).as_tuple().exponent)
            min_amount = float(self.market_info[self.pair()]["MinTradeVol"])
            rounded_amount = round(Wad.__float__(amount), order_qty_precision)

            if min_amount < rounded_amount:
                self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))
            else:
                logging.info(f"New {side} Order below size minimum of {min_amount}. Order of amount {amount} ignored.")

    def check_cancellations(self):
        """
        Check to see if ErisX has cancelled any of our orders.
        If an order has been cancelled, remove it from the order book
        """
        cancelled_orders = self.erisx_api.check_cancellations()

        for order in cancelled_orders:
            for index, existing_order in enumerate(self.orders):
                if order.order_id == existing_order.order_id:
                    del self.orders[index]
                    self.logger.info(f"Removed cancelled order: {order.order_id}")

    def synchronize_orders(self):
        bands = Bands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)
        order_book = self.order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()

        # Remove any orders cancelled by ErisX
        self.check_cancellations()

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

if __name__ == '__main__':
    ErisXMarketMakerKeeper(sys.argv[1:]).main()
