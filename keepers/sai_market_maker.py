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


class SaiMarketMaker(SaiKeeper):
    def lpc_conversions(self) -> List[Conversion]:
        return [LpcTakeRefConversion(self.lpc),
                LpcTakeAltConversion(self.lpc)]

    def sai_to_gem_conversion(self):
        return next(filter(lambda conversion: conversion.source_token == self.sai.address and
                                              conversion.target_token == self.gem.address, self.lpc_conversions()))

    def startup(self):
        self.approve()
        self.print_balances()

        logging.info(self.tub.tag())

        our_eth_engagement = Wad.from_number(0.5)

        # (1) Deposit some GEM
        # self.gem.deposit(our_eth_engagement)

        # (2) Exchange GEM to SKR
        # self.tub.join(our_eth_engagement)

        # (3) Open a new cup
        # our_cup = self.tub.open()
        # our_cup_id = self.tub.cupi()
        our_cup_id = 7

        # (4) Lock all SKR in the cup
        # our_skr = self.skr.balance_of(self.our_address)
        # self.tub.lock(our_cup_id, our_skr)

        # (5) Calculate the amount of SAI we want to draw, then draw it
        ### skr_collateral_value_in_ref = self.tub.ink(our_cup_id) * self.tub.tag()
        ### sai_debt_value_in_ref = self.tub.tab(our_cup_id) * self.tub.par()
        skr_collateral_value_in_sai = (self.tub.ink(our_cup_id) * self.tub.tag()) / self.tub.par()
        sai_debt_value_in_sai = self.tub.tab(our_cup_id)
        target_collateralization_ratio = Wad.from_number(2.5)
        amount_to_draw = skr_collateral_value_in_sai/target_collateralization_ratio - sai_debt_value_in_sai
        if amount_to_draw > Wad(0):
            self.tub.draw(our_cup_id, amount_to_draw)

        # (6) Exchange our SAI to ETH as we want to go long on ETH
        our_entire_sai = self.sai.balance_of(self.our_address)
        conversion = self.sai_to_gem_conversion()
        sequence = Sequence([conversion])
        if our_entire_sai - Wad.from_number(0.0001) > Wad(0):
            sequence.set_amounts(our_entire_sai - Wad.from_number(0.0001))
            receipt = sequence.steps[0].execute()
            if receipt:
                logging.info(receipt.transfers)

        # (7) Calculate our position
        our_entire_sai = self.sai.balance_of(self.our_address)
        our_entire_gem = self.gem.balance_of(self.our_address)

        our_current_debt_in_sai = self.tub.tab(our_cup_id)
        value_of_our_eth_in_sai = (Wad(Ray(our_entire_gem) / self.tub.per()) * self.tub.tag()) / self.tub.par()


        logging.info(f"Our debt: {our_current_debt_in_sai}")
        logging.info(f"Our ETH is worth: {value_of_our_eth_in_sai}")


        # IMAGINGE OUR ETH WENT 2 TIMES

        value_of_our_eth_in_sai *= 2

        logging.info(f"Our debt: {our_current_debt_in_sai}")
        logging.info(f"Our ETH is worth: {value_of_our_eth_in_sai}")


        #
        # self.tub.wipe(our_cup_id, Wad.from_number(63))



        # var pro = wmul(jar.tag(), ink(cup));
        # var con = wmul(tip.par(), tab(cup));
        # var min = rmul(con, mat);
        # return (pro >= min);


        

        # print(our_cup)
        # print(cupi)

        # self.on_block(self.synchronize_otc_offers)
        # self.otc.on_take(self.offer_taken)

    def print_balances(self):
        def balances():
            for token in [self.sai, self.gem]:
                yield f"{token.balance_of(self.our_address)} {token.name()}"
        logging.info(f"Keeper balances are {', '.join(balances())}.")

    def approve(self):
        """Approve all components that need to access our balances"""
        self.tub.approve(directly())
        self.lpc.approve(directly())
        # self.otc.approve([self.gem, self.sai], directly())


if __name__ == '__main__':
    SaiMarketMaker().start()
