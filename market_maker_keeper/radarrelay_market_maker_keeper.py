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
import itertools
import logging
import operator
import sys
import time
from functools import reduce

from retry import retry
from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.price import PriceFeedFactory
from pymaker import Address, synchronize
from pymaker.approval import directly
from pymaker.lifecycle import Web3Lifecycle
from pymaker.numeric import Wad
from pymaker.sai import Tub, Vox
from pymaker.token import ERC20Token
from pymaker.util import eth_balance
from pymaker.zrx import ZrxExchange, ZrxRelayerApi, Order


class RadarRelayMarketMakerKeeper:
    """Keeper acting as a market maker on RadarRelay, on the WETH/SAI pair."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='radarrelay-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--tub-address", type=str, required=True,
                            help="Ethereum address of the Tub contract")

        parser.add_argument("--exchange-address", type=str, required=True,
                            help="Ethereum address of the 0x Exchange contract")

        parser.add_argument("--weth-address", type=str, required=True,
                            help="Ethereum address of the WETH token")

        parser.add_argument("--relayer-api-server", type=str, required=True,
                            help="Address of the 0x Relayer API")

        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str,
                            help="Source of price feed. Tub price feed will be used if not specified")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of non-Tub price feed (in seconds, default: 120)")

        parser.add_argument("--order-expiry", type=int, required=True,
                            help="Expiration time of created orders (in seconds)")

        parser.add_argument("--order-expiry-threshold", type=int, default=0,
                            help="Order expiration time at which order is considered already expired (in seconds)")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper with either terminate or not start at all")

        parser.add_argument('--cancel-on-shutdown', dest='cancel_on_shutdown', action='store_true',
                            help="Whether should cancel all open orders on RadarRelay on keeper shutdown")

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--gas-price-increase", type=int,
                            help="Gas price increase (in Wei) if no confirmation within"
                                 " `--gas-price-increase-every` seconds")

        parser.add_argument("--gas-price-increase-every", type=int, default=120,
                            help="Gas price increase frequency (in seconds, default: 120)")

        parser.add_argument("--gas-price-max", type=int,
                            help="Maximum gas price (in Wei)")

        parser.add_argument("--gas-price-file", type=str,
                            help="Gas price configuration file")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        self.tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address))
        self.vox = Vox(web3=self.web3, address=self.tub.vox())
        self.sai = ERC20Token(web3=self.web3, address=self.tub.sai())
        self.ether_token = ERC20Token(web3=self.web3, address=Address(self.arguments.weth_address))

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))
        logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
        logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.INFO)

        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed,
                                                               self.arguments.price_feed_expiry, self.tub, self.vox)

        self.radar_relay = ZrxExchange(web3=self.web3, address=Address(self.arguments.exchange_address))
        self.radar_relay_api = ZrxRelayerApi(exchange=self.radar_relay, api_server=self.arguments.relayer_api_server)

    def main(self):
        with Web3Lifecycle(self.web3) as lifecycle:
            self.lifecycle = lifecycle
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
        """Approve 0x to access our 0x-WETH and SAI, so we can sell it on the exchange."""
        self.radar_relay.approve([self.ether_token, self.sai], directly(gas_price=self.gas_price))

    def place_order(self, order: Order, our_orders: list):
        order = self.radar_relay_api.calculate_fees(order)
        order = self.radar_relay.sign_order(order)
        self.radar_relay_api.submit_order(order)
        our_orders.append(order)

    def our_orders(self) -> list:
        our_orders = self.radar_relay_api.get_orders_by_maker(self.our_address)
        current_timestamp = int(time.time())

        our_orders = list(filter(lambda order: order.expiration > current_timestamp - self.arguments.order_expiry_threshold, our_orders))
        our_orders = list(filter(lambda order: self.radar_relay.get_unavailable_buy_amount(order) < order.buy_amount, our_orders))
        return our_orders

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.buy_token == self.sai.address and
                                         order.pay_token == self.ether_token.address, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.buy_token == self.ether_token.address and
                                         order.pay_token == self.sai.address, our_orders))

    def synchronize_orders(self):
        """Update our positions in the order book to reflect keeper parameters."""
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.lifecycle.terminate("Keeper balance is below the minimum, terminating.")
            self.cancel_orders(self.our_orders())
            return

        bands = Bands(self.bands_config)
        our_orders = self.our_orders()
        target_price = self.price_feed.get_price()

        if target_price is None:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_orders(our_orders)
            return

        self.cancel_orders(itertools.chain(bands.excessive_buy_orders(self.our_buy_orders(our_orders), target_price),
                                           bands.excessive_sell_orders(self.our_sell_orders(our_orders), target_price),
                                           bands.outside_orders(self.our_buy_orders(our_orders), self.our_sell_orders(our_orders), target_price)))
        self.top_up_bands(our_orders, bands.buy_bands, bands.sell_bands, target_price)

    def cancel_orders(self, orders):
        """Cancel orders asynchronously."""
        synchronize([self.radar_relay.cancel_order(order).transact_async(gas_price=self.gas_price) for order in orders])

    def top_up_bands(self, our_orders: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Create new buy and sell orders in all send and buy bands if necessary."""
        self.top_up_buy_bands(our_orders, buy_bands, target_price)
        self.top_up_sell_bands(our_orders, sell_bands, target_price)

    def top_up_sell_bands(self, our_orders: list, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement is not below minimum in all sell bands. Place new orders if necessary."""
        our_balance = self.ether_token.balance_of(self.our_address)
        for band in sell_bands:
            orders = [order for order in self.our_sell_orders(our_orders) if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = band.avg_price(target_price)
                pay_amount = Wad.min(band.avg_amount - total_amount, our_balance - self.total_amount(self.our_sell_orders(our_orders)))
                buy_amount = pay_amount * price
                if (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new sell order")

                    order = self.radar_relay.create_order(pay_token=self.ether_token.address, pay_amount=pay_amount,
                                                          buy_token=self.sai.address, buy_amount=buy_amount,
                                                          expiration=int(time.time()) + self.arguments.order_expiry)
                    self.place_order(order, our_orders)

    def top_up_buy_bands(self, our_orders: list, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement is not below minimum in all buy bands. Place new orders if necessary."""
        our_balance = self.sai.balance_of(self.our_address)
        for band in buy_bands:
            orders = [order for order in self.our_buy_orders(our_orders) if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = band.avg_price(target_price)
                pay_amount = Wad.min(band.avg_amount - total_amount, our_balance - self.total_amount(self.our_buy_orders(our_orders)))
                buy_amount = pay_amount / price
                if (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new buy order")

                    order = self.radar_relay.create_order(pay_token=self.sai.address, pay_amount=pay_amount,
                                                          buy_token=self.ether_token.address, buy_amount=buy_amount,
                                                          expiration=int(time.time()) + self.arguments.order_expiry)
                    self.place_order(order, our_orders)

    def total_amount(self, orders):
        pay_amount_available = lambda order: order.pay_amount - (self.radar_relay.get_unavailable_buy_amount(order) * order.pay_amount / order.buy_amount)
        return reduce(operator.add, map(pay_amount_available, orders), Wad(0))


if __name__ == '__main__':
    RadarRelayMarketMakerKeeper(sys.argv[1:]).main()
