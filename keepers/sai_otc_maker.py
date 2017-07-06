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
import copy
import operator
import time
from functools import reduce
from typing import List

import logging

from api import Address, Transfer
from api.numeric import Ray
from api.numeric import Wad
from api.otc import SimpleMarket, OfferInfo, LogTake
from api.sai import Tub, Lpc
from api.token import ERC20Token
from api.transact import Invocation, TxManager
from keepers import Keeper
from keepers.arbitrage.conversion import Conversion
from keepers.arbitrage.conversion import LpcTakeAltConversion, LpcTakeRefConversion
from keepers.arbitrage.conversion import OasisTakeConversion
from keepers.arbitrage.conversion import TubBoomConversion, TubBustConversion, TubExitConversion, TubJoinConversion
from keepers.arbitrage.opportunity import OpportunityFinder
from keepers.arbitrage.transfer_formatter import TransferFormatter


class SaiOtcMaker(Keeper):
    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--frequency", help="Monitoring frequency in seconds (default: 5)", default=5, type=float)

    def init(self):
        self.tub_address = Address(self.config.get_contract_address("saiTub"))
        self.tap_address = Address(self.config.get_contract_address("saiTap"))
        self.top_address = Address(self.config.get_contract_address("saiTop"))
        self.tub = Tub(web3=self.web3, address_tub=self.tub_address, address_tap=self.tap_address, address_top=self.top_address)
        self.lpc_address = Address(self.config.get_contract_address("saiLpc"))
        self.lpc = Lpc(web3=self.web3, address=self.lpc_address)
        self.otc_address = Address(self.config.get_contract_address("otc"))
        self.otc = SimpleMarket(web3=self.web3, address=self.otc_address)

        self.skr = ERC20Token(web3=self.web3, address=self.tub.skr())
        self.sai = ERC20Token(web3=self.web3, address=self.tub.sai())
        self.gem = ERC20Token(web3=self.web3, address=self.tub.gem())
        ERC20Token.register_token(self.tub.skr(), 'SKR')
        ERC20Token.register_token(self.tub.sai(), 'SAI')
        ERC20Token.register_token(self.tub.gem(), 'WETH')

        # TODO for now, to keep the code approval code unchanged
        # and ultimately move it to a common module
        self.tx_manager = None

        self.sell_token = self.gem.address
        self.buy_token = self.sai.address

        self.max_amount = Wad.from_number(1)
        self.min_amount = Wad.from_number(0.6)
        # for SAI: 60, 50, 30
        # for WETH: 1.2, 1, 0.6

        # TODO will probably need to change the unit
        self.min_gap = 0.01
        self.avg_gap = 0.03
        self.max_gap = 0.05

    def run(self):
        self.setup_allowances()
        self.print_balances()
        self.otc.on_take(self.order_take_handler)
        while True:
            self.update_otc_orders()
            time.sleep(self.arguments.frequency)

    def print_balances(self):
        def balances():
            for token in [self.sai, self.skr, self.gem]:
                yield f"{token.balance_of(self.our_address)} {token.name()}"
        logging.info(f"Keeper balances are {', '.join(balances())}.")

    def setup_allowances(self):
        """Approve all components that need to access our balances"""
        # self.setup_tub_allowances()
        self.setup_lpc_allowances()
        self.setup_otc_allowances()
        # self.setup_tx_manager_allowances()

    # def setup_tub_allowances(self):
    #     """Approve Tub components so we can call join()/exit() and boom()/bust()"""
    #     self.setup_allowance(self.gem, self.tub.jar(), 'Tub.jar')
    #     self.setup_allowance(self.skr, self.tub.jar(), 'Tub.jar')
    #     self.setup_allowance(self.skr, self.tub.pit(), 'Tub.pit')
    #     self.setup_allowance(self.sai, self.tub.pit(), 'Tub.pit')

    def setup_lpc_allowances(self):
        """Approve the Lpc so we can exchange WETH and SAI using it"""
        self.setup_allowance(self.gem, self.lpc.address, 'Lpc')
        self.setup_allowance(self.sai, self.lpc.address, 'Lpc')

    def setup_otc_allowances(self):
        """Approve OasisDEX so we can exchange all three tokens (WETH, SAI and SKR)"""
        self.setup_allowance(self.gem, self.otc.address, 'OasisDEX')
        self.setup_allowance(self.sai, self.otc.address, 'OasisDEX')
        # self.setup_allowance(self.skr, self.otc.address, 'OasisDEX')

    def setup_allowance(self, token: ERC20Token, spender_address: Address, spender_name: str):
        #TODO actually only one of these paths is needed, depending on whether we are using a
        #TxManager or not
        if token.allowance_of(self.our_address, spender_address) < Wad(2 ** 128 - 1):
            print(f"Approving {spender_name} ({spender_address}) to access our {token.name()} balance directly...")
            if not token.approve(spender_address):
                print(f"Approval failed!")
                exit(-1)

        if self.tx_manager and spender_address != self.tx_manager.address and \
                        token.allowance_of(self.tx_manager.address, spender_address) < Wad(2 ** 128 - 1):
            print(f"Approving {spender_name} ({spender_address}) to access our {token.name()} balance indirectly...")
            invocation = Invocation(address=token.address, calldata=token.approve_calldata(spender_address))
            if not self.tx_manager.execute([], [invocation]):
                print(f"Approval failed!")
                exit(-1)

    def lpc_conversions(self) -> List[Conversion]:
        return [LpcTakeRefConversion(self.lpc),
                LpcTakeAltConversion(self.lpc)]

    def conversion(self):
        return next(filter(lambda conversion: conversion.source_token == self.buy_token and
                                              conversion.target_token == self.sell_token, self.lpc_conversions()))

    def order_take_handler(self, log_take: LogTake):
        if log_take.maker == self.our_address \
                and log_take.have_token == self.sell_token and log_take.want_token == self.buy_token:

            conversion = copy.deepcopy(self.conversion())
            #TODO this should get extracted somewhere
            conversion.source_amount = Wad.min(log_take.give_amount, conversion.max_source_amount)
            conversion.target_amount = Wad(Ray(conversion.source_amount) * conversion.rate)

            print(f"Someone exchanged {log_take.take_amount} {ERC20Token.token_name_by_address(self.sell_token)} to {log_take.give_amount} {ERC20Token.token_name_by_address(self.buy_token)}")
            print(f"We will make an counterexchange of {conversion.source_amount} {ERC20Token.token_name_by_address(conversion.source_token)}"
                  f" to {conversion.target_amount} {ERC20Token.token_name_by_address(conversion.target_token)}")
            print(f"Executing {conversion.name()}")
            result = conversion.execute()
            if result:
                trans = list(result.transfers)
                trans.append(Transfer(log_take.have_token, log_take.maker, log_take.taker, log_take.take_amount))
                trans.append(Transfer(log_take.want_token, log_take.taker, log_take.maker, log_take.give_amount))
                print("OK")
                print(f"The profit we made is {TransferFormatter().format_net(trans, self.our_address)}.")
            else:
                print("FAILED!!!")

    def update_otc_orders(self):

        # cancel offers with prices outside allowed range
        for offer in self.otc_offers():
            rate = self.price(offer)
            rate_min = self.apply_gap(self.conversion().rate, self.min_gap)
            rate_max = self.apply_gap(self.conversion().rate, self.max_gap)
            if (rate < rate_max) or (rate > rate_min):
                self.otc.kill(offer.offer_id)

        # if our engagement is below the minimum amount,
        # create a new offer up to the maximum amount
        #TODO check our balance
        #TODO check max_amount on conversion??
        total_amount = self.total_amount(self.otc_offers())
        if total_amount < self.min_amount:
            have_amount = self.max_amount - total_amount
            want_amount = Wad(Ray(have_amount) / self.apply_gap(self.conversion().rate, self.avg_gap))
            self.otc.make(have_token=self.sell_token, have_amount=have_amount,
                          want_token=self.buy_token, want_amount=want_amount)

    @staticmethod
    def price(offer: OfferInfo) -> Ray:
        return Ray(offer.sell_how_much) / Ray(offer.buy_how_much)

    @staticmethod
    def total_amount(offers: List[OfferInfo]):
        return reduce(operator.add, map(lambda offer: offer.sell_how_much, offers), Wad(0))

    @staticmethod
    def apply_gap(rate: Ray, gap: float) -> Ray:
        return rate * Ray.from_number(1 - gap)

    def otc_offers(self):
        # TODO extract that to a method in otc(...) ?
        offers = [self.otc.get_offer(offer_id + 1) for offer_id in range(self.otc.get_last_offer_id())]
        offers = [offer for offer in offers if offer is not None]
        return list(filter(lambda offer: offer.owner == self.our_address and
                                         offer.sell_which_token == self.sell_token and
                                         offer.buy_which_token == self.buy_token, offers))






if __name__ == '__main__':
    SaiOtcMaker().start()
