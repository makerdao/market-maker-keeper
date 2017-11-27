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

import itertools
import operator
from functools import reduce

from keeper import Wad


class Band:
    def __init__(self,
                 min_margin: float,
                 avg_margin: float,
                 max_margin: float,
                 min_amount: Wad,
                 avg_amount: Wad,
                 max_amount: Wad,
                 dust_cutoff: Wad):
        assert(isinstance(min_margin, float))
        assert(isinstance(avg_margin, float))
        assert(isinstance(max_margin, float))
        assert(isinstance(min_amount, Wad))
        assert(isinstance(avg_amount, Wad))
        assert(isinstance(max_amount, Wad))
        assert(isinstance(dust_cutoff, Wad))

        self.min_margin = min_margin
        self.avg_margin = avg_margin
        self.max_margin = max_margin
        self.min_amount = min_amount
        self.avg_amount = avg_amount
        self.max_amount = max_amount
        self.dust_cutoff = dust_cutoff

        assert(self.min_amount <= self.avg_amount)
        assert(self.avg_amount <= self.max_amount)
        assert(self.min_margin <= self.avg_margin)
        assert(self.avg_margin <= self.max_margin)
        assert(self.min_margin < self.max_margin)

    def includes(self, order, target_price: Wad) -> bool:
        raise NotImplemented()

    def excessive_orders(self, orders: list, target_price: Wad):
        """Return offers which need to be cancelled to bring the total order amount in the band below maximum."""
        orders_in_band = [order for order in orders if self.includes(order, target_price)]
        if self._total_amount(orders_in_band) > self.max_amount:
            def calculate_all_subsets():
                for num in range(0, len(orders_in_band)):
                    for combination in itertools.combinations(orders_in_band, num):
                        yield set(combination)

            # all possible subsets of orders which can be left uncancelled, including the empty subset
            all_subsets = list(calculate_all_subsets())

            # we are only choosing from these subsets which bring us to or below `band.max_amount`
            candidate_subsets = list(filter(lambda subset: self._total_amount(subset) <= self.max_amount, all_subsets))

            # we calculate the size of the largest subset of these, as this will result in the lowest number
            # of order cancellations i.e. lowest gas consumption for the keeper
            #
            # then we only limit interesting subsets to the ones of that size, ignoring smaller ones
            highest_cnt = max(map(lambda subset: len(subset), candidate_subsets))
            candidate_subsets = filter(lambda subset: len(subset) == highest_cnt, candidate_subsets)

            # from the interesting subsets we choose the with the highest total amount
            found_subset = sorted(candidate_subsets, key=lambda subset: self._total_amount(subset), reverse=True)[0]

            # as we are supposed to return the offers which should be cancelled, we return the complement
            # of the found subset
            return set(orders_in_band) - set(found_subset)
        else:
            return []

    @staticmethod
    def _total_amount(orders: list):
        return reduce(operator.add, map(lambda order: order.remaining_sell_amount, orders), Wad(0))


class BuyBand(Band):
    def __init__(self, dictionary: dict):
        super().__init__(min_margin=dictionary['minMargin'],
                         avg_margin=dictionary['avgMargin'],
                         max_margin=dictionary['maxMargin'],
                         min_amount=Wad.from_number(dictionary['minSaiAmount']),
                         avg_amount=Wad.from_number(dictionary['avgSaiAmount']),
                         max_amount=Wad.from_number(dictionary['maxSaiAmount']),
                         dust_cutoff=Wad.from_number(dictionary['dustCutoff']))

    def includes(self, order, target_price: Wad) -> bool:
        price = order.sell_to_buy_price
        price_min = self._apply_margin(target_price, self.min_margin)
        price_max = self._apply_margin(target_price, self.max_margin)
        return (price > price_max) and (price <= price_min)

    def avg_price(self, target_price: Wad) -> Wad:
        return self._apply_margin(target_price, self.avg_margin)

    @staticmethod
    def _apply_margin(price: Wad, margin: float) -> Wad:
        return price * Wad.from_number(1 - margin)


class SellBand(Band):
    def __init__(self, dictionary: dict):
        super().__init__(min_margin=dictionary['minMargin'],
                         avg_margin=dictionary['avgMargin'],
                         max_margin=dictionary['maxMargin'],
                         min_amount=Wad.from_number(dictionary['minWEthAmount']),
                         avg_amount=Wad.from_number(dictionary['avgWEthAmount']),
                         max_amount=Wad.from_number(dictionary['maxWEthAmount']),
                         dust_cutoff=Wad.from_number(dictionary['dustCutoff']))

    def includes(self, order, target_price: Wad) -> bool:
        price = order.buy_to_sell_price
        price_min = self._apply_margin(target_price, self.min_margin)
        price_max = self._apply_margin(target_price, self.max_margin)
        return (price > price_min) and (price <= price_max)

    def avg_price(self, target_price: Wad) -> Wad:
        return self._apply_margin(target_price, self.avg_margin)

    @staticmethod
    def _apply_margin(price: Wad, margin: float) -> Wad:
        return price * Wad.from_number(1 + margin)
