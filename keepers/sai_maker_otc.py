#!/usr/bin/env python3
#
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

from api.approval import directly
from api.feed import DSValue
from api.numeric import Wad
from api.oasis import OfferInfo
from api.util import synchronize
from keepers.sai import SaiKeeper


class SaiMakerOtc(SaiKeeper):
    """SAI keeper to act as a market maker on OasisDEX.

    Keeper continuously monitors and adjusts its positions in order to act as a market maker.
    It aims to have open SAI sell orders for at least `--min-sai-amount` and open WETH sell
    orders for at least `--min-weth-amount`, with their price in the <min-margin,max-margin>
    range from the current SAI/GEM price.

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
        self.max_weth_amount = Wad.from_number(self.arguments.max_weth_amount)
        self.min_weth_amount = Wad.from_number(self.arguments.min_weth_amount)
        self.max_sai_amount = Wad.from_number(self.arguments.max_sai_amount)
        self.min_sai_amount = Wad.from_number(self.arguments.min_sai_amount)
        self.min_margin = self.arguments.min_margin
        self.avg_margin = self.arguments.avg_margin
        self.max_margin = self.arguments.max_margin
        self.round_places = self.arguments.round_places

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--min-margin", help="Minimum margin allowed", type=float, required=True)
        parser.add_argument("--avg-margin", help="Target margin, used on new order creation", type=float, required=True)
        parser.add_argument("--max-margin", help="Maximum margin allowed", type=float, required=True)
        parser.add_argument("--max-weth-amount", help="Maximum value of open WETH sell orders", type=float, required=True)
        parser.add_argument("--min-weth-amount", help="Minimum value of open WETH sell orders", type=float, required=True)
        parser.add_argument("--max-sai-amount", help="Maximum value of open SAI sell orders", type=float, required=True)
        parser.add_argument("--min-sai-amount", help="Minimum value of open SAI sell orders", type=float, required=True)
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
        self.cancel_offers(chain(self.excessive_buy_offers(active_offers),
                                 self.excessive_sell_offers(active_offers)))
        self.create_new_offers(active_offers)

    def excessive_buy_offers(self, active_offers: list):
        """Return buy offers with rates outside allowed margin range."""
        for offer in self.our_buy_offers(active_offers):
            rate = self.rate_buy(offer)
            rate_min = self.apply_buy_margin(self.target_price(), self.min_margin)
            rate_max = self.apply_buy_margin(self.target_price(), self.max_margin)
            if (rate < rate_max) or (rate > rate_min):
                yield offer

    def excessive_sell_offers(self, active_offers: list):
        """Return sell offers with rates outside allowed margin range."""
        for offer in self.our_sell_offers(active_offers):
            rate = self.rate_sell(offer)
            rate_min = self.apply_sell_margin(self.target_price(), self.min_margin)
            rate_max = self.apply_sell_margin(self.target_price(), self.max_margin)
            if (rate < rate_min) or (rate > rate_max):
                yield offer

    def cancel_offers(self, offers):
        """Cancel offers asynchronously."""
        synchronize([self.otc.kill(offer.offer_id).transact_async(self.default_options()) for offer in offers])

    def create_new_offers(self, active_offers: list):
        """Asynchronously create new buy and sell offers if necessary."""
        synchronize([transact.transact_async(self.default_options())
                     for transact in chain(self.new_buy_offer(active_offers), self.new_sell_offer(active_offers))])

    def new_sell_offer(self, active_offers: list):
        """If our WETH engagement is below the minimum amount, yield a new offer up to the maximum amount."""
        total_amount = self.total_amount(self.our_sell_offers(active_offers))
        if total_amount < self.min_weth_amount:
            our_balance = self.gem.balance_of(self.our_address)
            have_amount = Wad.min(self.max_weth_amount - total_amount, our_balance)
            if have_amount > Wad(0):
                want_amount = have_amount * self.apply_sell_margin(self.target_price(), self.avg_margin)
                yield self.otc.make(have_token=self.gem.address, have_amount=have_amount,
                                    want_token=self.sai.address, want_amount=want_amount)

    def new_buy_offer(self, active_offers: list):
        """If our SAI engagement is below the minimum amount, yield a new offer up to the maximum amount."""
        total_amount = self.total_amount(self.our_buy_offers(active_offers))
        if total_amount < self.min_sai_amount:
            our_balance = self.sai.balance_of(self.our_address)
            have_amount = Wad.min(self.max_sai_amount - total_amount, our_balance)
            if have_amount > Wad(0):
                want_amount = have_amount / self.apply_buy_margin(self.target_price(), self.avg_margin)
                yield self.otc.make(have_token=self.sai.address, have_amount=have_amount,
                                    want_token=self.gem.address, want_amount=want_amount)

    def target_price(self) -> Wad:
        ref_per_gem = Wad(DSValue(web3=self.web3, address=self.tub.pip()).read_as_int())
        return ref_per_gem / self.tub.par()

    @staticmethod
    def rate_buy(offer: OfferInfo) -> Wad:
        return offer.sell_how_much / offer.buy_how_much

    @staticmethod
    def rate_sell(offer: OfferInfo) -> Wad:
        return offer.buy_how_much / offer.sell_how_much

    @staticmethod
    def total_amount(offers: List[OfferInfo]):
        return reduce(operator.add, map(lambda offer: offer.sell_how_much, offers), Wad(0))

    @staticmethod
    def apply_buy_margin(rate: Wad, margin: float) -> Wad:
        return rate * Wad.from_number(1 - margin)

    @staticmethod
    def apply_sell_margin(rate: Wad, margin: float) -> Wad:
        return rate * Wad.from_number(1 + margin)


if __name__ == '__main__':
    SaiMakerOtc().start()
