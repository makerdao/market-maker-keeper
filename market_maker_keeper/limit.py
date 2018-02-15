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
from functools import reduce

from pymaker.numeric import Wad


class Limits:
    logger = logging.getLogger()

    def __init__(self, limits: list, history: list):
        assert(isinstance(limits, list))
        self.limits = list(map(Limit, limits))
        self.history = history

    def available_limit(self, timestamp: int):
        if len(self.limits) > 0:
            return Wad.min(*map(lambda limit: limit.available_limit(timestamp, self.history), self.limits))
        else:
            return Wad.from_number(2**256 - 1)

    def use_limit(self, timestamp: int, amount: Wad):
        self.history.append({'timestamp': timestamp, 'amount': amount})


class Limit:
    def __init__(self, limit: dict):
        assert(isinstance(limit, dict))
        self.amount = Wad.from_number(limit['amount'])
        self.time = self._to_seconds(limit['time'])

    def _to_seconds(self, string: str) -> int:
        assert(isinstance(string, str))
        seconds_per_unit = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
        return int(string[:-1]) * seconds_per_unit[string[-1]]

    def available_limit(self, timestamp: int, history: list):
        history_within_time = filter(lambda item: timestamp - self.time < item['timestamp'] <= timestamp, history)
        history_used_amount = reduce(Wad.__add__, map(lambda item: item['amount'], history_within_time), Wad(0))

        return Wad.max(self.amount - history_used_amount, Wad(0))
