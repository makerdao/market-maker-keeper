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

from typing import Optional

from market_maker_keeper.config import ReloadableConfig
from market_maker_keeper.gas_station import EthGasStation
from pymaker.gas import GasPrice, IncreasingGasPrice, FixedGasPrice, DefaultGasPrice
from pymaker.logger import Logger


class SmartGasPrice(GasPrice):
    """Simple and smart gas price scenario.

    Uses an EthGasStation feed. Starts with fast+10GWei, adding another 10GWei each 60 seconds
    up to fast+50GWei maximum. Falls back to a default scenario (incremental as well) if
    the EthGasStation feed unavailable for more than 10 minutes.
    """

    GWEI = 1000000000

    def __init__(self, logger: Logger):
        self.gas_station = EthGasStation(refresh_interval=60, expiry=600, logger=logger)

    def get_gas_price(self, time_elapsed: int) -> Optional[int]:
        fast_price = self.gas_station.fast_price()
        if fast_price is not None:
            # start from fast_price + 10 GWei
            # increase by 10 GWei every 60 seconds
            # max is fast_price + 50 GWei
            return min(fast_price+(10*self.GWEI) + int(time_elapsed/60)*(10*self.GWEI), fast_price+(50*self.GWEI))
        else:
            # default gas pricing when EthGasStation feed is down
            return IncreasingGasPrice(initial_price=50*self.GWEI,
                                      increase_by=10*self.GWEI,
                                      every_secs=60,
                                      max_price=100*self.GWEI).get_gas_price(time_elapsed)


class GasPriceFile(GasPrice):
    """Gas price configuration dynamically reloadable from a file.

    It is roughly an equivalent of implementation of :py:class:`pymaker.gas.IncreasingGasPrice`,
    but it uses `ReloadableConfig` to read the gas parameters from a file, and will dynamically
    reload that file whenever it changes. It allows to update the gas price dynamically
    for running keepers.

    Attributes:
        filename: Filename of the configuration file.
        logger: Logger used to log events.
    """
    def __init__(self, filename: str, logger: Logger):
        assert(isinstance(filename, str))
        assert(isinstance(logger, Logger))

        self.reloadable_config = ReloadableConfig(filename, logger)

    def get_gas_price(self, time_elapsed: int) -> Optional[int]:
        assert(isinstance(time_elapsed, int))

        config = self.reloadable_config.get_config()
        gas_price = config.get('gasPrice', None)
        gas_price_increase = config.get('gasPriceIncrease', None)
        gas_price_increase_every = config.get('gasPriceIncreaseEvery', None)
        gas_price_max = config.get('gasPriceMax', None)

        if gas_price is not None:
            if gas_price_increase and gas_price_increase_every:
                strategy = IncreasingGasPrice(gas_price, gas_price_increase, gas_price_increase_every, gas_price_max)
            else:
                strategy = FixedGasPrice(gas_price)
        else:
            strategy = DefaultGasPrice()

        return strategy.get_gas_price(time_elapsed=time_elapsed)
