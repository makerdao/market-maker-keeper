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

    #所有交易对
    def pairs(self):
        URL = "/api2/1/pairs"
        params=''
        return self.httpGet(self.api_server, URL, params)


    #市场订单参数
    def marketinfo(self):
        URL = "/api2/1/marketinfo"
        params=''
        return self.httpGet(self.api_server, URL, params)

    #交易市场详细行情
    def marketlist(self):
        URL = "/api2/1/marketlist"
        params=''
        return self.httpGet(self.api_server, URL, params)

    #所有交易行情
    def tickers(self):
        URL = "/api2/1/tickers"
        params=''
        return self.httpGet(self.api_server, URL, params)

    #单项交易行情
    def ticker(self,param):
        URL = "/api2/1/ticker"
        return self.httpGet(self.api_server, URL, param)


    # 所有交易对市场深度
    def orderBooks(self):
        URL = "/api2/1/orderBooks"
        param=''
        return self.httpGet(self.api_server, URL, param)


    # 单项交易对市场深度
    def orderBook(self,param):
        URL = "/api2/1/orderBook"
        return self.httpGet(self.api_server, URL, param)


    # 历史成交记录
    def tradeHistory(self, param):
        URL = "/api2/1/tradeHistory"
        return self.httpGet(self.api_server, URL, param)

    #获取帐号资金余额
    def balances(self):
        URL = "/api2/1/private/balances"
        param = {}
        return self.httpPost(self.api_server, URL, param, self.api_key, self.secret_key)


    # 获取充值地址
    def depositAddres(self,param):
        URL = "/api2/1/private/depositAddress"
        params = {'currency':param}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)


    # 获取充值提现历史
    def depositsWithdrawals(self, start,end):
        URL = "/api2/1/private/depositsWithdrawals"
        params = {'start': start,'end':end}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)


    # 买入
    def buy(self, currencyPair,rate, amount):
        URL = "/api2/1/private/buy"
        params = {'currencyPair': currencyPair,'rate':rate,'amount':amount}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    # 卖出
    def sell(self, currencyPair, rate, amount):
        URL = "/api2/1/private/sell"
        params = {'currencyPair': currencyPair, 'rate': rate, 'amount': amount}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    # 取消订单
    def cancelOrder(self, orderNumber, currencyPair):
        URL = "/api2/1/private/cancelOrder"
        params = {'orderNumber': orderNumber, 'currencyPair': currencyPair}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)


    # 取消所有订单
    def cancelAllOrders(self, type, currencyPair):
        URL = "/api2/1/private/cancelAllOrders"
        params = {'type': type, 'currencyPair': currencyPair}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)


    # 获取下单状态
    def getOrder(self, orderNumber, currencyPair):
        URL = "/api2/1/private/getOrder"
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)


    # 获取我的当前挂单列表
    def openOrders(self):
        URL = "/api2/1/private/openOrders"
        params = {}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)


    # 获取我的24小时内成交记录
    def mytradeHistory(self,currencyPair,orderNumber):
        URL = "/api2/1/private/tradeHistory"
        params = {'currencyPair': currencyPair, 'orderNumber': orderNumber}
        return self.httpPost(self.api_server, URL, params, self.api_key, self.secret_key)

    # 提现
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
