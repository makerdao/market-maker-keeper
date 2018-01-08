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

import logging
import threading
from typing import List

import time

from pymaker import Wad
from pymaker.bibox import BiboxApi, Order


class BiboxOrderBook:
    def __init__(self, orders: List[Order], balances: list, in_progress: bool):
        assert(isinstance(orders, list))
        assert(isinstance(balances, list))
        assert(isinstance(in_progress, bool))
        self.orders = orders
        self.balances = balances
        self.in_progress = in_progress


class BiboxState:
    def __init__(self, orders: List[Order], balances: list,
                 placement_count_before: int, orders_were_being_placed: bool,
                 cancellation_count_before: int, orders_were_being_cancelled: bool):
        assert(isinstance(orders, list))
        assert(isinstance(balances, list))
        assert(isinstance(placement_count_before, int))
        assert(isinstance(orders_were_being_placed, bool))
        assert(isinstance(cancellation_count_before, int))
        assert(isinstance(orders_were_being_cancelled, bool))

        self.orders = orders
        self.balances = balances
        self.placement_count_before = placement_count_before
        self.orders_were_being_placed = orders_were_being_placed
        self.cancellation_count_before = cancellation_count_before
        self.orders_were_being_cancelled = orders_were_being_cancelled


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
        self._order_ids_cancelling = set()
        self._order_ids_cancelled = set()
        self._placement_count = 0
        self._cancellation_count = 0
        threading.Thread(target=self._refresh_order_book, daemon=True).start()

    def get_order_book(self) -> BiboxOrderBook:
        while self._state is None:
            self.logger.info("Waiting for the order book to become available...")
            time.sleep(0.5)

        with self._lock:
            orders = list(filter(lambda order: order.order_id not in self._order_ids_cancelling and
                                               order.order_id not in self._order_ids_cancelled, self._state.orders))
            balances = self._state.balances
            in_progress = self._state.orders_were_being_placed or \
                          self._state.orders_were_being_cancelled or \
                          self._placement_count > self._state.placement_count_before or \
                          self._cancellation_count > self._state.cancellation_count_before

        return BiboxOrderBook(orders=orders, balances=balances, in_progress=in_progress)

    def place_order(self, is_sell: bool, amount: Wad, amount_symbol: str, money: Wad, money_symbol: str):
        assert(isinstance(is_sell, bool))
        assert(isinstance(amount, Wad))
        assert(isinstance(amount_symbol, str))
        assert(isinstance(money, Wad))
        assert(isinstance(money_symbol, str))

        with self._lock:
            self._currently_placing_orders += 1
            self._placement_count += 1

        threading.Thread(target=self._place_order_function(is_sell=is_sell,
                                                           amount=amount,
                                                           amount_symbol=amount_symbol,
                                                           money=money,
                                                           money_symbol=money_symbol)).start()

    def cancel_order(self, order_id: int, retry: bool = True):
        assert(isinstance(order_id, int))
        assert(isinstance(retry, bool))

        with self._lock:
            self._order_ids_cancelling.add(order_id)
            self._cancellation_count += 1

        threading.Thread(target=self._cancel_function(order_id, retry)).start()

    def wait_for_order_cancellation(self):
        while len(self._order_ids_cancelling) > 0:
            time.sleep(0.1)

    def _refresh_order_book(self):
        while True:
            try:
                with self._lock:
                    placement_count_before = self._placement_count
                    orders_were_being_placed = self._currently_placing_orders > 0
                    cancellation_count_before = self._cancellation_count
                    orders_were_being_cancelled = len(self._order_ids_cancelling) > 0

                    orders_already_cancelled_before = set(self._order_ids_cancelled)

                # get orders
                orders = self.bibox_api.get_orders(pair=self.pair, retry=True)

                # get balances
                balances = self.bibox_api.coin_list(retry=True)

                with self._lock:
                    self._order_ids_cancelled = self._order_ids_cancelled - orders_already_cancelled_before
                    self._state = BiboxState(orders=orders,
                                             balances=balances,
                                             placement_count_before=placement_count_before,
                                             orders_were_being_placed=orders_were_being_placed,
                                             cancellation_count_before=cancellation_count_before,
                                             orders_were_being_cancelled=orders_were_being_cancelled)

                self.logger.debug(f"Fetched the order book and balances,"
                                  f" will fetch it again in {self.refresh_frequency} seconds")
            except Exception as e:
                self.logger.info(f"Failed to fetch the order book or balances ({e}),"
                                 f" will try again in {self.refresh_frequency} seconds")

            time.sleep(self.refresh_frequency)

    def _place_order_function(self, is_sell: bool, amount: Wad, amount_symbol: str, money: Wad, money_symbol: str):
        assert(isinstance(is_sell, bool))
        assert(isinstance(amount, Wad))
        assert(isinstance(amount_symbol, str))
        assert(isinstance(money, Wad))
        assert(isinstance(money_symbol, str))

        def place_order_function():
            try:
                self.bibox_api.place_order(is_sell=is_sell,
                                           amount=amount,
                                           amount_symbol=amount_symbol,
                                           money=money,
                                           money_symbol=money_symbol)
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
