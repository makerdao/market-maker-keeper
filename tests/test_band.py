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

from market_maker_keeper.band import Bands
from market_maker_keeper.feed import EmptyFeed, FixedFeed
from market_maker_keeper.limit import History
from market_maker_keeper.price_feed import Price
from market_maker_keeper.reloadable_config import ReloadableConfig
from tests.band_config import BandConfig
from pymaker.numeric import Wad


class FakeOrder:
    def __init__(self, amount: Wad, price: Wad):
        self.amount = amount
        self.price = price

    @property
    def sell_to_buy_price(self) -> Wad:
        return self.price

    @property
    def buy_to_sell_price(self) -> Wad:
        return self.price

    @property
    def remaining_sell_amount(self) -> Wad:
        return self.amount


class TestBands:
    def test_should_not_create_orders_if_neither_buy_nor_sell_price_available(self, tmpdir):
        # given
        config = BandConfig.sample_config(tmpdir)
        bands = self.create_bands(config)

        # when
        price = Price(buy_price=None, sell_price=None)
        new_orders, _, _ = bands.new_orders([], [], Wad.from_number(1000000), Wad.from_number(1000000), price)

        # then
        assert(new_orders == [])

    def test_should_create_only_buy_orders_if_only_buy_price_is_available(self, tmpdir):
        # given
        config = BandConfig.sample_config(tmpdir)
        bands = self.create_bands(config)

        # when
        price = Price(buy_price=Wad.from_number(100), sell_price=None)
        new_orders, _, _ = bands.new_orders([], [], Wad.from_number(1000000), Wad.from_number(1000000), price)

        # then
        assert(len(new_orders) == 1)
        assert(new_orders[0].is_sell is False)
        assert(new_orders[0].price == Wad.from_number(96))
        assert(new_orders[0].amount == Wad.from_number(0.78125))

    def test_should_create_only_sell_orders_if_only_sell_price_is_available(self, tmpdir):
        # given
        config = BandConfig.sample_config(tmpdir)
        bands = self.create_bands(config)

        # when
        price = Price(buy_price=None, sell_price=Wad.from_number(200))
        new_orders, _, _ = bands.new_orders([], [], Wad.from_number(1000000), Wad.from_number(1000000), price)

        # then
        assert(len(new_orders) == 1)
        assert(new_orders[0].is_sell is True)
        assert(new_orders[0].price == Wad.from_number(208))
        assert(new_orders[0].amount == Wad.from_number(7.5))

    def test_should_create_both_buy_and_sell_orders_if_both_prices_are_available(self, tmpdir):
        # given
        config = BandConfig.sample_config(tmpdir)
        bands = self.create_bands(config)

        # when
        price = Price(buy_price=Wad.from_number(100), sell_price=Wad.from_number(200))
        new_orders, _, _ = bands.new_orders([], [], Wad.from_number(1000000), Wad.from_number(1000000), price)

        # then
        assert(len(new_orders) == 2)
        assert(new_orders[0].is_sell is False)
        assert(new_orders[0].price == Wad.from_number(96))
        assert(new_orders[0].amount == Wad.from_number(0.78125))
        assert(new_orders[1].is_sell is True)
        assert(new_orders[1].price == Wad.from_number(208))
        assert(new_orders[1].amount == Wad.from_number(7.5))

    def test_should_not_cancel_anything_if_no_orders_to_cancel_regardless_of_price_availability(self, tmpdir):
        # given
        config = BandConfig.sample_config(tmpdir)
        bands = self.create_bands(config)

        # when
        price = Price(buy_price=Wad.from_number(100), sell_price=Wad.from_number(200))
        orders_to_cancel = bands.cancellable_orders([], [], price)
        # then
        assert(orders_to_cancel == [])

        # when
        price = Price(buy_price=Wad.from_number(100), sell_price=None)
        orders_to_cancel = bands.cancellable_orders([], [], price)
        # then
        assert(orders_to_cancel == [])

        # when
        price = Price(buy_price=None, sell_price=Wad.from_number(200))
        orders_to_cancel = bands.cancellable_orders([], [], price)
        # then
        assert(orders_to_cancel == [])

        # when
        price = Price(buy_price=None, sell_price=None)
        orders_to_cancel = bands.cancellable_orders([], [], price)
        # then
        assert(orders_to_cancel == [])

    def test_should_cancel_orders_if_price_disappears(self, tmpdir):
        # given
        config = BandConfig.sample_config(tmpdir)
        bands = self.create_bands(config)

        # and
        buy_order = FakeOrder(Wad.from_number(75), Wad.from_number(96))
        sell_order = FakeOrder(Wad.from_number(7.5), Wad.from_number(208))

        # when
        price = Price(buy_price=Wad.from_number(100), sell_price=Wad.from_number(200))
        orders_to_cancel = bands.cancellable_orders([buy_order], [sell_order], price)
        # then
        assert(orders_to_cancel == [])

        # when
        price = Price(buy_price=Wad.from_number(100), sell_price=None)
        orders_to_cancel = bands.cancellable_orders([buy_order], [sell_order], price)
        # then
        assert(orders_to_cancel == [sell_order])

        # when
        price = Price(buy_price=None, sell_price=Wad.from_number(200))
        orders_to_cancel = bands.cancellable_orders([buy_order], [sell_order], price)
        # then
        assert(orders_to_cancel == [buy_order])

        # when
        price = Price(buy_price=None, sell_price=None)
        orders_to_cancel = bands.cancellable_orders([buy_order], [sell_order], price)
        # then
        assert(orders_to_cancel == [buy_order, sell_order])

    @staticmethod
    def create_bands(config_file):
        config = ReloadableConfig(str(config_file))
        return Bands.read(config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())

