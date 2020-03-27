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
import sys
import time

from eth_utils import from_wei

from pyexchange.dydx import DydxApi, Order
from pymaker.numeric import Wad

from market_maker_keeper.api import KeeperAPI


class DyDxMarketMakerKeeper(KeeperAPI):
    """Keeper acting as a market maker on DyDx."""

    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='dydx-market-maker-keeper')

        parser.add_argument("--dydx-api-server", type=str, default="https://www.dydx.com",
                            help="Address of the DyDx API server (default: 'https://www.dydx.com')")

        parser.add_argument("--dydx-private-key", type=str, required=True,
                            help="API key for the DyDx API")

        parser.add_argument("--dydx-timeout", type=float, default=9.5,
                            help="Timeout for accessing the DyDx API (in seconds, default: 9.5)")

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

        self.dydx_api = DydxApi(node=self.arguments.dydx_api_server,
                                private_key=self.arguments.dydx_private_key)

        super().__init__(self.arguments, self.dydx_api)

    def pair(self):
        return self.arguments.pair

    def token_sell(self) -> str:
        return self.arguments.pair.split('-')[0].lower()

    def token_buy(self) -> str:
        return self.arguments.pair.split('-')[1].lower()

    # TODO: fix handling negative balances
    # DyDx can have negative balances from native margin trading
    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        if token == 'weth':
            token = 'eth'

        wei_balance = list(filter(lambda x: x['currency'] == token.upper(), our_balances))[0]['wei']
        ## reconvert Wad to negative value if balance is negative
        # is_negative = False
        # if wei_balance < 0:
        #    is_negative = True
        balance = from_wei(abs(int(float(wei_balance))), 'ether')
        return Wad.from_number(balance)

    def place_orders(self, new_orders):
        def place_order_function(new_order_to_be_placed):
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            order_id = self.dydx_api.place_order(pair=self.pair().upper(),
                                                 is_sell=new_order_to_be_placed.is_sell,
                                                 price=round(Wad.__float__(new_order_to_be_placed.price), 18),
                                                 amount=round(Wad.__float__(amount), 18))

            return Order(str(order_id), int(time.time()), self.pair(), new_order_to_be_placed.is_sell, new_order_to_be_placed.price, amount)

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))


if __name__ == '__main__':
    DyDxMarketMakerKeeper(sys.argv[1:]).main()
