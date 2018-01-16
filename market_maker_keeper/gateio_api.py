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
        params=''
        return self.httpGet(self.api_server, URL, params)

    def marketinfo(self):
        URL = "/api2/1/marketinfo"
        params=''
        return self.httpGet(self.api_server, URL, params)

    def marketlist(self):
        URL = "/api2/1/marketlist"
        params=''
        return self.httpGet(self.api_server, URL, params)

    def tickers(self):
        URL = "/api2/1/tickers"
        params=''
        return self.httpGet(self.api_server, URL, params)

    def ticker(self,param):
        URL = "/api2/1/ticker"
        return self.httpGet(self.api_server, URL, param)

    def orderBooks(self):
        URL = "/api2/1/orderBooks"
        param=''
        return self.httpGet(self.api_server, URL, param)

    def orderBook(self,param):
        URL = "/api2/1/orderBook"
        return self.httpGet(self.api_server, URL, param)

    def tradeHistory(self, param):
        URL = "/api2/1/tradeHistory"
        return self.httpGet(self.api_server, URL, param)

    def balances(self):
        URL = "/api2/1/private/balances"
        param = {}
        return self.httpPost(self.api_server, URL, param, self.api_key, self.secret_key)

    def depositAddres(self,param):
        URL = "/api2/1/private/depositAddress"
        params = {'currency':param}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def depositsWithdrawals(self, start,end):
        URL = "/api2/1/private/depositsWithdrawals"
        params = {'start': start,'end':end}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def buy(self, currencyPair,rate, amount):
        URL = "/api2/1/private/buy"
        params = {'currencyPair': currencyPair,'rate':rate,'amount':amount}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def sell(self, currencyPair, rate, amount):
        URL = "/api2/1/private/sell"
        params = {'currencyPair': currencyPair, 'rate': rate, 'amount': amount}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def cancelOrder(self, orderNumber, currencyPair):
        URL = "/api2/1/private/cancelOrder"
        params = {'orderNumber': orderNumber, 'currencyPair': currencyPair}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def cancelAllOrders(self, type, currencyPair):
        URL = "/api2/1/private/cancelAllOrders"
        params = {'type': type, 'currencyPair': currencyPair}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def getOrder(self, orderNumber, currencyPair):
        URL = "/api2/1/private/getOrder"
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def openOrders(self):
        URL = "/api2/1/private/openOrders"
        params = {}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def mytradeHistory(self,currencyPair,orderNumber):
        URL = "/api2/1/private/tradeHistory"
        params = {'currencyPair': currencyPair, 'orderNumber': orderNumber}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def withdraw(self,currency,amount,address):
        URL = "/api2/1/private/withdraw"
        params = {'currency': currency, 'amount': amount,'address':address}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    def httpGet(self, url,resource,params=''):
        conn = http.client.HTTPSConnection(url, timeout=10)
        conn.request("GET",resource + '/' + params )
        response = conn.getresponse()
        data = response.read().decode('utf-8')
        return json.loads(data)

    def getSign(self, params,secretKey):
        sign = ''
        for key in (params.keys()):
            sign += key + '=' + str(params[key]) +'&'
        sign = sign[:-1]
        my_sign = hmac.new( bytes(secretKey,encoding='utf8'),bytes(sign,encoding='utf8'), sha512).hexdigest()
        return my_sign

    def httpPost(self, url,resource,params,apikey,secretkey):
        headers = {
            "Content-type" : "application/x-www-form-urlencoded",
            "KEY":apikey,
            "SIGN":self.getSign(params,secretkey)
        }
        conn = http.client.HTTPSConnection(url, timeout=10)
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
