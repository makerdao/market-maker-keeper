# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2020 mitakash
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
from math import log10
from market_maker_keeper.band import Bands, NewOrder
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pyexchange.leverjfutures import LeverjFuturesAPI, Order
from web3 import Web3, HTTPProvider
from pymaker.keys import register_keys
from decimal import *

_context = Context(prec=1000, rounding=ROUND_DOWN)


class LeverjMarketMakerKeeper:
    """Keeper acting as a market maker on leverj."""

    logger = logging.getLogger()

    def __init__(self, args: list):
        parser = argparse.ArgumentParser(prog='leverj-market-maker-keeper')

        parser.add_argument("--leverj-api-server", type=str, default="https://test.leverj.io",
                            help="Address of the leverj API server (default: 'https://test.leverj.io')")

        parser.add_argument("--account-id", type=str, default="",
                            help="Address of leverj api account id")

        parser.add_argument("--api-key", type=str, default="",
                            help="Address of leverj api key")

        parser.add_argument("--api-secret", type=str, default="",
                            help="Address of leverj api secret")

        parser.add_argument("--leverj-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Leverj API (in seconds, default: 9.5)")

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to watch our trades")

        parser.add_argument("--eth-key", type=str, nargs='*',
                            help="Ethereum private key(s) to use (e.g. 'key_file=aaa.json,pass_file=aaa.pass')")

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

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
    
        self.web3 = Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                      request_kwargs={"timeout": self.arguments.rpc_timeout}))

        self.web3.eth.defaultAccount = self.arguments.eth_from
        register_keys(self.web3, self.arguments.eth_key)

        setup_logging(self.arguments)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.control_feed = create_control_feed(self.arguments)
        self.order_history_reporter = create_order_history_reporter(self.arguments)
        self.target_price_lean = Wad(0)

        self.history = History()

        self.leverj_api = LeverjFuturesAPI(web3=self.web3,
                                    api_server=self.arguments.leverj_api_server,
                                    account_id=self.arguments.account_id,
                                    api_key=self.arguments.api_key,
                                    api_secret=self.arguments.api_secret,
                                    timeout=self.arguments.leverj_timeout)


        self.order_book_manager = OrderBookManager(refresh_frequency=self.arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: self.leverj_api.get_orders(self.pair()))
        self.order_book_manager.get_balances_with(lambda: self.leverj_api.get_balances())
        self.order_book_manager.cancel_orders_with(lambda order: self.leverj_api.cancel_order(order.order_id))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders,
                                                         self.our_sell_orders)
        self.order_book_manager.start()

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.initial_delay(1)
            lifecycle.on_startup(self.startup)
            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        quote_increment = self.leverj_api.get_tickSize(self.pair())
        self.precision = -(int(log10(float(quote_increment)))+1)

    def shutdown(self):
        self.order_book_manager.cancel_all_orders()

    def pair(self):
        name_to_id_map = {'BTCDAI': '1', 'ETHDAI': '2'}
        return name_to_id_map[self.arguments.pair.upper()]

    def token_sell(self) -> str:
        return self.arguments.pair.upper()[:3]

    def token_buy(self) -> str:
        return self.arguments.pair.upper()[3:]

    def allocated_balance(self, token: str) -> Wad:
        quote_asset_address = self.leverj_api.get_product(self.pair())["quote"]["address"]
        # for perpetual contracts, the quote balance is allocated across instruments and sides to enter into trades
        total_available = self.leverj_api.get_quote_balance(quote_asset_address)
        self.logger.debug(f'total_available: {total_available}')
        return self._allocate_to_pair(total_available).get(token)

    def _allocate_to_pair(self, total_available):
        # total number of instruments across which the total_available balance is distributed
        # total_available is denominated in quote units
        total_number_of_instruments = 1
        
        # there are 2 partitions for allocation per instrument
        # the dai amount is divided in 2, one for the buy side and another for the sell side
        number_of_partitions_for_allocation = Wad.from_number(total_number_of_instruments*2)
        
        
        # buffer_adjustment_factor is a small intentional buffer to avoid allocating the maximum possible. 
        # the allocated amount is a little smaller than the maximum possible allocation 
        # and that is determined by the buffer_adjustment_factor
        buffer_adjustment_factor = Wad.from_number(1.05)
        
        base = self.arguments.pair.upper()[:3]
        quote = self.arguments.pair.upper()[3:]
        target_price = self.price_feed.get_price()
        product = self.leverj_api.get_product(self.pair())
        minimum_order_quantity = self.leverj_api.get_minimum_order_quantity(self.pair())
        minimum_quantity_wad = Wad.from_number(minimum_order_quantity)

        if ((base == product['baseSymbol']) and (quote == product['quoteSymbol'])):
            if ((target_price is None) or (target_price.buy_price is None) or (target_price.sell_price is None)):
                base_allocation = Wad(0)
                quote_allocation = Wad(0)
                self.logger.debug(f'target_price not available to calculate allocations')
            else:
                average_price = (target_price.buy_price + target_price.sell_price)/Wad.from_number(2)
                # at 1x average_price * minimum_quantity_wad is the minimum_required_balance
                # multiplying this minimum_required_balance by 2 to avoid sending very small orders to the exchange
                minimum_required_balance = average_price*minimum_quantity_wad*Wad.from_number(2)
                # conversion_divisor is the divisor that determines how many chunks should Dai be distributed into. 
                # It considers the price of the base to convert into base denomination.
                conversion_divisor = average_price*number_of_partitions_for_allocation*buffer_adjustment_factor
                open_position_for_base = self.leverj_api.get_position_in_wad(base)
                total_available_wad = Wad.from_number(Decimal(total_available)/Decimal(Decimal(10)**Decimal(18)))
                base_allocation = total_available_wad/conversion_divisor
                quote_allocation = total_available_wad/number_of_partitions_for_allocation
                self.logger.debug(f'open_position_for_base: {open_position_for_base}')
                # bids are made basis quote_allocation and asks basis base_allocation
                # if open position is net long then quote_allocation is adjusted.
                # if open position is net too long then target_price is adjusted to reduce price of the asks/offers
                if (open_position_for_base.value > 0):
                    open_position_for_base_in_quote = open_position_for_base*average_price
                    net_adjusted_quote_value = quote_allocation.value - abs(open_position_for_base_in_quote.value)
                    self.logger.debug(f'net_adjusted_quote_value: {net_adjusted_quote_value}')
                    quote_allocation = Wad(net_adjusted_quote_value) if net_adjusted_quote_value > minimum_required_balance.value else Wad(0)
                    # if open position is within 1 Wad range or more than quote allocations then target price is leaned down by 0.1 percent
                    if Wad(net_adjusted_quote_value) < Wad(1):
                        self.target_price_lean = Wad.from_number(0.999)
                    else:
                        self.target_price_lean = Wad(0)
                elif (open_position_for_base.value < 0):
                    # if open position is net short then base_allocation is adjusted
                    # if open position is net too short then target_price is adjusted to increase price of the bids
                    net_adjusted_base_value = base_allocation.value - abs(open_position_for_base.value)
                    minimum_required_balance_in_base = minimum_required_balance/average_price
                    self.logger.debug(f'net_adjusted_base_value: {net_adjusted_base_value}')
                    base_allocation = Wad(net_adjusted_base_value) if net_adjusted_base_value > minimum_required_balance_in_base.value else Wad(0)
                    # if open position is within 1 Wad range or more than base allocations then target price is leaned up by 0.1 percent
                    if Wad(net_adjusted_base_value) < Wad(1):
                        self.target_price_lean = Wad.from_number(1.001)
                    else:
                        self.target_price_lean = Wad(0)
        else:
            base_allocation = Wad(0)
            quote_allocation = Wad(0)

        allocation = {base: base_allocation, quote: quote_allocation}
        self.logger.debug(f'allocation: {allocation}')
        return allocation

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def adjust_target_price(self, target_price):
        target_price_lean = self.target_price_lean
        if ((target_price is None) or (target_price.buy_price is None) or (target_price.sell_price is None)):
            return target_price
        if target_price_lean.value == 0:
            return target_price
        else:
            self.logger.debug(f'target_price_lean: {target_price_lean}')
            adjusted_target_price = target_price
            adjusted_target_price.buy_price = (target_price.buy_price)*target_price_lean
            adjusted_target_price.sell_price = (target_price.sell_price)*target_price_lean
            return adjusted_target_price

    def synchronize_orders(self):
        bands = Bands.read(self.bands_config, self.spread_feed, self.control_feed, self.history)

        order_book = self.order_book_manager.get_order_book()
        target_price = self.price_feed.get_price()
        target_price = self.adjust_target_price(target_price)
        self.logger.debug(f'target_price buy_price: {target_price.buy_price}, target_price sell_price: {target_price.sell_price}')
        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                                      our_sell_orders=self.our_sell_orders(order_book.orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.order_book_manager.cancel_orders(cancellable_orders)
            return

        # Do not place new orders if order book state is not confirmed
        if order_book.orders_being_placed or order_book.orders_being_cancelled:
            self.logger.info("Order book is in progress, not placing new orders")
            return

        # Place new orders
        new_orders = bands.new_orders(our_buy_orders=self.our_buy_orders(order_book.orders),
                                      our_sell_orders=self.our_sell_orders(order_book.orders),
                                      our_buy_balance=self.allocated_balance(self.token_buy()),
                                      our_sell_balance=self.allocated_balance(self.token_sell()),
                                      target_price=target_price)[0]
        self.place_orders(new_orders)

    def place_orders(self, new_orders: List[NewOrder]):
        def place_order_function(new_order_to_be_placed):
            price = round(new_order_to_be_placed.price, self.precision + 2)
            amount = new_order_to_be_placed.pay_amount if new_order_to_be_placed.is_sell else new_order_to_be_placed.buy_amount
            self.logger.debug(f'amount: {amount}')
            order_id = str(self.leverj_api.place_order(self.pair(), price, 'LMT', new_order_to_be_placed.is_sell, price, amount))
            return Order(order_id=order_id,
                         pair=self.pair(),
                         is_sell=new_order_to_be_placed.is_sell,
                         price=price,
                         amount=amount)

        for new_order in new_orders:
            self.order_book_manager.place_order(lambda new_order=new_order: place_order_function(new_order))


if __name__ == '__main__':
    LeverjMarketMakerKeeper(sys.argv[1:]).main()
