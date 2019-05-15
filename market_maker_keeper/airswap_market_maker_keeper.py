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

from typing import Tuple, Optional

from web3 import Web3, HTTPProvider
from flask import Flask, jsonify, request

from market_maker_keeper.price_feed import Price
from market_maker_keeper.feed import Feed
from market_maker_keeper.limit import SideLimits, History
from market_maker_keeper.band import Band, Bands, BuyBand, SellBand
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pyexchange.airswap import AirswapApi
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

        parser.add_argument("--airswap-api-server", type=str, default='http://localhost:5005',
                            help="Address of the Airswap API (default: 'http://localhost:5005')")

        parser.add_argument("--airswap-api-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Airswap API (in seconds, default: 9.5)")

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

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"https://parity1.makerfoundation.com:18545",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        register_keys(self.web3, self.arguments.eth_key)

        self.airswap_api = AirswapApi(self.arguments.airswap_api_server, self.arguments.airswap_api_timeout)

        if self.arguments.buy_token_address == '0x0000000000000000000000000000000000000000':
            self.token_buy = EthToken(Address(self.arguments.buy_token_address))
        else:
            self.token_buy = ERC20Token(web3=self.web3, address=Address(self.arguments.buy_token_address))

        if self.arguments.sell_token_address == '0x0000000000000000000000000000000000000000':
            self.token_sell = EthToken(Address(self.arguments.buy_token_address))
        else:
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
        bands = AirswapBands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)
        self.airswap_api.set_intents(self.token_buy.address.__str__(), self.token_sell.address.__str__())
        self.logger.info(f"intents to buy/sell set successfully: {self.token_buy.address.__str__()}, {self.token_sell.address.__str__()}")
        app.run(host="127.0.0.1", port=self.arguments.localhost_orderserver_port)

   # def startup(self):

   # def shutdown(self):

    def our_total_balance(self, token: ERC20Token) -> Wad:
        return token.balance_of(self.our_address)

    def _error_handler(self, err):
        logging.warning(f"Sending error back to caller {err.to_json()}")
        return err.to_json()

    def r_get_order(self):
        bands = AirswapBands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)
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

        # Only makerAmount or takerAmount should be sent in the request
        # Takers will usually request a makerAmount, however they can request takerAmount
        if 'makerAmount' in req['params']:
            maker_amount = Wad(int(req["params"]["makerAmount"]))
            taker_amount = Wad(0)

        elif 'takerAmount' in req['params']:
            taker_amount = Wad(int(req["params"]["takerAmount"]))
            maker_amount = Wad(0)

        else:
            raise CustomException('Neither takerAmount or makerAmount was specified in the request', status_code=400)

        # V2 should adjust for signed orders we already have out there (essentially create an orderbook)?
        # still debating...
        if (maker_token != self.token_buy.address.__str__()) and (maker_token != self.token_sell.address.__str__()):
            raise CustomException('Not set to trade this token pair', status_code=503)

        amount_side = 'buy' if maker_token == self.token_buy.address.__str__() else 'sell'
        our_buy_balance = self.our_total_balance(self.token_buy)
        our_sell_balance = self.our_total_balance(self.token_sell)
        target_price = self.price_feed.get_price()

        token_amnts = bands.new_orders(maker_amount, taker_amount, amount_side, our_buy_balance, our_sell_balance, target_price)
        if not token_amnts:
            raise CustomException('not enough in our wallet', status_code=409)

        else:
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

            # sign order with our private key
            signed_order = self.airswap_api.sign_order(new_order)

            # send signed order back to the taker
            logging.info(f"Sending order: {signed_order}")
            return signed_order


class EthToken():

    def __init__(self, address: Address):
        assert(isinstance(address, Address))

        self.address = address

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


