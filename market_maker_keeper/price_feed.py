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
from typing import Optional, List, Tuple

import os
import websocket

from market_maker_keeper.feed import ExpiringFeed, WebSocketFeed, Feed
from market_maker_keeper.setzer import Setzer
from pymaker.feed import DSValue
from pymaker.numeric import Wad
from pymaker.sai import Tub


class Price(object):
    def __init__(self, buy_price: Optional[Wad], sell_price: Optional[Wad]):
        assert(isinstance(buy_price, Wad) or buy_price is None)
        assert(isinstance(sell_price, Wad) or sell_price is None)

        self.buy_price = buy_price
        self.sell_price = sell_price


class PriceFeed(object):
    def get_price(self) -> Price:
        raise NotImplementedError("Please implement this method")


class FixedPriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, fixed_price: Wad):
        assert(isinstance(fixed_price, Wad))
        self.fixed_price = fixed_price

        self.logger.info(f"Using fixed price '{self.fixed_price}' as the price feed")

    def get_price(self) -> Price:
        return Price(buy_price=self.fixed_price, sell_price=self.fixed_price)


class TubPriceFeed(PriceFeed):
    def __init__(self, tub: Tub):
        assert(isinstance(tub, Tub))

        self.ds_value = DSValue(web3=tub.web3, address=tub.pip())

    def get_price(self) -> Price:
        tub_price = Wad(self.ds_value.read_as_int())

        return Price(buy_price=tub_price, sell_price=tub_price)


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

    def get_price(self) -> Price:
        if time.time() - self._timestamp > self.expiry:
            if not self._expired:
                self.logger.warning(f"Price feed from 'setzer' ({self.source}) has expired")
                self._expired = True

            return Price(buy_price=None, sell_price=None)

        else:
            value = self._price
            return Price(buy_price=value, sell_price=value)


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

    def get_price(self) -> Price:
        if time.time() - self._last_timestamp > self.expiry:
            if not self._expired:
                self.logger.warning(f"Price feed from GDAX ({self.product_id}) has expired")
                self._expired = True

            return Price(buy_price=None, sell_price=None)

        else:
            value = self._last_price
            return Price(buy_price=value, sell_price=value)

    def _process_ticker(self, message_obj):
        self._last_price = Wad.from_number(message_obj['price'])
        self._last_timestamp = time.time()

        self.logger.debug(f"Price feed from GDAX is {self._last_price} ({self.product_id})")

        if self._expired:
            self.logger.info(f"Price feed from GDAX ({self.product_id}) became available")
            self._expired = False

    def _process_heartbeat(self):
        self._last_timestamp = time.time()


class WebSocketPriceFeed(PriceFeed):
    def __init__(self, feed: Feed):
        assert(isinstance(feed, Feed))

        self.feed = feed

    def get_price(self) -> Price:
        data, timestamp = self.feed.get()

        try:
            if 'buyPrice' in data:
                buy_price = Wad.from_number(data['buyPrice'])

            elif 'price' in data:
                buy_price = Wad.from_number(data['price'])

            else:
                buy_price = None
        except:
            buy_price = None

        try:
            if 'sellPrice' in data:
                sell_price = Wad.from_number(data['sellPrice'])

            elif 'price' in data:
                sell_price = Wad.from_number(data['price'])

            else:
                sell_price = None
        except:
            sell_price = None

        return Price(buy_price=buy_price, sell_price=sell_price)


class AveragePriceFeed(PriceFeed):
    def __init__(self, feeds: List[PriceFeed]):
        assert(isinstance(feeds, list))
        self.feeds = feeds

    def get_price(self) -> Price:
        total_buy = Wad.from_number(0)
        count_buy = 0

        total_sell = Wad.from_number(0)
        count_sell = 0

        for feed in self.feeds:
            price = feed.get_price()
            if price.buy_price is not None:
                total_buy += price.buy_price
                count_buy += 1

            if price.sell_price is not None:
                total_sell += price.sell_price
                count_sell += 1

        buy_price = total_buy / Wad.from_number(count_buy) if count_buy > 0 else None
        sell_price = total_sell / Wad.from_number(count_sell) if count_sell > 0 else None

        return Price(buy_price=buy_price, sell_price=sell_price)


class ReversePriceFeed(PriceFeed):
    def __init__(self, price_feed: PriceFeed):
        assert(isinstance(price_feed, PriceFeed))
        self.price_feed = price_feed

    def get_price(self) -> Price:
        parent_price = self.price_feed.get_price()

        buy_price = Wad.from_number(1) / parent_price.buy_price if parent_price.buy_price is not None else None
        sell_price = Wad.from_number(1) / parent_price.sell_price if parent_price.sell_price is not None else None
        return Price(buy_price=buy_price, sell_price=sell_price)


class BackupPriceFeed(PriceFeed):
    logger = logging.getLogger()

    def __init__(self, feeds: List[PriceFeed]):
        assert(isinstance(feeds, list))
        self.feeds = feeds

    def get_price(self) -> Price:
        for feed in self.feeds:
            price = feed.get_price()
            if price.buy_price is not None or price.sell_price is not None:
                return price

        return Price(buy_price=None, sell_price=None)


class PriceFeedFactory:
    @staticmethod
    def create_price_feed(arguments, tub: Tub = None) -> PriceFeed:
        return PriceFeedFactory._create_price_feed(arguments.price_feed, arguments.price_feed_expiry, tub)

    @staticmethod
    def _create_price_feed(price_feed_argument: str, price_feed_expiry_argument: int, tub: Optional[Tub]):
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

        elif price_feed_argument == 'dai_eth':
            return ReversePriceFeed(PriceFeedFactory._create_price_feed('eth_dai', price_feed_expiry_argument, tub))

        elif price_feed_argument == 'dai_btc':
            return ReversePriceFeed(PriceFeedFactory._create_price_feed('btc_dai', price_feed_expiry_argument, tub))

        elif price_feed_argument == 'tub':
            if tub is not None:
                price_feed = TubPriceFeed(tub)
            else:
                raise Exception(f"'--price-feed tub' cannot be used as this keeper does not know about 'Tub'")

        elif price_feed_argument.startswith("fixed:"):
            price_feed = FixedPriceFeed(Wad.from_number(price_feed_argument[6:]))

        elif price_feed_argument.startswith("ws://") or price_feed_argument.startswith("wss://"):
            socket_feed = WebSocketFeed(price_feed_argument, 5)
            socket_feed = ExpiringFeed(socket_feed, price_feed_expiry_argument)

            price_feed = WebSocketPriceFeed(socket_feed)

        else:
            raise Exception(f"'--price-feed {price_feed_argument}' unknown")

        return price_feed
