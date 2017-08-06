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

import logging
from typing import List

from api import Address
from api.approval import directly
from api.numeric import Ray
from api.numeric import Wad
from keepers.conversion import Conversion
from keepers.conversion import LpcTakeAltConversion, LpcTakeRefConversion
from keepers.opportunity import Sequence
from keepers.sai import SaiKeeper


class SaiCreator(SaiKeeper):
    """This is an early experimental keeper!

    This is an early experimental keeper and probably doesn't even work anymore!
    """
    def lpc_conversions(self) -> List[Conversion]:
        return [LpcTakeRefConversion(self.lpc),
                LpcTakeAltConversion(self.lpc)]

    def sai_to_gem_conversion(self):
        return next(filter(lambda conversion: conversion.source_token == self.sai.address and
                                              conversion.target_token == self.gem.address, self.lpc_conversions()))

    def gem_to_sai_conversion(self):
        return next(filter(lambda conversion: conversion.source_token == self.gem.address and
                                              conversion.target_token == self.sai.address, self.lpc_conversions()))

    def startup(self):
        self.approve()
        self.print_balances()

        # self.prepare_balances()
        # self.open_position()

        self.test_position(9)

    def prepare_balances(self):
        recipient = Address('0x002ca7F9b416B2304cDd20c26882d1EF5c53F611')
        if self.sai.balance_of(self.our_address) > Wad(0):
            self.sai.transfer(recipient, self.sai.balance_of(self.our_address)).transact()
        if self.skr.balance_of(self.our_address) > Wad(0):
            self.skr.transfer(recipient, self.skr.balance_of(self.our_address)).transact()
        if self.gem.balance_of(self.our_address) > Wad.from_number(0.5):
            self.gem.transfer(recipient, self.gem.balance_of(self.our_address) - Wad.from_number(0.5)).transact()

    def open_position(self):
        our_eth_engagement = Wad.from_number(0.5)
        target_collateralization_ratio = Wad.from_number(2.5)

        # (1) Deposit some GEM
        # self.gem.deposit(our_eth_engagement)

        # (2) Exchange GEM to SKR
        self.tub.join(our_eth_engagement)

        # (3) Open a new cup
        self.tub.open()
        our_cup_id = self.tub.cupi()
        logging.info(f"Opened cup {our_cup_id}")
        # our_cup_id = 9

        # (4) Lock all SKR in the cup
        our_skr = self.skr.balance_of(self.our_address)
        self.tub.lock(our_cup_id, our_skr)

        # (5) Calculate the amount of SAI we want to draw, then draw it
        ### skr_collateral_value_in_ref = self.tub.ink(our_cup_id) * self.tub.tag()
        ### sai_debt_value_in_ref = self.tub.tab(our_cup_id) * self.tub.par()
        skr_collateral_value_in_sai = (self.tub.ink(our_cup_id) * self.tub.tag()) / self.tub.par()
        sai_debt_value_in_sai = self.tub.tab(our_cup_id)
        amount_to_draw = skr_collateral_value_in_sai/target_collateralization_ratio - sai_debt_value_in_sai
        # TODO I don't think we should draw if we have already drawn
        # this protects us from getting back to 250% collateralization
        # if our position has improved since then
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

    def test_position(self, cup_id):
        # (7) Calculate our position
        our_current_debt_in_sai = self.tub.tab(cup_id)

        our_entire_gem = self.gem.balance_of(self.our_address)
        value_of_our_eth_in_sai = (Wad(Ray(our_entire_gem) / self.tub.per()) * self.tub.tag()) / self.tub.par()

        logging.info(f"Our debt: {our_current_debt_in_sai}")
        logging.info(f"Our ETH is worth: {value_of_our_eth_in_sai}")
        logging.info(f"Ratio: {value_of_our_eth_in_sai / our_current_debt_in_sai}")

    def close_position(self):
        cup_id = 8

        # (1) Exchange our ETH to SAI as we want to repay our SAI debt
        our_entire_gem = self.gem.balance_of(self.our_address)
        conversion = self.gem_to_sai_conversion()
        sequence = Sequence([conversion])
        if our_entire_gem > Wad.from_number(0.0001):
            sequence.set_amounts(our_entire_gem)
            receipt = sequence.steps[0].execute()
            if receipt:
                logging.info(receipt.transfers)

        self.print_balances()

        our_debt = self.tub.tab(cup_id)
        print(our_debt)
        our_sai_balanace = self.sai.balance_of(self.our_address)
        if our_sai_balanace < our_debt:
            logging.info("NOT ENOUGH SAI TO REPAY OUR DEBT!")
            exit(-1)

        # (2) Repay the debt and get back our SKR
        # some surplus of SAI will be left, this is the profit we made
        # self.tub.wipe(cup_id, Wad.from_number(1))
        logging.info(self.tub.shut(cup_id))
        self.print_balances()

        # (3) Exchange SKR back to ETH
        if self.skr.balance_of(self.our_address) > Wad(0):
            logging.info(self.tub.exit(self.skr.balance_of(self.our_address)))
            self.print_balances()

    def print_balances(self):
        def balances():
            for token in [self.sai, self.gem, self.skr]:
                yield f"{token.balance_of(self.our_address)} {token.name()}"
        logging.info(f"Keeper balances are {', '.join(balances())}.")
        logging.info(f"ETH/USD is {self.tub.tag()}")

    def approve(self):
        """Approve all components that need to access our balances"""
        self.tub.approve(directly())
        self.lpc.approve(directly())


if __name__ == '__main__':
    SaiCreator().start()
