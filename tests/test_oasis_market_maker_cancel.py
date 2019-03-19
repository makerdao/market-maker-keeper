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

import unittest

from market_maker_keeper.oasis_market_maker_cancel import OasisMarketMakerCancel
from pymaker import Address
from pymaker.approval import directly
from pymaker.deployment import Deployment
from pymaker.numeric import Wad
from pymaker.token import DSToken
from tests.helper import args


class TestOasisMarketMakerCancel:
    @unittest.skip
    def test_should_cancel_orders_owned_by_us(self, deployment: Deployment):
        # given
        keeper = OasisMarketMakerCancel(args=args(f"--eth-from {deployment.web3.eth.defaultAccount} "
                                             f"--oasis-address {deployment.otc.address}"),
                                        web3=deployment.web3)

        # and
        DSToken(web3=deployment.web3, address=deployment.gem.address).mint(Wad.from_number(1000)).transact()
        DSToken(web3=deployment.web3, address=deployment.sai.address).mint(Wad.from_number(1000)).transact()

        # and
        deployment.otc.approve([deployment.gem, deployment.sai], directly())
        deployment.otc.make(deployment.gem.address, Wad.from_number(10), deployment.sai.address, Wad.from_number(5)).transact()
        deployment.otc.make(deployment.sai.address, Wad.from_number(5), deployment.gem.address, Wad.from_number(12)).transact()
        assert len(deployment.otc.get_orders()) == 2

        # when
        keeper.main()

        # then
        assert len(deployment.otc.get_orders()) == 0

    @unittest.skip
    def test_should_ignore_orders_owned_by_others(self, deployment: Deployment):
        # given
        keeper = OasisMarketMakerCancel(args=args(f"--eth-from {deployment.web3.eth.defaultAccount} "
                                             f"--oasis-address {deployment.otc.address}"),
                                        web3=deployment.web3)

        # and
        DSToken(web3=deployment.web3, address=deployment.gem.address).mint(Wad.from_number(1000)).transact()
        DSToken(web3=deployment.web3, address=deployment.sai.address).mint(Wad.from_number(1000)).transact()

        # and
        deployment.gem.transfer(Address(deployment.web3.eth.accounts[1]), Wad.from_number(500)).transact()
        deployment.sai.transfer(Address(deployment.web3.eth.accounts[1]), Wad.from_number(500)).transact()

        # and
        deployment.otc.approve([deployment.gem, deployment.sai], directly())
        deployment.otc.make(deployment.gem.address, Wad.from_number(10), deployment.sai.address, Wad.from_number(5)).transact()

        # and
        deployment.web3.eth.defaultAccount = deployment.web3.eth.accounts[1]
        deployment.otc.approve([deployment.gem, deployment.sai], directly())
        deployment.otc.make(deployment.sai.address, Wad.from_number(5), deployment.gem.address, Wad.from_number(12)).transact()
        deployment.web3.eth.defaultAccount = deployment.web3.eth.accounts[0]

        # and
        assert len(deployment.otc.get_orders()) == 2

        # when
        keeper.main()

        # then
        assert len(deployment.otc.get_orders()) == 1
        assert deployment.otc.get_orders()[0].maker == Address(deployment.web3.eth.accounts[1])

    @unittest.skip
    def test_should_use_gas_price_specified(self, deployment: Deployment):
        # given
        some_gas_price = 15000000000
        keeper = OasisMarketMakerCancel(args=args(f"--eth-from {deployment.web3.eth.defaultAccount} "
                                             f"--oasis-address {deployment.otc.address} "
                                             f"--gas-price {some_gas_price}"),
                                        web3=deployment.web3)

        # and
        DSToken(web3=deployment.web3, address=deployment.gem.address).mint(Wad.from_number(1000)).transact()
        DSToken(web3=deployment.web3, address=deployment.sai.address).mint(Wad.from_number(1000)).transact()

        # and
        deployment.otc.approve([deployment.gem, deployment.sai], directly())
        deployment.otc.make(deployment.sai.address, Wad.from_number(5), deployment.gem.address, Wad.from_number(12)).transact()
        assert len(deployment.otc.get_orders()) == 1

        # when
        keeper.main()

        # then
        assert len(deployment.otc.get_orders()) == 0
        assert deployment.web3.eth.getBlock('latest', True)['transactions'][0]['gasPrice'] == some_gas_price
