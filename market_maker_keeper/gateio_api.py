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

import hashlib
import logging
import urllib
import hmac

import requests

from pymaker import Wad


class GateIOApi:
    """Gate.io API interface.

    Developed according to the following manual:
    <https://gate.io/api2>.

    Inspired by the following example:
    <https://github.com/gateio/rest/tree/master/python>.
    """

    logger = logging.getLogger()

    def __init__(self, api_server: str, api_key: str, secret_key: str, timeout: float):
        assert(isinstance(api_server, str))
        assert(isinstance(api_key, str))
        assert(isinstance(secret_key, str))
        assert(isinstance(timeout, float))

        self.api_server = api_server
        self.api_key = api_key
        self.secret_key = secret_key
        self.timeout = timeout

    def marketinfo(self):
        return self._http_get("/api2/1/marketinfo", '')

    def marketlist(self):
        return self._http_get("/api2/1/marketlist", '')

    def ticker(self, pair: str):
        assert(isinstance(pair, str))
        return self._http_get("/api2/1/ticker", pair)

    def order_book(self, pair: str):
        assert(isinstance(pair, str))
        return self._http_get("/api2/1/orderBook", pair)

    def all_trade_history(self, pair: str):
        assert(isinstance(pair, str))
        return self._http_get("/api2/1/tradeHistory", pair)

    def get_balances(self):
        return self._http_post("/api2/1/private/balances", {})['available']

    def get_orders(self):
        return self._http_post("/api2/1/private/openOrders", {})

    def get_order(self, pair: str, order_id: int):
        assert(isinstance(pair, str))
        assert(isinstance(order_id, int))
        return self._http_post("/api2/1/private/getOrder", {'orderNumber': order_id, 'currencyPair': pair})

    def place_order(self, pair: str, is_sell: bool, price: Wad, amount: Wad):
        assert(isinstance(pair, str))
        assert(isinstance(is_sell, bool))
        assert(isinstance(price, Wad))
        assert(isinstance(amount, Wad))

        self.logger.info(f"Placing order ({'SELL' if is_sell else 'BUY'}, amount {amount} of {pair},"
                         f" price {price})...")

        url = "/api2/1/private/sell" if is_sell else "/api2/1/private/buy"
        self._http_post(url, {'currencyPair': pair, 'rate': float(price), 'amount': float(amount)})

        # TODO return order id, check if we get it...?

        self.logger.info(f"Placed order ({'SELL' if is_sell else 'BUY'}, amount {amount} of {pair},"
                         f" price {price})")

    def cancel_order(self, pair: str, order_id: int):
        assert(isinstance(pair, str))
        assert(isinstance(order_id, int))

        self.logger.info(f"Cancelling order #{order_id}...")
        self._http_post("/api2/1/private/cancelOrder", {'orderNumber': order_id, 'currencyPair': pair})
        self.logger.info(f"Cancelled order #{order_id}...")

    def cancel_all_orders(self, pair: str):
        assert(isinstance(pair, str))
        return self._http_post("/api2/1/private/cancelAllOrders", {'type': -1, 'currencyPair': pair})

    def get_trade_history(self, pair: str):
        assert(isinstance(pair, str))
        return self._http_post("/api2/1/private/tradeHistory", {'currencyPair': pair})

    def _http_get(self, resource: str, params: str):
        assert(isinstance(resource, str))
        assert(isinstance(params, str))

        return self._result(requests.get(url=f"{self.api_server}{resource}/{params}", timeout=self.timeout))

    @staticmethod
    def _result(result) -> dict:
        data = result.json()

        if 'result' not in data or data['result'] != 'true':
            raise Exception(f"Negative Gate.io response: {data}")

        return data

    def _create_signature(self, params):
        assert(isinstance(params, dict))

        sign = ''
        for key in (params.keys()):
            sign += key + '=' + str(params[key]) + '&'
        sign = sign[:-1]

        return hmac.new(key=bytes(self.secret_key, encoding='utf8'),
                        msg=bytes(sign, encoding='utf8'),
                        digestmod=hashlib.sha512).hexdigest()

    def _http_post(self, resource: str, params: dict):
        assert(isinstance(resource, str))
        assert(isinstance(params, dict))

        return self._result(requests.post(url=f"{self.api_server}{resource}",
                                          data=urllib.parse.urlencode(params),
                                          headers={"Content-Type": "application/x-www-form-urlencoded",
                                                   "KEY": self.api_key,
                                                   "SIGN": self._create_signature(params)},
                                          timeout=self.timeout))
