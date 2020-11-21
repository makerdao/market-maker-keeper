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

import signal
import json
import py
import pytest
import unittest
import logging
import time
import threading
import os

from argparse import Namespace
from enum import Enum
from web3 import Web3, HTTPProvider
from multiprocessing import Process

from market_maker_keeper.uniswapv2_market_maker_keeper import UniswapV2MarketMakerKeeper
from market_maker_keeper.staking_rewards_factory import StakingRewardsFactory, StakingRewardsName
from pyexchange.uniswap_staking_rewards import UniswapStakingRewards
from pymaker import Address, Contract
from pymaker.numeric import Wad
from pymaker.model import Token
from pymaker.token import DSToken
from tests.helper import args
from pymaker.keys import register_keys, register_private_key


class PRICES(Enum):
    DAI_USDC_ADD_LIQUIDITY = 1.03
    DAI_USDC_REMOVE_LIQUIDITY = 2.00
    ETH_DAI_ADD_LIQUIDITY = 400
    ETH_DAI_REMOVE_LIQUIDITY = 199
    WBTC_USDC_ADD_LIQUIDITY = 12100
    WBTC_USDC_REMOVE_LIQUIDITY = 8000
    KEEP_ETH_ADD_LIQUIDITY = 0.00293025
    KEEP_ETH_REMOVE_LIQUIDITY = 0.00091025

TARGET_AMOUNTS = {
    # adding 500 dai - 505 usdc
    "DAI_USDC_MIN_DAI": 490, # 2% shift
    "DAI_USDC_MAX_DAI": 510,
    "DAI_USDC_MIN_USDC": 494.9,
    "DAI_USDC_MAX_USDC": 515.1,

    # adding 420 dai - 1 eth
    "ETH_DAI_MIN_ETH": 0.5,
    "ETH_DAI_MAX_ETH": 10000000,
    "ETH_DAI_MIN_DAI": 105,
    "ETH_DAI_MAX_DAI": 840,

    # adding .042083 wbtc - 505 usdc
    "WBTC_USDC_MIN_WBTC": .0210415,
    "WBTC_USDC_MAX_WBTC": 20,
    "WBTC_USDC_MIN_USDC": 200,
    "WBTC_USDC_MAX_USDC": 1010,

    # adding 4000 keep - 6 eth
    "KEEP_ETH_MIN_KEEP": 1000,
    "KEEP_ETH_MAX_KEEP": 8000,
    "KEEP_ETH_MIN_ETH": 3,
    "KEEP_ETH_MAX_ETH": 10000000,

    # adding 1000000 lev - 244.85 eth
    "LEV_ETH_MIN_LEV": 300000,
    "LEV_ETH_MAX_LEV": 2000000,
    "LEV_ETH_MIN_ETH": 122.425,
    "LEV_ETH_MAX_ETH": 816.167
}


