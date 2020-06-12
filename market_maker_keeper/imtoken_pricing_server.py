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
import json
from cachetools import TTLCache
from market_maker_keeper.imtoken_utils import PairsHandler, IndicativePriceHandler,\
    PriceHandler, DealHandler, ImtokenPair, MarketArgs, ExceptionHandler

from market_maker_keeper.util import setup_logging
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.limit import History


class ImtokenPricingServer:
    """
    ImToken Market Maker Keeper -- https://docs.token.im/tokenlon-mmsk/en/

    ImToken requires their market makers to maintain a rest api / server in order to interact
    with the exchange/application. The endpoints we provide and the information we include in our reply
    are as follows:

    /pairs - Respond with what pairs we trade.
    /price - Respond with an active order for the amount requested.
    /deal - Respond with True if our cache is cleared. The order our /price endpoint replied with was excepted and executed. It is now a trade.
    /indicitivePrice - Respond with our price quote for the pair
    /exception - Respond with True if exception is handled False if error. ImToken sends errors when orders have issues being processed
   """

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

        parser.add_argument("--config", type=str, required=True,
                            help="Bands configuration file")

        parser.add_argument("--order-cache-maxsize", type=int, default=100000,
                            help="Maximum size of orders cache")

        parser.add_argument("--order-cache-ttl", type=int, default=10,
                            help="Orders time to live")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.cache = TTLCache(maxsize=self.arguments.order_cache_maxsize, ttl=self.arguments.order_cache_ttl)

        with open(self.arguments.config) as json_file:
            data = json.load(json_file)

        pairs, configs = self._parse_configs(data=data)

        application = tornado.web.Application([
            (r"/pairs", PairsHandler, dict(token_pairs=pairs)),
            (r"/indicativePrice", IndicativePriceHandler, dict(pairs=pairs,
                                                               configs=configs,
                                                               cache=self.cache)),
            (r"/price", PriceHandler, dict(pairs=pairs,
                                           configs=configs,
                                           cache=self.cache)),
            (r"/deal", DealHandler, dict(cache=self.cache,
                                         schema=deal_schema())),
            (r"/exception", ExceptionHandler, dict(cache=self.cache,
                                         schema=deal_schema())),
        ])
        application.listen(port=self.arguments.http_port,address=self.arguments.http_address)
        tornado.ioloop.IOLoop.current().start()

    #
    # Multiple markets configuration sample
    #
    # {
    #     "markets": [
    #         {
    #             "pair": "ETH/DAI",
    #             "bands": "~/imtoken-ethdai-bands.json",
    #             "price-feed": "eth_dai-pair-midpoint",
    #             "price-feed-expiry": 20
    #         },
    #         {
    #             "pair": "MKR/DAI",
    #             "bands": "~/imtoken-mkrdai-bands.json",
    #             "price-feed": "ws://admin:admin@localhost:9595/api/feeds/MKR_DAI_PRICE/socket",
    #             "price-feed-expiry": 20
    #         }
    #     ]
    # }
    @staticmethod
    def _parse_configs(data: dict) -> (list, dict):
        pairs = []
        configs = {}
        for market in data['markets']:
            pair = ImtokenPair(market['pair'])
            pairs.append(pair)

            band_config = ReloadableConfig(market['bands'])

            market_args = MarketArgs(market)
            price_feed = PriceFeedFactory().create_price_feed(market_args)
            spread_feed = create_spread_feed(market_args)
            control_feed = create_control_feed(market_args)

            config = {
                'bands_config': band_config,
                'price_feed': price_feed,
                'spread_feed': spread_feed,
                'control_feed': control_feed,
                'history': History()
            }

            configs[pair.base_pair] = config
            configs[pair.counter_pair] = config

        return pairs, configs


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
