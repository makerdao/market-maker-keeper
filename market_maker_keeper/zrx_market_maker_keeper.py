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
import time
from threading import Lock

from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands, NewOrder, BuyBand
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory, Price
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pyexchange.zrx import ZrxApi, Pair
from pymaker import Address
from pymaker.approval import directly
from pymaker.keys import register_keys
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.token import ERC20Token
from pymaker.util import eth_balance
from pymaker.zrx import ZrxExchange, ZrxRelayerApi


class ZrxMarketMakerKeeper:
    """Keeper acting as a market maker on any 0x exchange implementing the Standard 0x Relayer API V0."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='0x-market-maker-keeper')

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

        parser.add_argument("--relayer-api-server", type=str, required=True,
                            help="Address of the 0x Relayer API")

        parser.add_argument("--relayer-per-page", type=int, default=100,
                            help="Number of orders to fetch per one page from the 0x Relayer API (default: 100)")

        parser.add_argument("--buy-token-address", type=str, required=True,
                            help="Ethereum address of the buy token")

        parser.add_argument("--buy-token-decimals", type=int, default=18,
                            help="Number of decimals of the buy token")

        parser.add_argument("--sell-token-address", type=str, required=True,
                            help="Ethereum address of the sell token")

        parser.add_argument("--sell-token-decimals", type=int, default=18,
                            help="Number of decimals of the sell token")

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

        parser.add_argument("--order-expiry", type=int, required=True,
                            help="Expiration time of created orders (in seconds)")

        parser.add_argument("--order-expiry-threshold", type=int, default=0,
                            help="How long before order expiration it is considered already expired (in seconds)")

        parser.add_argument("--use-full-balances", dest='use_full_balances', action='store_true',
                            help="Do not subtract the amounts locked by current orders from available balances")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper will cease operation")

        parser.add_argument('--cancel-on-shutdown', dest='cancel_on_shutdown', action='store_true',
                            help="Whether should cancel all open orders on keeper shutdown")

        parser.add_argument("--remember-own-orders", dest='remember_own_orders', action='store_true',
                            help="Whether should the keeper remember his own submitted orders")

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--ethgasstation-api-key", type=str, default=None, help="ethgasstation API key")

        parser.add_argument("--refresh-frequency", type=int, default=3,
                            help="Order book refresh frequency (in seconds, default: 3)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        register_keys(self.web3, self.arguments.eth_key)

        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.gas_price = GasPriceFactory().create_gas_price(self.web3, self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)

        self.history = History()

        # Delegate 0x specific init to a function to permit overload for 0xv2
        self.zrx_exchange = None
        self.zrx_relayer_api = None
        self.zrx_api = None
        self.pair = None
        self.init_zrx()

        self.placed_zrx_orders = []
        self.placed_zrx_orders_lock = Lock()

        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: self.get_orders())
        self.order_book_manager.get_balances_with(lambda: self.get_balances())
        self.order_book_manager.place_orders_with(self.place_order_function)
        self.order_book_manager.cancel_orders_with(self.cancel_order_function)
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders, self.our_sell_orders)
        self.order_book_manager.start()

    def init_zrx(self):
        self.zrx_exchange = ZrxExchange(web3=self.web3, address=Address(self.arguments.exchange_address))
        self.zrx_relayer_api = ZrxRelayerApi(exchange=self.zrx_exchange, api_server=self.arguments.relayer_api_server)
        self.zrx_api = ZrxApi(zrx_exchange=self.zrx_exchange)

        self.pair = Pair(sell_token_address=Address(self.arguments.sell_token_address),
                         sell_token_decimals=self.arguments.sell_token_decimals,
                         buy_token_address=Address(self.arguments.buy_token_address),
                         buy_token_decimals=self.arguments.buy_token_decimals)


    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

    def shutdown(self):
        self.order_book_manager.cancel_all_orders(final_wait_time=60)

    def approve(self):
        token_buy = ERC20Token(web3=self.web3, address=Address(self.pair.buy_token_address))
        token_sell = ERC20Token(web3=self.web3, address=Address(self.pair.sell_token_address))

        self.zrx_exchange.approve([token_sell, token_buy], directly(gas_price=self.gas_price))

    def remove_expired_orders(self, orders: list) -> list:
        current_timestamp = int(time.time())
        return list(filter(lambda order: order.zrx_order.expiration > current_timestamp + self.arguments.order_expiry_threshold, orders))

    def remove_expired_zrx_orders(self, zrx_orders: list) -> list:
        current_timestamp = int(time.time())
        return list(filter(lambda order: order.expiration > current_timestamp + self.arguments.order_expiry_threshold, zrx_orders))

    def remove_filled_or_cancelled_zrx_orders(self, zrx_orders: list) -> list:
        return list(filter(lambda order: self.zrx_exchange.get_unavailable_buy_amount(order) < order.buy_amount, zrx_orders))

    def get_orders(self) -> list:
        def remove_old_zrx_orders(zrx_orders: list) -> list:
            return self.remove_filled_or_cancelled_zrx_orders(self.remove_expired_zrx_orders(zrx_orders))

        with self.placed_zrx_orders_lock:
            self.placed_zrx_orders = remove_old_zrx_orders(self.placed_zrx_orders)

        api_zrx_orders = remove_old_zrx_orders(self.zrx_relayer_api.get_orders_by_maker(self.our_address, self.arguments.relayer_per_page))

        with self.placed_zrx_orders_lock:
            zrx_orders = list(set(self.placed_zrx_orders + api_zrx_orders))

        return self.zrx_api.get_orders(self.pair, zrx_orders)

    def get_balances(self):
        balances = self.zrx_api.get_balances(self.pair)
        return balances[0], balances[1], eth_balance(self.web3, self.our_address)

    def our_total_sell_balance(self, balances) -> Wad:
        return balances[0]

    def our_total_buy_balance(self, balances) -> Wad:
        return balances[1]

    def our_eth_balance(self, balances) -> Wad:
        return balances[2]

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        bands = Bands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)
        order_book = self.order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()

        # We filter out expired orders from the order book snapshot. The reason for that is that
        # it allows us to replace expired orders faster. Without it, we would have to wait
        # for the next order book refresh in order to realize an order has expired. Unfortunately,
        # in case of 0x order book refresh can be quite slow as it involves making multiple calls
        # to the Ethereum node.
        #
        # By filtering out expired orders here, we can replace them the next `synchronize_orders`
        # tick after they expire. Which is ~ 1s delay, instead of avg ~ 5s without this trick.
        orders = self.remove_expired_orders(order_book.orders)

        if self.our_eth_balance(order_book.balances) < self.min_eth_balance:
            self.logger.warning("Keeper ETH balance below minimum. Cancelling all orders.")
            self.order_book_manager.cancel_all_orders()
            return

        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(orders),
                                                      our_sell_orders=self.our_sell_orders(orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.order_book_manager.cancel_orders(cancellable_orders)
            return

        # Do not place new orders if order book state is not confirmed
        if order_book.orders_being_placed or order_book.orders_being_cancelled:
            self.logger.debug("Order book is in progress, not placing new orders")
            return

        # Balances returned by `our_total_***_balance` still contain amounts "locked"
        # by currently open orders, so we need to explicitly subtract these amounts.
        if self.arguments.use_full_balances:
            our_buy_balance = self.our_total_buy_balance(order_book.balances)
            our_sell_balance = self.our_total_sell_balance(order_book.balances)
        else:
            our_buy_balance = self.our_total_buy_balance(order_book.balances) - Bands.total_amount(self.our_buy_orders(orders))
            our_sell_balance = self.our_total_sell_balance(order_book.balances) - Bands.total_amount(self.our_sell_orders(orders))

        # Place new orders
        self.order_book_manager.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(orders),
                                                              our_sell_orders=self.our_sell_orders(orders),
                                                              our_buy_balance=our_buy_balance,
                                                              our_sell_balance=our_sell_balance,
                                                              target_price=target_price)[0])

    def place_order_function(self, new_order: NewOrder):
        assert(isinstance(new_order, NewOrder))

        order_expiry = int(new_order.band.params.get('orderExpiry', self.arguments.order_expiry))

        zrx_order = self.zrx_api.place_order(pair=self.pair,
                                             is_sell=new_order.is_sell,
                                             price=new_order.price,
                                             amount=new_order.amount,
                                             expiration=int(time.time()) + order_expiry)

        zrx_order = self.zrx_relayer_api.calculate_fees(zrx_order)
        zrx_order = self.zrx_exchange.sign_order(zrx_order)

        if self.zrx_relayer_api.submit_order(zrx_order):
            if self.arguments.remember_own_orders:
                with self.placed_zrx_orders_lock:
                    self.placed_zrx_orders.append(zrx_order)

            order = self.zrx_api.get_orders(self.pair, [zrx_order])[0]

            return order

        else:
            return None

    def cancel_order_function(self, order):
        transact = self.zrx_exchange.cancel_order(order.zrx_order).transact(gas_price=self.gas_price)
        return transact is not None and transact.successful


if __name__ == '__main__':
    ZrxMarketMakerKeeper(sys.argv[1:]).main()
