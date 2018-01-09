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
    def __init__(self, tub: Tub):
        assert(isinstance(tub, Tub))

        self.ds_value = DSValue(web3=tub.web3, address=tub.pip())

    def get_price(self) -> Optional[Wad]:
        return Wad(self.ds_value.read_as_int())


class ApplyTargetPrice(PriceFeed):
    def __init__(self, price_feed: PriceFeed, vox: Vox):
        assert(isinstance(price_feed, PriceFeed))
        assert(isinstance(vox, Vox))

        self.price_feed = price_feed
        self.vox = vox

    def get_price(self) -> Optional[Wad]:
        price = self.price_feed.get_price()
        if price is None:
            return None
        else:
            return price / Wad(self.vox.par())


class SetzerPriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, source: str, expiry: int):
        assert(isinstance(source, str))
        assert(isinstance(expiry, int))

        self.source = source
        self.expiry = expiry
        self._price = None
        self._retries = 0
        self._timestamp = 0
        self._expired = True
        threading.Thread(target=self._background_run, daemon=True).start()

    def _fetch_price(self):
        try:
            self._price = Setzer().price(self.source)
            self._retries = 0
            self._timestamp = time.time()

            self.logger.debug(f"Fetched price from {self.source}: {self._price}")

            if self._expired:
                self.logger.info(f"Price feed from 'setzer' ({self.source}) became available")
                self._expired = False
        except:
            self._retries += 1
            if self._retries > 10:
                self.logger.warning(f"Failed to get price from 'setzer' ({self.source}), tried {self._retries} times")
                self.logger.warning(f"Please check if 'setzer' is installed and working correctly")

    def _background_run(self):
        while True:
            self._fetch_price()
            time.sleep(5)

    def get_price(self) -> Optional[Wad]:
        if time.time() - self._timestamp > self.expiry:
            if not self._expired:
                self.logger.warning(f"Price feed from 'setzer' ({self.source}) has expired")
                self._expired = True

            return None
        else:
            return self._price


class GdaxPriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, ws_url: str, expiry: int):
        assert(isinstance(ws_url, str))
        assert(isinstance(expiry, int))

        self.ws_url = ws_url
        self.expiry = expiry
        self._last_price = None
        self._last_timestamp = 0
        self._expired = True
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
        if time.time() - self._last_timestamp > self.expiry:
            if not self._expired:
                self.logger.warning(f"Price feed from GDAX has expired")
                self._expired = True
            return None
        else:
            return self._last_price

    def _process_ticker(self, message_obj):
        self._last_price = Wad.from_number(message_obj['price'])
        self._last_timestamp = time.time()

        self.logger.debug(f"Price feed from GDAX is {self._last_price}")

        if self._expired:
            self.logger.info(f"Price feed from GDAX became available")
            self._expired = False

    def _process_heartbeat(self):
        self._last_timestamp = time.time()


class PriceFeedFactory:
    @staticmethod
    def create_price_feed(price_feed_argument: str, price_feed_expiry_argument: int, tub: Tub, vox: Vox) -> PriceFeed:
        assert(isinstance(price_feed_argument, str) or price_feed_argument is None)
        assert(isinstance(price_feed_expiry_argument, int))

        if price_feed_argument is not None:
            if price_feed_argument.lower() == 'gdax-websocket':
                price_feed = GdaxPriceFeed("wss://ws-feed.gdax.com", expiry=price_feed_expiry_argument)
            else:
                price_feed = SetzerPriceFeed(price_feed_argument, expiry=price_feed_expiry_argument)
        else:
            price_feed = TubPriceFeed(tub)

        # Optimization.
        # Ultimately we should do: return ApplyTargetPrice(price_feed, vox)

        return price_feed
