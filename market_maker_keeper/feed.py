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


class WebSocketFeed:
    logger = logging.getLogger()

    def __init__(self, ws_url: str, reconnect_delay: int):
        assert(isinstance(ws_url, str))
        assert(isinstance(reconnect_delay, int))

        self.ws_url = ws_url
        self.reconnect_delay = reconnect_delay

        self._header = self._get_header(ws_url)
        self._sanitized_url = re.sub("://([^:@]+):([^:@]+)@", "://\g<1>@", ws_url)
        self._last = {}, 0
        self._lock = threading.Lock()

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

            self.logger.debug(f"WebSocket '{self._sanitized_url}' received message: '{message}'")
        except:
            self.logger.warning(f"WebSocket '{self._sanitized_url}' received invalid message: '{message}'")

    def _on_error(self, ws, error):
        self.logger.info(f"WebSocket '{self._sanitized_url}' error: '{error}'")

    def get(self) -> Tuple[dict, float]:
        with self._lock:
            return self._last


class ExpiringWebSocketFeed:
    pass