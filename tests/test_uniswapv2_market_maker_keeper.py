# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2020 MikeHathaway
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

import shutil
from functools import reduce

import py
import pytest
import unittest
from unittest.mock import MagicMock

from market_maker_keeper.uniswapv2_market_maker_keeper import UniswapV2MarketMakerKeeper
from pymaker import Address
from pymaker.deployment import Deployment
from pymaker.etherdelta import EtherDelta
from pymaker.feed import DSValue
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.token import DSToken
from pymaker.util import eth_balance
from tests.band_config import BandConfig
from tests.helper import args

@unittest.skip("TestUniswapV2MarketMakerKeeper testing skipping")
class TestUniswapV2MarketMakerKeeper:
    @staticmethod
    def mint_tokens(deployment: Deployment):
        DSToken(web3=deployment.web3, address=deployment.tub.gem()).mint(Wad.from_number(1000)).transact()
        DSToken(web3=deployment.web3, address=deployment.tub.sai()).mint(Wad.from_number(1000)).transact()

    @staticmethod
    def set_price(deployment: Deployment, price: Wad):
        DSValue(web3=deployment.web3, address=deployment.tub.pip()).poke_with_int(price.value).transact()

    def orders(self, keeper: UniswapV2MarketMakerKeeper):
        return list(filter(lambda order: order.remaining_sell_amount > Wad(0), keeper.our_orders))

    def orders_by_token(self, keeper: UniswapV2MarketMakerKeeper, token_address: Address):
        return list(filter(lambda order: order.pay_token == token_address, self.orders(keeper)))

    @staticmethod
    def orders_sorted(orders: list) -> list:
        return sorted(orders, key=lambda order: (order.pay_amount, order.buy_amount))

    def test_should_calculate_exchange_rate(self, deployment: Deployment, tmpdir: py.path.local):
        pass

    def test_should_calculate_liquidity_tokens(self) :
        keeper = UniswapV2MarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --rpc-host {config_file}"
                                                      f" --rpc-port 8545"
                                                      f" --eth-key {eth_key}"
                                                      f" --graph-url https://127.0.0.1:99999/"
                                                      f" --pair MKR-DAI"
                                                      f" --token-a-address {deployed_token_a_contract_address}"
                                                      f" --token-b-address {deployed_token_b_contract_address}"
                                                      f" --pair MKR-DAI"
                                                      f" --uniswap-feed ws://"),
                                            web3=deployment.web3)
        keeper.lifecycle = Lifecycle(web3=keeper.web3)

        token_a_balance = ''
        token_b_balance = ''
        uniswap_current_exchange_price = Wad.from_number()
        accepted_slippage = Wad.from_number()

        keeper._calculate_liquidity_tokens = MagicMock()

        expected_args_output = {

        }


    @unittest.skip
    def test_should_run_approval_on_startup(self, deployment: Deployment, tmpdir: py.path.local):
        pass

    def test_should_add_eth_dai_liquidity_if_not_added_already(self, deployment: Deployment, tmpdir: py.path.local):
        pass

    def test_should_add_mkr_dai_liquidity_if_not_added_already(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = RPC_HOST
        eth_key = ""

        # and
        keeper = UniswapV2MarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --rpc-host {config_file}"
                                                       f" --rpc-port 8545"
                                                       f" --eth-key {eth_key}"
                                                       f" --graph-url https://127.0.0.1:99999/"
                                                       f" --pair MKR-DAI"
                                                       f" --token-a-address {deployed_token_a_contract_address}"
                                                       f" --token-b-address {deployed_token_b_contract_address}"
                                                       f" --pair MKR-DAI"
                                                       f" --uniswap-feed ws://"),                                           
                                             web3=deployment.web3)
        keeper.lifecycle = Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()

        # and
        deployment.etherdelta.deposit(Wad.from_number(16)).transact()

        # when
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert deployment.etherdelta.balance_of(deployment.our_address) >= Wad.from_number(17)

