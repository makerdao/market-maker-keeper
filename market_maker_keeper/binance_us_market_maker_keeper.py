# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2020 ith-harvey, grandizzy, Exef
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
from datetime import datetime
from typing import List

import time
from math import log10

from market_maker_keeper.band import Band, Bands, NewOrder
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.feed import Feed
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pyexchange.binance_us import BinanceUsApi, BinanceUsOrder as Order, BinanceUsRules


class BinanceBands(Bands):
    @staticmethod
    def read(reloadable_config: ReloadableConfig, spread_feed: Feed, control_feed: Feed, history: History, rules: BinanceUsRules):
        assert(isinstance(rules, BinanceUsRules))
        bands = Bands.read(reloadable_config, spread_feed, control_feed, history)

        return BinanceBands(bands, rules)
    
    def __init__(self, bands: Bands, rules: BinanceUsRules):
        assert(isinstance(rules, BinanceUsRules))
        assert(isinstance(bands, Bands))

        self.buy_bands = bands.buy_bands
        self.buy_limits = bands.buy_limits
        self.sell_bands = bands.sell_bands
        self.sell_limits = bands.sell_limits

        self.rules = rules


    def _new_sell_orders(self, our_sell_orders: list, our_sell_balance: Wad, target_price: Wad):
        """Return sell orders which need to be placed to bring total amounts within all sell bands above minimums."""
        assert(isinstance(our_sell_orders, list))
        assert(isinstance(our_sell_balance, Wad))
        assert(isinstance(target_price, Wad))

        new_orders = []
        limit_amount = self.sell_limits.available_limit(time.time())
        missing_amount = Wad(0)

        for band in self.sell_bands:
            orders = [order for order in our_sell_orders if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = self._calculate_price(band, target_price)
                pay_amount = Wad.min(band.avg_amount - total_amount, our_sell_balance, limit_amount)
                buy_amount = self._calculate_buy_amount_for_sell_orders(price, pay_amount)
                missing_amount += Wad.max((band.avg_amount - total_amount) - our_sell_balance, Wad(0))
                if (price > Wad(0)) and (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.info(f"Sell band (spread <{band.min_margin}, {band.max_margin}>,"
                                     f" amount <{band.min_amount}, {band.max_amount}>) has amount {total_amount},"
                                     f" creating new sell order with price {price}")

                    our_sell_balance = our_sell_balance - pay_amount
                    limit_amount = limit_amount - pay_amount

                    new_orders.append(NewOrder(is_sell=True,
                                               price=price,
                                               amount=pay_amount,
                                               pay_amount=pay_amount,
                                               buy_amount=buy_amount,
                                               band=band,
                                               confirm_function=lambda: self.sell_limits.use_limit(time.time(), pay_amount)))

        return new_orders, missing_amount

    def _new_buy_orders(self, our_buy_orders: list, our_buy_balance: Wad, target_price: Wad):
        """Return buy orders which need to be placed to bring total amounts within all buy bands above minimums."""
        assert(isinstance(our_buy_orders, list))
        assert(isinstance(our_buy_balance, Wad))
        assert(isinstance(target_price, Wad))

        new_orders = []
        limit_amount = self.buy_limits.available_limit(time.time())
        missing_amount = Wad(0)

        for band in self.buy_bands:
            orders = [order for order in our_buy_orders if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = self._calculate_price(band, target_price)
                pay_amount = Wad.min(band.avg_amount - total_amount, our_buy_balance, limit_amount)
                buy_amount = self._calculate_buy_amount_for_buy_orders(price, pay_amount)
                missing_amount += Wad.max((band.avg_amount - total_amount) - our_buy_balance, Wad(0))
                if (price > Wad(0)) and (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.info(f"Buy band (spread <{band.min_margin}, {band.max_margin}>,"
                                     f" amount <{band.min_amount}, {band.max_amount}>) has amount {total_amount},"
                                     f" creating new buy order with price {price}")

                    our_buy_balance = our_buy_balance - pay_amount
                    limit_amount = limit_amount - pay_amount

                    new_orders.append(NewOrder(is_sell=False,
                                               price=price,
                                               amount=buy_amount,
                                               pay_amount=pay_amount,
                                               buy_amount=buy_amount,
                                               band=band,
                                               confirm_function=lambda: self.buy_limits.use_limit(time.time(), pay_amount)))

        return new_orders, missing_amount

    def _calculate_price(self, band: Band, target_price: Wad) -> Wad:
        price = band.avg_price(target_price)

        if self._is_incorrect_price(price):
            precision = -int((log10(float(self.rules.tick_size))))

            price = Wad.from_number(round(price, precision))

        return price

    def _calculate_buy_amount_for_sell_orders(self, price: Wad, pay_amount: Wad) -> Wad:
        buy_amount = pay_amount * price

        if self._is_incorrect_amount(buy_amount):
            precision = self._get_decimal_places(self.rules.tick_size)
            buy_amount = Wad.from_number(round(buy_amount, precision))

        return buy_amount
    
    def _calculate_buy_amount_for_buy_orders(self, price: Wad, pay_amount: Wad) -> Wad:
        buy_amount = pay_amount / price

        if self._is_incorrect_amount(buy_amount):
            precision = self._get_decimal_places(self.rules.step_size)
            buy_amount = Wad.from_number(round(buy_amount, precision))

        return buy_amount

    def _is_incorrect_price(self, price: Wad) -> bool:
        return not ((price - self.rules.min_price) % self.rules.tick_size == Wad(0))

    def _is_incorrect_amount(self, amount: Wad) -> bool:
        return not ((amount - self.rules.min_quantity) % self.rules.step_size == Wad(0))
              
    @staticmethod
    def _get_decimal_places(number: Wad) -> int:
        assert(isinstance(number, Wad))
        return -int((log10(float(number))))   


class BinanceUsMarketMakerKeeper:
    """Keeper acting as a market maker on Binance US."""

    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='binance-us-market-maker-keeper')

        parser.add_argument("--binance-us-api-server", type=str, default="https://api.binance.us",
                            help="Address of the Binance US API server (default: 'https://api.binance.us')")

        parser.add_argument("--binance-us-api-key", type=str, required=True,
                            help="API key for the Binance US API")

        parser.add_argument("--binance-us-secret-key", type=str, required=True,
                            help="Secret key for the Binance US API")

        parser.add_argument("--binance-us-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Binance US API (in seconds, default: 9.5)")
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
        setup_logging(self.arguments)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)

        self.history = History()

        self.binance_api = BinanceUsApi(api_server=self.arguments.binance_us_api_server,
                                        api_key=self.arguments.binance_us_api_key,
                                        secret_key=self.arguments.binance_us_secret_key,
                                        timeout=self.arguments.binance_us_timeout)

        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: self.binance_api.get_orders(self.pair()))
        self.order_book_manager.get_balances_with(lambda: self.binance_api.get_balances())
        self.order_book_manager.cancel_orders_with(lambda order: self.binance_api.cancel_order(order.order_id, self.pair()))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders,
                                                         self.our_sell_orders)
        self.order_book_manager.start()

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        quote_asset_precision, quote_precision = self.binance_api.get_precision(self.pair()) 
        self.quote_asset_precision = quote_asset_precision
        self.quote_precision = quote_precision

    def shutdown(self):
        self.order_book_manager.cancel_all_orders()

    def pair(self):
        return self.arguments.pair.upper()

    def token_sell(self) -> str:
        return self.arguments.pair.split('-')[0].upper()

    def token_buy(self) -> str:
        return self.arguments.pair.split('-')[1].upper()

    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        token_balance = our_balances.get(token, None)
        
        if token_balance:
            return Wad.from_number(token_balance['free'])
        else:
            return Wad(0)

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        rules = self.binance_api.get_rules(self.pair())
        bands = BinanceBands.read(self.bands_config, self.spread_feed, self.control_feed, self.history, rules)

        order_book = self.order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()

        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                                      our_sell_orders=self.our_sell_orders(order_book.orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.order_book_manager.cancel_orders(cancellable_orders)
            return

        # Do not place new orders if order book state is not confirmed
        if order_book.orders_being_placed or order_book.orders_being_cancelled:
            self.logger.debug("Order book is in progress, not placing new orders")
            return

        # Place new orders
        new_orders = bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                      our_sell_orders=self.our_sell_orders(order_book.orders),
                                      our_buy_balance=self.our_available_balance(order_book.balances, self.token_buy()),
                                      our_sell_balance=self.our_available_balance(order_book.balances, self.token_sell()),
                                      target_price=target_price)[0]

        self.place_orders(new_orders)

    def place_orders(self, new_orders: List[NewOrder]):
        def place_order_function(new_order_to_be_placed):
            price = round(new_order_to_be_placed.price, self.quote_precision)
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            amount = round(amount, self.quote_asset_precision)

            order_id = self.binance_api.place_order(self.pair(), new_order_to_be_placed.is_sell, price, amount)

            return Order(order_id=order_id,
                         pair=self.pair(),
                         is_sell=new_order_to_be_placed.is_sell,
                         price=price,                         
                         timestamp=int(datetime.now().timestamp()),
                         amount=amount)

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))


if __name__ == '__main__':
    BinanceUsMarketMakerKeeper(sys.argv[1:]).main()