class AirswapBands(Bands):

    @staticmethod
    def read(reloadable_config: ReloadableConfig, spread_feed: Feed, control_feed: Feed, history: History):
        assert(isinstance(reloadable_config, ReloadableConfig))
        assert(isinstance(spread_feed, Feed))
        assert(isinstance(control_feed, Feed))
        assert(isinstance(history, History))

        try:
            config = reloadable_config.get_config(spread_feed.get()[0])
            control_feed_value = control_feed.get()[0]

            buy_bands = list(map(BuyBand, config['buyBands']))
            buy_limits = SideLimits(config['buyLimits'] if 'buyLimits' in config else [], history.buy_history)
            sell_bands = list(map(SellBand, config['sellBands']))
            sell_limits = SideLimits(config['sellLimits'] if 'sellLimits' in config else [], history.sell_history)

            if len(buy_bands) != 1:
                logging.getLogger().warning("You must only have one buy band. This is required for airswap compatability.")
                buy_bands = []

            if len(sell_bands) != 1:
                logging.getLogger().warning("You must only have one sell band. This is required for airswap compatability.")
                sell_bands = []

            if 'canBuy' not in control_feed_value or 'canSell' not in control_feed_value:
                logging.getLogger().warning("Control feed expired. Assuming no buy bands and no sell bands.")

                buy_bands = []
                sell_bands = []

            else:
                if not control_feed_value['canBuy']:
                    logging.getLogger().warning("Control feed says we shall not buy. Assuming no buy bands.")
                    buy_bands = []

                if not control_feed_value['canSell']:
                    logging.getLogger().warning("Control feed says we shall not sell. Assuming no sell bands.")
                    sell_bands = []

        except Exception as e:
            logging.getLogger().exception(f"Config file is invalid ({e}). Treating the config file as it has no bands.")

            buy_bands = []
            buy_limits = SideLimits([], history.buy_history)
            sell_bands = []
            sell_limits = SideLimits([], history.buy_history)

        return AirswapBands(buy_bands=buy_bands, buy_limits=buy_limits, sell_bands=sell_bands, sell_limits=sell_limits)

    def new_orders(self,
                   maker_amount: Wad,
                   taker_amount: Wad,
                   side_amount: str,
                   our_buy_balance: Wad,
                   our_sell_balance: Wad,
                   target_price: Price) -> Tuple[list, Wad, Wad]:
        assert(isinstance(maker_amount, Wad))
        assert(isinstance(taker_amount, Wad))
        assert(isinstance(side_amount, str))
        assert(isinstance(our_buy_balance, Wad))
        assert(isinstance(our_sell_balance, Wad))
        assert(isinstance(target_price, Price))

        if target_price is not None:
            if side_amount == 'buy':
                new_order = self._new_buy_orders(maker_amount, taker_amount, our_buy_balance, target_price.buy_price) \
                    if target_price.buy_price is not None \
                    else {}
            else:
                new_order = self._new_sell_orders(maker_amount, taker_amount, our_sell_balance, target_price.sell_price) \
                    if target_price.sell_price is not None \
                    else {}

            return new_order

        # don't place orders
        else:
            return {}

    def _new_sell_orders(self, maker_amount: Wad, taker_amount: Wad, our_sell_balance: Wad, target_price: Wad):
        """Return sell orders which need to be placed to bring total amounts within all sell bands above minimums."""
        assert(isinstance(maker_amount, Wad))
        assert(isinstance(taker_amount, Wad))
        assert(isinstance(our_sell_balance, Wad))
        assert(isinstance(target_price, Wad))

        new_order = {}
        limit_amount = self.sell_limits.available_limit(time.time())
        band = self.sell_bands[0]

        if maker_amount == Wad(0):
            # need to build price by computing maker_amount
            # defaults to avg_price
            buy_amount = taker_amount
            price = band.avg_price(target_price)
            maker_amount = buy_amount / price
            pay_amount = Wad.min(maker_amount, our_sell_balance, limit_amount)

        else:
            # need to build price by computing taker_amount
            # finds closest margin to amount
            pay_amount = Wad.min(maker_amount, limit_amount, our_sell_balance)
            price = closest_margin_to_amount(band, maker_amount, target_price)
            buy_amount = pay_amount / price

        if (price > Wad(0)) and \
           (pay_amount > Wad(0)) and \
           (buy_amount > Wad(0)) and \
           (buy_amount >= taker_amount) and \
           (pay_amount >= maker_amount):

            self.logger.info(f"Sell band (spread <{band.min_margin}, {band.max_margin}>,"
                             f" amount <{band.min_amount}, {band.max_amount}>) has amount {pay_amount},"
                             f" creating new sell order with price {price}")

            new_order = {
                "maker_amount": pay_amount,
                "taker_amount": buy_amount
            }

        else:
            self.logger.warning(f"Was unable to build new order! Returning an empty dict.")

        return new_order

    def _new_buy_orders(self, maker_amount: Wad, taker_amount: Wad, our_buy_balance: Wad, target_price: Wad):
        """Return buy orders which need to be placed to bring total amounts within all buy bands above minimums."""
        assert(isinstance(maker_amount, Wad))
        assert(isinstance(taker_amount, Wad))
        assert(isinstance(our_buy_balance, Wad))
        assert(isinstance(target_price, Wad))

        new_order = {}
        limit_amount = self.buy_limits.available_limit(time.time())
        band = self.buy_bands[0]

        if maker_amount == Wad(0):
            # need to build price by computing maker_amount
            # defaults to avg_price
            buy_amount = taker_amount
            price = band.avg_price(target_price)
            maker_amount = buy_amount / price
            pay_amount = Wad.min(maker_amount, our_buy_balance, limit_amount)

        else:
            # need to build price by computing taker_amount
            pay_amount = Wad.min(maker_amount, limit_amount, our_buy_balance)
            price = closest_margin_to_amount(band, maker_amount, target_price)
            buy_amount = pay_amount * price

        if (price > Wad(0)) and \
           (pay_amount > Wad(0)) and \
           (buy_amount > Wad(0)) and \
           (buy_amount >= taker_amount) and \
           (pay_amount >= maker_amount):

            self.logger.info(f"Buy band (spread <{band.min_margin}, {band.max_margin}>,"
                             f" amount <{band.min_amount}, {band.max_amount}>) has amount {pay_amount},"
                             f" creating new buy order with price {price}")

            new_order = {
                "maker_amount": pay_amount,
                "taker_amount": buy_amount
            }

        else:
            self.logger.warning(f"Was unable to build new order! Returning an empty dict.")

        return new_order

