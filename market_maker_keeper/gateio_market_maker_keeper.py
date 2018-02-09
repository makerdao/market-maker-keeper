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
from typing import List

import time

from market_maker_keeper.band import Bands, NewOrder
from market_maker_keeper.price import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.util import setup_logging
from pyexchange.gateio import GateIOApi, Order
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad


class GateIOMarketMakerKeeper:
    """Keeper acting as a market maker on Gate.io."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='gateio-market-maker-keeper')

        parser.add_argument("--gateio-api-server", type=str, default="https://data.gate.io",
                            help="Address of the Gate.io API server (default: 'https://data.gate.io')")

        parser.add_argument("--gateio-api-key", type=str, required=True,
                            help="API key for the Gate.io API")

        parser.add_argument("--gateio-secret-key", type=str, required=True,
                            help="Secret key for the Gate.io API")

        parser.add_argument("--gateio-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Gate.io API (in seconds, default: 9.5)")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--config", type=str, required=True,
                            help="Bands configuration file")

        parser.add_argument("--price-feed", type=str, required=True,
                            help="Source of price feed")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of the price feed (in seconds, default: 120)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed, self.arguments.price_feed_expiry)

        self.gateio_api = GateIOApi(api_server=self.arguments.gateio_api_server,
                                    api_key=self.arguments.gateio_api_key,
                                    secret_key=self.arguments.gateio_secret_key,
                                    timeout=self.arguments.gateio_timeout)

        self._last_order_creation = 0

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.on_startup(self.startup)
            lifecycle.every(10, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.our_orders()
        self.our_balances()
        self.logger.info(f"Gate.io API key seems to be valid")
        self.logger.info(f"Keeper configured to work on the '{self.pair()}' pair")

    def shutdown(self):
        self.gateio_api.cancel_all_orders(self.pair())

    def pair(self):
        return self.arguments.pair.lower()

    def token_sell(self) -> str:
        return self.arguments.pair.split('_')[0].upper()

    def token_buy(self) -> str:
        return self.arguments.pair.split('_')[1].upper()

    def our_balances(self) -> dict:
        return self.gateio_api.get_balances()

    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        try:
            return Wad.from_number(our_balances['available'][token])
        except KeyError:
            return Wad(0)

    def our_orders(self) -> list:
        return self.gateio_api.get_orders(self.pair())

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        bands = Bands(self.bands_config)
        our_balances = self.our_balances()
        our_orders = self.our_orders()
        target_price = self.price_feed.get_price()

        if target_price is None:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_orders(our_orders)
            return

        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(our_orders),
                                                      our_sell_orders=self.our_sell_orders(our_orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.cancel_orders(cancellable_orders)
            return

        # Place new orders
        new_orders = bands.new_orders(our_buy_orders=self.our_buy_orders(our_orders),
                                      our_sell_orders=self.our_sell_orders(our_orders),
                                      our_buy_balance=self.our_available_balance(our_balances, self.token_buy()),
                                      our_sell_balance=self.our_available_balance(our_balances, self.token_sell()),
                                      target_price=target_price)[0]

        if len(new_orders) > 0:
            if self.can_create_orders():
                self.create_orders(new_orders)
                self.register_order_creation()
            else:
                self.logger.info("Too little time elapsed from last order creation, waiting...")

    # Unfortunately the gate.io API does not immediately reflect the fact that our orders have
    # been placed. In order to avoid placing orders twice we explicitly wait some time here.
    def can_create_orders(self) -> bool:
        return time.time() - self._last_order_creation > 15

    def register_order_creation(self):
        self._last_order_creation = time.time()

    def cancel_orders(self, orders: List[Order]):
        for order in orders:
            self.gateio_api.cancel_order(self.pair(), order.order_id)

    def create_orders(self, orders: List[NewOrder]):
        for order in orders:
            amount = order.pay_amount if order.is_sell else order.buy_amount
            self.gateio_api.place_order(self.pair(), order.is_sell, order.price, amount)


if __name__ == '__main__':
    GateIOMarketMakerKeeper(sys.argv[1:]).main()
