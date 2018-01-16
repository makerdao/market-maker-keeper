#!/usr/bin/python
# -*- coding: utf-8 -*-

import http.client
import urllib
import json
from hashlib import sha512
import hmac


class GateIOApi:

    def __init__(self,api_server: str, api_key: str, secret_key: str, timeout: float):
        assert(isinstance(api_server, str))
        assert(isinstance(api_key, str))
        assert(isinstance(secret_key, str))
        assert(isinstance(timeout, float))

        self.api_server = api_server
        self.api_key = api_key
        self.secret_key = secret_key
        self.timeout = timeout

    def pairs(self):
        URL = "/api2/1/pairs"
        return self._http_get(URL, '')

    def marketinfo(self):
        URL = "/api2/1/marketinfo"
        return self._http_get(URL, '')

    def marketlist(self):
        URL = "/api2/1/marketlist"
        return self._http_get(URL, '')

    def tickers(self):
        URL = "/api2/1/tickers"
        return self._http_get(URL, '')

    def ticker(self, pair: str):
        assert(isinstance(pair, str))

        URL = "/api2/1/ticker"
        return self._http_get(URL, pair)

    def orderBooks(self):
        URL = "/api2/1/orderBooks"
        return self._http_get(URL, '')

    def orderBook(self, pair: str):
        assert(isinstance(pair, str))

        URL = "/api2/1/orderBook"
        return self._http_get(URL, pair)

    def tradeHistory(self, pair):
        assert(isinstance(pair, str))

        URL = "/api2/1/tradeHistory"
        return self._http_get(URL, pair)

    def balances(self):
        URL = "/api2/1/private/balances"
        return self._http_post(URL, {}, self.api_key, self.secret_key)

    def depositAddres(self, param):
        URL = "/api2/1/private/depositAddress"
        params = {'currency': param}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def depositsWithdrawals(self, start, end):
        URL = "/api2/1/private/depositsWithdrawals"
        params = {'start': start, 'end':end}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def buy(self, currencyPair, rate, amount):
        URL = "/api2/1/private/buy"
        params = {'currencyPair': currencyPair, 'rate':rate, 'amount':amount}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def sell(self, currencyPair, rate, amount):
        URL = "/api2/1/private/sell"
        params = {'currencyPair': currencyPair, 'rate': rate, 'amount': amount}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def cancelOrder(self, orderNumber, currencyPair):
        URL = "/api2/1/private/cancelOrder"
        params = {'orderNumber': orderNumber, 'currencyPair': currencyPair}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def cancelAllOrders(self, type, currencyPair):
        URL = "/api2/1/private/cancelAllOrders"
        params = {'type': type, 'currencyPair': currencyPair}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def getOrder(self, orderNumber, currencyPair):
        URL = "/api2/1/private/getOrder"
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def openOrders(self):
        URL = "/api2/1/private/openOrders"
        params = {}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def mytradeHistory(self, currencyPair, orderNumber):
        URL = "/api2/1/private/tradeHistory"
        params = {'currencyPair': currencyPair, 'orderNumber': orderNumber}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def withdraw(self, currency, amount, address):
        URL = "/api2/1/private/withdraw"
        params = {'currency': currency, 'amount': amount, 'address':address}
        return self._http_post(URL, params, self.api_key, self.secret_key)

    def _http_get(self, resource: str, params: str):
        assert(isinstance(resource, str))
        assert(isinstance(params, str))

        conn = http.client.HTTPSConnection(self.api_server, timeout=self.timeout)
        conn.request("GET", resource + '/' + params)
        response = conn.getresponse()
        data = response.read().decode('utf-8')
        return json.loads(data)

    def _create_signature(self, params, secretKey):
        sign = ''
        for key in (params.keys()):
            sign += key + '=' + str(params[key]) + '&'
        sign = sign[:-1]
        my_sign = hmac.new(bytes(secretKey, encoding='utf8'), bytes(sign, encoding='utf8'), sha512).hexdigest()
        return my_sign

    def _http_post(self, resource: str, params: dict, apikey, secretkey):
        assert(isinstance(resource, str))
        assert(isinstance(params, dict))

        headers = {
            "Content-type": "application/x-www-form-urlencoded",
            "KEY": apikey,
            "SIGN": self._create_signature(params, secretkey)
        }
        conn = http.client.HTTPSConnection(self.api_server, timeout=self.timeout)
        if params:
            temp_params = urllib.parse.urlencode(params)
        else:
            temp_params = ''
        print(temp_params)
        conn.request("POST", resource, temp_params, headers)
        response = conn.getresponse()
        data = response.read().decode('utf-8')
        params.clear()
        conn.close()
        return data
