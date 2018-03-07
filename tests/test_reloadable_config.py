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
from unittest.mock import MagicMock

from market_maker_keeper.reloadable_config import ReloadableConfig


class TestReloadableConfig:
    @staticmethod
    def write_sample_config(tmpdir):
        file = tmpdir.join("sample_config.json")
        file.write("""{"a": "b"}""")
        return str(file)

    @staticmethod
    def write_advanced_config(tmpdir, value):
        file = tmpdir.join("advanced_config.json")
        file.write("""{"a": \"""" + value + """\", "c": self.a}""")
        return str(file)

    @staticmethod
    def write_spread_importing_config(tmpdir):
        file = tmpdir.join("spread_importing_config_config.json")
        file.write("""{
            local spreads = import "spread-feed",

            "usedBuySpread": spreads.buySpread,
            "usedSellSpread": spreads.sellSpread
        }""")
        return str(file)

    def test_should_read_simple_file(self, tmpdir):
        # when
        config = ReloadableConfig(self.write_sample_config(tmpdir)).get_config()

        # then
        assert len(config) == 1
        assert config["a"] == "b"

    def test_should_read_advanced_file(self, tmpdir):
        # when
        config = ReloadableConfig(self.write_advanced_config(tmpdir, "b")).get_config()

        # then
        assert len(config) == 2
        assert config["a"] == "b"
        assert config["c"] == "b"

    def test_should_read_file_again_if_changed(self, tmpdir):
        # given
        reloadable_config = ReloadableConfig(self.write_advanced_config(tmpdir, "b"))

        # when
        config = reloadable_config.get_config()

        # then
        assert config["a"] == "b"

        # when
        self.write_advanced_config(tmpdir, "z")
        config = reloadable_config.get_config()

        # then
        assert config["a"] == "z"

    def test_should_import_spreads(self, tmpdir):
        # given
        spread_feed = {
            "buySpread": 0.1,
            "sellSpread": 0.2
        }

        # when
        config = ReloadableConfig(self.write_spread_importing_config(tmpdir)).get_config(spread_feed)

        # then
        assert len(config) == 2
        assert config["usedBuySpread"] == 0.1
        assert config["usedSellSpread"] == 0.2

    def test_should_use_new_spreads_even_if_config_not_changed(self, tmpdir):
        # given
        reloadable_config = ReloadableConfig(self.write_spread_importing_config(tmpdir))
        reloadable_config.logger = MagicMock()

        # when
        spread_feed = {
            "buySpread": 0.1,
            "sellSpread": 0.2
        }
        config = reloadable_config.get_config(spread_feed)

        # then
        assert config["usedBuySpread"] == 0.1
        assert config["usedSellSpread"] == 0.2

        # and
        # [a log message that the config was loaded gets generated]
        assert reloadable_config.logger.info.call_count == 1

        # when
        spread_feed = {
            "buySpread": 0.3,
            "sellSpread": 0.4
        }
        config = reloadable_config.get_config(spread_feed)

        # then
        assert config["usedBuySpread"] == 0.3
        assert config["usedSellSpread"] == 0.4

        # and
        # [a log message that the config was reloaded gets generated]
        assert reloadable_config.logger.info.call_count == 2
