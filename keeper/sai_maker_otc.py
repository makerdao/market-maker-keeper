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
import random
from enum import Enum
from functools import reduce
from itertools import chain
from typing import List

import math

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
    def __init__(self, type: BandType, min_margin, avg_margin, max_margin, min_amount: Wad, max_amount: Wad, dust_cutoff: Wad):
        assert(isinstance(type, BandType))
        assert(isinstance(min_amount, Wad))
        assert(isinstance(max_amount, Wad))
        assert(isinstance(dust_cutoff, Wad))
        self.type = type
        self.min_margin = min_margin
        self.avg_margin = avg_margin
        self.max_margin = max_margin
        self.min_amount = min_amount
        self.max_amount = max_amount
        self.dust_cutoff = dust_cutoff

    def does_include_offer(self, offer: OfferInfo, target_price: Wad) -> bool:
        #TODO probably to be replaced with two separate band classes for buy and sell
        if self.type == BandType.BUY:
            rate = rate_buy(offer)
            rate_min = apply_buy_margin(target_price, self.min_margin)
            rate_max = apply_buy_margin(target_price, self.max_margin)
            return (rate > rate_max) and (rate <= rate_min)
        else:
            rate = rate_sell(offer)
            rate_min = apply_sell_margin(target_price, self.min_margin)
            rate_max = apply_sell_margin(target_price, self.max_margin)
            return (rate > rate_min) and (rate <= rate_max)


def apply_buy_margin(rate: Wad, margin: float) -> Wad:
    return rate * Wad.from_number(1 - margin)

def apply_sell_margin(rate: Wad, margin: float) -> Wad:
    return rate * Wad.from_number(1 + margin)

def rate_buy(offer: OfferInfo) -> Wad:
    return offer.sell_how_much / offer.buy_how_much

def rate_sell(offer: OfferInfo) -> Wad:
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
        self.buy_band = Band(type=BandType.BUY,
                             min_margin=self.arguments.min_margin_buy,
                             avg_margin=self.arguments.avg_margin_buy,
                             max_margin=self.arguments.max_margin_buy,
                             min_amount=Wad.from_number(self.arguments.min_sai_amount),
                             max_amount=Wad.from_number(self.arguments.max_sai_amount),
                             dust_cutoff=Wad.from_number(self.arguments.sai_dust_cutoff))
        self.buy_bands = [self.buy_band]
        self.sell_band = Band(type=BandType.SELL,
                              min_margin=self.arguments.min_margin_sell,
                              avg_margin=self.arguments.avg_margin_sell,
                              max_margin=self.arguments.max_margin_sell,
                              min_amount=Wad.from_number(self.arguments.min_weth_amount),
                              max_amount=Wad.from_number(self.arguments.max_weth_amount),
                              dust_cutoff=Wad.from_number(self.arguments.weth_dust_cutoff))
        self.sell_bands = [self.sell_band]
        self.round_places = self.arguments.round_places

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--min-margin-buy", help="Minimum margin allowed (buy)", type=float, required=True)
        parser.add_argument("--avg-margin-buy", help="Target margin, used on new order creation (buy)", type=float, required=True)
        parser.add_argument("--max-margin-buy", help="Maximum margin allowed (buy)", type=float, required=True)
        parser.add_argument("--min-margin-sell", help="Minimum margin allowed (sell)", type=float, required=True)
        parser.add_argument("--avg-margin-sell", help="Target margin, used on new order creation (sell)", type=float, required=True)
        parser.add_argument("--max-margin-sell", help="Maximum margin allowed (sell)", type=float, required=True)
        parser.add_argument("--max-weth-amount", help="Maximum value of open WETH sell orders", type=float, required=True)
        parser.add_argument("--min-weth-amount", help="Minimum value of open WETH sell orders", type=float, required=True)
        parser.add_argument("--max-sai-amount", help="Maximum value of open SAI sell orders", type=float, required=True)
        parser.add_argument("--min-sai-amount", help="Minimum value of open SAI sell orders", type=float, required=True)
        parser.add_argument("--sai-dust-cutoff", help="Minimum order value (SAI) for buy orders", type=int, default=0)
        parser.add_argument("--weth-dust-cutoff", help="Minimum order value (WETH) for sell orders", type=int, default=0)
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
        active_offers = self.otc.active_offers()
        target_price = self.tub_target_price()
        self.cancel_offers(chain(self.excessive_buy_offers(active_offers, target_price),
                                 self.excessive_sell_offers(active_offers, target_price)))
        self.create_new_offers(active_offers, target_price)

    def excessive_buy_offers(self, active_offers: list, target_price: Wad):
        """Return buy offers which do not fall into any buy band."""
        return self.excessive_offers(self.our_buy_offers(active_offers), self.buy_bands, target_price)

    def excessive_sell_offers(self, active_offers: list, target_price: Wad):
        """Return sell offers which do not fall into any sell band."""
        return self.excessive_offers(self.our_sell_offers(active_offers), self.sell_bands, target_price)

    @staticmethod
    def excessive_offers(offers: list, bands: List[Band], target_price: Wad):
        for offer in offers:
            if not any(band.does_include_offer(offer, target_price) for band in bands):
                yield offer

    def cancel_offers(self, offers):
        """Cancel offers asynchronously."""
        synchronize([self.otc.kill(offer.offer_id).transact_async(self.default_options()) for offer in offers])

    def create_new_offers(self, active_offers: list, target_price: Wad):
        """Asynchronously create new buy and sell offers if necessary."""
        synchronize([transact.transact_async(self.default_options())
                     for transact in chain(self.new_buy_offer(active_offers, target_price),
                                           self.new_sell_offer(active_offers, target_price))])

    def new_sell_offer(self, active_offers: list, target_price: Wad):
        """If our WETH engagement is below the minimum amount, yield a new offer up to the maximum amount."""
        total_amount = self.total_amount(self.our_sell_offers(active_offers))
        if total_amount < self.sell_band.min_amount:
            our_balance = self.gem.balance_of(self.our_address)
            have_amount = Wad.min(self.sell_band.max_amount - total_amount, our_balance)
            if (have_amount >= self.sell_band.dust_cutoff) and (have_amount > Wad(0)):
                want_amount = have_amount * round(apply_sell_margin(target_price, self.sell_band.avg_margin), self.round_places)
                yield self.otc.make(have_token=self.gem.address, have_amount=have_amount,
                                    want_token=self.sai.address, want_amount=want_amount)

    def new_buy_offer(self, active_offers: list, target_price: Wad):
        """If our SAI engagement is below the minimum amount, yield a new offer up to the maximum amount."""
        total_amount = self.total_amount(self.our_buy_offers(active_offers))
        if total_amount < self.buy_band.min_amount:
            our_balance = self.sai.balance_of(self.our_address)
            have_amount = Wad.min(self.buy_band.max_amount - total_amount, our_balance)
            if (have_amount >= self.buy_band.dust_cutoff) and (have_amount > Wad(0)):
                want_amount = have_amount / round(apply_buy_margin(target_price, self.buy_band.avg_margin), self.round_places)
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
