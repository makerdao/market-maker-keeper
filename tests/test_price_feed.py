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

import time
from typing import Optional
from typing import Tuple

from market_maker_keeper.feed import Feed
from market_maker_keeper.price_feed import PriceFeed, BackupPriceFeed, AveragePriceFeed, Price, WebSocketPriceFeed, \
    ReversePriceFeed
from pymaker.numeric import Wad


class FakeFeed(Feed):
    def __init__(self, data: dict):
        assert(isinstance(data, dict))
        self.data = data

    def get(self) -> Tuple[dict, float]:
        return self.data, time.time()


class FakePriceFeed(PriceFeed):
    def __init__(self):
        self.price = None

    def get_price(self) -> Price:
        return Price(buy_price=self.price, sell_price=self.price)

    def set_price(self, price: Optional[Wad]):
        self.price = price


class TestWebSocketPriceFeed:
    def test_should_handle_no_price(self):
        # when
        price_feed = WebSocketPriceFeed(FakeFeed({}))

        # then
        assert(price_feed.get_price().buy_price is None)
        assert(price_feed.get_price().sell_price is None)

    def test_should_use_same_buy_and_sell_price_if_only_one_price_available(self):
        # when
        price_feed = WebSocketPriceFeed(FakeFeed({"price": "125.75"}))

        # then
        assert(price_feed.get_price().buy_price == Wad.from_number(125.75))
        assert(price_feed.get_price().sell_price == Wad.from_number(125.75))

    def test_should_use_individual_buy_and_sell_prices_if_both_available(self):
        # when
        price_feed = WebSocketPriceFeed(FakeFeed({"buyPrice": "120.75", "sellPrice": "130.75"}))

        # then
        assert(price_feed.get_price().buy_price == Wad.from_number(120.75))
        assert(price_feed.get_price().sell_price == Wad.from_number(130.75))

    def test_should_default_to_price_if_no_buy_price_or_no_sell_price(self):
        # when
        price_feed = WebSocketPriceFeed(FakeFeed({"price": "125.0", "buyPrice": "120.75"}))
        # then
        assert(price_feed.get_price().buy_price == Wad.from_number(120.75))
        assert(price_feed.get_price().sell_price == Wad.from_number(125.0))

        # when
        price_feed = WebSocketPriceFeed(FakeFeed({"price": "125.0", "sellPrice": "130.75"}))
        # then
        assert(price_feed.get_price().buy_price == Wad.from_number(125.0))
        assert(price_feed.get_price().sell_price == Wad.from_number(130.75))

    def test_should_handle_only_buy_price_or_only_sell_price(self):
        # when
        price_feed = WebSocketPriceFeed(FakeFeed({"buyPrice": "120.75"}))
        # then
        assert(price_feed.get_price().buy_price == Wad.from_number(120.75))
        assert(price_feed.get_price().sell_price is None)

        # when
        price_feed = WebSocketPriceFeed(FakeFeed({"sellPrice": "130.75"}))
        # then
        assert(price_feed.get_price().buy_price is None)
        assert(price_feed.get_price().sell_price == Wad.from_number(130.75))


class TestAveragePriceFeed:
    def test_no_values(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        average_price_feed = AveragePriceFeed([price_feed_1, price_feed_2])

        # expect
        assert average_price_feed.get_price().buy_price is None
        assert average_price_feed.get_price().sell_price is None

    def test_value_1(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        average_price_feed = AveragePriceFeed([price_feed_1, price_feed_2])

        # and
        price_feed_1.set_price(Wad.from_number(10.5))

        # expect
        assert average_price_feed.get_price().buy_price == Wad.from_number(10.5)
        assert average_price_feed.get_price().sell_price == Wad.from_number(10.5)

    def test_value_2(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        average_price_feed = AveragePriceFeed([price_feed_1, price_feed_2])

        # and
        price_feed_2.set_price(Wad.from_number(17.5))

        # expect
        assert average_price_feed.get_price().buy_price == Wad.from_number(17.5)
        assert average_price_feed.get_price().sell_price == Wad.from_number(17.5)

    def test_two_values(self):
        # given
        price_feed_1 = FakePriceFeed()
        price_feed_2 = FakePriceFeed()
        average_price_feed = AveragePriceFeed([price_feed_1, price_feed_2])

        # and
        price_feed_1.set_price(Wad.from_number(10.5))
        price_feed_2.set_price(Wad.from_number(17.5))

        # expect
        assert average_price_feed.get_price().buy_price == Wad.from_number(14.0)
        assert average_price_feed.get_price().sell_price == Wad.from_number(14.0)


class TestReversePriceFeed:
    def test_no_values(self):
        # given
        price_feed = FakePriceFeed()
        reverse_price_feed = ReversePriceFeed(price_feed)

        # expect
        assert reverse_price_feed.get_price().buy_price is None
        assert reverse_price_feed.get_price().sell_price is None

    def test_values(self):
        # given
        price_feed = FakePriceFeed()
        reverse_price_feed = ReversePriceFeed(price_feed)

        # and
        price_feed.set_price(Wad.from_number(500))

        # expect
        assert reverse_price_feed.get_price().buy_price == Wad.from_number(0.002)
        assert reverse_price_feed.get_price().sell_price == Wad.from_number(0.002)


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
        assert backup_price_feed.get_price().buy_price is None
        assert backup_price_feed.get_price().sell_price is None

        # when
        price_feed_2.set_price(Wad.from_number(20))
        # then
        assert backup_price_feed.get_price().buy_price == Wad.from_number(20)
        assert backup_price_feed.get_price().sell_price == Wad.from_number(20)

        # when
        price_feed_1.set_price(Wad.from_number(10))
        # then
        assert backup_price_feed.get_price().buy_price == Wad.from_number(10)
        assert backup_price_feed.get_price().sell_price == Wad.from_number(10)

        # when
        price_feed_3.set_price(Wad.from_number(30))
        # then
        assert backup_price_feed.get_price().buy_price == Wad.from_number(10)
        assert backup_price_feed.get_price().sell_price == Wad.from_number(10)

        # when
        price_feed_1.set_price(None)
        # then
        assert backup_price_feed.get_price().buy_price == Wad.from_number(20)
        assert backup_price_feed.get_price().sell_price == Wad.from_number(20)

        # when
        price_feed_2.set_price(None)
        # then
        assert backup_price_feed.get_price().buy_price == Wad.from_number(30)
        assert backup_price_feed.get_price().sell_price == Wad.from_number(30)

        # when
        price_feed_3.set_price(None)
        # then
        assert backup_price_feed.get_price().buy_price is None
        assert backup_price_feed.get_price().sell_price is None
