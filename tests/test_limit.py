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

import pytest

from market_maker_keeper.limit import Limits, History
from pymaker.numeric import Wad


class TestLimits:
    time_zero = 1518440700

    @pytest.fixture
    def no_limits(self):
        return Limits([], History(), 'sell')

    @pytest.fixture
    def sample_limits(self):
        return Limits([{'amount': 100, 'time': '1h'},
                       {'amount': 500, 'time': '1d'}], History(), 'sell')

    def test_available_limit_is_always_max_if_no_limits_defined(self, no_limits):
        # expect
        assert no_limits.available_limit(self.time_zero) == Wad.from_number(2**256 - 1)
        assert no_limits.available_limit(self.time_zero + 60) == Wad.from_number(2**256 - 1)
        assert no_limits.available_limit(self.time_zero + 2*60) == Wad.from_number(2**256 - 1)

    def test_available_limit_is_always_max_if_no_limits_defined_even_when_orders_are_being_placed(self, no_limits):
        # when
        no_limits.use_limit(self.time_zero, Wad.from_number(5))
        # then
        assert no_limits.available_limit(self.time_zero - 1) == Wad.from_number(2**256 - 1)
        assert no_limits.available_limit(self.time_zero) == Wad.from_number(2**256 - 1)
        assert no_limits.available_limit(self.time_zero + 1) == Wad.from_number(2**256 - 1)

    def test_initial_limit_when_no_orders_placed_yet(self, sample_limits):
        # expect
        assert sample_limits.available_limit(self.time_zero) == Wad.from_number(100)
        assert sample_limits.available_limit(self.time_zero + 60 * 60) == Wad.from_number(100)
        assert sample_limits.available_limit(self.time_zero + 2 * 60 * 60) == Wad.from_number(100)
        assert sample_limits.available_limit(self.time_zero + 5 * 60 * 60) == Wad.from_number(100)

    def test_limit_descreases_with_new_orders(self, sample_limits):
        # when
        sample_limits.use_limit(self.time_zero, Wad.from_number(5))
        # then
        assert sample_limits.available_limit(self.time_zero - 1) == Wad.from_number(100)
        assert sample_limits.available_limit(self.time_zero) == Wad.from_number(95)
        assert sample_limits.available_limit(self.time_zero + 1) == Wad.from_number(95)

        # when
        sample_limits.use_limit(self.time_zero + 60, Wad.from_number(10))
        # then
        assert sample_limits.available_limit(self.time_zero + 59) == Wad.from_number(95)
        assert sample_limits.available_limit(self.time_zero + 60) == Wad.from_number(85)
        assert sample_limits.available_limit(self.time_zero + 61) == Wad.from_number(85)

    def test_limit_does_not_go_negative(self, sample_limits):
        # when
        sample_limits.use_limit(self.time_zero, Wad.from_number(110))
        # then
        assert sample_limits.available_limit(self.time_zero) == Wad.from_number(0)

    def test_limit_renews_when_the_slot_is_over(self, sample_limits):
        # when
        sample_limits.use_limit(self.time_zero, Wad.from_number(5))
        # then
        assert sample_limits.available_limit(self.time_zero) == Wad.from_number(95)
        assert sample_limits.available_limit(self.time_zero + 60*60 - 1) == Wad.from_number(95)
        assert sample_limits.available_limit(self.time_zero + 60*60) == Wad.from_number(100)

    def test_both_limits_are_obeyed_at_the_same_time(self, sample_limits):
        # when
        sample_limits.use_limit(self.time_zero, Wad.from_number(100))
        # then
        assert sample_limits.available_limit(self.time_zero) == Wad.from_number(0)

        # when
        sample_limits.use_limit(self.time_zero + 60*60, Wad.from_number(100))
        # then
        assert sample_limits.available_limit(self.time_zero + 60*60) == Wad.from_number(0)

        # when
        sample_limits.use_limit(self.time_zero + 60*60*2, Wad.from_number(100))
        # then
        assert sample_limits.available_limit(self.time_zero + 60*60*2) == Wad.from_number(0)

        # when
        sample_limits.use_limit(self.time_zero + 60*60*3, Wad.from_number(100))
        # then
        assert sample_limits.available_limit(self.time_zero + 60*60*3) == Wad.from_number(0)

        # when
        sample_limits.use_limit(self.time_zero + 60*60*4, Wad.from_number(100))
        # then
        assert sample_limits.available_limit(self.time_zero + 60*60*4) == Wad.from_number(0)
        assert sample_limits.available_limit(self.time_zero + 60*60*5) == Wad.from_number(0)
        assert sample_limits.available_limit(self.time_zero + 60*60*6) == Wad.from_number(0)
        assert sample_limits.available_limit(self.time_zero + 60*60*7) == Wad.from_number(0)
        assert sample_limits.available_limit(self.time_zero + 60*60*8) == Wad.from_number(0)
        assert sample_limits.available_limit(self.time_zero + 60*60*9) == Wad.from_number(0)
