# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2020 MikeHathaway
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
import operator
import sys
import time

from pyexchange.coinone import CoinoneApi, Order
from pymaker.numeric import Wad

from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.cex_api import CEXKeeperAPI
from market_maker_keeper.band import Bands


class CoinoneMarketMakerKeeper(CEXKeeperAPI):
    """
    Keeper acting as a market maker on Coinone.
    """
    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='coinone-market-maker-keeper')

        parser.add_argument("--coinone-api-server", type=str, default="https://api.coinone.co.kr",
                            help="Address of the Coinone API server (default: 'https://api.coinone.co.kr')")

        parser.add_argument("--coinone-access-token", type=str, required=True,
                            help="API access token for the Coinone API")

        parser.add_argument("--coinone-secret-key", type=str, required=True,
                            help="API secret key for the Coinone API")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

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

        parser.add_argument("--refresh-frequency", type=int, default=3,
                            help="Order book refresh frequency (in seconds, default: 3)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)

        self.coinone_api = CoinoneApi(api_server=self.arguments.coinone_api_server,
                                access_token=self.arguments.coinone_access_token,
                                secret_key=self.arguments.coinone_secret_key)

        super().__init__(self.arguments, self.coinone_api)

    # override init as cancel_orders() has a non standard interface
    def init_order_book_manager(self, arguments, coinone_api):
        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: self.coinone_api.get_orders(self.pair()))
        self.order_book_manager.get_balances_with(lambda: self.coinone_api.get_balances())
        self.order_book_manager.cancel_orders_with(
            lambda order: self.coinone_api.cancel_order(order.order_id, self.pair(), order.price, order.amount, order.is_sell))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders,
                                                         self.our_sell_orders)

        self.order_book_manager.pair = self.pair()
        self.order_book_manager.start()

    def pair(self):
        return self.arguments.pair

    def token_sell(self) -> str:
        return self.arguments.pair.split('-')[0].lower()

    def token_buy(self) -> str:
        return self.arguments.pair.split('-')[1].lower()

    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        return Wad.from_number(float(our_balances[token.lower()]["avail"]))

    def place_orders(self, new_orders):
        def place_order_function(new_order_to_be_placed):
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount

            order_id = self.coinone_api.place_order(pair=self.pair().upper(),
                                                  is_sell=new_order_to_be_placed.is_sell,
                                                  price=new_order_to_be_placed.price,
                                                  amount=amount)

            return Order(str(order_id), int(time.time()), self.pair(), new_order_to_be_placed.is_sell,
                         new_order_to_be_placed.price, amount)

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))



if __name__ == '__main__':
    CoinoneMarketMakerKeeper(sys.argv[1:]).main()