def min_price(band, target_price: Wad) -> Wad:
    return band._apply_margin(target_price, band.min_margin)

def max_price(band, target_price: Wad) -> Wad:
    return band._apply_margin(target_price, band.max_margin)

def closest_margin_to_amount(band, token_amount, target_price):
    # selects either the min, avg, or max margin to calculate
    # price based on which amount (min, avg, or max) is closer
    # to the token amount being traded.

    if token_amount >= band.max_amount:
        return max_price(band, target_price)

    elif token_amount <= band.min_amount:
        return min_price(band, target_price)

    elif token_amount == band.avg_amount:
        return band.avg_price(target_price)

    elif token_amount < band.avg_amount:
        # compare between min_amount and avg_amount
        closest_amount = _find_closest(band.min_amount, band.avg_amount, token_amount)
    else:
        # compare between avg_amount and max_amount
        closest_amount = band._find_closest(band.avg_amount, band.max_amount, token_amount)

    closest_margin = _amount_to_margin(closest_amount)
    return band._apply_margin(target_price, closest_margin)

def _amount_to_margin(band, amount):
    # returns the margin that matches to the corrosponding amount.
    # min_amount -> min_margin etc...
    if amount == band.min_amount:
        return band.min_margin
    elif amount == band.avg_amount:
        return band.avg_margin
    else:
        return band.max_margin

def _find_closest(band, val1, val2, target):
    return val2 if target - val1 >= val2 - target else val1


if __name__ == '__main__':
    airswap_app = AirswapMarketMakerKeeper(sys.argv[1:])
    app.add_url_rule('/getOrder', view_func=airswap_app.r_get_order, methods=["POST"])
    app.register_error_handler(CustomException, airswap_app._error_handler)
    airswap_app.main()
