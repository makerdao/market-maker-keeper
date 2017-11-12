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

from keeper import Wad
from keeper.api.oasis import OfferInfo


class BuyBand:
    def __init__(self, dictionary: dict):
        self.min_margin=dictionary['minMargin']
        self.avg_margin=dictionary['avgMargin']
        self.max_margin=dictionary['maxMargin']
        self.min_amount=Wad.from_number(dictionary['minSaiAmount'])
        self.avg_amount=Wad.from_number(dictionary['avgSaiAmount'])
        self.max_amount=Wad.from_number(dictionary['maxSaiAmount'])
        self.dust_cutoff=Wad.from_number(dictionary['dustCutoff'])

        assert(self.min_amount <= self.avg_amount)
        assert(self.avg_amount <= self.max_amount)
        assert(self.min_margin <= self.avg_margin)
        assert(self.avg_margin <= self.max_margin)
        assert(self.min_margin < self.max_margin)  # if min_margin == max_margin, we wouldn't be able to tell which order

    def includes(self, offer: OfferInfo, target_price: Wad) -> bool:
        price = offer.sell_how_much / offer.buy_how_much
        price_min = self.apply_margin(target_price, self.min_margin)
        price_max = self.apply_margin(target_price, self.max_margin)
        return (price > price_max) and (price <= price_min)

    def avg_price(self, target_price: Wad) -> Wad:
        return self.apply_margin(target_price, self.avg_margin)

    def apply_margin(self, price: Wad, margin: float) -> Wad:
        return price * Wad.from_number(1 - margin)


class SellBand:
    def __init__(self, dictionary: dict):
        self.min_margin=dictionary['minMargin']
        self.avg_margin=dictionary['avgMargin']
        self.max_margin=dictionary['maxMargin']
        self.min_amount=Wad.from_number(dictionary['minWEthAmount'])
        self.avg_amount=Wad.from_number(dictionary['avgWEthAmount'])
        self.max_amount=Wad.from_number(dictionary['maxWEthAmount'])
        self.dust_cutoff=Wad.from_number(dictionary['dustCutoff'])

        assert(self.min_amount <= self.avg_amount)
        assert(self.avg_amount <= self.max_amount)
        assert(self.min_margin <= self.avg_margin)
        assert(self.avg_margin <= self.max_margin)
        assert(self.min_margin < self.max_margin)  # if min_margin == max_margin, we wouldn't be able to tell which order

    def includes(self, offer: OfferInfo, target_price: Wad) -> bool:
        price = offer.buy_how_much / offer.sell_how_much
        price_min = self.apply_margin(target_price, self.min_margin)
        price_max = self.apply_margin(target_price, self.max_margin)
        return (price > price_min) and (price <= price_max)

    def avg_price(self, target_price: Wad) -> Wad:
        return self.apply_margin(target_price, self.avg_margin)

    def apply_margin(self, price: Wad, margin: float) -> Wad:
        return price * Wad.from_number(1 + margin)
