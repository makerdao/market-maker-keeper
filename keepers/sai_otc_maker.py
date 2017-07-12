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
from api.numeric import Ray
from api.numeric import Wad
from api.oasis import OfferInfo, LogTake
from api.token import ERC20Token
from keepers.arbitrage.conversion import Conversion
from keepers.arbitrage.conversion import LpcTakeAltConversion, LpcTakeRefConversion
from keepers.arbitrage.opportunity import Sequence
from keepers.arbitrage.transfer_formatter import TransferFormatter
from keepers.sai import SaiKeeper


class SaiOtcMaker(SaiKeeper):
    def __init__(self):
        super().__init__()
        self.sell_token = ERC20Token.token_address_by_name(self.arguments.sell_token)
        self.buy_token = ERC20Token.token_address_by_name(self.arguments.buy_token)
        self.max_amount = Wad.from_number(self.arguments.max_amount)
        self.min_amount = Wad.from_number(self.arguments.min_amount)
        self.min_spread = self.arguments.min_spread
        self.avg_spread = self.arguments.avg_spread
        self.max_spread = self.arguments.max_spread

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--sell-token", help="Token to put on sale on OasisDEX", type=str)
        parser.add_argument("--buy-token", help="Token we will be paid with on OasisDEX", type=str)
        parser.add_argument("--min-spread", help="Minimum spread allowed", type=float)
        parser.add_argument("--avg-spread", help="Average spread (used on order creation)", type=float)
        parser.add_argument("--max-spread", help="Maximum spread allowed", type=float)
        parser.add_argument("--max-amount", help="Maximum value of open orders owned by keeper", type=float)
        parser.add_argument("--min-amount", help="Minimum value of open orders owned by keeper", type=float)

    def startup(self):
        self.approve()
        self.print_balances()
        self.on_block(self.synchronize_otc_offers)
        self.otc.on_take(self.offer_taken)

    def shutdown(self):
        self.cancel_all_offers()

    def print_balances(self):
        def balances():
            for token in [self.sai, self.gem]:
                yield f"{token.balance_of(self.our_address)} {token.name()}"
        logging.info(f"Keeper balances are {', '.join(balances())}.")

    def approve(self):
        """Approve all components that need to access our balances"""
        self.lpc.approve(directly())
        self.otc.approve([self.gem, self.sai], directly())

    def lpc_conversions(self) -> List[Conversion]:
        return [LpcTakeRefConversion(self.lpc),
                LpcTakeAltConversion(self.lpc)]

    def conversion(self):
        return next(filter(lambda conversion: conversion.source_token == self.buy_token and
                                              conversion.target_token == self.sell_token, self.lpc_conversions()))

    def our_offers(self):
        return list(filter(lambda offer: offer.owner == self.our_address and
                                         offer.sell_which_token == self.sell_token and
                                         offer.buy_which_token == self.buy_token, self.otc.active_offers()))

    def offer_taken(self, log_take: LogTake):
        """If our offer has been partially or completely taken, make an exchange."""
        if log_take.maker == self.our_address and \
                        log_take.have_token == self.sell_token and log_take.want_token == self.buy_token:
            self.exchange(log_take)

    def synchronize_otc_offers(self):
        """Update our positions in the order book to reflect settings."""
        self.cancel_excessive_offers()
        self.create_new_offer()

    def cancel_excessive_offers(self):
        """Cancel offers with rates outside allowed spread range."""
        for offer in self.our_offers():
            rate = self.rate(offer)
            rate_min = self.apply_spread(self.conversion().rate, self.min_spread)
            rate_max = self.apply_spread(self.conversion().rate, self.max_spread)
            if (rate < rate_max) or (rate > rate_min):
                self.otc.kill(offer.offer_id)

    def cancel_all_offers(self):
        """Cancel all our offers."""
        for offer in self.our_offers():
            self.otc.kill(offer.offer_id)

    #TODO check max_amount on conversion??
    def create_new_offer(self):
        """If our engagement is below the minimum amount, create a new offer up to the maximum amount"""
        total_amount = self.total_amount(self.our_offers())
        if total_amount < self.min_amount:
            our_balance = ERC20Token(web3=self.web3, address=self.sell_token).balance_of(self.our_address)
            have_amount = Wad.min(self.max_amount - total_amount, our_balance)
            want_amount = Wad(Ray(have_amount) / self.apply_spread(self.conversion().rate, self.avg_spread))
            if have_amount > Wad(0):
                self.otc.make(have_token=self.sell_token, have_amount=have_amount,
                              want_token=self.buy_token, want_amount=want_amount)

    def exchange(self, log_take: LogTake):
        sequence = Sequence(conversions=[self.conversion()])
        sequence.set_amounts(log_take.give_amount)

        logging.info(f"Someone exchanged {log_take.take_amount} {ERC20Token.token_name_by_address(self.sell_token)}"
                     f" to {log_take.give_amount} {ERC20Token.token_name_by_address(self.buy_token)}")
        logging.info(f"We will exchange {sequence.steps[0].source_amount} {ERC20Token.token_name_by_address(sequence.steps[0].source_token)}"
                     f" to {sequence.steps[0].target_amount} {ERC20Token.token_name_by_address(sequence.steps[0].target_token)}")

        result = sequence.steps[0].execute()
        if result:
            trans = list(result.transfers)
            trans.append(Transfer(log_take.have_token, log_take.maker, log_take.taker, log_take.take_amount))
            trans.append(Transfer(log_take.want_token, log_take.taker, log_take.maker, log_take.give_amount))
            logging.info(f"We made {TransferFormatter().format_net(trans, self.our_address)} profit")

    @staticmethod
    def rate(offer: OfferInfo) -> Ray:
        return Ray(offer.sell_how_much) / Ray(offer.buy_how_much)

    @staticmethod
    def total_amount(offers: List[OfferInfo]):
        return reduce(operator.add, map(lambda offer: offer.sell_how_much, offers), Wad(0))

    @staticmethod
    def apply_spread(rate: Ray, spread: float) -> Ray:
        return rate * Ray.from_number(1 - spread)


if __name__ == '__main__':
    SaiOtcMaker().start()
