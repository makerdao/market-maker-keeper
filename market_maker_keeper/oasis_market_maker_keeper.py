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

from market_maker_keeper.band import Bands, NewOrder
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pymaker import Address, web3_via_http
from pymaker.approval import directly
from pymaker.keys import register_keys
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.model import Token
from pymaker.oasis import Order, MatchingMarket
from pymaker.sai import Tub
from pymaker.token import ERC20Token
from pymaker.transactional import TxManager
from pymaker.util import eth_balance


class OasisMarketMakerKeeper:
    """Keeper acting as a market maker on OasisDEX."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='oasis-market-maker-keeper')

        parser.add_argument("--endpoint-uri", type=str,
                            help="JSON-RPC uri (example: `http://localhost:8545`)")

        parser.add_argument("--rpc-host", default="localhost", type=str,
                            help="[DEPRECATED] JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", default=8545, type=int,
                            help="[DEPRECATED] JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--eth-key", type=str, nargs='*',
                            help="Ethereum private key(s) to use (e.g. 'key_file=aaa.json,pass_file=aaa.pass')")

        parser.add_argument("--tub-address", type=str, required=False,
                            help="Ethereum address of the Tub contract")

        parser.add_argument("--oasis-address", type=str, required=True,
                            help="Ethereum address of the OasisDEX contract")

        parser.add_argument("--oasis-support-address", type=str, required=False,
                            help="Ethereum address of the OasisDEX support contract")

        parser.add_argument("--buy-token-address", type=str, required=True,
                            help="Ethereum address of the buy token")

        parser.add_argument("--sell-token-address", type=str, required=True,
                            help="Ethereum address of the sell token")

        parser.add_argument("--buy-token-name", type=str, required=True,
                            help="Ethereum address of the buy token")

        parser.add_argument("--sell-token-name", type=str, required=True,
                            help="Ethereum address of the sell token")
        
        parser.add_argument("--buy-token-decimals", type=int, required=True,
                            help="Ethereum address of the buy token")

        parser.add_argument("--sell-token-decimals", type=int, required=True,
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

        parser.add_argument("--control-feed", type=str,
                            help="Source of control feed")

        parser.add_argument("--control-feed-expiry", type=int, default=86400,
                            help="Maximum age of the control feed (in seconds, default: 86400)")

        parser.add_argument("--order-history", type=str,
                            help="Endpoint to report active orders to")

        parser.add_argument("--order-history-every", type=int, default=30,
                            help="Frequency of reporting active orders (in seconds, default: 30)")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper will cease operation")

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--ethgasstation-api-key", type=str, default=None, help="ethgasstation API key")

        parser.add_argument("--refresh-frequency", type=int, default=10,
                            help="Order book refresh frequency (in seconds, default: 10)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)
    
        if 'web3' in kwargs:
            self.web3 = kwargs['web3']
        elif self.arguments.endpoint_uri:
            self.web3: Web3 = web3_via_http(self.arguments.endpoint_uri, self.arguments.rpc_timeout)
        else:
            self.logger.warning("Configuring node endpoint by host and port is deprecated; please use --endpoint-uri")
            self.web3 = Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                          request_kwargs={"timeout": self.arguments.rpc_timeout}))

        self.web3.eth.defaultAccount = self.arguments.eth_from
        register_keys(self.web3, self.arguments.eth_key)
        self.our_address = Address(self.arguments.eth_from)
        self.otc = MatchingMarket(web3=self.web3,
                                  address=Address(self.arguments.oasis_address),
                                  support_address=Address(self.arguments.oasis_support_address)
                                    if self.arguments.oasis_support_address else None)

        tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address)) \
            if self.arguments.tub_address is not None else None

        self.token_buy = ERC20Token(web3=self.web3, address=Address(self.arguments.buy_token_address))
        self.token_sell = ERC20Token(web3=self.web3, address=Address(self.arguments.sell_token_address))
        self.buy_token = Token(name=self.arguments.buy_token_name, address=Address(self.arguments.buy_token_address), decimals=self.arguments.buy_token_decimals)
        self.sell_token = Token(name=self.arguments.sell_token_name, address=Address(self.arguments.sell_token_address), decimals=self.arguments.sell_token_decimals)
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.gas_price = GasPriceFactory().create_gas_price(self.web3, self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments, tub)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)

        self.history = History()
        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: self.our_orders())
        self.order_book_manager.place_orders_with(self.place_order_function)
        self.order_book_manager.cancel_orders_with(self.cancel_order_function)
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders, self.our_sell_orders)
        self.order_book_manager.start()

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
        """Approve OasisDEX to access our balances, so we can place orders."""
        self.otc.approve([self.token_sell, self.token_buy], directly(gas_price=self.gas_price))

    def our_available_balance(self, token: ERC20Token) -> Wad:
        if token.symbol() == self.buy_token.name:
            return self.buy_token.normalize_amount(token.balance_of(self.our_address))
        else:
            return self.sell_token.normalize_amount(token.balance_of(self.our_address))

    def our_orders(self):
        return list(filter(lambda order: order.maker == self.our_address,
                           self.otc.get_orders(self.sell_token, self.buy_token) +
                           self.otc.get_orders(self.buy_token, self.sell_token)))

    def our_sell_orders(self, our_orders: list):
        return list(filter(lambda order: order.buy_token == self.token_buy.address and
                                         order.pay_token == self.token_sell.address, our_orders))

    def our_buy_orders(self, our_orders: list):
        return list(filter(lambda order: order.buy_token == self.token_sell.address and
                                         order.pay_token == self.token_buy.address, our_orders))

    def synchronize_orders(self):
        # If keeper balance is below `--min-eth-balance`, cancel all orders but do not terminate
        # the keeper, keep processing blocks as the moment the keeper gets a top-up it should
        # resume activity straight away, without the need to restart it.
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.logger.warning("Keeper ETH balance below minimum. Cancelling all orders.")
            self.order_book_manager.cancel_all_orders()
            return

        bands = Bands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)
        order_book = self.order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()
        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                                      our_sell_orders=self.our_sell_orders(order_book.orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.order_book_manager.cancel_orders(cancellable_orders)
            return

        # Do not place new orders if other new orders are being placed. In contrary to other keepers,
        # we allow placing new orders when other orders are being cancelled. This is because Ethereum
        # transactions are ordered so we are sure that the order placement will not 'overtake'
        # order cancellation.
        if order_book.orders_being_placed:
            self.logger.debug("Other orders are being placed, not placing new orders")
            return

        # Place new orders
        self.order_book_manager.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                                              our_sell_orders=self.our_sell_orders(order_book.orders),
                                                              our_buy_balance=self.our_available_balance(self.token_buy),
                                                              our_sell_balance=self.our_available_balance(self.token_sell),
                                                              target_price=target_price)[0])

    def place_order_function(self, new_order: NewOrder):
        assert(isinstance(new_order, NewOrder))

        if new_order.is_sell:
            buy_or_sell = "SELL"
            pay_token = self.token_sell.address
            buy_token = self.token_buy.address
            new_order.buy_amount = self.buy_token.unnormalize_amount(new_order.buy_amount)
            b_token = self.buy_token
            p_token = self.sell_token
            new_order.pay_amount = self.sell_token.unnormalize_amount(new_order.pay_amount)
            token_name = self.sell_token.name
            quote_token = self.buy_token.name

        else:
            buy_or_sell = "BUY"
            pay_token = self.token_buy.address
            buy_token = self.token_sell.address
            new_order.pay_amount = self.buy_token.unnormalize_amount(new_order.pay_amount)
            p_token = self.buy_token
            b_token = self.sell_token
            new_order.buy_amount = self.sell_token.unnormalize_amount(new_order.buy_amount)
            token_name = self.sell_token.name
            quote_token = self.buy_token.name


        transact = self.otc.make(p_token=p_token, pay_amount=new_order.pay_amount,
                                 b_token=b_token, buy_amount=new_order.buy_amount).transact(gas_price=self.gas_price)

        if new_order.is_sell:
            new_order.buy_amount = self.buy_token.normalize_amount(new_order.buy_amount)
            new_order.pay_amount = self.sell_token.normalize_amount(new_order.pay_amount)
            buy_or_sell_price = new_order.buy_amount/new_order.pay_amount
            amount = new_order.pay_amount

        else:
            new_order.pay_amount = self.buy_token.normalize_amount(new_order.pay_amount)
            new_order.buy_amount = self.sell_token.normalize_amount(new_order.buy_amount)
            buy_or_sell_price = new_order.pay_amount/new_order.buy_amount
            amount = new_order.buy_amount

        if transact is not None and transact.successful and transact.result is not None:
            self.logger.info(f'Placing {buy_or_sell} order of amount {amount} {token_name} @ price {buy_or_sell_price} {quote_token}') 
            self.logger.info(f'Placing {buy_or_sell} order pay token: {p_token.name} with amount: {new_order.pay_amount}, buy token: {b_token.name} with amount: {new_order.buy_amount}')
            return Order(market=self.otc,
                         order_id=transact.result,
                         maker=self.our_address,
                         pay_token=pay_token,
                         pay_amount=new_order.pay_amount,
                         buy_token=buy_token,
                         buy_amount=new_order.buy_amount,
                         timestamp=0)
        else:
            return None

    def cancel_order_function(self, order):
        transact = self.otc.kill(order.order_id).transact(gas_price=self.gas_price)
        return transact is not None and transact.successful


if __name__ == '__main__':
    OasisMarketMakerKeeper(sys.argv[1:]).main()
