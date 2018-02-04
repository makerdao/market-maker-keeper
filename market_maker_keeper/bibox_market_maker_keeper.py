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

from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands
from market_maker_keeper.bibox_order_book import BiboxOrderBookManager
from market_maker_keeper.price import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from pyexchange.bibox import BiboxApi
from pymaker import Address
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.sai import Tub


class BiboxMarketMakerKeeper:
    """Keeper acting as a market maker on Bibox."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='bibox-market-maker-keeper')

        parser.add_argument("--bibox-api-server", type=str, default="https://api.bibox.com",
                            help="Address of the Bibox API server (default: 'https://api.bibox.com')")

        parser.add_argument("--bibox-api-key", type=str, required=True,
                            help="API key for the Bibox API")

        parser.add_argument("--bibox-secret", type=str, required=True,
                            help="Secret for the Bibox API")

        parser.add_argument("--bibox-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Bibox API (in seconds, default: 9.5)")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair on which the keeper should operate")

        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str, required=True,
                            help="Source of price feed. Tub price feed will be used if not specified")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of non-Tub price feed (in seconds, default: 120)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))
        logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
        logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.INFO)

        self.bibox_api = BiboxApi(api_server=self.arguments.bibox_api_server,
                                  api_key=self.arguments.bibox_api_key,
                                  secret=self.arguments.bibox_secret,
                                  timeout=self.arguments.bibox_timeout)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed,
                                                               self.arguments.price_feed_expiry, None)

        self.bibox_order_book_manager = BiboxOrderBookManager(bibox_api=self.bibox_api,
                                                              pair=self.pair(),
                                                              refresh_frequency=3)

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        user_info = self.bibox_api.user_info(retry=True)

        self.logger.info(f"Bibox API key seems to be valid")
        self.logger.info(f"Accessing Bibox as user_id: '{user_info['user_id']}', email: '{user_info['email']}'")

    def shutdown(self):
        while True:
            try:
                our_orders = self.bibox_api.get_orders(self.bibox_order_book_manager.pair, retry=True)
            except:
                continue

            if len(our_orders) == 0:
                break

            self.cancel_orders(our_orders)
            self.bibox_order_book_manager.wait_for_order_cancellation()

    def price(self) -> Wad:
        return self.price_feed.get_price()

    def pair(self):
        return self.arguments.pair.upper()

    def token_sell(self) -> str:
        return self.arguments.pair.split('_')[0].upper()

    def token_buy(self) -> str:
        return self.arguments.pair.split('_')[1].upper()

    def our_balance(self, our_balances: list, token: str) -> Wad:
        return Wad.from_number(next(filter(lambda coin: coin['symbol'] == token, our_balances))['balance'])

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        bands = Bands(self.bands_config)
        order_book = self.bibox_order_book_manager.get_order_book()
        target_price = self.price()

        if target_price is None:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_orders(order_book.orders)
            return

        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                                      our_sell_orders=self.our_sell_orders(order_book.orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.cancel_orders(cancellable_orders)
            return

        # Do not place new orders if order book state is not confirmed
        if order_book.in_progress:
            self.logger.debug("Order book is in progress, not placing new orders")
            return

        # Place new orders
        self.create_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                            our_sell_orders=self.our_sell_orders(order_book.orders),
                                            our_buy_balance=self.our_balance(order_book.balances, self.token_buy()),
                                            our_sell_balance=self.our_balance(order_book.balances, self.token_sell()),
                                            target_price=target_price)[0])

    def cancel_orders(self, orders):
        for order in orders:
            self.bibox_order_book_manager.cancel_order(order.order_id)

    def create_orders(self, orders):
        for order in orders:
            amount = order.pay_amount if order.is_sell else order.buy_amount
            money = order.buy_amount if order.is_sell else order.pay_amount

            self.bibox_order_book_manager.place_order(is_sell=order.is_sell,
                                                      amount=amount, amount_symbol=self.token_sell(),
                                                      money=money, money_symbol=self.token_buy())


if __name__ == '__main__':
    BiboxMarketMakerKeeper(sys.argv[1:]).main()
