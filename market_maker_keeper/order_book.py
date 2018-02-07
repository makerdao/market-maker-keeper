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

import logging
import threading

import time


class OrderBook:
    def __init__(self,
                 orders,
                 balances,
                 orders_being_placed: bool,
                 orders_being_cancelled: bool):
        assert(isinstance(orders_being_placed, bool))
        assert(isinstance(orders_being_cancelled, bool))
        self.orders = orders
        self.balances = balances
        self.orders_being_placed = orders_being_placed
        self.orders_being_cancelled = orders_being_cancelled


class OrderBookManager:
    logger = logging.getLogger()

    def __init__(self, refresh_frequency: int):
        assert(isinstance(refresh_frequency, int))

        self.refresh_frequency = refresh_frequency
        self.get_orders_function = None
        self.get_balances_function = None

        self._lock = threading.Lock()
        self._state = None
        self._currently_placing_orders = 0
        self._orders_placed = list()
        self._order_ids_cancelling = set()
        self._order_ids_cancelled = set()

    def get_orders_with(self, get_orders_function):
        assert(callable(get_orders_function))
        self.get_orders_function = get_orders_function

    def get_balances_with(self, get_balances_function):
        assert(callable(get_balances_function))
        self.get_balances_function = get_balances_function

    def start(self):
        threading.Thread(target=self._thread_refresh_order_book, daemon=True).start()

    def get_order_book(self) -> OrderBook:
        while self._state is None:
            self.logger.info("Waiting for the order book to become available...")
            time.sleep(0.5)

        with self._lock:
            self.logger.debug(f"Getting the order book")
            self.logger.debug(f"Orders retrieved last time: {[order.order_id for order in self._state['orders']]}")
            self.logger.debug(f"Orders placed since then: {[order.order_id for order in self._orders_placed]}")
            self.logger.debug(f"Orders cancelled since then: {[order_id for order_id in self._order_ids_cancelled]}")
            self.logger.debug(f"Orders being cancelled: {[order_id for order_id in self._order_ids_cancelling]}")

            # TODO: below we remove orders which are being or have been cancelled, and orders
            # which have been placed, but we to not update the balances accordingly. it will
            # work correctly as long as the market maker keeper has enough balance available.
            # when it will get low on balance, order placement may fail or too tiny replacement
            # orders may get created for a while.

            # Add orders which have been placed.
            orders = list(self._state['orders'])
            for order in self._orders_placed:
                if order.order_id not in list(map(lambda order: order.order_id, orders)):
                    orders.append(order)

            # Remove orders being cancelled and already cancelled.
            orders = list(filter(lambda order: order.order_id not in self._order_ids_cancelling and
                                               order.order_id not in self._order_ids_cancelled, orders))

            self.logger.debug(f"Returned orders: {[order.order_id for order in orders]}")

        return OrderBook(orders=orders,
                         balances=self._state['balances'],
                         orders_being_placed=self._currently_placing_orders > 0,
                         orders_being_cancelled=len(self._order_ids_cancelling) > 0)

    def place_order(self, place_order_function):
        assert(callable(place_order_function))

        with self._lock:
            self._currently_placing_orders += 1

        threading.Thread(target=self._thread_place_order(place_order_function)).start()

    def cancel_order(self, order_id: int, cancel_order_function):
        assert(isinstance(order_id, int))
        assert(callable(cancel_order_function))

        with self._lock:
            self._order_ids_cancelling.add(order_id)

        threading.Thread(target=self._thread_cancel_order(order_id, cancel_order_function)).start()

    def wait_for_order_cancellation(self):
        while len(self._order_ids_cancelling) > 0:
            time.sleep(0.1)

    def _thread_refresh_order_book(self):
        while True:
            try:
                with self._lock:
                    orders_already_cancelled_before = set(self._order_ids_cancelled)
                    orders_already_placed_before = set(self._orders_placed)

                # get orders, get balances
                orders = self.get_orders_function()
                balances = self.get_balances_function() if self.get_balances_function is not None else None

                with self._lock:
                    self._order_ids_cancelled = self._order_ids_cancelled - orders_already_cancelled_before
                    for order in orders_already_placed_before:
                        self._orders_placed.remove(order)

                    self._state = {'orders': orders, 'balances': balances}

                self.logger.debug(f"Fetched the order book"
                                  f" (orders: {[order.order_id for order in orders]})")
            except Exception as e:
                self.logger.info(f"Failed to fetch the order book ({e})")

            time.sleep(self.refresh_frequency)

    def _thread_place_order(self, place_order_function):
        assert(callable(place_order_function))

        def func():
            try:
                new_order = place_order_function()

                with self._lock:
                    self._orders_placed.append(new_order)
            finally:
                with self._lock:
                    self._currently_placing_orders -= 1

        return func

    def _thread_cancel_order(self, order_id: int, cancel_order_function):
        assert(isinstance(order_id, int))
        assert(callable(cancel_order_function))

        def func():
            try:
                if cancel_order_function():
                    with self._lock:
                        self._order_ids_cancelled.add(order_id)
                        self._order_ids_cancelling.remove(order_id)
            finally:
                with self._lock:
                    try:
                        self._order_ids_cancelling.remove(order_id)
                    except KeyError:
                        pass

        return func
