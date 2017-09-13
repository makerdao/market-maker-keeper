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
import json
import operator
from enum import Enum
from functools import reduce
from itertools import chain
from typing import List

from keeper.api.approval import directly
from keeper.api.numeric import Wad
from keeper.api.oasis import OfferInfo
from keeper.api.util import synchronize

from keeper.api.feed import DSValue
from keeper.sai import SaiKeeper


class BandType(Enum):
    BUY = 1
    SELL = 2


class Band:
    def __init__(self,
                 type: BandType,
                 min_margin: float,
                 avg_margin: float,
                 max_margin: float,
                 min_amount: Wad,
                 avg_amount: Wad,
                 max_amount: Wad,
                 dust_cutoff: Wad):
        assert(isinstance(type, BandType))
        assert(isinstance(min_margin, float))
        assert(isinstance(avg_margin, float))
        assert(isinstance(max_margin, float))
        assert(isinstance(min_amount, Wad))
        assert(isinstance(avg_amount, Wad))
        assert(isinstance(max_amount, Wad))
        assert(isinstance(dust_cutoff, Wad))
        assert(min_amount <= avg_amount)
        assert(avg_amount <= max_amount)
        assert(min_margin <= avg_margin)
        assert(avg_margin <= max_margin)
        assert(min_margin < max_margin)  # if min_margin == max_margin, we wouldn't be able to tell which order

        self.type = type
        self.min_margin = min_margin
        self.avg_margin = avg_margin
        self.max_margin = max_margin
        self.min_amount = min_amount
        self.avg_amount = avg_amount
        self.max_amount = max_amount
        self.dust_cutoff = dust_cutoff

    def includes(self, offer: OfferInfo, target_price: Wad) -> bool:
        #TODO probably to be replaced with two separate band classes for buy and sell
        if self.type == BandType.BUY:
            rate = self._rate_buy(offer)
            rate_min = self._apply_buy_margin(target_price, self.min_margin)
            rate_max = self._apply_buy_margin(target_price, self.max_margin)
            return (rate > rate_max) and (rate <= rate_min)
        else:
            rate = self._rate_sell(offer)
            rate_min = self._apply_sell_margin(target_price, self.min_margin)
            rate_max = self._apply_sell_margin(target_price, self.max_margin)
            return (rate > rate_min) and (rate <= rate_max)

    def avg_price(self, target_price: Wad) -> Wad:
        if self.type == BandType.BUY:
            return self._apply_buy_margin(target_price, self.avg_margin)
        else:
            return self._apply_sell_margin(target_price, self.avg_margin)

    @staticmethod
    def _apply_buy_margin(rate: Wad, margin: float) -> Wad:
        return rate * Wad.from_number(1 - margin)

    @staticmethod
    def _apply_sell_margin(rate: Wad, margin: float) -> Wad:
        return rate * Wad.from_number(1 + margin)

    @staticmethod
    def _rate_buy(offer: OfferInfo) -> Wad:
        return offer.sell_how_much / offer.buy_how_much

    @staticmethod
    def _rate_sell(offer: OfferInfo) -> Wad:
        return offer.buy_how_much / offer.sell_how_much


