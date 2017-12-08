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
import operator
import os
import sys
import time
from functools import reduce

import pkg_resources
from web3 import Web3, HTTPProvider

from market_maker_keeper.band import BuyBand, SellBand
from market_maker_keeper.price import SetzerPriceFeed, TubPriceFeed
from pymaker import Address, synchronize, Contract
from pymaker.approval import directly
from pymaker.config import ReloadableConfig
from pymaker.gas import GasPrice, FixedGasPrice, DefaultGasPrice
from pymaker.lifecycle import Web3Lifecycle
from pymaker.logger import Logger
from pymaker.numeric import Wad
from pymaker.sai import Tub
from pymaker.token import ERC20Token
from pymaker.util import eth_balance, chain
from pymaker.zrx import ZrxExchange, ZrxRelayerApi


class RadarRelayMarketMakerKeeper:
    """Keeper to act as a market maker on RadarRelay, on the WETH/SAI pair."""

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='radarrelay-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

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

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        parser.add_argument("--trace", dest='trace', action='store_true',
                            help="Enable trace output")

        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}"))
        self.web3.eth.defaultAccount = self.arguments.eth_from

        self.chain = chain(self.web3)
        self.our_address = Address(self.arguments.eth_from)
        self.tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address))
        self.sai = ERC20Token(web3=self.web3, address=self.tub.sai())
        self.ether_token = ERC20Token(web3=self.web3, address=Address(self.arguments.weth_address))

        _json_log = os.path.abspath(pkg_resources.resource_filename(__name__, f"../logs/radarrelay-market-maker-keeper_{self.chain}_{self.our_address}.json.log".lower()))
        self.logger = Logger('radarrelay-market-maker-keeper', self.chain, _json_log, self.arguments.debug, self.arguments.trace)
        Contract.logger = self.logger

        self.bands_config = ReloadableConfig(self.arguments.config, self.logger)
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)

        # Choose the price feed
        if self.arguments.price_feed is not None:
            self.price_feed = SetzerPriceFeed(self.tub, self.arguments.price_feed, self.logger)
        else:
            self.price_feed = TubPriceFeed(self.tub)

        self.radar_relay = ZrxExchange(web3=self.web3, address=Address(self.arguments.exchange_address))
        self.radar_relay_api = ZrxRelayerApi(exchange=self.radar_relay,
                                             api_server=self.arguments.relayer_api_server,
                                             logger=self.logger)

        # so the token names are printed nicer
        ERC20Token.register_token(self.radar_relay.zrx_token(), 'ZRX')
        ERC20Token.register_token(self.ether_token.address, '0x-WETH')

    def main(self):
        with Web3Lifecycle(self.web3, self.logger) as lifecycle:
            self.lifecycle = lifecycle
            lifecycle.on_startup(self.startup)
            lifecycle.every(15, self.synchronize_orders)
            lifecycle.every(60*60, self.print_balances)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

    def shutdown(self):
        if self.arguments.cancel_on_shutdown:
            self.cancel_orders(self.our_orders())

    def print_balances(self):
        sai_owned = self.sai.balance_of(self.our_address)
        weth_owned = self.ether_token.balance_of(self.our_address)

        self.logger.info(f"Keeper balances are {sai_owned} SAI, {weth_owned} + 0x-WETH")

    def approve(self):
        """Approve 0x to access our 0x-WETH and SAI, so we can sell it on the exchange."""
        self.radar_relay.approve([self.ether_token, self.sai], directly())

    def band_configuration(self):
        config = self.bands_config.get_config()
        buy_bands = list(map(BuyBand, config['buyBands']))
        sell_bands = list(map(SellBand, config['sellBands']))

        if self.bands_overlap(buy_bands) or self.bands_overlap(sell_bands):
            self.lifecycle.terminate(f"Bands in the config file overlap. Terminating the keeper.")
            return [], []
        else:
            return buy_bands, sell_bands

    def bands_overlap(self, bands: list):
        def two_bands_overlap(band1, band2):
            return band1.min_margin < band2.max_margin and band2.min_margin < band1.max_margin

        for band1 in bands:
            if len(list(filter(lambda band2: two_bands_overlap(band1, band2), bands))) > 1:
                return True

        return False

    def our_orders(self) -> list:
        our_orders = self.radar_relay_api.get_orders_by_maker(self.our_address)
        current_timestamp = int(time.time())

        our_orders = list(filter(lambda order: order.expiration > current_timestamp - self.arguments.order_expiry_threshold, our_orders))
        our_orders = list(filter(lambda order: self.radar_relay.get_unavailable_taker_token_amount(order) < order.taker_token_amount, our_orders))
        return our_orders

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.taker_token_address == self.sai.address and
                                         order.maker_token_address == self.ether_token.address, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.taker_token_address == self.ether_token.address and
                                         order.maker_token_address == self.sai.address, our_orders))

    def synchronize_orders(self):
        """Update our positions in the order book to reflect keeper parameters."""
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.lifecycle.terminate("Keeper balance is below the minimum, terminating.")
            self.cancel_orders(self.our_orders())
            return

        buy_bands, sell_bands = self.band_configuration()
        our_orders = self.our_orders()
        target_price = self.price_feed.get_price()

        if target_price is not None:
            self.cancel_orders(itertools.chain(self.excessive_buy_orders(our_orders, buy_bands, target_price),
                                               self.excessive_sell_orders(our_orders, sell_bands, target_price),
                                               self.outside_orders(our_orders, buy_bands, sell_bands, target_price)))
            self.top_up_bands(our_orders, buy_bands, sell_bands, target_price)
        else:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_orders(our_orders)

    def outside_orders(self, our_orders: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Return orders which do not fall into any buy or sell band."""
        def outside_any_band_orders(orders: list, bands: list, target_price: Wad):
            for order in orders:
                if not any(band.includes(order, target_price) for band in bands):
                    yield order

        return itertools.chain(outside_any_band_orders(self.our_buy_orders(our_orders), buy_bands, target_price),
                               outside_any_band_orders(self.our_sell_orders(our_orders), sell_bands, target_price))

    def cancel_orders(self, orders):
        """Cancel orders asynchronously."""
        synchronize([self.radar_relay.cancel_order(order).transact_async(gas_price=self.gas_price()) for order in orders])

    def excessive_sell_orders(self, our_orders: list, sell_bands: list, target_price: Wad):
        """Return sell orders which need to be cancelled to bring total amounts within all sell bands below maximums."""
        for band in sell_bands:
            for order in self.excessive_orders_in_band(band, self.our_sell_orders(our_orders), target_price):
                yield order

    def excessive_buy_orders(self, our_orders: list, buy_bands: list, target_price: Wad):
        """Return buy orders which need to be cancelled to bring total amounts within all buy bands below maximums."""
        for band in buy_bands:
            for order in self.excessive_orders_in_band(band, self.our_buy_orders(our_orders), target_price):
                yield order

    def excessive_orders_in_band(self, band, orders: list, target_price: Wad):
        """Return orders which need to be cancelled to bring the total order amount in the band below maximum."""
        # if total amount of orders in this band is greater than the maximum, we cancel them all
        #
        # if may not be the best solution as cancelling only some of them could bring us below
        # the maximum, but let's stick to it for now
        orders_in_band = [order for order in orders if band.includes(order, target_price)]
        return orders_in_band if self.total_amount(orders_in_band) > band.max_amount else []

    def top_up_bands(self, our_orders: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Create new buy and sell orders in all send and buy bands if necessary."""
        self.top_up_buy_bands(our_orders, buy_bands, target_price)
        self.top_up_sell_bands(our_orders, sell_bands, target_price)

    def top_up_sell_bands(self, our_orders: list, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement is not below minimum in all sell bands. Place new orders if necessary."""
        our_balance = self.ether_token.balance_of(self.our_address)  #TODO deduct orders / or maybe not...?
        for band in sell_bands:
            orders = [order for order in self.our_sell_orders(our_orders) if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                have_amount = Wad.min(band.avg_amount - total_amount, our_balance)
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    our_balance = our_balance - have_amount  #TODO I think this line is unnecessary here
                    want_amount = have_amount * round(band.avg_price(target_price))
                    if want_amount > Wad(0):
                        order = self.radar_relay.create_order(maker_token_amount=have_amount,
                                                              taker_token_amount=want_amount,
                                                              maker_token_address=self.ether_token.address,
                                                              taker_token_address=self.sai.address,
                                                              expiration=int(time.time()) + self.arguments.order_expiry)

                        order = self.radar_relay_api.calculate_fees(order)
                        order = self.radar_relay.sign_order(order)
                        self.radar_relay_api.submit_order(order)

    def top_up_buy_bands(self, our_orders: list, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement is not below minimum in all buy bands. Place new orders if necessary."""
        our_balance = self.sai.balance_of(self.our_address)  #TODO deduct orders / or maybe not...?
        for band in buy_bands:
            orders = [order for order in self.our_buy_orders(our_orders) if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                have_amount = Wad.min(band.avg_amount - total_amount, our_balance)
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    our_balance = our_balance - have_amount  #TODO I think this line is unnecessary here
                    want_amount = have_amount / round(band.avg_price(target_price))
                    if want_amount > Wad(0):
                        order = self.radar_relay.create_order(maker_token_amount=have_amount,
                                                              taker_token_amount=want_amount,
                                                              maker_token_address=self.sai.address,
                                                              taker_token_address=self.ether_token.address,
                                                              expiration=int(time.time()) + self.arguments.order_expiry)

                        order = self.radar_relay_api.calculate_fees(order)
                        order = self.radar_relay.sign_order(order)
                        self.radar_relay_api.submit_order(order)

    def total_amount(self, orders):
        maker_token_amount_available = lambda order: order.maker_token_amount - (self.radar_relay.get_unavailable_taker_token_amount(order) * order.maker_token_amount / order.taker_token_amount)
        return reduce(operator.add, map(maker_token_amount_available, orders), Wad(0))

    def gas_price(self) -> GasPrice:
        if self.arguments.gas_price > 0:
            return FixedGasPrice(self.arguments.gas_price)
        else:
            return DefaultGasPrice()


if __name__ == '__main__':
    RadarRelayMarketMakerKeeper(sys.argv[1:]).main()
