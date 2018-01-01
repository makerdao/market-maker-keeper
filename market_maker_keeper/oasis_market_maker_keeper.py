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
from typing import List

from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.price import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from pymaker import Address
from pymaker.approval import directly
from pymaker.lifecycle import Web3Lifecycle
from pymaker.numeric import Wad
from pymaker.oasis import Order, MatchingMarket
from pymaker.sai import Tub, Vox
from pymaker.token import ERC20Token
from pymaker.util import synchronize, eth_balance


class OasisMarketMakerKeeper:
    """Keeper acting as a market maker on OasisDEX, on the W-ETH/SAI pair."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='oasis-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--tub-address", type=str, required=True,
                            help="Ethereum address of the Tub contract")

        parser.add_argument("--oasis-address", type=str, required=True,
                            help="Ethereum address of the OasisDEX contract")

        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str,
                            help="Source of price feed. Tub price feed will be used if not specified")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of non-Tub price feed (in seconds, default: 120)")

        parser.add_argument("--round-places", type=int, default=2,
                            help="Number of decimal places to round order prices to (default=2)")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper with either terminate or not start at all")

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

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}"))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        self.otc = MatchingMarket(web3=self.web3, address=Address(self.arguments.oasis_address))
        self.tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address))
        self.vox = Vox(web3=self.web3, address=self.tub.vox())
        self.sai = ERC20Token(web3=self.web3, address=self.tub.sai())
        self.gem = ERC20Token(web3=self.web3, address=self.tub.gem())

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))
        logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
        logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.INFO)

        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.bands_config = ReloadableConfig(self.arguments.config)
        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed,
                                                               self.arguments.price_feed_expiry, self.tub, self.vox)

    def main(self):
        with Web3Lifecycle(self.web3) as lifecycle:
            self.lifecycle = lifecycle
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.on_block(self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

    def shutdown(self):
        self.cancel_all_orders()

    def approve(self):
        """Approve OasisDEX to access our balances, so we can place orders."""
        self.otc.approve([self.gem, self.sai], directly(gas_price=self.gas_price))

    def our_orders(self):
        return list(filter(lambda order: order.maker == self.our_address, self.otc.get_orders()))

    def our_sell_orders(self, our_orders: list):
        return list(filter(lambda order: order.buy_token == self.sai.address and
                                         order.pay_token == self.gem.address, our_orders))

    def our_buy_orders(self, our_orders: list):
        return list(filter(lambda order: order.buy_token == self.gem.address and
                                         order.pay_token == self.sai.address, our_orders))

    def synchronize_orders(self):
        # If market is closed, cancel all orders but do not terminate the keeper.
        if self.otc.is_closed():
            self.logger.warning("Marked is closed. Cancelling all orders.")
            self.cancel_all_orders()
            return

        # If keeper balance is below `--min-eth-balance`, cancel all orders but do not terminate
        # the keeper, keep processing blocks as the moment the keeper gets a top-up it should
        # resume activity straight away, without the need to restart it.
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.logger.warning("Keeper ETH balance below minimum. Cancelling all orders.")
            self.cancel_all_orders()
            return

        bands = Bands(self.bands_config)
        our_orders = self.our_orders()
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
        orders_to_cancel = list(itertools.chain(bands.excessive_buy_orders(self.our_buy_orders(our_orders), target_price),
                                                bands.excessive_sell_orders(self.our_sell_orders(our_orders), target_price),
                                                bands.outside_orders(self.our_buy_orders(our_orders), self.our_sell_orders(our_orders), target_price)))
        if len(orders_to_cancel) > 0:
            self.cancel_orders(orders_to_cancel)
        else:
            self.top_up_bands(our_orders, bands.buy_bands, bands.sell_bands, target_price)

            # We do wait some time after the orders have been created. The reason for that is sometimes
            # orders that have been just placed were not picked up by the next `our_orders()` call
            # (one can presume the block hasn't been fully imported into the node yet), which made
            # the keeper try to place same order(s) again. Of course the second transaction did fail, but it
            # resulted in wasted gas and significant delay in keeper operation.
            #
            # There is no specific reason behind choosing to wait exactly 7s.
            time.sleep(7)

    def cancel_all_orders(self):
        """Cancel all orders owned by the keeper."""
        self.cancel_orders(self.our_orders())

    def cancel_orders(self, orders):
        """Cancel orders asynchronously."""
        synchronize([self.otc.kill(order.order_id).transact_async(gas_price=self.gas_price)
                     for order in orders])

    def top_up_bands(self, our_orders: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Asynchronously create new buy and sell orders in all send and buy bands if necessary."""
        synchronize([transact.transact_async(gas_price=self.gas_price)
                     for transact in itertools.chain(self.top_up_buy_bands(our_orders, buy_bands, target_price),
                                                     self.top_up_sell_bands(our_orders, sell_bands, target_price))])

    def top_up_sell_bands(self, our_orders: list, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement is not below minimum in all sell bands. Yield new orders if necessary."""
        our_balance = self.gem.balance_of(self.our_address)
        for band in sell_bands:
            orders = [order for order in self.our_sell_orders(our_orders) if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = round(band.avg_price(target_price), self.arguments.round_places)
                have_amount = Wad.min(band.avg_amount - total_amount, our_balance)
                want_amount = have_amount * price
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)) and (want_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new sell order")

                    our_balance = our_balance - have_amount
                    yield self.otc.make(pay_token=self.gem.address, pay_amount=have_amount,
                                        buy_token=self.sai.address, buy_amount=want_amount)

    def top_up_buy_bands(self, our_orders: list, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement is not below minimum in all buy bands. Yield new orders if necessary."""
        our_balance = self.sai.balance_of(self.our_address)
        for band in buy_bands:
            orders = [order for order in self.our_buy_orders(our_orders) if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = round(band.avg_price(target_price), self.arguments.round_places)
                have_amount = Wad.min(band.avg_amount - total_amount, our_balance)
                want_amount = have_amount / price
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)) and (want_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new buy order")

                    our_balance = our_balance - have_amount
                    yield self.otc.make(pay_token=self.sai.address, pay_amount=have_amount,
                                        buy_token=self.gem.address, buy_amount=want_amount)

    @staticmethod
    def total_amount(orders: List[Order]):
        return reduce(operator.add, map(lambda order: order.pay_amount, orders), Wad(0))


if __name__ == '__main__':
    OasisMarketMakerKeeper(sys.argv[1:]).main()
