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

from market_maker_keeper.band import Bands
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.limit import History
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.util import setup_logging
from pymaker import Address, synchronize
from pymaker.approval import directly
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

        parser.add_argument("--exchange-address", type=str, required=True,
                            help="Ethereum address of the 0x Exchange contract")

        parser.add_argument("--relayer-api-server", type=str, required=True,
                            help="Address of the 0x Relayer API")

        parser.add_argument("--relayer-per-page", type=int, default=100,
                            help="Number of orders to fetch per one page from the 0x Relayer API (default: 100)")

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

        parser.add_argument("--order-expiry", type=int, required=True,
                            help="Expiration time of created orders (in seconds)")

        parser.add_argument("--order-expiry-threshold", type=int, default=0,
                            help="How long before order expiration it is considered already expired (in seconds)")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper will cease operation")

        parser.add_argument('--cancel-on-shutdown', dest='cancel_on_shutdown', action='store_true',
                            help="Whether should cancel all open orders on keeper shutdown")

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

        self.token_buy = ERC20Token(web3=self.web3, address=Address(self.arguments.buy_token_address))
        self.token_sell = ERC20Token(web3=self.web3, address=Address(self.arguments.sell_token_address))
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)

        self.history = History()
        self.zrx_exchange = ZrxExchange(web3=self.web3, address=Address(self.arguments.exchange_address))
        self.zrx_relayer_api = ZrxRelayerApi(exchange=self.zrx_exchange, api_server=self.arguments.relayer_api_server)
        self.placed_orders = []

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.every(15, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

    @retry(delay=5, logger=logger)
    def shutdown(self):
        if self.arguments.cancel_on_shutdown:
            self.cancel_orders(self.our_orders())

    def approve(self):
        self.zrx_exchange.approve([self.token_sell, self.token_buy], directly(gas_price=self.gas_price))

    def our_total_balance(self, token: ERC20Token) -> Wad:
        return token.balance_of(self.our_address)

    def our_orders(self) -> list:
        api_orders = self.zrx_relayer_api.get_orders_by_maker(self.our_address, self.arguments.relayer_per_page)
        all_orders = list(set(self.placed_orders + api_orders))
        return self.remove_old_orders(all_orders)

    def remove_old_orders(self, orders: list) -> list:
        current_timestamp = int(time.time())
        orders = list(filter(lambda order: order.expiration > current_timestamp - self.arguments.order_expiry_threshold, orders))
        orders = list(filter(lambda order: self.zrx_exchange.get_unavailable_buy_amount(order) < order.buy_amount, orders))
        return orders

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.buy_token == self.token_buy.address and
                                         order.pay_token == self.token_sell.address, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.buy_token == self.token_sell.address and
                                         order.pay_token == self.token_buy.address, our_orders))

    def synchronize_orders(self):
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.logger.warning("Keeper ETH balance below minimum. Cancelling all orders.")
            self.cancel_orders(self.our_orders())
            return

        bands = Bands(self.bands_config, self.history)
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

        # Balances returned by `our_total_balance` still contain amounts "locked"
        # by currently open orders, so we need to explicitly subtract these amounts.
        our_buy_balance = self.our_total_balance(self.token_buy) - Bands.total_amount(self.our_buy_orders(our_orders))
        our_sell_balance = self.our_total_balance(self.token_sell) - Bands.total_amount(self.our_sell_orders(our_orders))

        # Place new orders
        self.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(our_orders),
                                           our_sell_orders=self.our_sell_orders(our_orders),
                                           our_buy_balance=our_buy_balance,
                                           our_sell_balance=our_sell_balance,
                                           target_price=target_price)[0])

    def cancel_orders(self, orders):
        synchronize([self.zrx_exchange.cancel_order(order).transact_async(gas_price=self.gas_price) for order in orders])

    def place_orders(self, new_orders):
        for new_order in new_orders:
            pay_token = self.token_sell if new_order.is_sell else self.token_buy
            buy_token = self.token_buy if new_order.is_sell else self.token_sell

            zrx_order = self.zrx_exchange.create_order(pay_token=pay_token.address, pay_amount=new_order.pay_amount,
                                                       buy_token=buy_token.address, buy_amount=new_order.buy_amount,
                                                       expiration=int(time.time()) + self.arguments.order_expiry)

            zrx_order = self.zrx_relayer_api.calculate_fees(zrx_order)
            zrx_order = self.zrx_exchange.sign_order(zrx_order)

            if self.zrx_relayer_api.submit_order(zrx_order):
                self.placed_orders = self.remove_old_orders(self.placed_orders)
                self.placed_orders.append(zrx_order)


if __name__ == '__main__':
    ZrxMarketMakerKeeper(sys.argv[1:]).main()
