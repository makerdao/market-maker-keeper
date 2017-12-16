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
    """Asynchronous client of the ethgasstation.info API.

    Creating an instance of this class runs a background thread, which fetches current
    recommended gas prices from EthGasStation every `refresh_interval` seconds. If due
    to network issues no current gas prices have been fetched for `expiry` seconds,
    old values expire and all `*_price()` methods will start returning `None` until
    the feed becomes available again.

    Also the moment before the first fetch has finished, all `*_price()` methods
    of this class return `None`.

    All gas prices are returned in Wei.

    Attributes:
        refresh_interval: Refresh frequency (in seconds).
        expiry: Expiration time (in seconds).
    """

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
        """Returns the current 'SafeLow (<60m)' gas rice (in Wei).

        Returns:
            The current 'SafeLow (<60m)' gas price (in Wei), or `None` if the EthGasStation
            feed has expired.
        """
        return self._return_value_if_valid(self._safe_low_price)

    def standard_price(self) -> Optional[int]:
        """Returns the current 'Standard (<5m)' gas price (in Wei).

        Returns:
            The current 'Standard (<5m)' gas price (in Wei), or `None` if the EthGasStation
            feed has expired.
        """
        return self._return_value_if_valid(self._standard_price)

    def fast_price(self) -> Optional[int]:
        """Returns the current 'Fast (<2m)' gas price (in Wei).

        Returns:
            The current 'Fast (<2m)' gas price (in Wei), or `None` if the EthGasStation
            feed has expired.
        """
        return self._return_value_if_valid(self._fast_price)

    def fastest_price(self) -> Optional[int]:
        """Returns the current fastest (undocumented!) gas price (in Wei).

        Returns:
            The current fastest (undocumented!) gas price (in Wei), or `None` if the EthGasStation
            feed has expired.
        """
        return self._return_value_if_valid(self._fastest_price)
