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
from typing import Optional

import requests

from market_maker_keeper.util import sanitize_url


class OrderHistoryReporter:

    logger = logging.getLogger()

    def __init__(self, endpoint: str, frequency: int):
        assert(isinstance(endpoint, str))
        assert(isinstance(frequency, int))

        self.endpoint = endpoint
        self.sanitized_endpoint = sanitize_url(endpoint)
        self.frequency = frequency
        self._last_reported = 0

    def report_orders(self, our_buy_orders: list, our_sell_orders: list):
        assert(isinstance(our_buy_orders, list))
        assert(isinstance(our_sell_orders, list))

        if time.time() - self._last_reported < self.frequency:
            return

        self._last_reported = time.time()

        threading.Thread(target=self._thread_report_function(time.time(), our_buy_orders, our_sell_orders), daemon=True).start()

    def _thread_report_function(self, timestamp: float, buy_orders: list, sell_orders: list):
        assert(isinstance(timestamp, float))
        assert(isinstance(buy_orders, list))
        assert(isinstance(sell_orders, list))

        orders = list(map(lambda order: {
            "amount": str(order.remaining_buy_amount),
            "price": str(order.sell_to_buy_price),
            "type": "buy"
        }, buy_orders)) + list(map(lambda order: {
            "amount": str(order.remaining_sell_amount),
            "price": str(order.buy_to_sell_price),
            "type": "sell"
        }, sell_orders))

        record = {
            "timestamp": timestamp,
            "orders": orders
        }

        def _func():
            result = requests.post(url=self.endpoint, json=record, timeout=15.5)

            if result.ok:
                print(record)
                self.logger.debug(f"Successfully reported {len(orders)} orders to '{self.sanitized_endpoint}'")
            else:
                self.logger.warning(f"Failed to report orders to '{self.sanitized_endpoint}': {result.status_code} {result.text}")

        return _func


def create_order_history_reporter(arguments) -> Optional[OrderHistoryReporter]:
    if arguments.order_history:
        return OrderHistoryReporter(arguments.order_history, 30)

    else:
        return None
