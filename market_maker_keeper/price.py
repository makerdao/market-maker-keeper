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

import threading
import time
from typing import Optional

from market_maker_keeper.setzer import Setzer
from pymaker.feed import DSValue
from pymaker.logger import Logger
from pymaker.numeric import Wad
from pymaker.sai import Tub


class PriceFeed(object):
    def get_price(self) -> Optional[Wad]:
        raise NotImplementedError("Please implement this method")


class TubPriceFeed(PriceFeed):
    def __init__(self, tub: Tub):
        self.tub = tub
        self.ds_value = DSValue(web3=self.tub.web3, address=self.tub.pip())

    def get_ref_per_gem(self):
        return Wad(self.ds_value.read_as_int())

    def get_price(self) -> Optional[Wad]:
        return self.get_ref_per_gem() / self.tub.par()


class SetzerPriceFeed(PriceFeed):
    def __init__(self, tub: Tub, setzer_source: str, logger: Logger):
        self.tub = tub
        self.setzer_price = None
        self.setzer_retries = 0
        self.setzer_source = setzer_source
        self.logger = logger
        self._fetch_price()
        threading.Thread(target=self._background_run, daemon=True).start()

    def _fetch_price(self):
        try:
            self.setzer_price = Setzer().price(self.setzer_source)
            self.setzer_retries = 0
            self.logger.debug(f"Fetched price from {self.setzer_source}: {self.setzer_price}")
        except:
            self.setzer_retries += 1
            if self.setzer_retries > 10:
                self.setzer_price = None
            self.logger.warning(f"Failed to fetch price from {self.setzer_source}, tried {self.setzer_retries} times")
            if self.setzer_price is None:
                self.logger.warning(f"There is no valid price as maximum number of tries has been reached!")

    def _background_run(self):
        while True:
            time.sleep(10)
            self._fetch_price()

    def get_price(self) -> Optional[Wad]:
        if self.setzer_price is None:
            return None
        else:
            return self.setzer_price / self.tub.par()
