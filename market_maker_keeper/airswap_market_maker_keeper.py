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

import argparse
import logging
import sys
import time
import random
import requests
import json

from retry import retry
from web3 import Web3, HTTPProvider
from flask import Flask, jsonify, request

from market_maker_keeper.airswap_band import Bands
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pyexchange.ddex import DdexApi, Order
from pymaker import Address
from pymaker.approval import directly
from pymaker.keys import register_keys
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.token import ERC20Token
from pymaker.util import eth_balance
from pymaker.zrx import ZrxExchange

from flask import Flask, Response

app = Flask(__name__)


from flask import jsonify

class CustomException(Exception):

    def __init__(self, message, status_code=None, payload=None):

        Exception.__init__(self)
        self.message = message
        if status_code is not None:
            self.status_code = status_code
        self.payload = payload

    def to_dict(self):
        rv = dict(self.payload or ())
        rv['message'] = self.message
        rv['code'] = self.status_code
        exception = {'error': rv}
        return exception

    def to_json(self):
        return json.dumps(self.to_dict())

class AirswapMarketMakerKeeper:
    """Keeper acting as a market maker on Airswap."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='airswap-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--eth-key", type=str, nargs='*',
                            help="Ethereum private key(s) to use (e.g. 'key_file=aaa.json,pass_file=aaa.pass')")

        parser.add_argument("--exchange-address", type=str, required=True,
                            help="Ethereum address of the 0x Exchange contract")

        parser.add_argument("--localhost-orderserver-port", type=str, default='5004',
                            help="Port of the order server (default: '5004')")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--buy-token-address", type=str, required=True,
                            help="Ethereum address of the buy token")

        parser.add_argument("--sell-token-address", type=str, required=True,
                            help="Ethereum address of the sell token")

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

        parser.add_argument("--order-history", type=str,
                            help="Endpoint to report active orders to")

        parser.add_argument("--order-history-every", type=int, default=30,
                            help="Frequency of reporting active orders (in seconds, default: 30)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"https://rinkeby.infura.io",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        register_keys(self.web3, self.arguments.eth_key)

        self.token_buy = ERC20Token(web3=self.web3, address=Address(self.arguments.buy_token_address))
        self.token_sell = ERC20Token(web3=self.web3, address=Address(self.arguments.sell_token_address))
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_max_decimals = None
        self.amount_max_decimals = None
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)

        self.history = History()

    def main(self):
        bands = Bands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)

        buy_intent = self.build_intents(self.token_buy.address.__str__(), self.token_sell.address.__str__())
        sell_intent = self.build_intents(self.token_sell.address.__str__(), self.token_buy.address.__str__())
        self._set_intents(buy_intent)
        self._set_intents(sell_intent)

        app.run(host="0.0.0.0", port=self.arguments.localhost_orderserver_port)


    def build_intents(self, maker_token_address, taker_token_address):
        return {
            "makerToken": maker_token_address,
            "takerToken": taker_token_address,
            "role": "maker"
        }

   # def startup(self):

   # def shutdown(self):

   # def approve(self):

    def our_total_balance(self, token: ERC20Token) -> Wad:
        return token.balance_of(self.our_address)


    def _set_intents(self, intent):
        headers = {'content-type': 'application/json'}
        r = requests.post(f"http://localhost:5005/setIntents",
                             data=json.dumps([intent]),
                             headers=headers)

        logging.info(f"intent set: {intent} -> {r.text}")
        return r.text

    def _sign_order(self, order):
        headers = {'content-type': 'application/json'}
        r = requests.post(f"http://localhost:5005/signOrder",
                             data=json.dumps(order),
                             headers=headers)
        return r.text

    def r_get_order(self):
        bands = Bands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)
        req = request.get_json()
        logging.info("Received getOrder: {req}".format(req=req))

        assert('makerAddress' in req)
        assert('takerAddress' in req['params'])
        assert('makerToken' in req['params'])
        assert('takerToken' in req['params'])

        maker_address = req["makerAddress"]
        taker_address = req["params"]["takerAddress"]
        maker_token = req["params"]["makerToken"]
        taker_token = req["params"]["takerToken"]

        # Only one or the other should be sent in the request
        # Takers will usually request a makerAmount, however they can reqeust takerAmount
        if 'makerAmount' in req['params']:
            req_token_amount = Wad(int(req["params"]["makerAmount"]))

        elif 'takerAmount' in req['params']:
            req_token_amount = Wad(int(req["params"]["takerAmount"]))

        else:
            raise CustomException('neither takerAmount or makerAmount was specified in the request', status_code=400)

        # V2 should adjust for signed orders we already have out there? still debating...
        amount_side = 'buy' if maker_token == self.token_buy.address.__str__() else 'sell'
        our_buy_balance = self.our_total_balance(self.token_buy)
        our_sell_balance = self.our_total_balance(self.token_sell)
        target_price = self.price_feed.get_price()

        token_amnts = bands.new_order(req_token_amount, amount_side, our_buy_balance, our_sell_balance, target_price)

        # Set 5-minute expiration on this order
        expiration = str(int(time.time()) + 300)
        nonce = random.randint(0, 99999)

        new_order = {
            "makerAddress": maker_address,
            "makerToken": maker_token,
            "makerAmount": str(token_amnts["maker_amount"].value),
            "takerAddress": taker_address,
            "takerToken": taker_token,
            "takerAmount": str(token_amnts["taker_amount"].value),
            "expiration": expiration,
            "nonce": nonce
        }

        signed_order = self._sign_order(new_order)
        logging.info(f"Sending order: {signed_order}")
        return signed_order

    def r_error_handler(self, err):
        logging.warning(f"Sending error back to caller {err.to_json()}")
        return err.to_json()

if __name__ == '__main__':
    airswap_app = AirswapMarketMakerKeeper(sys.argv[1:])
    app.add_url_rule('/getOrder', view_func=airswap_app.r_get_order, methods=["POST"])
    app.register_error_handler(CustomException, airswap_app.r_error_handler)
    airswap_app.main()
