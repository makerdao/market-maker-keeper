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

from retry import retry
from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands, NewOrder
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pymaker import Address
from pymaker.approval import directly
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.oasis import Order, MatchingMarket
from pymaker.sai import Tub
from pymaker.token import ERC20Token
from pymaker.util import synchronize, eth_balance


class OasisMarketMakerKeeper:
    """Keeper acting as a market maker on OasisDEX."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='oasis-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--tub-address", type=str, required=False,
                            help="Ethereum address of the Tub contract")

        parser.add_argument("--oasis-address", type=str, required=True,
                            help="Ethereum address of the OasisDEX contract")

        parser.add_argument("--buy-token-address", type=str, required=True,
                            help="Ethereum address of the buy token")

        parser.add_argument("--sell-token-address", type=str, required=True,
                            help="Ethereum address of the sell token")

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

        parser.add_argument("--round-places", type=int, default=2,
                            help="Number of decimal places to round order prices to (default=2)")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper will cease operation")

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        self.otc = MatchingMarket(web3=self.web3, address=Address(self.arguments.oasis_address))

        tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address)) \
            if self.arguments.tub_address is not None else None

        self.token_buy = ERC20Token(web3=self.web3, address=Address(self.arguments.buy_token_address))
        self.token_sell = ERC20Token(web3=self.web3, address=Address(self.arguments.sell_token_address))
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments, tub)
        self.spread_feed = create_spread_feed(self.arguments)

        self.history = History()
        self.order_book_manager = OrderBookManager(refresh_frequency=3)
        self.order_book_manager.get_orders_with(lambda: self.our_orders())
        self.order_book_manager.start()

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.on_block(self.on_block)
            lifecycle.every(3, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

    @retry(delay=5, logger=logger)
    def shutdown(self):
        self.cancel_all_orders()

    def on_block(self):
        # This method is present only so the lifecycle binds the new block listener, which makes
        # it then terminate the keeper if no new blocks have been arriving for 300 seconds.
        pass

    def approve(self):
        """Approve OasisDEX to access our balances, so we can place orders."""
        self.otc.approve([self.token_sell, self.token_buy], directly(gas_price=self.gas_price))

    def our_available_balance(self, token: ERC20Token) -> Wad:
        return token.balance_of(self.our_address)

    def our_orders(self):
        return list(filter(lambda order: order.maker == self.our_address,
                           self.otc.get_orders(self.token_sell.address, self.token_buy.address) +
                           self.otc.get_orders(self.token_buy.address, self.token_sell.address)))

    def our_sell_orders(self, our_orders: list):
        return list(filter(lambda order: order.buy_token == self.token_buy.address and
                                         order.pay_token == self.token_sell.address, our_orders))

    def our_buy_orders(self, our_orders: list):
        return list(filter(lambda order: order.buy_token == self.token_sell.address and
                                         order.pay_token == self.token_buy.address, our_orders))

    def synchronize_orders(self):
        # If market is closed, cancel all orders but do not terminate the keeper.
        if self.otc.is_closed():
            self.logger.warning("Market is closed. Cancelling all orders.")
            self.cancel_all_orders()
            return

        # If keeper balance is below `--min-eth-balance`, cancel all orders but do not terminate
        # the keeper, keep processing blocks as the moment the keeper gets a top-up it should
        # resume activity straight away, without the need to restart it.
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.logger.warning("Keeper ETH balance below minimum. Cancelling all orders.")
            self.cancel_all_orders()
            return

        bands = Bands(self.bands_config, self.history)
        order_book = self.order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()

        # If the is no target price feed, cancel all orders but do not terminate the keeper.
        # The moment the price feed comes back, the keeper will resume placing orders.
        if target_price is None:
            self.logger.warning("No price feed available. Cancelling all orders.")
            self.cancel_all_orders()
            return

        # If there are any orders to be cancelled, cancel them. It is deliberate that we wait with topping-up
        # bands until the next block. This way we would create new orders based on the most recent price and
        # order book state. We could theoretically retrieve both (`target_price` and `our_orders`) again here,
        # but it just seems cleaner to do it in one place instead of in two.
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
                                           our_buy_balance=self.our_available_balance(self.token_buy),
                                           our_sell_balance=self.our_available_balance(self.token_sell),
                                           target_price=target_price)[0])

    def cancel_all_orders(self):
        # Wait for the order book to stabilize
        while True:
            order_book = self.order_book_manager.get_order_book()
            if not order_book.orders_being_cancelled and not order_book.orders_being_placed:
                break

        # Cancel all open orders
        self.cancel_orders(self.order_book_manager.get_order_book().orders)
        self.order_book_manager.wait_for_order_cancellation()

    def cancel_orders(self, orders):
        for order in orders:
            self.order_book_manager.cancel_order(order.order_id, lambda order=order: self.otc.kill(order.order_id).transact(gas_price=self.gas_price).successful)

    def place_orders(self, new_orders):
        def place_order_function(new_order_to_be_placed: NewOrder):
            assert(isinstance(new_order_to_be_placed, NewOrder))

            if new_order_to_be_placed.is_sell:
                pay_token = self.token_sell.address
                buy_token = self.token_buy.address
            else:
                pay_token = self.token_buy.address
                buy_token = self.token_sell.address

            transact = self.otc.make(pay_token=pay_token, pay_amount=new_order_to_be_placed.pay_amount,
                                     buy_token=buy_token, buy_amount=new_order_to_be_placed.buy_amount).transact(gas_price=self.gas_price)

            if transact is not None and transact.successful and transact.result is not None:
                return Order(market=self.otc,
                             order_id=transact.result,
                             maker=self.our_address,
                             pay_token=pay_token,
                             pay_amount=new_order_to_be_placed.pay_amount,
                             buy_token=buy_token,
                             buy_amount=new_order_to_be_placed.buy_amount,
                             timestamp=0)
            else:
                return None

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))


if __name__ == '__main__':
    OasisMarketMakerKeeper(sys.argv[1:]).main()