class TestUniswapV2MarketMakerKeeper:

    router_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Router02.abi')
    router_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Router02.bin')
    factory_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Factory.abi')
    factory_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Factory.bin')
    weth_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/WETH.abi')
    weth_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/WETH.bin')

    uni_staking_rewards_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/UniStakingRewards.abi')['abi']
    uni_staking_rewards_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/UniStakingRewards.bin')

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
                "KEEP": {
                    "tokenAddress": self.ds_keep.address.address
                },
                "LEV": {
                    "tokenAddress": self.ds_lev.address.address,
                    "tokenDecimals": 9
                },                
                "USDC": {
                    "tokenAddress": self.ds_usdc.address.address,
                    "tokenDecimals": 6
                },
                "WBTC": {
                    "tokenAddress": self.ds_wbtc.address.address,
                    "tokenDecimals": 8
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
        self.ds_keep = DSToken.deploy(self.web3, 'KEEP')
        self.ds_lev = DSToken.deploy(self.web3, 'LEV')
        self.ds_usdc = DSToken.deploy(self.web3, 'USDC')
        self.ds_wbtc = DSToken.deploy(self.web3, 'WBTC')

        self.token_dai = Token("DAI", self.ds_dai.address, 18)
        self.token_keep = Token("KEEP", self.ds_keep.address, 18)
        self.token_lev = Token("LEV", self.ds_lev.address, 9)
        self.token_usdc = Token("USDC", self.ds_usdc.address, 6)
        self.token_wbtc = Token("WBTC", self.ds_wbtc.address, 8)
        self.token_weth = Token("WETH", self.weth_address, 18)

    def deploy_staking_rewards(self, liquidity_token_address: Address):
        self.ds_reward_dai = DSToken.deploy(self.web3, 'REWARD_DAI')
        self.reward_token = Token("REWARD_DAI", self.ds_dai.address, 18)

        self.uni_staking_rewards_address = Contract._deploy(self.web3, self.uni_staking_rewards_abi, self.uni_staking_rewards_bin, [self.our_address.address, self.reward_token.address.address, liquidity_token_address.address])
        self.uni_staking_rewards = UniswapStakingRewards(self.web3, self.our_address, Address(self.uni_staking_rewards_address), "UniswapStakingRewards")

    def mint_tokens(self):
        self.ds_dai.mint(Wad.from_number(500)).transact(from_address=self.our_address)
        self.ds_keep.mint(Wad.from_number(5000)).transact(from_address=self.our_address)
        self.ds_usdc.mint(self.token_usdc.unnormalize_amount(Wad.from_number(505))).transact(from_address=self.our_address)
        self.ds_wbtc.mint(self.token_wbtc.unnormalize_amount(Wad.from_number(15))).transact(from_address=self.our_address)

    def get_target_balances(self, pair: str) -> dict:
        assert (isinstance(pair, str))

        formatted_pair = "_".join(pair.split("-")).upper()
        token_a = formatted_pair.split("_")[0]
        token_b = formatted_pair.split("_")[1]

        return {
            "min_a": TARGET_AMOUNTS[f"{formatted_pair}_MIN_{token_a}"],
            "max_a": TARGET_AMOUNTS[f"{formatted_pair}_MAX_{token_a}"],
            "min_b": TARGET_AMOUNTS[f"{formatted_pair}_MIN_{token_b}"],
            "max_b": TARGET_AMOUNTS[f"{formatted_pair}_MAX_{token_b}"]
        }

    def instantiate_keeper(self, pair: str) -> UniswapV2MarketMakerKeeper:
        if pair == "DAI-USDC":
            feed_price = "fixed:1.01"
        elif pair == "ETH-DAI":
            feed_price = "fixed:420"
        elif pair == "WBTC-USDC":
            feed_price = "fixed:12000"
        elif pair == "KEEP-ETH":
            feed_price = "fixed:0.00291025"
        elif pair == "LEV-ETH":
            feed_price = "fixed:0.00024496"

        target_balances = self.get_target_balances(pair)

        return UniswapV2MarketMakerKeeper(args=args(f"--eth-from {self.our_address} --endpoint-uri http://localhost:8555"
                                                      f" --eth-key {self.private_key}"
                                                      f" --pair {pair}"
                                                      f" --accepted-price-slippage-up 50"
                                                      f" --accepted-price-slippage-down 30"
                                                      f" --target-a-min-balance {target_balances['min_a']}"
                                                      f" --target-a-max-balance {target_balances['max_a']}"
                                                      f" --target-b-min-balance {target_balances['min_b']}"
                                                      f" --target-b-max-balance {target_balances['max_b']}"
                                                      f" --token-config ./test-token-config.json"
                                                      f" --router-address {self.router_address.address}"
                                                      f" --factory-address {self.factory_address.address}"
                                                      f" --initial-delay 3"
                                                      f" --price-feed {feed_price}"),
                                                      web3=self.web3)

    def test_should_stake_liquidity(self):
        # given
        self.mint_tokens()

        keeper = self.instantiate_keeper("ETH-DAI")
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

        # when
        self.deploy_staking_rewards(keeper.uniswap.pair_address)
        staking_rewards_contract_address = self.uni_staking_rewards_address
        staking_rewards_args = Namespace(eth_from=self.our_address, staking_rewards_name=StakingRewardsName.UNISWAP_STAKING_REWARDS, staking_rewards_contract_address=self.uni_staking_rewards_address)
        keeper.staking_rewards = StakingRewardsFactory().create_staking_rewards(staking_rewards_args, self.web3)

        keeper.staking_rewards.approve(keeper.uniswap.pair_address)

        # when REMOVE LIQUIDITY TO READD
        keeper.testing_feed_price = True
        keeper.test_price = Wad.from_number(PRICES.ETH_DAI_REMOVE_LIQUIDITY.value)

        time.sleep(10)

        keeper.testing_feed_price = True
        keeper.test_price = Wad.from_number(PRICES.ETH_DAI_ADD_LIQUIDITY.value)

        time.sleep(10)

        # then
        staked_liquidity_balance = keeper.staking_rewards.balance_of()

        assert staked_liquidity_balance > Wad(0)

    def test_should_withdraw_liquidity(self):
        # given
        self.mint_tokens()

        keeper = self.instantiate_keeper("ETH-DAI")
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

        # when
        self.deploy_staking_rewards(keeper.uniswap.pair_address)
        staking_rewards_contract_address = self.uni_staking_rewards_address
        staking_rewards_args = Namespace(eth_from=self.our_address, staking_rewards_name=StakingRewardsName.UNISWAP_STAKING_REWARDS, staking_rewards_contract_address=self.uni_staking_rewards_address)
        keeper.staking_rewards = StakingRewardsFactory().create_staking_rewards(staking_rewards_args, self.web3)

        keeper.staking_rewards.approve(keeper.uniswap.pair_address)

        # when REMOVE LIQUIDITY TO READD
        keeper.testing_feed_price = True
        keeper.test_price = Wad.from_number(PRICES.ETH_DAI_REMOVE_LIQUIDITY.value)

        time.sleep(10)

        keeper.testing_feed_price = True
        keeper.test_price = Wad.from_number(PRICES.ETH_DAI_ADD_LIQUIDITY.value)

        time.sleep(10)

        # then
        staked_liquidity_balance = keeper.staking_rewards.balance_of()
        assert staked_liquidity_balance > Wad(0)

        keeper.testing_feed_price = True
        keeper.test_price = Wad.from_number(PRICES.ETH_DAI_REMOVE_LIQUIDITY.value)

        time.sleep(10)

        staked_liquidity_balance = keeper.staking_rewards.balance_of()

        assert staked_liquidity_balance == Wad(0)

    def test_calculate_token_liquidity_to_add(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-USDC")
        keeper.uniswap_current_exchange_price = Wad.from_number(PRICES.DAI_USDC_ADD_LIQUIDITY.value)

        # when
        dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)
        liquidity_to_add = keeper.calculate_liquidity_args(dai_balance, usdc_balance)

        # then
        assert all(map(lambda x: x > Wad(0), liquidity_to_add.values()))
        assert liquidity_to_add['amount_a_desired'] > liquidity_to_add['amount_a_min']
        assert liquidity_to_add['amount_b_desired'] > liquidity_to_add['amount_b_min']
    
    def test_calculate_eth_liquidity_to_add(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("ETH-DAI")
        keeper.uniswap_current_exchange_price = Wad.from_number(PRICES.ETH_DAI_ADD_LIQUIDITY.value)

        # when
        dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        eth_balance = keeper.uniswap.get_account_eth_balance()
        
        liquidity_to_add = keeper.calculate_liquidity_args(eth_balance, dai_balance)

        # then
        assert all(map(lambda x: x > Wad(0), liquidity_to_add.values()))

        assert liquidity_to_add['amount_b_desired'] > liquidity_to_add['amount_b_min']
        assert liquidity_to_add['amount_a_desired'] > liquidity_to_add['amount_a_min']

    def test_should_ensure_adequate_eth_for_gas(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("ETH-DAI")

        # when
        dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        liquidity_to_add = keeper.calculate_liquidity_args(Wad.from_number(0.5), dai_balance)

        # then
        assert liquidity_to_add is None

    def test_should_determine_add_liquidity(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-USDC")

        # when
        add_liquidity, remove_liquidity = keeper.determine_liquidity_action()

        # then
        assert add_liquidity == True
        assert remove_liquidity == False

    def test_should_add_dai_usdc_liquidity(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-USDC")

        initial_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        initial_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()

        time.sleep(10)

        added_liquidity = keeper.calculate_liquidity_args(initial_dai_balance, initial_usdc_balance)


        # then
        exchange_dai_balance = keeper.uniswap.get_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        exchange_usdc_balance = keeper.uniswap.get_exchange_balance(self.token_usdc, keeper.uniswap.pair_address)
        final_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        final_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        assert keeper.uniswap.get_our_exchange_balance(self.token_usdc, keeper.uniswap.pair_address) > Wad.from_number(0)
        assert keeper.uniswap.get_our_exchange_balance(self.token_dai, keeper.uniswap.pair_address) > Wad.from_number(0)
        assert initial_dai_balance > final_dai_balance
        assert initial_usdc_balance > final_usdc_balance
        assert added_liquidity['amount_a_desired'] == exchange_dai_balance
        assert self.token_usdc.normalize_amount(added_liquidity['amount_b_desired']) == exchange_usdc_balance

    def test_should_add_wbtc_usdc_liquidity(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("WBTC-USDC")
        initial_wbtc_balance = keeper.uniswap.get_account_token_balance(self.token_wbtc)
        initial_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()

        time.sleep(10)

        added_liquidity = keeper.calculate_liquidity_args(initial_wbtc_balance, initial_usdc_balance)

        # then
        exchange_wbtc_balance = keeper.uniswap.get_exchange_balance(self.token_wbtc, keeper.uniswap.pair_address)
        exchange_usdc_balance = keeper.uniswap.get_exchange_balance(self.token_usdc, keeper.uniswap.pair_address)
        final_wbtc_balance = keeper.uniswap.get_account_token_balance(self.token_wbtc)
        final_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        assert initial_wbtc_balance > final_wbtc_balance
        assert initial_usdc_balance > final_usdc_balance
        assert self.token_wbtc.normalize_amount(added_liquidity['amount_a_desired']) == exchange_wbtc_balance
        assert self.token_usdc.normalize_amount(added_liquidity['amount_b_desired']) == exchange_usdc_balance

    def test_should_add_dai_eth_liquidity(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("ETH-DAI")
        dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        eth_balance = keeper.uniswap.get_account_eth_balance()

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()
        time.sleep(12)

        # then
        final_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        final_eth_balance = keeper.uniswap.get_account_eth_balance()

        assert dai_balance > final_dai_balance
        assert eth_balance > final_eth_balance # gas usage breaks eth_balance assertion
        assert keeper.uniswap.get_our_exchange_balance(self.token_dai, keeper.uniswap.pair_address) > Wad.from_number(0)
        assert keeper.uniswap.get_our_exchange_balance(self.token_weth, keeper.uniswap.pair_address) > Wad.from_number(0)

    def test_should_remove_dai_usdc_liquidity(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("DAI-USDC")
        initial_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        initial_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()

        time.sleep(10)

        added_liquidity = keeper.calculate_liquidity_args(initial_dai_balance, initial_usdc_balance)

        post_add_exchange_dai_balance = keeper.uniswap.get_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        post_add_exchange_usdc_balance = keeper.uniswap.get_exchange_balance(self.token_usdc, keeper.uniswap.pair_address)
        post_add_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_add_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        assert initial_dai_balance > post_add_dai_balance
        assert initial_usdc_balance > post_add_usdc_balance
        assert added_liquidity['amount_a_desired'] == post_add_exchange_dai_balance
        assert self.token_usdc.normalize_amount(added_liquidity['amount_b_desired']) == post_add_exchange_usdc_balance

        keeper.testing_feed_price = True
        keeper.test_price = Wad.from_number(PRICES.DAI_USDC_REMOVE_LIQUIDITY.value)

        time.sleep(10)

        post_remove_exchange_dai_balance = keeper.uniswap.get_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        post_remove_exchange_usdc_balance = keeper.uniswap.get_exchange_balance(self.token_usdc, keeper.uniswap.pair_address)
        post_remove_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_remove_usdc_balance = keeper.uniswap.get_account_token_balance(self.token_usdc)

        assert post_add_exchange_dai_balance > post_remove_exchange_dai_balance
        assert post_add_exchange_usdc_balance > post_remove_exchange_usdc_balance
        assert post_remove_dai_balance > post_add_dai_balance
        assert post_remove_usdc_balance > post_add_usdc_balance

    def test_should_remove_dai_eth_liquidity(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("ETH-DAI")
        initial_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        initial_eth_balance = keeper.uniswap.get_account_eth_balance()

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()

        time.sleep(10)

        # then
        post_add_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_add_eth_balance = keeper.uniswap.get_account_eth_balance()
        post_add_exchange_dai_balance = keeper.uniswap.get_our_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        post_add_exchange_weth_balance = keeper.uniswap.get_our_exchange_balance(self.token_weth, keeper.uniswap.pair_address)

        assert initial_dai_balance > post_add_dai_balance
        assert initial_eth_balance > post_add_eth_balance

        keeper.testing_feed_price = True
        keeper.test_price = Wad.from_number(PRICES.ETH_DAI_REMOVE_LIQUIDITY.value)

        time.sleep(25)

        post_remove_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_remove_eth_balance = keeper.uniswap.get_account_eth_balance()
        post_remove_exchange_dai_balance = keeper.uniswap.get_our_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        post_remove_exchange_weth_balance = keeper.uniswap.get_exchange_balance(self.token_weth, keeper.uniswap.pair_address)
        
        assert post_remove_exchange_dai_balance < post_add_exchange_dai_balance
        assert post_remove_exchange_weth_balance < post_add_exchange_weth_balance
        assert post_remove_dai_balance > post_add_dai_balance
        assert post_remove_eth_balance > post_add_eth_balance

    def test_should_remove_liquidity_if_price_feed_is_null(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("ETH-DAI")
        initial_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        initial_eth_balance = keeper.uniswap.get_account_eth_balance()

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()

        time.sleep(10)

        # then
        post_add_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_add_eth_balance = keeper.uniswap.get_account_eth_balance()
        post_add_exchange_dai_balance = keeper.uniswap.get_our_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        post_add_exchange_weth_balance = keeper.uniswap.get_our_exchange_balance(self.token_weth, keeper.uniswap.pair_address)

        assert post_add_exchange_dai_balance > Wad.from_number(0)
        assert post_add_exchange_weth_balance > Wad.from_number(0)
        assert initial_dai_balance > post_add_dai_balance
        assert initial_eth_balance > post_add_eth_balance

        # when
        keeper.testing_feed_price = True
        keeper.test_price = None
        keeper.price_feed_accepted_delay = 2
        
        time.sleep(25)

        # then
        post_remove_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_remove_eth_balance = keeper.uniswap.get_account_eth_balance()
        post_remove_exchange_dai_balance = keeper.uniswap.get_our_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        post_remove_exchange_weth_balance = keeper.uniswap.get_exchange_balance(self.token_weth, keeper.uniswap.pair_address)

        assert post_remove_exchange_dai_balance < post_add_exchange_dai_balance
        assert post_remove_exchange_weth_balance < post_add_exchange_weth_balance
        assert post_remove_dai_balance > post_add_dai_balance
        assert post_remove_eth_balance > post_add_eth_balance

    @unittest.skip
    def test_should_remove_liquidity_if_shutdown_signal_received(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("ETH-DAI")
        initial_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        initial_eth_balance = keeper.uniswap.get_account_eth_balance()

        # when
        # keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()
        keeper_process = Process(target=keeper.main, daemon=True).start()
        time.sleep(10)

        # then
        post_add_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_add_eth_balance = keeper.uniswap.get_account_eth_balance()
        post_add_exchange_dai_balance = keeper.uniswap.get_our_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        post_add_exchange_weth_balance = keeper.uniswap.get_our_exchange_balance(self.token_weth, keeper.uniswap.pair_address)

        assert post_add_exchange_dai_balance > Wad.from_number(0)
        assert post_add_exchange_weth_balance > Wad.from_number(0)
        assert initial_dai_balance > post_add_dai_balance
        assert initial_eth_balance > post_add_eth_balance

        # when
        # send system interrupt signal to the process and wait for shutdown
        # pid = os.getpid()
        pid = keeper_process.current_process().pid
        os.kill(pid, signal.SIGINT)
        time.sleep(10)

        # then
        post_remove_dai_balance = keeper.uniswap.get_account_token_balance(self.token_dai)
        post_remove_eth_balance = keeper.uniswap.get_account_eth_balance()
        post_remove_exchange_dai_balance = keeper.uniswap.get_our_exchange_balance(self.token_dai, keeper.uniswap.pair_address)
        post_remove_exchange_weth_balance = keeper.uniswap.get_our_exchange_balance(self.token_weth, keeper.uniswap.pair_address)

        assert post_add_exchange_weth_balance > post_remove_exchange_dai_balance
        assert post_add_exchange_weth_balance > post_remove_exchange_weth_balance

    def test_should_remove_liquidity_if_target_amounts_are_breached(self):
        # given
        self.mint_tokens()
        keeper = self.instantiate_keeper("KEEP-ETH")
        initial_keep_balance = keeper.uniswap.get_account_token_balance(self.token_keep)
        initial_eth_balance = keeper.uniswap.get_account_eth_balance()

        # when
        keeper_thread = threading.Thread(target=keeper.main, daemon=True).start()
        time.sleep(10)

        # then
        post_add_keep_balance = keeper.uniswap.get_account_token_balance(self.token_keep)
        post_add_eth_balance = keeper.uniswap.get_account_eth_balance()
        post_add_exchange_keep_balance = keeper.uniswap.get_our_exchange_balance(self.token_keep, keeper.uniswap.pair_address)
        post_add_exchange_weth_balance = keeper.uniswap.get_our_exchange_balance(self.token_weth, keeper.uniswap.pair_address)

        assert initial_keep_balance > post_add_keep_balance
        assert initial_eth_balance > post_add_eth_balance

        # when
        # execute a swap that will break the balances target amount and wait for removal
        eth_to_swap = Wad.from_number(15)
        min_amount_out = keeper.uniswap.get_amounts_out(eth_to_swap, [self.token_weth, self.token_keep])

        keeper.uniswap.swap_exact_eth_for_tokens(eth_to_swap, min_amount_out[1], [self.token_weth.address.address, self.token_keep.address.address]).transact()
        time.sleep(25)

        # then    
        post_remove_keep_balance = keeper.uniswap.get_account_token_balance(self.token_keep)
        post_remove_eth_balance = keeper.uniswap.get_account_eth_balance()
 
        assert post_remove_keep_balance > post_add_keep_balance
        assert post_remove_eth_balance > post_add_eth_balance
        assert initial_keep_balance > post_remove_keep_balance
        assert initial_eth_balance > post_remove_eth_balance
