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

from retry import retry
from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.price import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.util import setup_logging
from pyexchange.paradex import ParadexApi
from pymaker import Address
from pymaker.approval import directly
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pymaker.token import ERC20Token
from pymaker.util import eth_balance
from pymaker.zrx import ZrxExchange


class ParadexMarketMakerKeeper:
    """Keeper acting as a market maker on Paradex."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='paradex-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--eth-key-file", type=str, required=True,
                            help="File with the private key file for the Ethereum account")

        parser.add_argument("--eth-password-file", type=str, required=True,
                            help="File with the private key password for the Ethereum account")

        parser.add_argument("--exchange-address", type=str, required=True,
                            help="Ethereum address of the 0x Exchange contract")

        parser.add_argument("--paradex-api-server", type=str, default='https://api.paradex.io/consumer',
                            help="Address of the Paradex API (default: 'https://api.paradex.io/consumer')")

        parser.add_argument("--paradex-api-key", type=str, required=True,
                            help="API key for the Paradex API")

        parser.add_argument("--paradex-api-timeout", type=float, default=9.5,
                            help="Timeout for accessing the Paradex API (in seconds, default: 9.5)")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

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

        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed,
                                                               self.arguments.price_feed_expiry)

        self.zrx_exchange = ZrxExchange(web3=self.web3, address=Address(self.arguments.exchange_address))
        self.paradex_api = ParadexApi(self.zrx_exchange,
                                      self.arguments.paradex_api_server,
                                      self.arguments.paradex_api_key,
                                      self.arguments.paradex_api_timeout,
                                      self.arguments.eth_key_file,
                                      self.read_password(self.arguments.eth_password_file))

    @staticmethod
    def read_password(filename: str):
        with open(filename) as file:
            return "".join(line.rstrip() for line in file)

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.every(3, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

    @retry(delay=5, logger=logger)
    def shutdown(self):
        self.cancel_orders(self.our_orders())

    def approve(self):
        self.zrx_exchange.approve([self.token_sell(), self.token_buy()], directly(gas_price=self.gas_price))

    def price(self) -> Wad:
        return self.price_feed.get_price()

    def pair(self):
        return self.arguments.pair.upper()

    def token_sell(self) -> ERC20Token:
        return ERC20Token(web3=self.web3, address=Address(self.arguments.sell_token_address))

    def token_buy(self) -> ERC20Token:
        return ERC20Token(web3=self.web3, address=Address(self.arguments.buy_token_address))

    def our_total_balance(self, token: ERC20Token) -> Wad:
        return token.balance_of(self.our_address)

    def our_orders(self) -> list:
        return self.paradex_api.get_orders(self.pair())

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        """Update our positions in the order book to reflect keeper parameters."""
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.logger.warning("Keeper ETH balance below minimum. Cancelling all orders.")
            self.cancel_orders(self.our_orders())
            return

        bands = Bands(self.bands_config)
        our_orders = self.our_orders()
        target_price = self.price()

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

        # In case of Paradex, balances returned by `our_total_balance` still contain amounts "locked"
        # by currently open orders, so we need to explicitly subtract these amounts.
        our_buy_balance = self.our_total_balance(self.token_buy()) - Bands.total_amount(self.our_buy_orders(our_orders))
        our_sell_balance = self.our_total_balance(self.token_sell()) - Bands.total_amount(self.our_sell_orders(our_orders))

        # Place new orders
        self.place_orders(bands.new_orders(our_buy_orders=self.our_buy_orders(our_orders),
                                           our_sell_orders=self.our_sell_orders(our_orders),
                                           our_buy_balance=our_buy_balance,
                                           our_sell_balance=our_sell_balance,
                                           target_price=target_price)[0])

    def cancel_orders(self, orders):
        for order in orders:
            self.paradex_api.cancel_order(order.order_id)

    def place_orders(self, new_orders):
        for new_order in new_orders:
            amount = new_order.pay_amount if new_order.is_sell else new_order.buy_amount
            self.paradex_api.place_order(self.pair(), new_order.is_sell, new_order.price, amount, self.arguments.order_expiry)


if __name__ == '__main__':
    ParadexMarketMakerKeeper(sys.argv[1:]).main()
