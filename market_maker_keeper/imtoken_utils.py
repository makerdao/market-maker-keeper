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


class MarketArgs:

    def __init__(self, market: dict):
        assert(isinstance(market, dict))

        self.price_feed = market['price-feed']
        self.price_feed_expiry = market['price-feed-expiry'] if 'price-feed-expiry' in market else 30
        self.spread_feed = market['spread-feed'] if 'spread-feed' in market else None
        self.spread_feed_expiry = market['spread-feed-expiry'] if 'spread-feed-expiry' in market else 3600
        self.control_feed = market['control-feed'] if 'control-feed' in market else None
        self.control_feed_expiry = market['control-feed-expiry'] if 'control-feed-expiry' in market else 86400


class ImtokenPair:

    def __init__(self, pair: str):
        assert(isinstance(pair, str))

        self.base_pair = pair
        pair_split = pair.split('/')
        self.counter_pair = f"{pair_split[1].upper()}/{pair_split[0].upper()}"


class PairsHandler(tornado.web.RequestHandler):

    def initialize(self, token_pairs):
        self.pairs = []
        [self.pairs.extend([pair.base_pair, pair.counter_pair]) for pair in token_pairs]

    @gen.coroutine
    def get(self):
        response = {
            "result": True,
            "pairs": self.pairs
        }
        self.write(response)


class PriceHandler(tornado.web.RequestHandler):

    def initialize(self, pairs, configs, cache):
        self.pairs = pairs
        self.configs = configs
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
        #TODO: edit order calculation so as order amount increases so does our spread (the quote price).

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
        if query_pair not in self.configs:
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
        target_price = self.configs[query_pair]['price_feed'].get_price()

        logging.info(f" Feed price: buy {target_price.buy_price} ; sell {target_price.sell_price}")

        if target_price.buy_price is None or target_price.sell_price is None:
            return {
                "result": False,
                "exchangeable": False,
                "minAmount": 0.0,
                "maxAmount": 0.0,
                "message": f"internal server error, please retry later"
            }

        logging.info(f"Query pair is {query_pair}")

        bands = Bands.read(self.configs[query_pair]['bands_config'],
                           self.configs[query_pair]['spread_feed'],
                           self.configs[query_pair]['control_feed'],
                           self.configs[query_pair]['history'])

        if not self.is_base_pair(query_pair) and our_side == "BUY":
            band = bands.buy_bands[0]
            price = band.avg_price(target_price.buy_price)

        if not self.is_base_pair(query_pair) and our_side == "SELL":
            band = bands.sell_bands[0]
            price = band.avg_price(target_price.sell_price)

        if self.is_base_pair(query_pair) and our_side == "SELL":
            band = bands.buy_bands[0]
            price = Wad.from_number(1) / band.avg_price(target_price.sell_price)

        if self.is_base_pair(query_pair) and our_side == "BUY":
            band = bands.sell_bands[0]
            price = Wad.from_number(1) / band.avg_price(target_price.buy_price)

        logging.info(f"price: {str(price)}  minAmount: {str(band.min_amount)}  maxAmount: {str(band.max_amount)}")

        return {
            "result": True,
            "exchangeable": Wad.from_number(amount) <= band.max_amount,
            "price": float(price),
            "minAmount": float(band.min_amount),
            "maxAmount": float(band.max_amount)
        }

    def is_base_pair(self, token_pair: str) -> bool:
        for pair in self.pairs:
            if pair.base_pair == token_pair:
                return True
        return False


class IndicativePriceHandler(PriceHandler):
    @gen.coroutine
    def get(self):
        amount = self.get_query_argument('amount', default=0)
        return self.write(self._get_price_response(amount))


class QuoteProcessHandler(tornado.web.RequestHandler):
    def initialize(self, cache, schema):
        self.cache = cache
        self.schema = schema

    def delete_quote(self, request_body, type):

        quote_id = request_body['quoteId']
        processed_quote = self.cache[quote_id]
        self.cache.__delitem__(quote_id)

        if type == 'EXCEPTION':
            logging.warning(f"{request_body.type} quote removed from cache: {processed_quote}")

        else:
            logging.info(f"quote processing: {processed_quote}")


class DealHandler(QuoteProcessHandler):

    @gen.coroutine
    def post(self):
        if self.request.body:

            processed = False
            quote_id = None

            try:
                request_body = tornado.escape.json_decode(self.request.body)
                jsonschema.validate(request_body, self.schema)

                self.delete_quote(request_body, 'DEAL')
                processed = True

            except KeyError:
                logging.info(f"Cannot find quote in cache with quoteId {quote_id}")

            except (ValueError, jsonschema.exceptions.ValidationError, jsonschema.exceptions.SchemaError) as e:
                logging.exception(e)

            response = {
                "result": processed
            }

            self.write(response)


class ExceptionHandler(QuoteProcessHandler):

    @gen.coroutine
    def post(self):
        if self.request.body:

            processed = False
            quote_id = None

            try:
                request_body = tornado.escape.json_decode(self.request.body)
                jsonschema.validate(request_body, self.schema)

                self.delete_quote(request_body, 'EXCEPTION')
                processed = True

            except KeyError:
                logging.info(f"Cannot find quote in cache with quoteId {quote_id}")

            except (ValueError, jsonschema.exceptions.ValidationError, jsonschema.exceptions.SchemaError) as e:
                logging.exception(e)

            response = {
                "result": processed
            }

            self.write(response)
