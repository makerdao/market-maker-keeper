# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017-2020 Exef
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

from market_maker_keeper.binance_us_market_maker_keeper import BinanceBands, BinanceUsRules

from tests.test_band import TestBands


class TestBinanceBands(TestBands):
    def test_get_precision(self):
        number = Wad.from_number(1)
        decimal_places = BinanceBands._get_decimal_places(number)
        assert(decimal_places == 0)

        number = Wad.from_number(0.1)
        decimal_places = BinanceBands._get_decimal_places(number)
        assert(decimal_places == 1)

        number = Wad.from_number(0.01)
        decimal_places = BinanceBands._get_decimal_places(number)
        assert(decimal_places == 2)

        number = Wad.from_number(0.001)
        decimal_places = BinanceBands._get_decimal_places(number)
        assert(decimal_places == 3)

        number = Wad.from_number(0.0001)
        decimal_places = BinanceBands._get_decimal_places(number)
        assert(decimal_places == 4)

    def test_should_create_both_buy_and_sell_orders_when_rules_allows_it(self, tmpdir):
        config = BandConfig.sample_config(tmpdir)
        bands = self.create_bands(config)

        price = Price(buy_price=Wad.from_number(100.01), sell_price=Wad.from_number(200.03))
        new_orders, _, _ = bands.new_orders([], [], Wad.from_number(1000000), Wad.from_number(1000000), price)

        assert(len(new_orders) == 2)
        assert(new_orders[0].is_sell is False)
        assert(new_orders[0].amount == Wad.from_number(0.78117))
        assert(new_orders[0].price == Wad.from_number(96.01))
        assert(new_orders[1].is_sell is True)
        assert(new_orders[1].price == Wad.from_number(208.03))
        assert(new_orders[1].amount == Wad.from_number(7.5))

    def test_should_create_both_buy_and_sell_orders_and_modifie_it_according_rules(self, tmpdir):
        config = BandConfig.sample_config(tmpdir)
        bands = self.create_bands(config)

        buy_price_to_round_up = Wad.from_number(100.009)
        sell_price_to_round_up = Wad.from_number(200.039)

        price = Price(buy_price=buy_price_to_round_up, sell_price=sell_price_to_round_up)
        new_orders, _, _ = bands.new_orders([], [], Wad.from_number(1000000), Wad.from_number(1000000), price)

        assert(len(new_orders) == 2)
        assert(new_orders[0].is_sell is False)
        assert(new_orders[0].price == Wad.from_number(96.01))
        assert(new_orders[0].amount == Wad.from_number(0.78117))
        assert(new_orders[1].is_sell is True)
        assert(new_orders[1].price == Wad.from_number(208.04))
        assert(new_orders[1].amount == Wad.from_number(7.5))

        buy_price_to_round_down = Wad.from_number(100.022)
        sell_price_to_round_down = Wad.from_number(200.014)

        price = Price(buy_price=buy_price_to_round_down, sell_price=sell_price_to_round_down)
        new_orders, _, _ = bands.new_orders([], [], Wad.from_number(1000000), Wad.from_number(1000000), price)

        assert(len(new_orders) == 2)
        assert(new_orders[0].is_sell is False)
        assert(new_orders[0].price == Wad.from_number(96.02))
        assert(new_orders[0].amount == Wad.from_number(0.78109))
        assert(new_orders[1].is_sell is True)
        assert(new_orders[1].price == Wad.from_number(208.01))
        assert(new_orders[1].amount == Wad.from_number(7.5))

    @staticmethod
    def create_bands(config_file, rules=None):
        if rules is None:
          rules = BinanceUsRules(pair="ETH-USDC", 
                                 min_price=Wad.from_number(0.01), 
                                 max_price=Wad.from_number(100000.0), 
                                 tick_size=Wad.from_number(0.01),
                                 min_quantity=Wad.from_number(0.00001),
                                 max_quantity=Wad.from_number(9000.0),
                                 step_size=Wad.from_number(0.00001))

        config = ReloadableConfig(str(config_file))
        return BinanceBands.read(config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History(), rules)