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

from market_maker_keeper.gas import GasPriceFile


class TestGasPriceFile:
    @staticmethod
    def config_file(body, tmpdir):
        file = tmpdir.join("gas_price.json")
        file.write(body)
        return str(file)

    def test_can_behave_as_default_gas_price(self, tmpdir):
        # given
        file = self.config_file("""{}""", tmpdir)
        file_gas_price = GasPriceFile(file)

        # expect
        assert file_gas_price.get_gas_price(0) is None
        assert file_gas_price.get_gas_price(1) is None
        assert file_gas_price.get_gas_price(1000000) is None

    def test_can_behave_as_fixed_gas_price(self, tmpdir):
        # given
        file = self.config_file("""{"gasPrice": 7000000000}""", tmpdir)
        file_gas_price = GasPriceFile(file)

        # expect
        assert file_gas_price.get_gas_price(0) == 7000000000
        assert file_gas_price.get_gas_price(1) == 7000000000
        assert file_gas_price.get_gas_price(1000000) == 7000000000

    def test_can_behave_as_increasing_gas_price_without_max(self, tmpdir):
        # given
        file = self.config_file("""{
            "gasPrice": 7000000000,
            "gasPriceIncrease": 1000000000,
            "gasPriceIncreaseEvery": 60}""", tmpdir)
        file_gas_price = GasPriceFile(file)

        # expect
        assert file_gas_price.get_gas_price(0) == 7000000000
        assert file_gas_price.get_gas_price(1) == 7000000000
        assert file_gas_price.get_gas_price(59) == 7000000000
        assert file_gas_price.get_gas_price(60) == 8000000000
        assert file_gas_price.get_gas_price(119) == 8000000000
        assert file_gas_price.get_gas_price(120) == 9000000000
        assert file_gas_price.get_gas_price(1200) == 27000000000

    def test_can_behave_as_increasing_gas_price_with_max(self, tmpdir):
        # given
        file = self.config_file("""{
            "gasPrice": 7000000000,
            "gasPriceIncrease": 1000000000,
            "gasPriceIncreaseEvery": 60,
            "gasPriceMax": 12000000000}""", tmpdir)
        file_gas_price = GasPriceFile(file)

        # expect
        assert file_gas_price.get_gas_price(0) == 7000000000
        assert file_gas_price.get_gas_price(1) == 7000000000
        assert file_gas_price.get_gas_price(59) == 7000000000
        assert file_gas_price.get_gas_price(60) == 8000000000
        assert file_gas_price.get_gas_price(119) == 8000000000
        assert file_gas_price.get_gas_price(120) == 9000000000
        assert file_gas_price.get_gas_price(1200) == 12000000000
