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
from base64 import b64encode
from typing import Tuple

import re
from urllib.parse import urlparse

import websocket

from market_maker_keeper.util import sanitize_url


class Feed(object):
    def get(self) -> Tuple[dict, float]:
        raise NotImplementedError()

    def on_update(self, on_update_function):
        raise NotImplementedError()


class EmptyFeed(Feed):
    def get(self) -> Tuple[dict, float]:
        return {}, 0.0


class FixedFeed(Feed):
    def __init__(self, value: dict):
        assert(isinstance(value, dict))

        self.value = value

    def get(self) -> Tuple[dict, float]:
        return self.value, time.time()


class WebSocketFeed(Feed):
    logger = logging.getLogger()

    def __init__(self, ws_url: str, reconnect_delay: int):
        assert(isinstance(ws_url, str))
        assert(isinstance(reconnect_delay, int))

        self.ws_url = ws_url
        self.reconnect_delay = reconnect_delay

        self._header = self._get_header(ws_url)
        self._sanitized_url = sanitize_url(ws_url)
        self._last = {}, 0.0
        self._lock = threading.Lock()
        self._on_update_function = None

        threading.Thread(target=self._background_run, daemon=True).start()

    @staticmethod
    def _get_header(ws_url: str):
        parsed_url = urlparse(ws_url)
        basic_header = b64encode(bytes(parsed_url.username + ":" + parsed_url.password, "utf-8")).decode("utf-8")

        return ["Authorization: Basic %s" % basic_header]

    def _background_run(self):
        while True:
            ws = websocket.WebSocketApp(url=self.ws_url,
                                        header=self._header,
                                        on_message=self._on_message,
                                        on_error=self._on_error,
                                        on_open=self._on_open,
                                        on_close=self._on_close)
            ws.run_forever(ping_interval=15, ping_timeout=10)
            time.sleep(self.reconnect_delay)

    def _on_open(self, ws):
        self.logger.info(f"WebSocket '{self._sanitized_url}' connected")

    def _on_close(self, ws):
        self.logger.info(f"WebSocket '{self._sanitized_url}' disconnected")

    def _on_message(self, ws, message):
        try:
            message_obj = json.loads(message)

            data = dict(message_obj['data'])
            timestamp = float(message_obj['timestamp'])
            with self._lock:
                self._last = data, timestamp

            if self._on_update_function is not None:
                self._on_update_function()

            self.logger.debug(f"WebSocket '{self._sanitized_url}' received message: '{message}'")
        except:
            self.logger.warning(f"WebSocket '{self._sanitized_url}' received invalid message: '{message}'")

    def _on_error(self, ws, error):
        self.logger.info(f"WebSocket '{self._sanitized_url}' error: '{error}'")

    def get(self) -> Tuple[dict, float]:
        with self._lock:
            return self._last

    def on_update(self, on_update_function):
        assert(callable(on_update_function))

        self._on_update_function = on_update_function


class ExpiringFeed(Feed):
    def __init__(self, feed: Feed, expiry: int):
        assert(isinstance(feed, Feed))
        assert(isinstance(expiry, int))

        self.feed = feed
        self.expiry = expiry

    def get(self) -> Tuple[dict, float]:
        data, timestamp = self.feed.get()

        if time.time() - timestamp <= self.expiry:
            return data, timestamp
        else:
            return {}, 0.0

    def on_update(self, on_update_function):
        self.feed.on_update(on_update_function)