class SaiMakerOtc(SaiKeeper):
    """SAI keeper to act as a market maker on OasisDEX, on the W-ETH/SAI pair.

    Keeper continuously monitors and adjusts its positions in order to act as a market maker.
    It aims to have open SAI sell orders for at least `--min-sai-amount` and open WETH sell
    orders for at least `--min-weth-amount`, with their price in the <min-margin,max-margin>
    range from the current SAI/W-ETH price.

    When started, the keeper places orders for the maximum allowed amounts (`--max-sai-amount`
    and `--max-weth-amount`) and uses `avg-margin` to calculate the order price.

    As long as the price of existing orders is within the <min-margin,max-margin> range,
    the keeper keeps them open. If they fall outside that range, they get cancelled.
    If the total amount of open orders falls below either `--min-sai-amount` or
    `--min-weth-amount`, a new order gets created for the remaining amount so the total
    amount of orders is equal to `--max-sai-amount` / `--max-weth-amount`.

    This keeper will constantly use gas to move orders as the SAI/GEM price changes,
    but it can be limited by setting the margin and amount ranges wide enough.
    """
    def __init__(self):
        super().__init__()
        self.round_places = self.arguments.round_places

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--config", help="Buy/sell bands configuration file", type=str, required=True)
        parser.add_argument("--round-places", help="Number of decimal places to round order prices to (default=2)", type=int, default=2)

    def startup(self):
        self.approve()
        self.on_block(self.synchronize_offers)
        self.every(60*60, self.print_balances)

    def shutdown(self):
        self.cancel_offers(self.our_offers(self.otc.active_offers()))

    def print_balances(self):
        def balances():
            for token in [self.sai, self.gem]:
                yield f"{token.balance_of(self.our_address)} {token.name()}"
        self.logger.info(f"Keeper balances are {', '.join(balances())}.")

    def approve(self):
        """Approve OasisDEX to access our balances, so we can place orders."""
        self.otc.approve([self.gem, self.sai], directly())

    def band_configuration(self):
        def load_buy_band(dictionary: dict):
            return Band(type=BandType.BUY,
                        min_margin=dictionary['minMargin'],
                        avg_margin=dictionary['avgMargin'],
                        max_margin=dictionary['maxMargin'],
                        min_amount=Wad.from_number(dictionary['minSaiAmount']),
                        avg_amount=Wad.from_number(dictionary['avgSaiAmount']),
                        max_amount=Wad.from_number(dictionary['maxSaiAmount']),
                        dust_cutoff=Wad.from_number(dictionary['dustCutoff']))

        def load_sell_band(dictionary: dict):
            return Band(type=BandType.SELL,
                        min_margin=dictionary['minMargin'],
                        avg_margin=dictionary['avgMargin'],
                        max_margin=dictionary['maxMargin'],
                        min_amount=Wad.from_number(dictionary['minWEthAmount']),
                        avg_amount=Wad.from_number(dictionary['avgWEthAmount']),
                        max_amount=Wad.from_number(dictionary['maxWEthAmount']),
                        dust_cutoff=Wad.from_number(dictionary['dustCutoff']))

        with open(self.arguments.config) as data_file:
            data = json.load(data_file)
            buy_bands = list(map(load_buy_band, data['buyBands']))
            sell_bands = list(map(load_sell_band, data['sellBands']))
            # TODO we should check if bands do not intersect

            # TODO we should sort bands so it we run out of tokens, the bands closest to the
            # TODO target_price will be served first

            return buy_bands, sell_bands

    def our_offers(self, active_offers: list):
        return list(filter(lambda offer: offer.owner == self.our_address, active_offers))

    def our_sell_offers(self, active_offers: list):
        return list(filter(lambda offer: offer.buy_which_token == self.sai.address and
                                         offer.sell_which_token == self.gem.address, self.our_offers(active_offers)))

    def our_buy_offers(self, active_offers: list):
        return list(filter(lambda offer: offer.buy_which_token == self.gem.address and
                                         offer.sell_which_token == self.sai.address, self.our_offers(active_offers)))

    def synchronize_offers(self):
        """Update our positions in the order book to reflect keeper parameters."""
        buy_bands, sell_bands = self.band_configuration()
        active_offers = self.otc.active_offers()
        target_price = self.tub_target_price()
        self.cancel_offers(chain(self.excessive_buy_offers(active_offers, buy_bands, target_price),
                                 self.excessive_sell_offers(active_offers, sell_bands, target_price),
                                 self.outside_offers(active_offers, buy_bands, sell_bands, target_price)))

        active_offers = self.otc.active_offers()
        self.top_up_bands(active_offers, buy_bands, sell_bands, target_price)

    def outside_offers(self, active_offers: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Return offers which do not fall into any buy or sell band."""
        def outside_any_band_offers(offers: list, bands: List[Band], target_price: Wad):
            for offer in offers:
                if not any(band.includes(offer, target_price) for band in bands):
                    yield offer

        return chain(outside_any_band_offers(self.our_buy_offers(active_offers), buy_bands, target_price),
                     outside_any_band_offers(self.our_sell_offers(active_offers), sell_bands, target_price))

    def cancel_offers(self, offers):
        """Cancel offers asynchronously."""
        synchronize([self.otc.kill(offer.offer_id).transact_async(self.default_options()) for offer in offers])

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

    def excessive_offers_in_band(self, band: Band, offers: list, target_price: Wad):
        """Return offers which need to be cancelled to bring the total offer amount in the band below maximum."""
        # if total amount of orders in this band is greater than the maximum, we cancel them all
        #
        # if may not be the best solution as cancelling only some of them could bring us below
        # the maximum, but let's stick to it for now
        offers_in_band = [offer for offer in offers if band.includes(offer, target_price)]
        return offers_in_band if self.total_amount(offers_in_band) > band.max_amount else []

    def top_up_bands(self, active_offers: list, buy_bands: list, sell_bands: list, target_price: Wad):
        """Asynchronously create new buy and sell offers in all send and buy bands if necessary."""
        synchronize([transact.transact_async(self.default_options())
                     for transact in chain(self.top_up_buy_bands(active_offers, buy_bands, target_price),
                                           self.top_up_sell_bands(active_offers, sell_bands, target_price))])

    def top_up_sell_bands(self, active_offers: list, sell_bands: list, target_price: Wad):
        """Ensure our WETH engagement if not below minimum in all sell bands. Yield new offers if necessary."""
        for band in sell_bands:
            offers = [offer for offer in self.our_sell_offers(active_offers) if band.includes(offer, target_price)]
            total_amount = self.total_amount(offers)
            if total_amount < band.min_amount:
                #TODO balance checking does not work correctly as orders are placed in parallel
                #TODO it means that if we do not have enough balance for all bands, some txs will fail
                our_balance = self.gem.balance_of(self.our_address)
                have_amount = Wad.min(band.avg_amount - total_amount, our_balance)
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    want_amount = have_amount * round(band.avg_price(target_price), self.round_places)
                    yield self.otc.make(have_token=self.gem.address, have_amount=have_amount,
                                        want_token=self.sai.address, want_amount=want_amount)

    def top_up_buy_bands(self, active_offers: list, buy_bands: list, target_price: Wad):
        """Ensure our SAI engagement if not below minimum in all buy bands. Yield new offers if necessary."""
        for band in buy_bands:
            offers = [offer for offer in self.our_buy_offers(active_offers) if band.includes(offer, target_price)]
            total_amount = self.total_amount(offers)
            if total_amount < band.min_amount:
                #TODO balance checking does not work correctly as orders are placed in parallel
                #TODO it means that if we do not have enough balance for all bands, some txs will fail
                our_balance = self.sai.balance_of(self.our_address)
                have_amount = Wad.min(band.avg_amount - total_amount, our_balance)
                if (have_amount >= band.dust_cutoff) and (have_amount > Wad(0)):
                    want_amount = have_amount / round(band.avg_price(target_price), self.round_places)
                    yield self.otc.make(have_token=self.sai.address, have_amount=have_amount,
                                        want_token=self.gem.address, want_amount=want_amount)

    def tub_target_price(self) -> Wad:
        ref_per_gem = Wad(DSValue(web3=self.web3, address=self.tub.pip()).read_as_int())
        return ref_per_gem / self.tub.par()

    @staticmethod
    def total_amount(offers: List[OfferInfo]):
        return reduce(operator.add, map(lambda offer: offer.sell_how_much, offers), Wad(0))


if __name__ == '__main__':
    SaiMakerOtc().start()
