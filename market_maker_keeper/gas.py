# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017-2020 reverendus, MikeHathaway
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

from argparse import Namespace, ArgumentParser
from typing import Optional

from pymaker.gas import GasPrice, GeometricGasPrice, IncreasingGasPrice, FixedGasPrice, DefaultGasPrice, NodeAwareGasPrice
from pygasprice_client import EtherchainOrg, EthGasStation, POANetwork
from pygasprice_client.aggregator import Aggregator
from web3 import Web3

def add_gas_arguments(parser: ArgumentParser):
    gas_group = parser.add_mutually_exclusive_group()

    gas_group.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                        help="DEPRACATED use dynamic-gas-price instead where possible. Use smart gas pricing strategy, based on the ethgasstation.info feed.")

    gas_group.add_argument("--dynamic-gas-price", dest='dynamic_gas_price', action='store_true',
                        help="Use dynamic gas pricing strategy, based on pygasprice-client.aggregator")

    parser.add_argument("--oracle-gas-price", action='store_true',
                            help="Use a fast gas price aggregated across multiple oracles")

    parser.add_argument('--fixed-gas-price', type=float, default=20,
                           help="Uses a fixed value (in Gwei) instead of an external API to determine initial gas")

    parser.add_argument("--ethgasstation-api-key", type=str, default=None, help="ethgasstation API key")

    parser.add_argument("--etherscan-api-key", type=str, default=None, help="etherscan API key")

    parser.add_argument("--poanetwork-url", type=str, default=None, help="Alternative POANetwork URL")

    parser.add_argument("--gas-replace-after", type=int, default=42,
                        help="Replace pending transactions after this many seconds")
    parser.add_argument("--gas-initial-multiplier", type=float, default=1.0,
                        help="Adjusts the initial API-provided 'fast' gas price")
    parser.add_argument("--gas-reactive-multiplier", type=float, default=1.424,
                        help="Increases gas price when transactions haven't been mined after some time")
    parser.add_argument("--gas-maximum", type=float, default=8000,
                        help="Places an upper bound (in Gwei) on the amount of gas to use for a single TX")


class SmartGasPrice(GasPrice):
    """
    DEPRACATED. This class is maintained for legacy support. All new development should utilize DynamicGasPrice below

    Simple and smart gas price scenario.
    Uses an EthGasStation feed. Starts with fast+10GWei, adding another 10GWei each 60 seconds
    up to fast+50GWei maximum. Falls back to a default scenario (incremental as well) if
    the EthGasStation feed unavailable for more than 10 minutes.
    """

    def __init__(self, api_key: None):
        self.gas_station = EthGasStation(refresh_interval=60, expiry=600, api_key=api_key)

    def get_gas_price(self, time_elapsed: int) -> Optional[int]:
        fast_price = self.gas_station.fast_price()
        if fast_price is not None:
            # start from fast_price + 10 GWei
            # increase by 10 GWei every 60 seconds
            # max is fast_price + 50 GWei
            return min(int(fast_price*1.1) + int(time_elapsed/60)*(10*self.GWEI), int(fast_price*1.1)+(50*self.GWEI))
        else:
            # default gas pricing when EthGasStation feed is down
            return IncreasingGasPrice(initial_price=20*self.GWEI,
                                      increase_by=10*self.GWEI,
                                      every_secs=60,
                                      max_price=100*self.GWEI).get_gas_price(time_elapsed)


class DynamicGasPrice(NodeAwareGasPrice):
    every_secs = 42

    def __init__(self, web3: Web3, arguments: Namespace):
        assert isinstance(web3, Web3)

        self.gas_station = None
        self.fixed_gas = None
        self.web3 = web3
        if arguments.oracle_gas_price:
            self.gas_station = Aggregator(refresh_interval=60, expiry=600,
                                          ethgasstation_api_key=arguments.ethgasstation_api_key,
                                          poa_network_alt_url=arguments.poanetwork_url,
                                          etherscan_api_key=arguments.etherscan_api_key,
                                          gasnow_app_name="makerdao/market-maker-keeper")
        elif arguments.fixed_gas_price:
            self.fixed_gas = int(round(arguments.fixed_gas_price * self.GWEI))
        self.initial_multiplier = arguments.gas_initial_multiplier
        self.reactive_multiplier = arguments.gas_reactive_multiplier
        self.gas_maximum = int(round(arguments.gas_maximum * self.GWEI))
        if self.fixed_gas:
            assert self.fixed_gas <= self.gas_maximum

    def __del__(self):
        if self.gas_station:
            self.gas_station.running = False


    def get_gas_price(self, time_elapsed: int) -> Optional[int]:
        # start with fast price from the configured gas API
        fast_price = self.gas_station.fast_price() if self.gas_station else None

        # if API produces no price, or remote feed not configured, start with a fixed price
        if fast_price is None:
            if self.fixed_gas:
                initial_price = self.fixed_gas
            else:
                initial_price = int(round(self.get_node_gas_price() * self.initial_multiplier))
        # otherwise, use the API's fast price, adjusted by a coefficient, as our starting point
        else:
            initial_price = int(round(fast_price * self.initial_multiplier))

        return GeometricGasPrice(initial_price=initial_price,
                                 every_secs=DynamicGasPrice.every_secs,
                                 coefficient=self.reactive_multiplier,
                                 max_price=self.gas_maximum).get_gas_price(time_elapsed)

class GasPriceFactory:
    @staticmethod
    def create_gas_price(web3: Web3, arguments: Namespace) -> GasPrice:
        if arguments.smart_gas_price:
            return SmartGasPrice(arguments.ethgasstation_api_key)
        elif arguments.dynamic_gas_price:
            return DynamicGasPrice(web3, arguments)
        else:
            return DefaultGasPrice()
