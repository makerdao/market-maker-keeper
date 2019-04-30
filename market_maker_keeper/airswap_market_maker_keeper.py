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

from retry import retry
from web3 import Web3, HTTPProvider
from flask import Flask, jsonify, request

from market_maker_keeper.band import Bands
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

        parser.add_argument("--locahost-orderserver-port", type=str, default='5004',
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

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
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
        print(f"in main!")
        order_api = Flask(__name__)
        order_api.run(host="0.0.0.0", port=self.arguments.locahost_orderserver_port)

        @order_api.route("/getOrder", methods=["POST"])
        def get_order():
            print(f"someone is calling getOrder!")


   # def startup(self):

   # def shutdown(self):

   # def approve(self):

    def our_total_balance(self, token: ERC20Token) -> Wad:
        return token.balance_of(self.our_address)




if __name__ == '__main__':
    AirswapMarketMakerKeeper(sys.argv[1:]).main()
