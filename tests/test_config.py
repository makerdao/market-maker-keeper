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

from mock import MagicMock

from market_maker_keeper.config import ReloadableConfig
from pymaker.logger import Logger


class TestReloadableConfig:
    logger = MagicMock(spec=Logger)

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

    def test_should_read_simple_file(self, tmpdir):
        # when
        config = ReloadableConfig(self.write_sample_config(tmpdir), self.logger).get_config()

        # then
        assert len(config) == 1
        assert config["a"] == "b"

    def test_should_read_advanced_file(self, tmpdir):
        # when
        config = ReloadableConfig(self.write_advanced_config(tmpdir, "b"), self.logger).get_config()

        # then
        assert len(config) == 2
        assert config["a"] == "b"
        assert config["c"] == "b"

    def test_should_read_file_again_if_changed(self, tmpdir):
        # given
        reloadable_config = ReloadableConfig(self.write_advanced_config(tmpdir, "b"), self.logger)

        # when
        config = reloadable_config.get_config()

        # then
        assert config["a"] == "b"

        # when
        self.write_advanced_config(tmpdir, "z")
        config = reloadable_config.get_config()

        # then
        assert config["a"] == "z"
