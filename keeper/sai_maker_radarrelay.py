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
from functools import reduce
from itertools import chain

import time

from keeper import ERC20Token, Wad
from keeper.api import Address, synchronize
from keeper.api.approval import directly
from keeper.api.price import SetzerPriceFeed, TubPriceFeed
from keeper.api.radarrelay import RadarRelay, RadarRelayApi, Order
from keeper.band import BuyBand, SellBand
from keeper.sai import SaiKeeper


class SaiMakerRadarRelay(SaiKeeper):
    """SAI keeper to act as a market maker on RadarRelay, on the WETH/SAI pair."""
    def __init__(self, args: list, **kwargs):
        super().__init__(args, **kwargs)
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
        self.radar_relay_api = RadarRelayApi(contract_address=self.radar_relay.address,
                                             api_server=self.config.get_config()["radarRelay"]["apiServer"])

        # so the token names are printed nicer
        ERC20Token.register_token(self.radar_relay.zrx_token(), 'ZRX')
        ERC20Token.register_token(self.ether_token.address, '0x-WETH')

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str,
                            help="Source of price feed. Tub price feed will be used if not specified")

        # parser.add_argument("--round-places", type=int, default=2,
        #                     help="Number of decimal places to round order prices to (default=2)")

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
        self.on_block(self.synchronize_orders)
        self.every(60*60, self.print_balances)

    def shutdown(self):
        if self.arguments.cancel_on_shutdown:
            self.cancel_all_offers()

    def print_balances(self):
        sai_owned = self.sai.balance_of(self.our_address)
        weth_owned = self.ether_token.balance_of(self.our_address)

        self.logger.info(f"Keeper balances are {sai_owned} SAI, {weth_owned} + 0x-WETH")

    def approve(self):
        """Approve 0x to access our 0x-WETH and SAI, so we can sell it on the exchange."""
        self.radar_relay.approve([self.ether_token, self.sai], directly())

    def band_configuration(self):
        config = self.get_config(self.arguments.config)
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

    def our_offers(self) -> list:
        our_orders = self.radar_relay_api.get_orders_by_maker(self.our_address)
        current_timestamp = int(time.time())

        our_orders = list(filter(lambda order: order.expiration_unix_timestamp_sec > current_timestamp - self.arguments.order_expiry_threshold, our_orders))
        our_orders = list(filter(lambda order: self.radar_relay.get_unavailable_taker_token_amount(order) < order.taker_token_amount, our_orders))
        return our_orders

    def our_sell_offers(self, our_orders: list) -> list:
        return list(filter(lambda order: order.taker_token_address == self.sai.address and
                                         order.maker_token_address == self.ether_token.address, our_orders))

    def our_buy_offers(self, our_orders: list) -> list:
        return list(filter(lambda order: order.taker_token_address == self.ether_token.address and
                                         order.maker_token_address == self.sai.address, our_orders))

    def synchronize_orders(self):
        """Update our positions in the order book to reflect keeper parameters."""
        if self.eth_balance(self.our_address) < self.min_eth_balance:
            self.terminate("Keeper balance is below the minimum, terminating.")
            self.cancel_all_offers()
            return

        buy_bands, sell_bands = self.band_configuration()
        our_orders = self.our_offers()
        target_price = self.price_feed.get_price()

        if target_price is not None:
            self.cancel_offers(chain(self.excessive_buy_offers(our_orders, buy_bands, target_price),
                                     self.excessive_sell_offers(our_orders, sell_bands, target_price),
                                     self.outside_offers(buy_bands, sell_bands, target_price)))
            self.top_up_bands(buy_bands, sell_bands, target_price)
        else:
            self.logger.warning("Cancelling all offers as no price feed available.")
            self.cancel_all_offers(our_orders)

    def outside_offers(self, active_offers: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Return offers which do not fall into any buy or sell band."""
        def outside_any_band_offers(offers: list, bands: list, target_price: Wad):
            for offer in offers:
                if not any(band.includes(offer, target_price) for band in bands):
                    yield offer

        return chain(outside_any_band_offers(self.our_buy_offers(active_offers), buy_bands, target_price),
                     outside_any_band_offers(self.our_sell_offers(active_offers), sell_bands, target_price))

    def cancel_offers(self, offers):
        """Cancel offers asynchronously."""
        synchronize([self.radar_relay.cancel_order(offer).transact_async(gas_price=self.gas_price) for offer in offers])

    def excessive_sell_offers(self, active_offers: list, sell_bands: list, target_price: Wad):
        """Return sell offers which need to be cancelled to bring total amounts within all sell bands below maximums."""
        for band in sell_bands:
            for offer in self.excessive_offers_in_band(band, self.our_sell_offers(active_offers), target_price):
                yield offer

    def excessive_buy_offers(self, active_offers: list, buy_bands: list, target_price: Wad):
        """Return buy offers which need to be cancelled to bring total amounts within all buy bands below maximums."""
        for band in buy_bands:
            for offer in self.excessive_offers_in_band(band, self.our_buy_offers(active_offers), target_price):
                yield offer

    def excessive_offers_in_band(self, band, offers: list, target_price: Wad):
        """Return offers which need to be cancelled to bring the total offer amount in the band below maximum."""
        # if total amount of orders in this band is greater than the maximum, we cancel them all
        #
        # if may not be the best solution as cancelling only some of them could bring us below
        # the maximum, but let's stick to it for now
        offers_in_band = [offer for offer in offers if band.includes(offer, target_price)]
        return offers_in_band if self.total_amount(offers_in_band) > band.max_amount else []

    def cancel_all_offers(self, active_offers: list):
        """Cancel all our offers."""
        self.cancel_offers(active_offers)

    def top_up_bands(self, buy_bands: list, sell_bands: list, target_price: Wad):
        """Create new buy and sell offers in all send and buy bands if necessary."""
        self.top_up_buy_bands(buy_bands, target_price)
        self.top_up_sell_bands(sell_bands, target_price)

    def top_up_sell_bands(self, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement is not below minimum in all sell bands. Place new offers if necessary."""
        our_balance = self.etherdelta.balance_of(self.our_address)
        for band in sell_bands:
            offers = [offer for offer in self.our_sell_offers() if band.includes(offer, target_price)]
            total_amount = self.total_amount(offers)
            if total_amount < band.min_amount:
                have_amount = self.fix_amount(Wad.min(band.avg_amount - total_amount, our_balance))
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    our_balance = our_balance - have_amount
                    want_amount = self.fix_amount(have_amount * round(band.avg_price(target_price)))
                    if want_amount > Wad(0):
                        order = self.etherdelta.create_offchain_order(token_get=self.sai.address,
                                                                      amount_get=want_amount,
                                                                      token_give=EtherDelta.ETH_TOKEN,
                                                                      amount_give=have_amount,
                                                                      expires=self.web3.eth.blockNumber + self.order_age)
                        if self.deposit_for_sell_order_if_needed(order):
                            return
                        self.place_order(order)

    def top_up_buy_bands(self, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement is not below minimum in all buy bands. Place new offers if necessary."""
        our_balance = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        for band in buy_bands:
            offers = [offer for offer in self.our_buy_offers() if band.includes(offer, target_price)]
            total_amount = self.total_amount(offers)
            if total_amount < band.min_amount:
                have_amount = self.fix_amount(Wad.min(band.avg_amount - total_amount, our_balance))
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    our_balance = our_balance - have_amount
                    want_amount = self.fix_amount(have_amount / round(band.avg_price(target_price)))
                    if want_amount > Wad(0):
                        order = self.etherdelta.create_offchain_order(token_get=EtherDelta.ETH_TOKEN,
                                                                      amount_get=want_amount,
                                                                      token_give=self.sai.address,
                                                                      amount_give=have_amount,
                                                                      expires=self.web3.eth.blockNumber + self.order_age)
                        if self.deposit_for_buy_order_if_needed(order):
                            return
                        self.place_order(order)

    def total_amount(self, orders):
        give_available = lambda order: order.amount_give - (self.etherdelta.amount_filled(order) * order.amount_give / order.amount_get)
        return reduce(operator.add, map(give_available, orders), Wad(0))


if __name__ == '__main__':
    SaiMakerRadarRelay(sys.argv[1:]).start()
