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

import tornado.web
from tornado import gen
import uuid
import logging
import jsonschema
from market_maker_keeper.band import Bands
from pymaker.numeric import Wad


class ImtokenPair:

    def __init__(self, base_pair: str, counter_pair: str):
        assert(isinstance(base_pair, str))
        assert(isinstance(counter_pair, str))

        self.base_pair = base_pair
        self.counter_pair = counter_pair


class PairsHandler(tornado.web.RequestHandler):

    def initialize(self, pair: ImtokenPair):
        self.pairs = [pair.base_pair, pair.counter_pair]

    @gen.coroutine
    def get(self):
        response = {
            "result": True,
            "pairs": self.pairs
        }
        self.write(response)


class PriceHandler(tornado.web.RequestHandler):

    def initialize(self, pair,
                   base_bands_config,
                   counter_bands_config,
                   price_feed,
                   spread_feed,
                   control_feed,
                   history,
                   cache):
        self.pair = pair
        self.base_bands_config = base_bands_config
        self.counter_bands_config = counter_bands_config
        self.price_feed = price_feed
        self.spread_feed = spread_feed
        self.control_feed = control_feed
        self.history = history
        self.cache = cache

    @gen.coroutine
    def get(self):
        amount = self.get_query_argument('amount')
        response = self._get_price_response(amount)
        uniqId = self.get_query_argument('uniqId')

        quote_id = str(uuid.uuid4())
        self.cache[quote_id] = {
            "uniqId": uniqId,
            "price": response['price'],
            "amount": amount
        }

        response["quoteId"] = quote_id
        return self.write(response)

    def _get_price_response(self, amount):
        base = self.get_query_argument('base')
        quote = self.get_query_argument('quote')
        side = str(self.get_query_argument('side'))

        if side != "BUY" and side != "SELL":
            return {
                "result": False,
                "exchangeable": False,
                "minAmount": 0.0,
                "maxAmount": 0.0,
                "message": "side value should be BUY or SELL"
            }

        query_pair = f"{quote}/{base}"
        if query_pair != self.pair.base_pair and query_pair != self.pair.counter_pair:
            logging.info(f"Pair {base}/{quote} not supported")
            return {
                "result": False,
                "exchangeable": False,
                "minAmount": 0.0,
                "maxAmount": 0.0,
                "message": f"pair not supported"
            }
        if side == "SELL":
            our_side = "BUY"
        else:
            our_side = "SELL"
        target_price = self.price_feed.get_midpoint_price()

        logging.info(f" Base pair is {self.pair.base_pair} ; Query pair is {query_pair}")

        if query_pair == self.pair.counter_pair and our_side == "BUY":
            bands = Bands.read(self.base_bands_config, self.spread_feed, self.control_feed, self.history)
            band = bands.buy_bands[0]
            price = band.avg_price(target_price.buy_price)

        if query_pair == self.pair.counter_pair and our_side == "SELL":
            bands = Bands.read(self.counter_bands_config, self.spread_feed, self.control_feed, self.history)
            band = bands.sell_bands[0]
            price = band.avg_price(target_price.sell_price)

        if query_pair == self.pair.base_pair and our_side == "SELL":
            bands = Bands.read(self.counter_bands_config, self.spread_feed, self.control_feed, self.history)
            band = bands.buy_bands[0]
            price = 1 / int(band.avg_price(target_price.sell_price))

        if query_pair == self.pair.base_pair and our_side == "BUY":
            bands = Bands.read(self.base_bands_config, self.spread_feed, self.control_feed, self.history)
            band = bands.sell_bands[0]
            price = 1 / int(band.avg_price(target_price.buy_price))

        logging.info(f"price: {str(price)}  minAmount: {str(band.min_amount)}  maxAmount: {str(band.max_amount)}")

        return {
            "result": True,
            "exchangeable": Wad.from_number(amount) <= band.max_amount,
            "price": float(price),
            "minAmount": float(band.min_amount),
            "maxAmount": float(band.max_amount)
        }


class IndicativePriceHandler(PriceHandler):
    @gen.coroutine
    def get(self):
        amount = self.get_query_argument('amount', default=0)
        return self.write(self._get_price_response(amount))


class DealHandler(tornado.web.RequestHandler):

    def initialize(self, cache, schema):
        self.cache = cache
        self.schema = schema

    @gen.coroutine
    def post(self):
        if self.request.body:

            processed = False
            quote_id = None

            try:
                request_body = tornado.escape.json_decode(self.request.body)
                jsonschema.validate(request_body, self.schema)

                logging.debug(f"deal request: {request_body} ")

                quote_id = request_body['quoteId']
                quote = self.cache[quote_id]
                self.cache.__delitem__(quote_id)

                logging.info(f"processing quote {quote}")

                processed = True

            except KeyError:
                logging.info(f"Cannot find deal with quoteId {quote_id}")
            except (ValueError, jsonschema.exceptions.ValidationError, jsonschema.exceptions.SchemaError) as e:
                logging.exception(e)

            response = {
                "result": processed
            }
            self.write(response)
