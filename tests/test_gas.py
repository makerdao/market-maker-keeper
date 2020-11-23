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

import json
import pytest

from tests.helper import args
from web3 import Web3, HTTPProvider

from pygasprice_client.aggregator import Aggregator
from pymaker import Address, Contract
from pymaker.keys import register_keys, register_private_key
from pymaker.model import Token
from pymaker.numeric import Wad
from pymaker.token import DSToken
from market_maker_keeper.gas import DynamicGasPrice
from market_maker_keeper.uniswapv2_market_maker_keeper import UniswapV2MarketMakerKeeper

GWEI = 1000000000
default_max_gas = 2000
every_secs = 42

TARGET_AMOUNTS = {
    # adding 420 dai - 1 eth
    "ETH_DAI_MIN_ETH": 0.5,
    "ETH_DAI_MAX_ETH": 10000000,
    "ETH_DAI_MIN_DAI": 105,
    "ETH_DAI_MAX_DAI": 840,
}


class TestDynamicGasPrice:

    router_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Router02.abi')
    router_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Router02.bin')
    factory_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Factory.abi')
    factory_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/UniswapV2Factory.bin')
    weth_abi = Contract._load_abi(__name__, '../lib/pyexchange/pyexchange/abi/WETH.abi')
    weth_bin = Contract._load_bin(__name__, '../lib/pyexchange/pyexchange/abi/WETH.bin')

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

        self.ds_dai = DSToken.deploy(self.web3, 'DAI')
        self.ds_dai.mint(Wad.from_number(500)).transact(from_address=self.our_address)
        self.token_dai = Token("DAI", self.ds_dai.address, 18)
        self.token_weth = Token("WETH", self.weth_address, 18)

        token_config = {
            "tokens": {
                "DAI": {
                    "tokenAddress": self.ds_dai.address.address
                },
                "WETH": {
                    "tokenAddress": self.weth_address.address
                }
            }
        }
        # write token config with locally deployed addresses to file
        with open("test-token-config.json", "w+") as outfile:
            outfile.write(json.dumps(token_config))

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

    def instantiate_uniswap_keeper_using_dynamic_gas(self, pair: str) -> UniswapV2MarketMakerKeeper:
        if pair == "DAI-USDC":
            feed_price = "fixed:1.01"
        elif pair == "ETH-DAI":
            feed_price = "fixed:420"

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
                                                      f" --dynamic-gas-price"
                                                      f" --oracle-gas-price"                                                      
                                                      f" --price-feed {feed_price}"),
                                                      web3=self.web3)

    def instantiate_uniswap_keeper_using_fixed_gas(self, pair: str) -> UniswapV2MarketMakerKeeper:
        if pair == "DAI-USDC":
            feed_price = "fixed:1.01"
        elif pair == "ETH-DAI":
            feed_price = "fixed:420"

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
                                                      f" --dynamic-gas-price"
                                                      f" --fixed-gas-price 20"                                                      
                                                      f" --price-feed {feed_price}"),
                                                      web3=self.web3)

    def test_dynamic_oracle_gas_uniswap(self):
        keeper = self.instantiate_uniswap_keeper_using_dynamic_gas('ETH-DAI')

        assert isinstance(keeper.gas_price.gas_station, Aggregator)
        assert keeper.gas_price.gas_station.URL == "aggregator"
        assert isinstance(keeper.gas_price, DynamicGasPrice)

    def test_dynamic_fixed_gas_uniswap(self):
        keeper = self.instantiate_uniswap_keeper_using_dynamic_gas('ETH-DAI')

        assert keeper.gas_price.get_gas_price(0) == 20000000000
        assert isinstance(keeper.gas_price, DynamicGasPrice)