# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017 reverendus
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
from functools import reduce

import itertools
from typing import Tuple, List

from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands
from market_maker_keeper.bibox_order_book import BiboxOrderBookManager
from market_maker_keeper.okcoin import OKCoinApi
from market_maker_keeper.price import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from pymaker import Address, Wad
from pymaker.bibox import BiboxApi, Order
from pymaker.lifecycle import Web3Lifecycle
from pymaker.sai import Tub, Vox


class OkexMarketMakerKeeper:
    """Keeper acting as a market maker on OKEX."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='okex-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--okex-api-server", type=str, default="https://www.okex.com",
                            help="Address of the OKEX API server (default: 'https://www.okex.com')")

        parser.add_argument("--okex-api-key", type=str, required=True,
                            help="API key for the OKEX API")

        parser.add_argument("--okex-secret-key", type=str, required=True,
                            help="Secret key for the OKEX API")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair on which the keeper should operate")

        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str,
                            help="Source of price feed")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of the price feed (in seconds, default: 120)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)

        # TODO web3 connection exists only so the `Web3Lifecycle` works
        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))
        logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
        logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.INFO)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed, self.arguments.price_feed_expiry)

        self.okex_api = OKCoinApi(api_server=self.arguments.okex_api_server,
                                  api_key=self.arguments.okex_api_key,
                                  secret_key=self.arguments.okex_secret_key,
                                  timeout=9.5)

    def main(self):
        with Web3Lifecycle(self.web3) as lifecycle:
            self.lifecycle = lifecycle
            lifecycle.wait_for_sync(False)
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.okex_api.get_orders(self.arguments.pair)
        self.logger.info(f"OKEX API key seems to be valid")
        self.logger.info(f"Keeper configured to work on the '{self.arguments.pair}' pair")

    def shutdown(self):
        pass
        # while True:
        #     try:
        #         our_orders = self.bibox_api.get_orders(self.bibox_order_book_manager.pair, retry=True)
        #     except:
        #         continue
        #
        #     if len(our_orders) == 0:
        #         break
        #
        #     self.cancel_orders(our_orders)
        #     self.bibox_order_book_manager.wait_for_order_cancellation()

    def token_sell(self) -> str:
        return self.arguments.pair[0:3]

    def token_buy(self) -> str:
        return self.arguments.pair[4:7]

    def our_balance(self, our_balances: dict, symbol: str) -> Wad:
        print(our_balances['free']['eth'])
        print(our_balances['freezed']['eth'])
        return Wad.from_number(our_balances['free'][symbol])

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        bands = Bands(self.bands_config)
        our_balances = self.okex_api.get_balances()
        our_orders = self.okex_api.get_orders(self.arguments.pair)  # order_book = self.bibox_order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()

        if target_price is None:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_orders(our_orders)
            return

        orders_to_cancel = list(itertools.chain(bands.excessive_buy_orders(self.our_buy_orders(our_orders), target_price),
                                                bands.excessive_sell_orders(self.our_sell_orders(our_orders), target_price),
                                                bands.outside_orders(self.our_buy_orders(our_orders),
                                                                     self.our_sell_orders(our_orders), target_price)))
        if len(orders_to_cancel) > 0:
            self.cancel_orders(orders_to_cancel)
        else:
            # if not order_book.in_progress:
            self.top_up_bands(our_orders, our_balances, bands.buy_bands, bands.sell_bands, target_price)
            # else:
            #     self.logger.debug("Order book is in progress, not placing new orders")

    def cancel_orders(self, orders):
        for order in orders:
            self.okex_api.cancel_order(self.arguments.pair, order.order_id)
            # self.bibox_order_book_manager.cancel_order(order.order_id, retry=True)

    def top_up_bands(self, our_orders: list, our_balances: dict, buy_bands: list, sell_bands: list, target_price: Wad):
        """Create new buy and sell orders in all send and buy bands if necessary."""
        self.top_up_buy_bands(our_orders, our_balances, buy_bands, target_price)
        self.top_up_sell_bands(our_orders, our_balances, sell_bands, target_price)

    def top_up_sell_bands(self, our_orders: list, our_balances: dict, sell_bands: list, target_price: Wad):
        """Ensure our sell engagement is not below minimum in all sell bands. Place new orders if necessary."""
        our_available_balance = self.our_balance(our_balances, self.token_sell())
        for band in sell_bands:
            orders = [order for order in self.our_sell_orders(our_orders) if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = band.avg_price(target_price)
                pay_amount = Wad.min(band.avg_amount - total_amount, our_available_balance)
                buy_amount = pay_amount * price
                if (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new sell order")

                    self.bibox_order_book_manager.place_order(is_sell=True,
                                                              amount=pay_amount, amount_symbol='ETH',
                                                              money=buy_amount, money_symbol='DAI')
                    our_available_balance = our_available_balance - buy_amount

    def top_up_buy_bands(self, our_orders: list, our_balances: dict, buy_bands: list, target_price: Wad):
        """Ensure our buy engagement is not below minimum in all buy bands. Place new orders if necessary."""
        our_available_balance = self.our_balance(our_balances, self.token_buy())
        for band in buy_bands:
            orders = [order for order in self.our_buy_orders(our_orders) if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = band.avg_price(target_price)
                pay_amount = Wad.min(band.avg_amount - total_amount, our_available_balance)
                buy_amount = pay_amount / price
                if (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new buy order")

                    self.okex_api.place_order(pair=self.arguments.pair, is_sell=False, price=price, amount=buy_amount)
                    our_available_balance = our_available_balance - pay_amount

    def total_amount(self, orders):
        return reduce(operator.add, map(lambda order: order.amount if order.is_sell else order.money, orders), Wad(0))


if __name__ == '__main__':
    OkexMarketMakerKeeper(sys.argv[1:]).main()
