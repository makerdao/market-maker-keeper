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

import os
import sys

from keeper import Config
from keeper.api.oasis import SimpleMarket, MatchingMarket

sys.path.append(os.path.dirname(__file__) + "/../..")

import json

import pkg_resources
import pytest

from keeper.api import Address
from keeper.api import Wad
from keeper.api.approval import directly
from keeper.api.auth import DSGuard
from keeper.api.feed import DSValue
from keeper.api.sai import Tub, Tap, Top
from keeper.api.token import DSToken
from keeper.api.vault import DSVault
from web3 import EthereumTesterProvider
from web3 import Web3


class SaiDeployment:
    def __init__(self,
                 web3: Web3,
                 our_address: Address,
                 gem: DSToken,
                 sai: DSToken,
                 sin: DSToken,
                 skr: DSToken,
                 tub: Tub,
                 tap: Tap,
                 top: Top,
                 otc: MatchingMarket):
        self.web3 = web3
        self.our_address = our_address
        self.gem = gem
        self.sai = sai
        self.sin = sin
        self.skr = skr
        self.tub = tub
        self.tap = tap
        self.top = top
        self.otc = otc

    def get_config(self):
        return Config({
            'contracts': {
                "otc": self.otc.address.address,
                "saiTub": self.tub.address.address,
                "saiTap": self.tap.address.address,
                "saiTop": self.top.address.address
            }
        })


@pytest.fixture(scope='session')
def new_sai() -> SaiDeployment:
    #TODO duplicate of the deploy method in test_radarrelay.py
    def deploy(web3, contract_name, args=None):
        contract_factory = web3.eth.contract(abi=json.loads(pkg_resources.resource_string('keeper.api.feed', f'abi/{contract_name}.abi')),
                                             bytecode=pkg_resources.resource_string('keeper.api.feed', f'abi/{contract_name}.bin'))
        tx_hash = contract_factory.deploy(args=args)
        receipt = web3.eth.getTransactionReceipt(tx_hash)
        return receipt['contractAddress']

    web3 = Web3(EthereumTesterProvider())
    web3.eth.defaultAccount = web3.eth.accounts[0]
    our_address = Address(web3.eth.defaultAccount)
    sai = DSToken.deploy(web3, 'SAI')
    sin = DSToken.deploy(web3, 'SIN')
    gem = DSToken.deploy(web3, 'ETH')
    pip = DSValue.deploy(web3)
    skr = DSToken.deploy(web3, 'SKR')
    pot = DSVault.deploy(web3)
    pit = DSVault.deploy(web3)
    tip = deploy(web3, 'Tip')
    dad = DSGuard.deploy(web3)
    jug = deploy(web3, 'SaiJug', [sai.address.address, sin.address.address])
    jar = deploy(web3, 'SaiJar', [skr.address.address, gem.address.address, pip.address.address])

    tub = Tub.deploy(web3, Address(jar), Address(jug), pot.address, pit.address, Address(tip))
    tap = Tap.deploy(web3, tub.address, pit.address)
    top = Top.deploy(web3, tub.address, tap.address)
    otc = MatchingMarket.deploy(web3, 2600000000)

    # set permissions
    dad.permit(DSGuard.ANY, DSGuard.ANY, DSGuard.ANY).transact()
    tub.set_authority(dad.address)
    for auth in [sai, sin, skr, pot, pit, tap, top]:
        auth.set_authority(dad.address).transact()

    # whitelist pairs
    otc.add_token_pair_whitelist(sai.address, gem.address).transact()

    # approve, mint some GEMs
    tub.approve(directly())
    gem.mint(Wad.from_number(1000000)).transact()

    web3.providers[0].rpc_methods.evm_snapshot()
    return SaiDeployment(web3, our_address, gem, sai, sin, skr, tub, tap, top, otc)


@pytest.fixture()
def sai(new_sai: SaiDeployment) -> SaiDeployment:
    new_sai.web3.providers[0].rpc_methods.evm_revert()
    new_sai.web3.providers[0].rpc_methods.evm_snapshot()
    new_sai.otc._none_offers = set()
    return new_sai
