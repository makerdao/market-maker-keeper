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

from market_maker_keeper.band import Bands
from market_maker_keeper.limit import History
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pyexchange.okex import OKEXApi
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad


class OkexMarketMakerKeeper:
    """Keeper acting as a market maker on OKEX."""

    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='okex-market-maker-keeper')

        parser.add_argument("--okex-api-server", type=str, default="https://www.okex.com",
                            help="Address of the OKEX API server (default: 'https://www.okex.com')")

        parser.add_argument("--okex-api-key", type=str, required=True,
                            help="API key for the OKEX API")

        parser.add_argument("--okex-secret-key", type=str, required=True,
                            help="Secret key for the OKEX API")

        parser.add_argument("--okex-timeout", type=float, default=9.5,
                            help="Timeout for accessing the OKEX API (in seconds, default: 9.5)")

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

        parser.add_argument("--order-history", type=str,
                            help="Endpoint to report active orders to")

        parser.add_argument("--order-history-every", type=int, default=30,
                            help="Frequency of reporting active orders (in seconds, default: 30)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)

        self.history = History()
        self.okex_api = OKEXApi(api_server=self.arguments.okex_api_server,
                                api_key=self.arguments.okex_api_key,
                                secret_key=self.arguments.okex_secret_key,
                                timeout=self.arguments.okex_timeout)

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.every(3, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    @retry(delay=5, logger=logger)
    def shutdown(self):
        self.cancel_orders(self.our_orders())

    def pair(self):
        return self.arguments.pair.lower()

    def token_sell(self) -> str:
        return self.arguments.pair.split('_')[0].lower()

    def token_buy(self) -> str:
        return self.arguments.pair.split('_')[1].lower()

    def our_balances(self) -> dict:
        return self.okex_api.get_balances()

    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        return Wad.from_number(our_balances['free'][token])

    def our_orders(self) -> list:
        return self.okex_api.get_orders(self.pair())

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        bands = Bands(self.bands_config, self.spread_feed, self.history)
        our_balances = self.our_balances()
        our_orders = self.our_orders()
        target_price = self.price_feed.get_price()

        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(our_orders),
                                                      our_sell_orders=self.our_sell_orders(our_orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.cancel_orders(cancellable_orders)
            return

        # Place new orders
        self.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(our_orders),
                                           our_sell_orders=self.our_sell_orders(our_orders),
                                           our_buy_balance=self.our_available_balance(our_balances, self.token_buy()),
                                           our_sell_balance=self.our_available_balance(our_balances, self.token_sell()),
                                           target_price=target_price)[0])

    def cancel_orders(self, orders):
        for order in orders:
            self.okex_api.cancel_order(self.pair(), order.order_id)

    def place_orders(self, new_orders):
        for new_order in new_orders:
            amount = new_order.pay_amount if new_order.is_sell else new_order.buy_amount
            self.okex_api.place_order(pair=self.pair(), is_sell=new_order.is_sell, price=new_order.price, amount=amount)


if __name__ == '__main__':
    OkexMarketMakerKeeper(sys.argv[1:]).main()
