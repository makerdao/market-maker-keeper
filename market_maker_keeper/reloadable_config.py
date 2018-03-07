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

import _jsonnet
import json
import logging
import os
import zlib
from typing import Optional


class ReloadableConfig:
    """Reloadable JSON config file reader, capable of using jsonnet expressions.

    This reader will always read most up-to-date version of the config file from disk
    on each call to `get_config()`. In addition to that, whenever the config file changes,
    a log event is emitted.

    This reader uses _jsonnet_ data templating language, so the JSON config files can use
    some advanced expressions documented here: <https://github.com/google/jsonnet>.

    Attributes:
        filename: Filename of the configuration file.
    """

    logger = logging.getLogger()

    def __init__(self, filename: str):
        assert(isinstance(filename, str))

        self.filename = filename
        self._checksum = None
        self._config = None
        self._mtime = None
        self._spread_feed = None

    @staticmethod
    def _spread_feed_import_callback(spread_feed: Optional[dict]):
        def callback(path, file):
            if file == "spread-feed" and spread_feed is not None:
                return path, json.dumps(dict(map(lambda kv: (kv[0], float(kv[1])), spread_feed.items())))

        return callback

    def get_config(self, spread_feed: dict = None):
        """Reads the JSON config file from disk and returns it as a Python object.

        Returns:
            Current configuration as a `dict` or `list` object.
        """

        mtime = os.path.getmtime(self.filename)

        # If the modification time has not change since the last time we have read the file,
        # we return the last content without opening and parsing it. It saves us around ~ 30ms.
        #
        # Ultimately something like `watchdog` (<https://pythonhosted.org/watchdog/index.html>)
        # should be used to watch the filesystem changes asynchronously.
        if self._config is not None and self._mtime is not None:
            if mtime == self._mtime and spread_feed == self._spread_feed:
                return self._config

        with open(self.filename) as data_file:
            content_file = data_file.read()
            content_config = _jsonnet.evaluate_snippet("snippet", content_file, ext_vars={},
                                                       import_callback=self._spread_feed_import_callback(spread_feed))
            result = json.loads(content_config)

            # Report if file has been newly loaded or reloaded
            checksum = zlib.crc32(content_config.encode('utf-8'))
            if self._checksum is None:
                self.logger.info(f"Loaded configuration from '{self.filename}'")
                self.logger.debug(f"Config file is: " + json.dumps(result, indent=4))
            elif self._checksum != checksum:
                self.logger.info(f"Reloaded configuration from '{self.filename}'")
                self.logger.debug(f"Reloaded config file is: " + json.dumps(result, indent=4))

            self._checksum = checksum
            self._config = result
            self._mtime = mtime
            self._spread_feed = spread_feed

            return result
