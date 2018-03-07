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

from market_maker_keeper.band import Bands
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.util import setup_logging
from pyexchange.bibox import BiboxApi, Order
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad


class BiboxMarketMakerKeeper:
    """Keeper acting as a market maker on Bibox."""

    logger = logging.getLogger()

    def __init__(self, args: list):
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

        self.history = History()
        self.bibox_api = BiboxApi(api_server=self.arguments.bibox_api_server,
                                  api_key=self.arguments.bibox_api_key,
                                  secret=self.arguments.bibox_secret,
                                  timeout=self.arguments.bibox_timeout)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)

        self.order_book_manager = OrderBookManager(refresh_frequency=3)
        self.order_book_manager.get_orders_with(lambda: self.bibox_api.get_orders(pair=self.pair(), retry=True))
        self.order_book_manager.get_balances_with(lambda: self.bibox_api.coin_list(retry=True))
        self.order_book_manager.start()

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def shutdown(self):
        while True:
            try:
                our_orders = self.bibox_api.get_orders(self.pair(), retry=True)
            except:
                continue

            if len(our_orders) == 0:
                break

            self.cancel_orders(our_orders)
            self.order_book_manager.wait_for_order_cancellation()

    def pair(self):
        return self.arguments.pair.upper()

    def token_sell(self) -> str:
        return self.arguments.pair.split('_')[0].upper()

    def token_buy(self) -> str:
        return self.arguments.pair.split('_')[1].upper()

    def our_available_balance(self, our_balances: list, token: str) -> Wad:
        return Wad.from_number(next(filter(lambda coin: coin['symbol'] == token, our_balances))['balance'])

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        bands = Bands(self.bands_config, self.history)
        order_book = self.order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()

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
        if order_book.orders_being_placed or order_book.orders_being_cancelled:
            self.logger.debug("Order book is in progress, not placing new orders")
            return

        # Place new orders
        self.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                           our_sell_orders=self.our_sell_orders(order_book.orders),
                                           our_buy_balance=self.our_available_balance(order_book.balances, self.token_buy()),
                                           our_sell_balance=self.our_available_balance(order_book.balances, self.token_sell()),
                                           target_price=target_price)[0])

    def cancel_orders(self, orders):
        for order in orders:
            self.order_book_manager.cancel_order(order.order_id, lambda order=order: self.bibox_api.cancel_order(order.order_id))

    def place_orders(self, new_orders):
        def place_order_function(new_order_to_be_placed):
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            amount_symbol = self.token_sell()
            money = new_order_to_be_placed.buy_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.pay_amount
            money_symbol = self.token_buy()

            new_order_id = self.bibox_api.place_order(is_sell=new_order_to_be_placed.is_sell,
                                                      amount=amount,
                                                      amount_symbol=amount_symbol,
                                                      money=money,
                                                      money_symbol=money_symbol)

            return Order(new_order_id, 0, new_order_to_be_placed.is_sell, Wad(0), amount, amount_symbol, money, money_symbol)

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))


if __name__ == '__main__':
    BiboxMarketMakerKeeper(sys.argv[1:]).main()
