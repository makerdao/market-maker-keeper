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
import py
from web3 import Web3, EthereumTesterProvider

from keeper import Address, ERC20Token, Wad, DefaultGasPrice
from keeper.api.feed import DSValue
from keeper.api.oasis import SimpleMarket
from keeper.api.token import DSEthToken, DSToken
from keeper.sai_bite import SaiBite
from keeper.sai_maker_otc import SaiMakerOtc
from tests.conftest import SaiDeployment


class TestSaiMakerOtc:
    @staticmethod
    def setup_keeper(sai: SaiDeployment):
        # for Keeper
        keeper = SaiMakerOtc.__new__(SaiMakerOtc)
        keeper.web3 = sai.web3
        keeper.web3.eth.defaultAccount = keeper.web3.eth.accounts[0]
        keeper.our_address = Address(keeper.web3.eth.defaultAccount)
        keeper.chain = 'unittest'
        keeper.config = None
        keeper.terminated = False
        keeper.fatal_termination = False
        keeper._last_block_time = None
        keeper._on_block_callback = None

        # for SaiKeeper
        keeper.tub = sai.tub
        keeper.tap = sai.tap
        keeper.top = sai.top
        keeper.otc = SimpleMarket.deploy(keeper.web3)
        keeper.skr = ERC20Token(web3=keeper.web3, address=keeper.tub.skr())
        keeper.sai = ERC20Token(web3=keeper.web3, address=keeper.tub.sai())
        keeper.gem = DSEthToken(web3=keeper.web3, address=keeper.tub.gem())
        ERC20Token.register_token(keeper.tub.skr(), 'SKR')
        ERC20Token.register_token(keeper.tub.sai(), 'SAI')
        ERC20Token.register_token(keeper.tub.gem(), 'WETH')
        return keeper

    @staticmethod
    def write_sample_config(file):
        file.write("""{
            "buyBands": [
                {
                    "minMargin": 0.01,
                    "avgMargin": 0.02,
                    "maxMargin": 0.03,
                    "minSaiAmount": 50.0,
                    "avgSaiAmount": 75.0,
                    "maxSaiAmount": 100.0,
                    "dustCutoff": 0.0
                }
            ],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.03,
                    "maxMargin": 0.04,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 10.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")

    def test_should_create_offers_on_startup(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        keeper = self.setup_keeper(sai)
        keeper.round_places = 2
        keeper.arguments = lambda: None
        keeper.arguments.config = tmpdir.join("config.json")
        keeper.gas_price = DefaultGasPrice()

        # and
        self.write_sample_config(keeper.arguments.config)

        # and
        DSToken(web3=sai.web3, address=sai.tub.gem()).mint(Wad.from_number(1000)).transact()
        DSToken(web3=sai.web3, address=sai.tub.sai()).mint(Wad.from_number(1000)).transact()

        # and
        print(sai.tub.pip())
        DSValue(web3=sai.web3, address=sai.tub.pip()).poke_with_int(Wad.from_number(250).value).transact()

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(keeper.otc.active_offers()) == 2

    def test_should_cancel_offers_on_shutdown(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        keeper = self.setup_keeper(sai)
        keeper.arguments = lambda: None
        keeper.arguments.config = tmpdir.join("config.json")
        keeper.round_places = 2
        keeper.gas_price = DefaultGasPrice()

        # and
        self.write_sample_config(keeper.arguments.config)

        # and
        DSToken(web3=sai.web3, address=sai.tub.gem()).mint(Wad.from_number(1000)).transact()
        DSToken(web3=sai.web3, address=sai.tub.sai()).mint(Wad.from_number(1000)).transact()

        # and
        print(sai.tub.pip())
        DSValue(web3=sai.web3, address=sai.tub.pip()).poke_with_int(Wad.from_number(250).value).transact()

        # and
        keeper.approve()
        keeper.synchronize_offers()
        assert len(keeper.otc.active_offers()) == 2

        # when
        keeper.shutdown()

        # then
        assert len(keeper.otc.active_offers()) == 0
