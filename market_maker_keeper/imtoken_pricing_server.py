# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2019 grandizzy
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

import sys
import argparse
import logging
import tornado.ioloop
import tornado.web
from cachetools import TTLCache
from market_maker_keeper.imtoken_utils import PairsHandler, IndicativePriceHandler,\
    PriceHandler, DealHandler, ImtokenPair

from market_maker_keeper.util import setup_logging
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.limit import History


class ImtokenPricingServer:
    """Imtoken pricing server."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='imtoken-pricing-server')

        parser.add_argument("--http-address", type=str, default='',
                            help="Address of the Imtoken Pricing server")

        parser.add_argument("--http-port", type=int, default=8777,
                            help="Port of the Imtoken Pricing server")

        parser.add_argument("--imtoken-api-server", type=str, default='http://localhost:8157',
                            help="Address of the Imtoken API server (default: 'http://localhost:8157')")

        parser.add_argument("--imtoken-api-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Imtoken API (in seconds, default: 9.5)")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--config", type=str, required=True,
                            help="Bands configuration file")

        parser.add_argument("--price-feed", type=str, required=True,
                            help="Source of price feed")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of the price feed (in seconds, default: 120)")

        parser.add_argument("--spread-feed", type=str,
                            help="Source of spread feed")

        parser.add_argument("--spread-feed-expiry", type=int, default=3600,
                            help="Maximum age of the spread feed (in seconds, default: 3600)")

        parser.add_argument("--control-feed", type=str,
                            help="Source of control feed")

        parser.add_argument("--control-feed-expiry", type=int, default=86400,
                            help="Maximum age of the control feed (in seconds, default: 86400)")

        parser.add_argument("--order-cache-maxsize", type=int, default=100000,
                            help="Maximum size of orders cache")

        parser.add_argument("--order-cache-ttl", type=int, default=10,
                            help="Orders time to live")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.cache = TTLCache(maxsize=self.arguments.order_cache_maxsize, ttl=self.arguments.order_cache_ttl)
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)

        self.history = History()

        pair = ImtokenPair(self.arguments.pair)

        application = tornado.web.Application([
            (r"/pairs", PairsHandler, dict(pair=pair)),
            (r"/indicativePrice", IndicativePriceHandler, dict(pair=pair,
                                                               config=self.bands_config,
                                                               price_feed=self.price_feed,
                                                               spread_feed=self.spread_feed,
                                                               control_feed=self.control_feed,
                                                               history=self.history,
                                                               cache=self.cache)),
            (r"/price", PriceHandler, dict(pair=pair,
                                           config=self.bands_config,
                                           price_feed=self.price_feed,
                                           spread_feed=self.spread_feed,
                                           control_feed=self.control_feed,
                                           history=self.history,
                                           cache=self.cache)),
            (r"/deal", DealHandler, dict(cache=self.cache,
                                         schema=deal_schema())),
        ])
        application.listen(port=self.arguments.http_port,address=self.arguments.http_address)
        tornado.ioloop.IOLoop.current().start()


def deal_schema():
    return {
        "type": "object",
        "properties": {
            "makerToken": {
                "type": "string"
            },
            "takerToken": {
                "type": "string"
            },
            "makerTokenAmount": {
                "type": "string"
            },
            "takerTokenAmount": {
                "type": "string"
            },
            "quoteId": {
                "type": "string"
            },
            "timestamp": {
                "type": "number"
            }
        }
    }


if __name__ == '__main__':
    ImtokenPricingServer(sys.argv[1:]).main()
