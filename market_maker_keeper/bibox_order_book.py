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

import threading
from typing import List, Tuple

import time

from pymaker.bibox import BiboxApi, Order


class BiboxOrderBook:
    def __init__(self, bibox_api: BiboxApi):
        assert(isinstance(bibox_api, BiboxApi))

        self.bibox_api = bibox_api
        self._orders_being_cancelled = set()

    def get_orders(self, pair: str, retry: bool = False) -> Tuple[List[Order], bool]:
        assert(isinstance(pair, str))
        assert(isinstance(retry, bool))

        remote_orders = self.bibox_api.get_orders(pair, retry)

        orders = list(filter(lambda order: order.order_id not in self._orders_being_cancelled, remote_orders))
        orders_are_final = len(self._orders_being_cancelled) == 0
        return orders, orders_are_final

    def cancel_order(self, order_id: int, retry: bool = True):
        assert(isinstance(order_id, int))
        assert(isinstance(retry, bool))

        self._orders_being_cancelled.add(order_id)
        threading.Thread(target=self._cancel_function(order_id, retry)).start()

    def wait_for_order_cancellation(self):
        while len(self._orders_being_cancelled) > 0:
            time.sleep(0.1)

    def _cancel_function(self, order_id: int, retry: bool = True):
        assert(isinstance(order_id, int))
        assert(isinstance(retry, bool))

        def cancel_function():
            try:
                self.bibox_api.cancel_order(order_id, retry)
            finally:
                self._orders_being_cancelled.remove(order_id)

        return cancel_function
