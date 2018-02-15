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

import json
import logging
import threading
import time
from typing import Optional, List

import os
import websocket

from market_maker_keeper.setzer import Setzer
from pymaker.feed import DSValue
from pymaker.numeric import Wad
from pymaker.sai import Tub, Vox


class PriceFeed(object):
    def get_price(self) -> Optional[Wad]:
        raise NotImplementedError("Please implement this method")


class FixedPriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, fixed_price: Wad):
        assert(isinstance(fixed_price, Wad))
        self.fixed_price = fixed_price

        self.logger.info(f"Using fixed price '{self.fixed_price}' as the price feed")

    def get_price(self) -> Optional[Wad]:
        return self.fixed_price


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


class FilePriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, filename: str, expiry: int):
        assert(isinstance(filename, str))
        assert(isinstance(expiry, int))

        self.filename = filename
        self.expiry = expiry
        self._price = None
        self._timestamp = 0
        self._expired = True

    def _read_price(self):
        try:
            if not os.path.isfile(self.filename):
                self._price = None
                self._timestamp = 0
                return

            with open(self.filename) as file:
                new_price = Wad.from_number(json.load(file)['price'])
                self.logger.debug(f"Read price from '{self.filename}': {new_price}")

                if self._price is None or new_price != self._price:
                    self.logger.info(f"Price feed updated to {new_price}")

                self._price = new_price
                self._timestamp = os.path.getmtime(self.filename)
        except Exception as e:
            self.logger.debug(f"Failed to read price from '{self.filename}': {e}")

    def get_price(self) -> Optional[Wad]:
        self._read_price()

        if time.time() - self._timestamp > self.expiry:
            if not self._expired:
                self.logger.warning(f"Price feed from '{self.filename}' has expired")
                self._expired = True

            return None
        else:
            if self._expired:
                self.logger.info(f"Price feed from '{self.filename}' became available")
                self._expired = False

            return self._price


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
            time.sleep(60)

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

    def __init__(self, ws_url: str, product_id: str, expiry: int):
        assert(isinstance(ws_url, str))
        assert(isinstance(product_id, str))
        assert(isinstance(expiry, int))

        self.ws_url = ws_url
        self.product_id = product_id
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
        self.logger.info(f"GDAX {self.product_id} WebSocket connected")
        ws.send("""{
            "type": "subscribe",
            "channels": [
                { "name": "ticker", "product_ids": ["%s"] },
                { "name": "heartbeat", "product_ids": ["%s"] }
            ]}""" % (self.product_id, self.product_id))

    def _on_close(self, ws):
        self.logger.info(f"GDAX {self.product_id} WebSocket disconnected")

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
                self.logger.warning(f"GDAX {self.product_id} WebSocket received unknown message type: '{message}'")
        except:
            self.logger.warning(f"GDAX {self.product_id} WebSocket received invalid message: '{message}'")

    def _on_error(self, ws, error):
        self.logger.info(f"GDAX {self.product_id} WebSocket error: '{error}'")

    def get_price(self) -> Optional[Wad]:
        if time.time() - self._last_timestamp > self.expiry:
            if not self._expired:
                self.logger.warning(f"Price feed from GDAX ({self.product_id}) has expired")
                self._expired = True
            return None
        else:
            return self._last_price

    def _process_ticker(self, message_obj):
        self._last_price = Wad.from_number(message_obj['price'])
        self._last_timestamp = time.time()

        self.logger.debug(f"Price feed from GDAX is {self._last_price} ({self.product_id})")

        if self._expired:
            self.logger.info(f"Price feed from GDAX ({self.product_id}) became available")
            self._expired = False

    def _process_heartbeat(self):
        self._last_timestamp = time.time()


class AveragePriceFeed(PriceFeed):
    def __init__(self, feeds: List[PriceFeed]):
        assert(isinstance(feeds, list))
        self.feeds = feeds

    def get_price(self) -> Optional[Wad]:
        total = Wad.from_number(0)
        count = 0

        for feed in self.feeds:
            price = feed.get_price()
            if price is not None:
                total += price
                count += 1

        if count > 0:
            return total / Wad.from_number(count)
        else:
            return None


class BackupPriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, feeds: List[PriceFeed]):
        assert(isinstance(feeds, list))
        self.feeds = feeds

    def get_price(self) -> Optional[Wad]:
        for feed in self.feeds:
            price = feed.get_price()
            if price is not None:
                return price

        return None


class PriceFeedFactory:
    @staticmethod
    def create_price_feed(price_feed_argument: str,
                          price_feed_expiry_argument: int,
                          tub: Tub = None) -> PriceFeed:
        assert(isinstance(price_feed_argument, str))
        assert(isinstance(price_feed_expiry_argument, int))
        assert(isinstance(tub, Tub) or tub is None)

        gdax_ws_url = "wss://ws-feed.gdax.com"

        if price_feed_argument == 'eth_dai':
            # main price feed
            main_price_feed = GdaxPriceFeed(ws_url=gdax_ws_url,
                                            product_id="ETH-USD",
                                            expiry=price_feed_expiry_argument)

            # emergency price feed
            emergency_price_feed = AveragePriceFeed([SetzerPriceFeed('kraken', expiry=price_feed_expiry_argument),
                                                     SetzerPriceFeed('gemini', expiry=price_feed_expiry_argument)])

            if tub is not None:
                # last resort price feed
                last_resort_price_feed = TubPriceFeed(tub)
                price_feed = BackupPriceFeed([main_price_feed, emergency_price_feed, last_resort_price_feed])
            else:
                price_feed = BackupPriceFeed([main_price_feed, emergency_price_feed])

        elif price_feed_argument == 'btc_dai':
            return GdaxPriceFeed(ws_url=gdax_ws_url,
                                 product_id="BTC-USD",
                                 expiry=price_feed_expiry_argument)

        elif price_feed_argument == 'tub':
            if tub is not None:
                price_feed = TubPriceFeed(tub)
            else:
                raise Exception(f"'--price-feed tub' cannot be used as this keeper does not know about 'Tub'")

        elif price_feed_argument.startswith("fixed:"):
            price_feed = FixedPriceFeed(Wad.from_number(price_feed_argument[6:]))

        elif price_feed_argument.startswith("file:"):
            price_feed = FilePriceFeed(filename=price_feed_argument[5:], expiry=price_feed_expiry_argument)

        else:
            raise Exception(f"'--price-feed {price_feed_argument}' unknown")

        # Optimization.
        # Ultimately we should do:
        # if vox is not None:
        #     return ApplyTargetPrice(price_feed, vox)
        #
        # Actually, maybe there should be a distinction between `eth_usd`/`btc_usd` and `eth_dai`/`btc_dai`,
        # the former being a raw price feed whereas the latter would take Vox into account...?

        return price_feed
