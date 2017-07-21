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
        self.max_weth_amount = Wad.from_number(self.arguments.max_weth_amount)
        self.min_weth_amount = Wad.from_number(self.arguments.min_weth_amount)
        self.max_sai_amount = Wad.from_number(self.arguments.max_sai_amount)
        self.min_sai_amount = Wad.from_number(self.arguments.min_sai_amount)
        self.min_spread = self.arguments.min_spread
        self.avg_spread = self.arguments.avg_spread
        self.max_spread = self.arguments.max_spread

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--min-spread", help="Minimum spread allowed", type=float)
        parser.add_argument("--avg-spread", help="Average (target) spread, used on new order creation", type=float)
        parser.add_argument("--max-spread", help="Maximum spread allowed", type=float)
        parser.add_argument("--max-weth-amount", help="Maximum value of open WETH sell orders", type=float)
        parser.add_argument("--min-weth-amount", help="Minimum value of open WETH sell orders", type=float)
        parser.add_argument("--max-sai-amount", help="Maximum value of open SAI sell orders", type=float)
        parser.add_argument("--min-sai-amount", help="Minimum value of open SAI sell orders", type=float)

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
        return list(filter(lambda offer: offer.owner == self.our_address, self.otc.active_offers()))

    def our_buy_offers(self):
        return list(filter(lambda offer: offer.buy_which_token == self.sai.address and
                                         offer.sell_which_token == self.gem.address, self.our_offers()))

    def our_sell_offers(self):
        return list(filter(lambda offer: offer.buy_which_token == self.gem.address and
                                         offer.sell_which_token == self.sai.address, self.our_offers()))

    def synchronize_otc_offers(self):
        """Update our positions in the order book to reflect settings."""
        self.cancel_excessive_buy_offers()
        self.cancel_excessive_sell_offers()
        self.create_new_buy_offer()
        self.create_new_sell_offer()

    def cancel_excessive_buy_offers(self):
        """Cancel buy offers with rates outside allowed spread range."""
        for offer in self.our_buy_offers():
            rate = self.rate_buy(offer)
            rate_min = self.apply_buy_spread(self.target_rate(), self.min_spread)
            rate_max = self.apply_buy_spread(self.target_rate(), self.max_spread)
            if (rate < rate_max) or (rate > rate_min):
                self.otc.kill(offer.offer_id)

    def cancel_excessive_sell_offers(self):
        """Cancel sell offers with rates outside allowed spread range."""
        for offer in self.our_sell_offers():
            rate = self.rate_sell(offer)
            rate_min = self.apply_sell_spread(self.target_rate(), self.min_spread)
            rate_max = self.apply_sell_spread(self.target_rate(), self.max_spread)
            if (rate < rate_min) or (rate > rate_max):
                self.otc.kill(offer.offer_id)

    def cancel_all_offers(self):
        """Cancel all our offers."""
        for offer in self.our_offers():
            self.otc.kill(offer.offer_id)

    def create_new_buy_offer(self):
        """If our WETH engagement is below the minimum amount, create a new offer up to the maximum amount"""
        total_amount = self.total_amount(self.our_buy_offers())
        if total_amount < self.min_weth_amount:
            our_balance = self.gem.balance_of(self.our_address)
            have_amount = Wad.min(self.max_weth_amount - total_amount, our_balance)
            want_amount = have_amount / self.apply_buy_spread(self.target_rate(), self.avg_spread)
            if have_amount > Wad(0):
                self.otc.make(have_token=self.gem.address, have_amount=have_amount,
                              want_token=self.sai.address, want_amount=want_amount)

    def create_new_sell_offer(self):
        """If our SAI engagement is below the minimum amount, create a new offer up to the maximum amount"""
        total_amount = self.total_amount(self.our_sell_offers())
        if total_amount < self.min_sai_amount:
            our_balance = self.sai.balance_of(self.our_address)
            have_amount = Wad.min(self.max_sai_amount - total_amount, our_balance)
            want_amount = have_amount * self.apply_sell_spread(self.target_rate(), self.avg_spread)
            if have_amount > Wad(0):
                self.otc.make(have_token=self.sai.address, have_amount=have_amount,
                              want_token=self.gem.address, want_amount=want_amount)

    @staticmethod
    def rate_buy(offer: OfferInfo) -> Wad:
        return offer.sell_how_much / offer.buy_how_much

    @staticmethod
    def rate_sell(offer: OfferInfo) -> Wad:
        return offer.buy_how_much / offer.sell_how_much

    def target_rate(self) -> Wad:
        ref_per_gem = Wad(DSValue(web3=self.web3, address=self.tub.pip()).read_as_int())
        return self.tub.par() / ref_per_gem

    @staticmethod
    def total_amount(offers: List[OfferInfo]):
        return reduce(operator.add, map(lambda offer: offer.sell_how_much, offers), Wad(0))

    @staticmethod
    def apply_buy_spread(rate: Wad, spread: float) -> Wad:
        return rate * Wad.from_number(1 - spread)

    @staticmethod
    def apply_sell_spread(rate: Wad, spread: float) -> Wad:
        return rate * Wad.from_number(1 + spread)


if __name__ == '__main__':
    SaiMakerOtc().start()
