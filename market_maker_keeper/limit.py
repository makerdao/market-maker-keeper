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
from functools import reduce

from pymaker.numeric import Wad


class History:
    def __init__(self):
        self.buy_history = SideHistory()
        self.sell_history = SideHistory()


class SideHistory:
    def __init__(self):
        self.items = []
        self._lock = threading.Lock()

    def add_item(self, item: dict):
        assert(isinstance(item, dict))

        with self._lock:
            self.items.append(item)

    def get_items(self) -> list:
        with self._lock:
            return list(self.items)


class SideLimits:
    logger = logging.getLogger()

    def __init__(self, limits: list, side_history: SideHistory):
        assert(isinstance(limits, list))
        assert(isinstance(side_history, SideHistory))

        self.side_limits = list(map(SideLimit, limits))
        self.side_history = side_history

    def available_limit(self, timestamp: int):
        if len(self.side_limits) > 0:
            return Wad.min(*map(lambda limit: limit.available_limit(timestamp, self.side_history), self.side_limits))
        else:
            return Wad.from_number(2**256 - 1)

    def use_limit(self, timestamp: int, amount: Wad):
        self.side_history.add_item({'timestamp': timestamp, 'amount': amount})


class SideLimit:
    def __init__(self, limit: dict):
        assert(isinstance(limit, dict))
        self.amount = Wad.from_number(limit['amount'])
        self.seconds = self._to_seconds(limit['period'])

    def _to_seconds(self, string: str) -> int:
        assert(isinstance(string, str))
        seconds_per_unit = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        return int(string[:-1]) * seconds_per_unit[string[-1]]

    def available_limit(self, timestamp: int, side_history: SideHistory):
        assert(isinstance(side_history, SideHistory))

        items = filter(lambda item: timestamp - self.seconds < item['timestamp'] <= timestamp, side_history.get_items())
        used_amount = reduce(Wad.__add__, map(lambda item: item['amount'], items), Wad(0))

        return Wad.max(self.amount - used_amount, Wad(0))
