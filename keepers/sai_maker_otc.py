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
from typing import List

import logging

from api import Transfer
from api.approval import directly
from api.feed import DSValue
from api.numeric import Ray
from api.numeric import Wad
from api.oasis import OfferInfo, LogTake
from api.token import ERC20Token
from keepers.arbitrage.conversion import Conversion
from keepers.arbitrage.conversion import LpcTakeAltConversion, LpcTakeRefConversion
from keepers.arbitrage.opportunity import Sequence
from keepers.arbitrage.transfer_formatter import TransferFormatter
from keepers.sai import SaiKeeper


class SaiMakerOtc(SaiKeeper):
    def __init__(self):
        super().__init__()
        self.max_amount = Wad.from_number(self.arguments.max_amount)
        self.min_amount = Wad.from_number(self.arguments.min_amount)
        self.min_spread = self.arguments.min_spread
        self.avg_spread = self.arguments.avg_spread
        self.max_spread = self.arguments.max_spread

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--min-spread", help="Minimum spread allowed", type=float)
        parser.add_argument("--avg-spread", help="Average (target) spread, used on new order creation", type=float)
        parser.add_argument("--max-spread", help="Maximum spread allowed", type=float)
        parser.add_argument("--max-amount", help="Maximum value of open orders owned by the keeper", type=float)
        parser.add_argument("--min-amount", help="Minimum value of open orders owned by the keeper", type=float)

    def startup(self):
        self.approve()
        self.print_balances()
        self.on_block(self.synchronize_otc_offers)

    def shutdown(self):
        self.cancel_all_offers()

    def print_balances(self):
        def balances():
            for token in [self.sai, self.gem]:
                yield f"{token.balance_of(self.our_address)} {token.name()}"
        logging.info(f"Keeper balances are {', '.join(balances())}.")

    def approve(self):
        """Approve OasisDEX to access our balances, so we can place orders"""
        self.otc.approve([self.gem, self.sai], directly())

    def our_offers(self):
        return list(filter(lambda offer: offer.owner == self.our_address and
                                         offer.sell_which_token in [self.gem.address, self.sai.address] and
                                         offer.buy_which_token in [self.gem.address, self.sai.address],
                           self.otc.active_offers()))

    def our_buy_offers(self):
        return list(filter(lambda offer: offer.buy_which_token == self.sai.address and
                                         offer.sell_which_token == self.gem.address, self.our_offers()))

    def synchronize_otc_offers(self):
        """Update our positions in the order book to reflect settings."""
        self.cancel_excessive_offers()
        self.create_new_buy_offer()

    def cancel_excessive_offers(self):
        """Cancel offers with rates outside allowed spread range."""
        for offer in self.our_buy_offers():
            rate = self.rate_buy(offer)
            rate_min = self.apply_spread(self.target_price(), self.min_spread)
            rate_max = self.apply_spread(self.target_price(), self.max_spread)
            if (rate < rate_min) or (rate > rate_max):
                self.otc.kill(offer.offer_id)

    def cancel_all_offers(self):
        """Cancel all our offers."""
        for offer in self.our_offers():
            self.otc.kill(offer.offer_id)

    def create_new_buy_offer(self):
        """If our engagement is below the minimum amount, create a new offer up to the maximum amount"""
        total_amount = self.total_amount(self.our_offers())
        if total_amount < self.min_amount:
            our_balance = self.gem.balance_of(self.our_address)
            have_amount = Wad.min(self.max_amount - total_amount, our_balance)
            want_amount = have_amount * self.apply_spread(self.target_price(), self.avg_spread)
            if have_amount > Wad(0):
                self.otc.make(have_token=self.gem.address, have_amount=have_amount,
                              want_token=self.sai.address, want_amount=want_amount)

    @staticmethod
    def rate(offer: OfferInfo) -> Ray:
        return Ray(offer.sell_how_much) / Ray(offer.buy_how_much)

    @staticmethod
    def rate_buy(offer: OfferInfo) -> Wad:
        return Wad(offer.buy_how_much) / Wad(offer.sell_how_much)

    def target_price(self):
        ref_per_gem = Wad(DSValue(web3=self.web3, address=self.tub.pip()).read_as_int())
        ref_per_sai = self.tub.par()
        return ref_per_gem/ref_per_sai

    @staticmethod
    def total_amount(offers: List[OfferInfo]):
        return reduce(operator.add, map(lambda offer: offer.sell_how_much, offers), Wad(0))

    @staticmethod
    def apply_spread(rate: Wad, spread: float) -> Wad:
        return rate * Wad.from_number(1 + spread)


if __name__ == '__main__':
    SaiMakerOtc().start()
