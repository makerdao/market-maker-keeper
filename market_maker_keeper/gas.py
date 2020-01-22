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

from typing import Optional

from ethgasstation_client import EthGasStation
from pymaker.gas import GasPrice, IncreasingGasPrice, FixedGasPrice, DefaultGasPrice


class SmartGasPrice(GasPrice):
    """Simple and smart gas price scenario.

    Uses an EthGasStation feed. Starts with fast+10GWei, adding another 10GWei each 60 seconds
    up to fast+50GWei maximum. Falls back to a default scenario (incremental as well) if
    the EthGasStation feed unavailable for more than 10 minutes.
    """

    GWEI = 1000000000

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
            return IncreasingGasPrice(initial_price=50*self.GWEI,
                                      increase_by=10*self.GWEI,
                                      every_secs=60,
                                      max_price=100*self.GWEI).get_gas_price(time_elapsed)


class GasPriceFactory:
    @staticmethod
    def create_gas_price(arguments) -> GasPrice:
        if arguments.smart_gas_price:
            return SmartGasPrice(arguments.ethgasstation_api_key)
        elif arguments.gas_price:
            return FixedGasPrice(arguments.gas_price)
        else:
            return DefaultGasPrice()
