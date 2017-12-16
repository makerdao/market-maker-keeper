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

import logging
import threading
import time
from typing import Optional

import requests


class EthGasStation:
    URL = "https://ethgasstation.info/json/ethgasAPI.json"
    SCALE = 100000000

    logger = logging.getLogger('eth-gas-station')

    def __init__(self, refresh_interval: int, expiry: int):
        assert(isinstance(refresh_interval, int))
        assert(isinstance(expiry, int))

        self.refresh_interval = refresh_interval
        self.expiry = expiry
        self._safe_low_price = None
        self._standard_price = None
        self._fast_price = None
        self._fastest_price = None
        self._last_refresh = 0
        self._expired = True
        threading.Thread(target=self._background_run, daemon=True).start()

    def _background_run(self):
        while True:
            self._fetch_price()
            time.sleep(self.refresh_interval)

    def _fetch_price(self):
        try:
            data = requests.get(self.URL).json()
            self._safe_low_price = int(data['safeLow']*self.SCALE)
            self._standard_price = int(data['average']*self.SCALE)
            self._fast_price = int(data['fast']*self.SCALE)
            self._fastest_price = int(data['fastest']*self.SCALE)
            self._last_refresh = int(time.time())

            self.logger.debug(f"Fetched data from {self.URL}: {data}")

            if self._expired:
                self.logger.info(f"Data feed from 'ethgasstation.info' became available")
                self._expired = False
        except:
            self.logger.warning(f"Failed to fetch data from {self.URL}")

    def _return_value_if_valid(self, value: int) -> Optional[int]:
        if int(time.time()) - self._last_refresh <= self.expiry:
            return value
        else:
            if not self._expired:
                self.logger.warning(f"Data feed from 'ethgasstation.info' has expired")
                self._expired = True
            return None

    def safe_low_price(self) -> Optional[int]:
        return self._return_value_if_valid(self._safe_low_price)

    def standard_price(self) -> Optional[int]:
        return self._return_value_if_valid(self._standard_price)

    def fast_price(self) -> Optional[int]:
        return self._return_value_if_valid(self._fast_price)

    def fastest_price(self) -> Optional[int]:
        return self._return_value_if_valid(self._fastest_price)
