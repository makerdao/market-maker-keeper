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

from typing import Optional

from market_maker_keeper.price_feed import PriceFeed, BackupPriceFeed, AveragePriceFeed
from pymaker.numeric import Wad


class FakePriceFeed(PriceFeed):
    def __init__(self):
        self.price = None

    def get_price(self) -> Optional[Wad]:
        return self.price

    def set_price(self, price: Optional[Wad]):
        self.price = price


class TestAveragePriceFeed:
    def test_no_values(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        average_price_feed = AveragePriceFeed([price_feed_1, price_feed_2])

        # expect
        assert average_price_feed.get_price() is None

    def test_value_1(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        average_price_feed = AveragePriceFeed([price_feed_1, price_feed_2])

        # and
        price_feed_1.set_price(Wad.from_number(10.5))

        # expect
        assert average_price_feed.get_price() == Wad.from_number(10.5)

    def test_value_2(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        average_price_feed = AveragePriceFeed([price_feed_1, price_feed_2])

        # and
        price_feed_2.set_price(Wad.from_number(17.5))

        # expect
        assert average_price_feed.get_price() == Wad.from_number(17.5)

    def test_two_values(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        average_price_feed = AveragePriceFeed([price_feed_1, price_feed_2])

        # and
        price_feed_1.set_price(Wad.from_number(10.5))
        price_feed_2.set_price(Wad.from_number(17.5))

        # expect
        assert average_price_feed.get_price() == Wad.from_number(14.0)


class TestBackupPriceFeed:
    def test_backup_behaviour(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        price_feed_3 = FakePriceFeed()

        # and
        backup_price_feed = BackupPriceFeed([price_feed_1, price_feed_2, price_feed_3])

        # when
        # (no price is available)
        # then
        assert backup_price_feed.get_price() is None

        # when
        price_feed_2.set_price(Wad.from_number(20))
        # then
        assert backup_price_feed.get_price() == Wad.from_number(20)

        # when
        price_feed_1.set_price(Wad.from_number(10))
        # then
        assert backup_price_feed.get_price() == Wad.from_number(10)

        # when
        price_feed_3.set_price(Wad.from_number(30))
        # then
        assert backup_price_feed.get_price() == Wad.from_number(10)

        # when
        price_feed_1.set_price(None)
        # then
        assert backup_price_feed.get_price() == Wad.from_number(20)

        # when
        price_feed_2.set_price(None)
        # then
        assert backup_price_feed.get_price() == Wad.from_number(30)

        # when
        price_feed_3.set_price(None)
        # then
        assert backup_price_feed.get_price() is None
