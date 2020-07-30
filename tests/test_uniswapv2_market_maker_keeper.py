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
import json
import py
import pytest
import unittest
import logging
import time
import threading

from unittest.mock import MagicMock
from enum import Enum
from web3 import Web3, HTTPProvider
from typing import List

from market_maker_keeper.uniswapv2_market_maker_keeper import UniswapV2MarketMakerKeeper
from pymaker import Address, Contract
from pymaker.feed import DSValue
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.model import Token
from pymaker.token import DSToken
from tests.helper import args
from pymaker.keys import register_keys, register_private_key


class INITIAL_PRICES(Enum):
    DAI_USDC_ADD_LIQUIDITY = 1.03
    DAI_USDC_REMOVE_LIQUIDITY = 1.00
    DAI_ETH_ADD_LIQUIDITY = 318


class TestUniswapV2MarketMakerKeeper:

    Irouter_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/IUniswapV2Router02.abi')['abi']
    router_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Router02.abi')
    router_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Router02.bin')
    factory_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Factory.abi')
    factory_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Factory.bin')
    weth_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/WETH.abi')
    weth_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/WETH.bin')

    logger = logging.getLogger()

    def setup_method(self):

        # Use Ganache docker container
        self.web3 = Web3(HTTPProvider("http://0.0.0.0:8555"))
        self.web3.eth.defaultAccount = Web3.toChecksumAddress("0x9596C16D7bF9323265C2F2E22f43e6c80eB3d943")
        self.our_address = Address(self.web3.eth.defaultAccount)
        
        self.private_key = "0x91cf2cc3671a365fcbf38010ff97ee31a5b7e674842663c56769e41600696ead"
        register_private_key(self.web3, self.private_key)

        self.weth_address = Contract._deploy(self.web3, self.weth_abi, self.weth_bin, [])
        self.factory_address = Contract._deploy(self.web3, self.factory_abi, self.factory_bin, [self.our_address.address])
        self.router_address = Contract._deploy(self.web3, self.router_abi, self.router_bin, [self.factory_address.address, self.weth_address.address])
        self._weth_contract = Contract._get_contract(self.web3, self.weth_abi, self.weth_address)
        
        self.deploy_tokens()

        token_config = {
            "tokens": {
                "DAI": {
                    "tokenAddress": self.ds_dai.address.address
                },
                "USDC": {
                    "tokenAddress": self.ds_usdc.address.address,
                    "tokenDecimals": 6
                },
                "WETH": {
                    "tokenAddress": self.weth_address.address
                }
            }
        }
        # write token config with locally deployed addresses to file
        with open("test-token-config.json", "w+") as outfile:
            outfile.write(json.dumps(token_config)) 


    def deploy_tokens(self):
        self.ds_dai = DSToken.deploy(self.web3, 'DAI')
        self.ds_usdc = DSToken.deploy(self.web3, 'USDC')
        self.token_dai = Token("DAI", self.ds_dai.address, 18)
        self.token_usdc = Token("USDC", self.ds_usdc.address, 6)
        self.token_weth = Token("WETH", self.weth_address, 18)

    def mint_tokens(self):
        self.ds_dai.mint(Wad(17 * 10**18)).transact(from_address=self.our_address)
        self.ds_usdc.mint(self.token_usdc.unnormalize_amount(Wad.from_number(9))).transact(from_address=self.our_address)

    def instantiate_keeper(self, pair: str, initial_price: float) -> UniswapV2MarketMakerKeeper:
        if pair == "DAI-USDC":
            feed_price = "fixed:1.025"
        elif pair == "DAI-ETH":
            feed_price = "fixed:320"
        return UniswapV2MarketMakerKeeper(args=args(f"--eth-from {self.our_address} --rpc-host http://localhost"
                                                      f" --rpc-port 8545"
                                                      f" --eth-key {self.private_key}"
                                                      f" --pair {pair}"
                                                      f" --initial-exchange-rate {initial_price}"
                                                      f" --token-config ./test-token-config.json"
                                                      f" --router-address {self.router_address.address}"
                                                      f" --factory-address {self.factory_address.address}"
                                                      f" --initial-delay 3"
                                                      f" --price-feed {feed_price}"),
                                                      web3=self.web3)

    @staticmethod
    def calculate_token_liquidity_to_add(keeper: UniswapV2MarketMakerKeeper, token_a_balance: Wad, token_b_balance: Wad) -> dict:
        return keeper._calculate_liquidity_tokens(token_a_balance, token_b_balance, Wad.from_number(keeper.initial_exchange_rate), keeper.accepted_slippage)

    @staticmethod
    def calculate_eth_liquidity_to_add(keeper: UniswapV2MarketMakerKeeper, token_a_balance: Wad, token_b_balance: Wad) -> dict:
        return keeper._calculate_liquidity_eth(token_a_balance, token_b_balance, Wad.from_number(keeper.initial_exchange_rate), keeper.accepted_slippage)

    def calculate_initial_price(self):
        pass

    def test_calculate_token_liquidity_to_add(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-USDC", INITIAL_PRICES.DAI_USDC_ADD_LIQUIDITY.value)

        # when
        dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)
        liquidity_to_add = self.calculate_token_liquidity_to_add(keeper, dai_balance, usdc_balance)

        # then
        assert all(map(lambda x: x > Wad(0), liquidity_to_add.values()))
    
    def test_calculate_eth_liquidity_to_add(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-ETH", INITIAL_PRICES.DAI_ETH_ADD_LIQUIDITY.value)

        # when
        dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        eth_balance = keeper.uniswap.get_account_eth_balance()

        liquidity_to_add = self.calculate_token_liquidity_to_add(keeper, dai_balance, eth_balance)

        # then
        assert all(map(lambda x: x > Wad(0), liquidity_to_add.values()))

    def test_should_ensure_adequate_eth_for_gas(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-ETH", INITIAL_PRICES.DAI_ETH_ADD_LIQUIDITY.value)

        # when
        dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        liquidity_to_add = keeper._calculate_liquidity_eth(dai_balance, Wad.from_number(0.5), Wad.from_number(keeper.initial_exchange_rate), keeper.accepted_slippage)

        # then
        assert liquidity_to_add is None

    def test_should_determine_add_liquidity(self):
        keeper = self.instantiate_keeper("DAI-USDC", INITIAL_PRICES.DAI_USDC_ADD_LIQUIDITY.value)
        add_liquidity, remove_liquidity = keeper.determine_liquidity_action(Wad.from_number(keeper.initial_exchange_rate))

        assert add_liquidity == True
        assert remove_liquidity == False

    def test_should_determine_remove_liquidity(self):
        keeper = self.instantiate_keeper("DAI-USDC", INITIAL_PRICES.DAI_USDC_REMOVE_LIQUIDITY.value)
        add_liquidity, remove_liquidity = keeper.determine_liquidity_action(Wad.from_number(keeper.initial_exchange_rate))

        assert add_liquidity == False
        assert remove_liquidity == True

    def test_should_add_dai_usdc_liquidity(self):
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-USDC", INITIAL_PRICES.DAI_USDC_ADD_LIQUIDITY.value)

        print("before", keeper.uniswap.get_account_token_balance(self.token_dai))
        initial_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        initial_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()

        added_liquidity = self.calculate_token_liquidity_to_add(keeper, initial_dai_balance, initial_usdc_balance)

        time.sleep(10)

        # then
        exchange_dai_balance = keeper.uniswap.get_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        exchange_usdc_balance = keeper.uniswap.get_exchange_balance(self.token_usdc, keeper.uniswap.pair_address)
        final_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        final_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        assert initial_dai_balance > final_dai_balance
        assert initial_usdc_balance > final_usdc_balance

    def test_should_add_dai_eth_liquidity(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-ETH", INITIAL_PRICES.DAI_ETH_ADD_LIQUIDITY.value)

        dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        eth_balance = keeper.uniswap.get_account_eth_balance()

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()

        time.sleep(10)

        # then
        final_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        final_eth_balance = keeper.uniswap.get_account_eth_balance()

        assert dai_balance > final_dai_balance
        assert eth_balance > final_eth_balance

    def test_should_remove_dai_usdc_liquidity(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-USDC", INITIAL_PRICES.DAI_USDC_ADD_LIQUIDITY.value)

        initial_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        initial_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()

        time.sleep(10)

        post_add_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_add_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        assert initial_dai_balance > post_add_dai_balance
        assert initial_usdc_balance > post_add_usdc_balance

        keeper.price_feed = Wad.from_number(INITIAL_PRICES.DAI_USDC_REMOVE_LIQUIDITY.value)

        time.sleep(10)

        post_remove_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_remove_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        #  TODO: asert correct remove of calculated amount
        assert post_remove_dai_balance > post_add_dai_balance
        assert post_remove_usdc_balance > post_add_usdc_balance

    def test_should_remove_dai_eth_liquidity(self):
        pass

