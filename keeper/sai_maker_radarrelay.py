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
import operator
import sys
import time
from functools import reduce
from itertools import chain

from keeper import ERC20Token, Wad
from keeper.api import Address, synchronize
from keeper.api.approval import directly
from keeper.api.config import ReloadableConfig
from keeper.api.radarrelay import RadarRelay, RadarRelayApi
from keeper.band import BuyBand, SellBand
from keeper.price import SetzerPriceFeed, TubPriceFeed
from keeper.sai import SaiKeeper


class SaiMakerRadarRelay(SaiKeeper):
    """SAI keeper to act as a market maker on RadarRelay, on the WETH/SAI pair."""
    def __init__(self, args: list, **kwargs):
        super().__init__(args, **kwargs)
        self.bands_config = ReloadableConfig(self.arguments.config, self.logger)
        self.order_expiry = self.arguments.order_expiry
        self.order_expiry_threshold = self.arguments.order_expiry_threshold
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)

        # Choose the price feed
        if self.arguments.price_feed is not None:
            self.price_feed = SetzerPriceFeed(self.tub, self.arguments.price_feed, self.logger)
        else:
            self.price_feed = TubPriceFeed(self.tub)

        self.ether_token = ERC20Token(web3=self.web3, address=Address(self.config.get_config()["0x"]["etherToken"]))
        self.radar_relay = RadarRelay(web3=self.web3, address=Address(self.config.get_config()["0x"]["exchange"]))
        self.radar_relay_api = RadarRelayApi(exchange=self.radar_relay,
                                             api_server=self.config.get_config()["radarRelay"]["apiServer"],
                                             logger=self.logger)

        # so the token names are printed nicer
        ERC20Token.register_token(self.radar_relay.zrx_token(), 'ZRX')
        ERC20Token.register_token(self.ether_token.address, '0x-WETH')

    def args(self, parser: argparse.ArgumentParser):
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

    def startup(self):
        self.approve()
        self.every(15, self.synchronize_orders)
        self.every(60*60, self.print_balances)

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
            self.terminate(f"Bands in the config file overlap. Terminating the keeper.")
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
        if self.eth_balance(self.our_address) < self.min_eth_balance:
            self.terminate("Keeper balance is below the minimum, terminating.")
            self.cancel_orders(self.our_orders())
            return

        buy_bands, sell_bands = self.band_configuration()
        our_orders = self.our_orders()
        target_price = self.price_feed.get_price()

        if target_price is not None:
            self.cancel_orders(chain(self.excessive_buy_orders(our_orders, buy_bands, target_price),
                                     self.excessive_sell_orders(our_orders, sell_bands, target_price),
                                     self.outside_orders(our_orders, buy_bands, sell_bands, target_price)))
            self.top_up_bands(our_orders, buy_bands, sell_bands, target_price)
        else:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_orders(our_orders)

    def outside_orders(self, active_orders: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Return orders which do not fall into any buy or sell band."""
        def outside_any_band_orders(orders: list, bands: list, target_price: Wad):
            for order in orders:
                if not any(band.includes(order, target_price) for band in bands):
                    yield order

        return chain(outside_any_band_orders(self.our_buy_orders(active_orders), buy_bands, target_price),
                     outside_any_band_orders(self.our_sell_orders(active_orders), sell_bands, target_price))

    def cancel_orders(self, orders):
        """Cancel orders asynchronously."""
        synchronize([self.radar_relay.cancel_order(order).transact_async(gas_price=self.gas_price) for order in orders])

    def excessive_sell_orders(self, active_orders: list, sell_bands: list, target_price: Wad):
        """Return sell orders which need to be cancelled to bring total amounts within all sell bands below maximums."""
        for band in sell_bands:
            for order in self.excessive_orders_in_band(band, self.our_sell_orders(active_orders), target_price):
                yield order

    def excessive_buy_orders(self, active_orders: list, buy_bands: list, target_price: Wad):
        """Return buy orders which need to be cancelled to bring total amounts within all buy bands below maximums."""
        for band in buy_bands:
            for order in self.excessive_orders_in_band(band, self.our_buy_orders(active_orders), target_price):
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
                    our_balance = our_balance - have_amount
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
                    our_balance = our_balance - have_amount
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


if __name__ == '__main__':
    SaiMakerRadarRelay(sys.argv[1:]).start()
