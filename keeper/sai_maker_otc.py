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
from functools import reduce
from itertools import chain
from typing import List

import sys

import itertools

from keeper import Event
from keeper.api.approval import directly
from keeper.api.config import ReloadableConfig
from keeper.api.numeric import Wad
from keeper.api.oasis import OfferInfo
from keeper.api.price import TubPriceFeed, SetzerPriceFeed
from keeper.api.util import synchronize

from keeper.band import BuyBand, SellBand
from keeper.sai import SaiKeeper


class SaiMakerOtc(SaiKeeper):
    """SAI keeper to act as a market maker on OasisDEX, on the W-ETH/SAI pair.

    Keeper continuously monitors and adjusts its positions in order to act as a market maker.
    It maintains buy and sell orders in multiple bands at the same time. In each buy band,
    it aims to have open SAI sell orders for at least `minSaiAmount`. In each sell band
    it aims to have open WETH sell orders for at least `minWEthAmount`. In both cases,
    it will ensure the price of open orders stays within the <minMargin,maxMargin> range
    from the current SAI/W-ETH price.

    When started, the keeper places orders for the average amounts (`avgSaiAmount`
    and `avgWEthAmount`) in each band and uses `avgMargin` to calculate the order price.

    As long as the price of orders stays within the band (i.e. is in the <minMargin,maxMargin>
    range from the current SAI/W-ETH price, which is of course constantly moving), the keeper
    keeps them open. If they leave the band, they either enter another adjacent band
    or fall outside all bands. In case of the latter, they get immediately cancelled. In case of
    the former, the keeper can keep these orders open as long as their amount is within the
    <minSaiAmount,maxSaiAmount> (for buy bands) or <minWEthAmount,maxWEthAmount> (for sell bands)
    ranges for the band they just entered. If it is above the maximum, all open orders will get
    cancelled and a new one will be created (for the `avgSaiAmount` / `avgWEthAmount`). If it is below
    the minimum, a new order gets created for the remaining amount so the total amount of orders
    in this band is equal to `avgSaiAmount` or `avgWEthAmount`.

    The same thing will happen if the total amount of open orders in a band falls below either
    `minSaiAmount` or `minWEthAmount` as a result of other market participants taking these orders.
    In this case also a new order gets created for the remaining amount so the total
    amount of orders in this band is equal to `avgSaiAmount` / `avgWEthAmount`.

    This keeper will constantly use gas to move orders as the SAI/GEM price changes. Gas usage
    can be limited by setting the margin and amount ranges wide enough and also by making
    sure that bands are always adjacent to each other and that their <min,max> amount ranges
    overlap.
    """
    def __init__(self, args: list, **kwargs):
        super().__init__(args, **kwargs)
        self.round_places = self.arguments.round_places
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)

        self.bands_config = ReloadableConfig(self.arguments.config, self.logger)

        # Choose the price feed
        if self.arguments.price_feed is not None:
            self.price_feed = SetzerPriceFeed(self.tub, self.arguments.price_feed, self.logger)
        else:
            self.price_feed = TubPriceFeed(self.tub)

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str,
                            help="Source of price feed. Tub price feed will be used if not specified")

        parser.add_argument("--round-places", type=int, default=2,
                            help="Number of decimal places to round order prices to (default=2)")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper with either terminate or not start at all")

    def startup(self):
        self.approve()
        self.on_block(self.synchronize_offers)
        # self.every(20 * 60, self.print_eth_balance)
        # self.every(20 * 60, self.print_token_balances)

    def shutdown(self):
        self.cancel_all_orders()

    def print_token_balances(self):
        for token in [self.sai, self.gem]:
            our_sell_offers = filter(lambda o: o.sell_which_token == token.address, self.our_orders())
            balance_in_our_sell_offers = sum(map(lambda o: o.sell_how_much, our_sell_offers), Wad.from_number(0))
            balance_in_account = token.balance_of(self.our_address)
            total_balance = balance_in_our_sell_offers + balance_in_account
            self.logger.info(f"Keeper {token.name()} balance is {total_balance} {token.name()}"
                             f" ({balance_in_account} {token.name()} in keeper account,"
                             f" {balance_in_our_sell_offers} {token.name()} in open orders)",
                             Event.token_balance(self.our_address, token.address, token.name(), total_balance))

    def approve(self):
        """Approve OasisDEX to access our balances, so we can place orders."""
        self.otc.approve([self.gem, self.sai], directly())

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

    def our_orders(self):
        return list(filter(lambda order: order.owner == self.our_address, self.otc.active_offers()))

    def our_sell_offers(self, our_orders: list):
        return list(filter(lambda order: order.buy_which_token == self.sai.address and
                                         order.sell_which_token == self.gem.address, our_orders))

    def our_buy_offers(self, our_orders: list):
        return list(filter(lambda order: order.buy_which_token == self.gem.address and
                                         order.sell_which_token == self.sai.address, our_orders))

    def synchronize_offers(self):
        """Update our positions in the order book to reflect keeper parameters."""
        if self.eth_balance(self.our_address) < self.min_eth_balance:
            self.terminate("Keeper balance is below the minimum, terminating.")
            self.cancel_all_orders()
            return

        buy_bands, sell_bands = self.band_configuration()
        our_orders = self.our_orders()
        target_price = self.price_feed.get_price()

        if target_price is not None:
            self.cancel_offers(chain(self.excessive_buy_offers(our_orders, buy_bands, target_price),
                                     self.excessive_sell_offers(our_orders, sell_bands, target_price),
                                     self.outside_offers(our_orders, buy_bands, sell_bands, target_price)))

            our_orders = self.our_orders()
            self.top_up_bands(our_orders, buy_bands, sell_bands, target_price)
        else:
            self.logger.warning("Cancelling all offers as no price feed available.")
            self.cancel_all_orders()

    def outside_offers(self, our_orders: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Return offers which do not fall into any buy or sell band."""
        def outside_any_band_offers(offers: list, bands: list, target_price: Wad):
            for offer in offers:
                if not any(band.includes(offer, target_price) for band in bands):
                    yield offer

        return chain(outside_any_band_offers(self.our_buy_offers(our_orders), buy_bands, target_price),
                     outside_any_band_offers(self.our_sell_offers(our_orders), sell_bands, target_price))

    def cancel_all_orders(self):
        """Cancel all orders owned by the keeper."""
        self.cancel_offers(self.our_orders())

    def cancel_offers(self, offers):
        """Cancel offers asynchronously."""
        synchronize([self.otc.kill(offer.offer_id).transact_async(gas_price=self.gas_price) for offer in offers])

    def excessive_sell_offers(self, our_orders: list, sell_bands: list, target_price: Wad):
        """Return sell orders which need to be cancelled to bring total amounts within all sell bands below maximums."""
        for band in sell_bands:
            for offer in band.excessive_orders(self.our_sell_offers(our_orders), target_price):
                yield offer

    def excessive_buy_offers(self, our_orders: list, buy_bands: list, target_price: Wad):
        """Return buy orders which need to be cancelled to bring total amounts within all buy bands below maximums."""
        for band in buy_bands:
            for offer in band.excessive_orders(self.our_buy_offers(our_orders), target_price):
                yield offer

    def top_up_bands(self, our_orders: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Asynchronously create new buy and sell orders in all send and buy bands if necessary."""
        synchronize([transact.transact_async(gas_price=self.gas_price)
                     for transact in chain(self.top_up_buy_bands(our_orders, buy_bands, target_price),
                                           self.top_up_sell_bands(our_orders, sell_bands, target_price))])

    def top_up_sell_bands(self, our_orders: list, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement is not below minimum in all sell bands. Yield new offers if necessary."""
        our_balance = self.gem.balance_of(self.our_address)
        for band in sell_bands:
            offers = [offer for offer in self.our_sell_offers(our_orders) if band.includes(offer, target_price)]
            total_amount = self.total_amount(offers)
            if total_amount < band.min_amount:
                have_amount = Wad.min(band.avg_amount - total_amount, our_balance)
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    our_balance = our_balance - have_amount
                    want_amount = have_amount * round(band.avg_price(target_price), self.round_places)
                    if want_amount > Wad(0):
                        yield self.otc.make(have_token=self.gem.address, have_amount=have_amount,
                                            want_token=self.sai.address, want_amount=want_amount)

    def top_up_buy_bands(self, our_orders: list, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement is not below minimum in all buy bands. Yield new offers if necessary."""
        our_balance = self.sai.balance_of(self.our_address)
        for band in buy_bands:
            offers = [offer for offer in self.our_buy_offers(our_orders) if band.includes(offer, target_price)]
            total_amount = self.total_amount(offers)
            if total_amount < band.min_amount:
                have_amount = Wad.min(band.avg_amount - total_amount, our_balance)
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    our_balance = our_balance - have_amount
                    want_amount = have_amount / round(band.avg_price(target_price), self.round_places)
                    if want_amount > Wad(0):
                        yield self.otc.make(have_token=self.sai.address, have_amount=have_amount,
                                            want_token=self.gem.address, want_amount=want_amount)

    @staticmethod
    def total_amount(offers: List[OfferInfo]):
        return reduce(operator.add, map(lambda offer: offer.sell_how_much, offers), Wad(0))


if __name__ == '__main__':
    SaiMakerOtc(sys.argv[1:]).start()
