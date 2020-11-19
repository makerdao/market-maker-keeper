# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2020 Exef
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
from math import log10
import sys
from datetime import datetime
from typing import List

from market_maker_keeper.band import NewOrder
from market_maker_keeper.cex_api import CEXKeeperAPI
from pymaker.numeric import Wad
from pyexchange.gemini import GeminiApi, GeminiOrder as Order


class GeminiMarketMakerKeeper(CEXKeeperAPI):
    """Keeper acting as a market maker on gemini."""

    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='gemini-market-maker-keeper')

        parser.add_argument("--gemini-api-server", type=str, default="https://api.gemini.com",
                            help="Address of the gemini API server (default: 'https://api.gemini.com')")

        parser.add_argument("--gemini-api-key", type=str, required=True,
                            help="API key for the gemini API")

        parser.add_argument("--gemini-secret-key", type=str, required=True,
                            help="Secret key for the gemini API")

        parser.add_argument("--gemini-timeout", type=float, default=9.5,
                            help="Timeout for accessing the gemini API (in seconds, default: 9.5)")
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
        
        self.gemini_api = GeminiApi(api_server=self.arguments.gemini_api_server,
                                      api_key=self.arguments.gemini_api_key,
                                      api_secret=self.arguments.gemini_secret_key,
                                      timeout=self.arguments.gemini_timeout)

        super().__init__(self.arguments, self.gemini_api)

    def startup(self):
        minimum_order_size, tick_size, quote_currency_price_increment = self.gemini_api.get_rules(self.pair())

        self.minimum_order_size = minimum_order_size
        self.price_precision = tick_size 
        self.amount_precision = quote_currency_price_increment 

    def shutdown(self):
        self.logger.info(f'Keeper shutting down...')
        self.order_book_manager.cancel_all_orders(final_wait_time=60)

    def pair(self):
        return self.arguments.pair.upper()

    def token_sell(self) -> str:
        return self.arguments.pair.split('-')[0].upper()

    def token_buy(self) -> str:
        return self.arguments.pair.split('-')[1].upper()

    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        token_balances = our_balances.get(token, None)
        if token_balances:
            return Wad.from_number(token_balances['availableForTrade'])
        else:
            return Wad(0)

    def place_orders(self, new_orders: List[NewOrder]):
        def place_order_function(new_order_to_be_placed):
            price = round(new_order_to_be_placed.price, int(self.price_precision))

            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            amount = round(amount, int(self.amount_precision))

            if Wad.from_number(amount) < self.minimum_order_size:
                self.logger.error(f"Not placing order: Amount {amount} lower that required minimum order size {self.minimum_order_size}")
                return

            self.logger.info(f'Placing an order of amount {amount} {self.token_sell()} @ price {price} {self.token_buy()}') 
            order_id = self.gemini_api.place_order(self.pair(), new_order_to_be_placed.is_sell, price, amount)

            return Order(order_id=order_id,
                         pair=self.pair(),
                         is_sell=new_order_to_be_placed.is_sell,
                         price=price,
                         timestamp=int(datetime.now().timestamp()),
                         amount=amount)

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))


if __name__ == '__main__':
    GeminiMarketMakerKeeper(sys.argv[1:]).main()
