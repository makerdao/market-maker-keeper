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
from typing import List

import time

from pyexchange.bibox import BiboxApi, Order
from pymaker.numeric import Wad


class BiboxOrderBook:
    def __init__(self, orders: List[Order], balances: list, in_progress: bool):
        assert(isinstance(orders, list))
        assert(isinstance(balances, list))
        assert(isinstance(in_progress, bool))
        self.orders = orders
        self.balances = balances
        self.in_progress = in_progress


class BiboxState:
    def __init__(self, orders: List[Order], balances: list):
        assert(isinstance(orders, list))
        assert(isinstance(balances, list))

        self.orders = orders
        self.balances = balances


class BiboxOrderBookManager:
    logger = logging.getLogger()

    def __init__(self, bibox_api: BiboxApi, pair: str, refresh_frequency: int):
        assert(isinstance(bibox_api, BiboxApi))
        assert(isinstance(pair, str))
        assert(isinstance(refresh_frequency, int))

        self.bibox_api = bibox_api
        self.pair = pair
        self.refresh_frequency = refresh_frequency
        self._lock = threading.Lock()
        self._state = None
        self._currently_placing_orders = 0
        self._orders_placed = list()
        self._order_ids_cancelling = set()
        self._order_ids_cancelled = set()
        threading.Thread(target=self._refresh_order_book, daemon=True).start()

    def get_order_book(self) -> BiboxOrderBook:
        while self._state is None:
            self.logger.info("Waiting for the order book to become available...")
            time.sleep(0.5)

        with self._lock:
            self.logger.debug(f"Getting the order book")
            self.logger.debug(f"Orders kept in the internal state: {[order.order_id for order in self._state.orders]}")
            self.logger.debug(f"Orders placed: {[order.order_id for order in self._orders_placed]}")
            self.logger.debug(f"Orders being cancelled: {[order_id for order_id in self._order_ids_cancelling]},"
                              f" orders already cancelled: {[order_id for order_id in self._order_ids_cancelled]}")

            # TODO: below we remove orders which are being or have been cancelled, and orders
            # which have been placed, but we to not update the balances accordingly. it will
            # work correctly as long as the market maker keeper has enough balance available.
            # when it will get low on balance, order placement may fail or too tiny replacement
            # orders may get created for a while.

            # Add orders which have been placed.
            orders = list(self._state.orders)
            for order in self._orders_placed:
                if order.order_id not in list(map(lambda order: order.order_id, orders)):
                    orders.append(order)

            # Remove orders being cancelled and already cancelled.
            orders = list(filter(lambda order: order.order_id not in self._order_ids_cancelling and
                                               order.order_id not in self._order_ids_cancelled, orders))

            self.logger.debug(f"Returned orders: {[order.order_id for order in orders]}")

            balances = self._state.balances
            in_progress = self._currently_placing_orders > 0 or len(self._order_ids_cancelling) > 0

        return BiboxOrderBook(orders=orders, balances=balances, in_progress=in_progress)

    def place_order(self, is_sell: bool, amount: Wad, amount_symbol: str, money: Wad, money_symbol: str):
        assert(isinstance(is_sell, bool))
        assert(isinstance(amount, Wad))
        assert(isinstance(amount_symbol, str))
        assert(isinstance(money, Wad))
        assert(isinstance(money_symbol, str))

        with self._lock:
            self._currently_placing_orders += 1

        threading.Thread(target=self._place_order_function(is_sell=is_sell,
                                                           amount=amount,
                                                           amount_symbol=amount_symbol,
                                                           money=money,
                                                           money_symbol=money_symbol)).start()

    def cancel_order(self, order_id: int, retry: bool = False):
        assert(isinstance(order_id, int))
        assert(isinstance(retry, bool))

        with self._lock:
            self._order_ids_cancelling.add(order_id)

        threading.Thread(target=self._cancel_function(order_id, retry)).start()

    def wait_for_order_cancellation(self):
        while len(self._order_ids_cancelling) > 0:
            time.sleep(0.1)

    def _refresh_order_book(self):
        while True:
            try:
                with self._lock:
                    orders_already_cancelled_before = set(self._order_ids_cancelled)
                    orders_already_placed_before = set(self._orders_placed)

                # get orders, get balances
                orders = self.bibox_api.get_orders(pair=self.pair, retry=True)
                balances = self.bibox_api.coin_list(retry=True)

                with self._lock:
                    self._order_ids_cancelled = self._order_ids_cancelled - orders_already_cancelled_before
                    for order in orders_already_placed_before:
                        self._orders_placed.remove(order)

                    self._state = BiboxState(orders=orders, balances=balances)

                self.logger.debug(f"Fetched the order book and balances"
                                  f" (orders from server: {[order.order_id for order in orders]})")
            except Exception as e:
                self.logger.info(f"Failed to fetch the order book or balances ({e})")

            time.sleep(self.refresh_frequency)

    def _place_order_function(self, is_sell: bool, amount: Wad, amount_symbol: str, money: Wad, money_symbol: str):
        assert(isinstance(is_sell, bool))
        assert(isinstance(amount, Wad))
        assert(isinstance(amount_symbol, str))
        assert(isinstance(money, Wad))
        assert(isinstance(money_symbol, str))

        def place_order_function():
            try:
                new_order_id = self.bibox_api.place_order(is_sell=is_sell,
                                                          amount=amount,
                                                          amount_symbol=amount_symbol,
                                                          money=money,
                                                          money_symbol=money_symbol)

                with self._lock:
                    self._orders_placed.append(Order(new_order_id, 0, is_sell, Wad(0), amount, amount_symbol, money, money_symbol))
            finally:
                with self._lock:
                    self._currently_placing_orders -= 1

        return place_order_function

    def _cancel_function(self, order_id: int, retry: bool = True):
        assert(isinstance(order_id, int))
        assert(isinstance(retry, bool))

        def cancel_function():
            try:
                if self.bibox_api.cancel_order(order_id, retry):
                    with self._lock:
                        self._order_ids_cancelled.add(order_id)
                        self._order_ids_cancelling.remove(order_id)
            finally:
                with self._lock:
                    try:
                        self._order_ids_cancelling.remove(order_id)
                    except KeyError:
                        pass

        return cancel_function
