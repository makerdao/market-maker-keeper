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
import hmac
import json
import logging
from pprint import pformat
from typing import List

import requests

from pymaker import Wad


class Order:
    def __init__(self,
                 order_id: int,
                 created_at: int,
                 is_sell: bool,
                 price: Wad,
                 amount: Wad,
                 amount_symbol: str,
                 money: Wad,
                 money_symbol: str):
        assert(isinstance(order_id, int))
        assert(isinstance(created_at, int))
        assert(isinstance(is_sell, bool))
        assert(isinstance(price, Wad))
        assert(isinstance(amount, Wad))
        assert(isinstance(amount_symbol, str))
        assert(isinstance(money, Wad))
        assert(isinstance(money_symbol, str))

        self.order_id = order_id
        self.created_at = created_at
        self.is_sell = is_sell
        self.price = price
        self.amount = amount
        self.amount_symbol = amount_symbol
        self.money = money
        self.money_symbol = money_symbol

    @property
    def sell_to_buy_price(self) -> Wad:
        return self.money / self.amount

    @property
    def buy_to_sell_price(self) -> Wad:
        return self.money / self.amount

    @property
    def remaining_sell_amount(self) -> Wad:
        return self.amount if self.is_sell else self.money

    def __eq__(self, other):
        assert(isinstance(other, Order))
        return self.order_id == other.order_id and \
               self.created_at == other.created_at and \
               self.is_sell == other.is_sell and \
               self.price == other.price and \
               self.amount == other.amount and \
               self.amount_symbol == other.amount_symbol and \
               self.money == other.money and \
               self.money_symbol == other.money_symbol

    def __hash__(self):
        return hash((self.order_id,
                     self.created_at,
                     self.is_sell,
                     self.price,
                     self.amount,
                     self.amount_symbol,
                     self.money,
                     self.money_symbol))

    def __repr__(self):
        return pformat(vars(self))


class BiboxApi:
    logger = logging.getLogger('bibox-api')

    def __init__(self, api_server: str, api_key: str, secret: str):
        assert(isinstance(api_server, str))
        assert(isinstance(api_key, str))
        assert(isinstance(secret, str))

        self.api_path = api_server
        self.api_key = api_key
        self.secret = secret

    def _request(self, path: str, cmd: dict):
        assert(isinstance(path, str))
        assert(isinstance(cmd, dict))

        cmds = json.dumps([cmd])
        call = {
            "cmds": cmds,
            "apikey": self.api_key,
            "sign": self._sign(cmds)
        }

        result = requests.post(self.api_path + path, json=call, timeout=15.5)
        result_json = result.json()

        if 'error' in result_json:
            raise Exception(f"API error, code {result_json['error']['code']}, msg: '{result_json['error']['msg']}'")

        return result_json['result'][0]['result']

    def _sign(self, msg: str) -> str:
        assert(isinstance(msg, str))
        return hmac.new(key=self.secret.encode('utf-8'), msg=msg.encode('utf-8'), digestmod=hashlib.md5).hexdigest()

    def user_info(self) -> dict:
        return self._request('/v1/user', {"cmd": "user/userInfo", "body": {}})

    def coin_list(self) -> list:
        return self._request('/v1/transfer', {"cmd": "transfer/coinList", "body": {}})

    def assets(self) -> dict:
        return self._request('/v1/transfer', {"cmd": "transfer/assets", "body": {}})

    def get_orders(self, pair: str) -> List[Order]:
        result = self._request('/v1/orderpending', {"cmd": "orderpending/orderPendingList", "body": {"pair": pair,
                                                                                                     "account_type": 0,
                                                                                                     "page": 1,
                                                                                                     "size": 900}})

        # We are interested in limit orders only ("order_type":2)
        items = filter(lambda item: item['order_type'] == 2, result['items'])

        return list(map(lambda item: Order(order_id=item['id'],
                                           created_at=item['createdAt'],
                                           is_sell=True if item['order_side'] == 2 else False,
                                           price=Wad.from_number(item['price']),
                                           amount=Wad.from_number(item['amount']),
                                           amount_symbol=item['coin_symbol'],
                                           money=Wad.from_number(item['money']),
                                           money_symbol=item['currency_symbol']), items))

    def place_order(self, is_sell: bool, amount: Wad, amount_symbol: str, money: Wad, money_symbol: str) -> int:
        assert(isinstance(is_sell, bool))
        assert(isinstance(amount, Wad))
        assert(isinstance(amount_symbol, str))
        assert(isinstance(money, Wad))
        assert(isinstance(money_symbol, str))

        order_id = self._request('/v1/orderpending', {"cmd": "orderpending/trade",
                                                      "body": {
                                                          "pair": amount_symbol + "_" + money_symbol,
                                                          "account_type": 0,
                                                          "order_type": 2,
                                                          "order_side": 2 if is_sell else 1,
                                                          "pay_bix": 0,
                                                          "price": float(money / amount),
                                                          "amount": float(amount),
                                                          "money": float(money)
                                                      }})

        self.logger.info(f"Placed order #{order_id} ({'SELL' if is_sell else 'BUY'}, amount {amount} {amount_symbol},"
                         f" money {money} {money_symbol})")

        return order_id

    def cancel_order(self, order_id: int):
        assert(isinstance(order_id, int))

        self._request('/v1/orderpending', {"cmd": "orderpending/cancelTrade", "body": {"orders_id": order_id}})
        self.logger.info(f"Cancelled order #{order_id}")
