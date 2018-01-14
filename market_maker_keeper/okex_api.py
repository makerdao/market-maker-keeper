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

import urllib
import hashlib
from pprint import pformat
from typing import List

import requests

from pymaker import Wad


class Order:
    def __init__(self, order_id: int, timestamp: int, pair: str,
                 is_sell: bool, price: Wad, amount: Wad, deal_amount: Wad):
        assert(isinstance(order_id, int))
        assert(isinstance(timestamp, int))
        assert(isinstance(pair, str))
        assert(isinstance(is_sell, bool))
        assert(isinstance(price, Wad))
        assert(isinstance(amount, Wad))
        assert(isinstance(deal_amount, Wad))

        self.order_id = order_id
        self.timestamp = timestamp
        self.pair = pair
        self.is_sell = is_sell
        self.price = price
        self.amount = amount
        self.deal_amount = deal_amount

    @property
    def sell_to_buy_price(self) -> Wad:
        return self.price

    @property
    def buy_to_sell_price(self) -> Wad:
        return self.price

    @property
    def remaining_sell_amount(self) -> Wad:
        return (self.amount - self.deal_amount) if self.is_sell else (self.amount - self.deal_amount)*self.price

    def __eq__(self, other):
        assert(isinstance(other, Order))

        return self.order_id == other.order_id and \
               self.pair == other.pair

    def __hash__(self):
        return hash((self.order_id, self.pair))

    def __repr__(self):
        return pformat(vars(self))


class OKEXApi:
    """OKCoin and OKEX API interface.

    Developed according to the following manual:
    <https://www.okex.com/intro_apiOverview.html>.

    Inspired by the following example:
    <https://github.com/OKCoin/rest>, <https://github.com/OKCoin/rest/tree/master/python>.
    """

    def __init__(self, api_server: str, api_key: str, secret_key: str, timeout: float):
        assert(isinstance(api_server, str))
        assert(isinstance(api_key, str))
        assert(isinstance(secret_key, str))
        assert(isinstance(timeout, float))

        self.api_server = api_server
        self.api_key = api_key
        self.secret_key = secret_key
        self.timeout = timeout

    def ticker(self, pair: str):
        assert(isinstance(pair, str))
        return self._http_get("/api/v1/ticker.do", f"symbol={pair}")

    def depth(self, pair: str):
        assert(isinstance(pair, str))
        return self._http_get("/api/v1/depth.do", f"symbol={pair}")

    def trades(self, pair: str):
        assert(isinstance(pair, str))
        return self._http_get("/api/v1/trades.do", f"symbol={pair}")
    
    def get_balances(self) -> dict:
        return self._http_post("/api/v1/userinfo.do", {})["info"]["funds"]

    def get_orders(self, pair: str) -> List[Order]:
        assert(isinstance(pair, str))

        result = self._http_post("/api/v1/order_info.do", {
            'symbol': pair,
            'order_id': '-1'
        })

        orders = filter(lambda item: item['type'] in ['buy', 'sell'], result['orders'])
        return list(map(lambda item: Order(order_id=item['order_id'],
                                           timestamp=int(item['create_date']/1000),
                                           pair=item['symbol'],
                                           is_sell=item['type'] == 'sell',
                                           price=Wad.from_number(item['price']),
                                           amount=Wad.from_number(item['amount']),
                                           deal_amount=Wad.from_number(item['deal_amount'])), orders))

    def place_order(self, pair: str, is_sell: bool, price: Wad, amount: Wad) -> int:
        assert(isinstance(pair, str))
        assert(isinstance(is_sell, bool))
        assert(isinstance(price, Wad))
        assert(isinstance(amount, Wad))

        result = self._http_post("/api/v1/trade.do", {
            'symbol': pair,
            'type': 'sell' if is_sell else 'buy',
            'price': float(price),
            'amount': float(amount)
        })

        return int(result['order_id'])

    def cancel_order(self, pair: str, order_id: int) -> bool:
        assert(isinstance(pair, str))
        assert(isinstance(order_id, int))

        result = self._http_post("/api/v1/cancel_order.do", {
            'symbol': pair,
            'order_id': order_id
        })

        return int(result['order_id']) == order_id

    def _create_signature(self, params: dict):
        assert(isinstance(params, dict))

        sign = ''
        for key in sorted(params.keys()):
            sign += key + '=' + str(params[key]) + '&'
        data = sign + 'secret_key=' + self.secret_key
        return hashlib.md5(data.encode("utf8")).hexdigest().upper()

    @staticmethod
    def _result(result) -> dict:
        data = result.json()

        if 'error_code' in data:
            raise Exception(f"OKCoin API error: {data['error_code']}")

        if 'result' not in data or data['result'] is not True:
            raise Exception(f"Negative OKCoin response: {data}")

        return data

    def _http_get(self, resource: str, params: str):
        assert(isinstance(resource, str))
        assert(isinstance(params, str))

        return self._result(requests.get(url=f"{self.api_server}{resource}?{params}",
                                         timeout=self.timeout))

    def _http_post(self, resource: str, params: dict):
        assert(isinstance(resource, str))
        assert(isinstance(params, dict))

        params['api_key'] = self.api_key
        params['sign'] = self._create_signature(params)

        return self._result(requests.post(url=f"{self.api_server}{resource}",
                                          data=urllib.parse.urlencode(params),
                                          headers={"Content-Type": "application/x-www-form-urlencoded"},
                                          timeout=self.timeout))
