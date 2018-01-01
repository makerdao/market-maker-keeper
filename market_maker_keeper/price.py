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

import json
import logging
import threading
import time
from typing import Optional

import websocket

from market_maker_keeper.setzer import Setzer
from pymaker.feed import DSValue
from pymaker.numeric import Wad
from pymaker.sai import Tub, Vox


class PriceFeed(object):
    def get_price(self) -> Optional[Wad]:
        raise NotImplementedError("Please implement this method")


class TubPriceFeed(PriceFeed):
    def __init__(self, tub: Tub, vox: Vox):
        assert(isinstance(tub, Tub))
        assert(isinstance(vox, Vox))

        self.tub = tub
        self.vox = vox
        self.ds_value = DSValue(web3=self.tub.web3, address=self.tub.pip())

    def get_ref_per_gem(self):
        return Wad(self.ds_value.read_as_int())

    def get_price(self) -> Optional[Wad]:
        return self.get_ref_per_gem() / Wad(self.vox.par())


class SetzerPriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, vox: Vox, setzer_source: str):
        assert(isinstance(vox, Vox))
        assert(isinstance(setzer_source, str))

        self.vox = vox
        self.setzer_price = None
        self.setzer_retries = 0
        self.setzer_source = setzer_source
        threading.Thread(target=self._background_run, daemon=True).start()

    def _fetch_price(self):
        try:
            self.setzer_price = Setzer().price(self.setzer_source)
            self.setzer_retries = 0
            self.logger.debug(f"Fetched price from {self.setzer_source}: {self.setzer_price}")
        except:
            self.setzer_retries += 1
            if self.setzer_retries > 10:
                self.logger.warning(f"Failed to get price from {self.setzer_source}, tried {self.setzer_retries} times")
                self.logger.warning(f"Please check if 'setzer' is installed and working correctly")
            if self.setzer_retries > 20:
                self.setzer_price = None
                self.logger.warning(f"There is no valid price feed as maximum number of tries has been reached")

    def _background_run(self):
        while True:
            self._fetch_price()
            time.sleep(5)

    def get_price(self) -> Optional[Wad]:
        if self.setzer_price is None:
            return None
        else:
            return self.setzer_price / Wad(self.vox.par())


class GdaxPriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, ws_url: str, expiry: int):
        assert(isinstance(ws_url, str))
        assert(isinstance(expiry, int))

        self.ws_url = ws_url
        self.expiry = expiry
        self.last_price = None
        self.last_timestamp = None
        threading.Thread(target=self._background_run, daemon=True).start()

    def _background_run(self):
        while True:
            ws = websocket.WebSocketApp(url=self.ws_url,
                                        on_message=self._on_message,
                                        on_error=self._on_error,
                                        on_open=self._on_open,
                                        on_close=self._on_close)
            ws.run_forever(ping_interval=15, ping_timeout=10)
            time.sleep(1)

    def _on_open(self, ws):
        self.logger.info(f"GDAX WebSocket connected")
        ws.send("""{
            "type": "subscribe",
            "channels": [
                { "name": "ticker", "product_ids": ["ETH-USD"] },
                { "name": "heartbeat", "product_ids": ["ETH-USD"] }
            ]}""")

    def _on_close(self, ws):
        self.logger.info(f"GDAX WebSocket disconnected")

    def _on_message(self, ws, message):
        self.logger.debug(f"GDAX WebSocket message received: '{message}'")
        try:
            message_obj = json.loads(message)
            if message_obj['type'] == 'subscriptions':
                pass
            elif message_obj['type'] == 'ticker':
                self._process_ticker(message_obj)
            elif message_obj['type'] == 'heartbeat':
                self._process_heartbeat()
            else:
                self.logger.warning(f"GDAX WebSocket received unknown message type: '{message}'")
        except:
            self.logger.warning(f"GDAX WebSocket received invalid message: '{message}'")

    def _on_error(self, ws, error):
        self.logger.info(f"GDAX WebSocket error: '{error}'")

    def get_price(self) -> Optional[Wad]:
        if self.last_timestamp is None:
            return None
        elif time.time() - self.last_timestamp > self.expiry:
            return None
        else:
            return self.last_price

    def _process_ticker(self, message_obj):
        self.last_price = Wad.from_number(message_obj['price'])
        self.last_timestamp = time.time()

    def _process_heartbeat(self):
        self.last_timestamp = time.time()


class PriceFeedFactory:
    @staticmethod
    def create_price_feed(price_feed_argument: str, tub: Tub, vox: Vox) -> PriceFeed:
        if price_feed_argument is not None:
            return SetzerPriceFeed(vox, price_feed_argument)
        else:
            return TubPriceFeed(tub, vox)
