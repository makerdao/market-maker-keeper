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

import sys

import time

from keeper.api import Address
from keeper.api.approval import directly
from keeper.api.feed import DSValue
from keeper.api.numeric import Wad

from keeper.api.etherdelta import EtherDelta, Order, EtherDeltaApi, OffChainOrder
from keeper.sai import SaiKeeper


class SaiMakerEtherDelta(SaiKeeper):
    """SAI keeper to act as a market maker on EtherDelta, on the ETH/SAI pair.

    Due to limitations of EtherDelta, the development of this keeper has been
    discontinued. It works most of the time, but due to the fact that EtherDelta
    was a bit unpredictable in terms of placing orders at the time this keeper
    was developed, we abandoned it and decided to stick to SaiMakerOtc for now.
    """
    def __init__(self, args: list, **kwargs):
        super().__init__(args, **kwargs)
        self.order_age = self.arguments.order_age
        self.max_eth_amount = Wad.from_number(self.arguments.max_eth_amount)
        self.min_eth_amount = Wad.from_number(self.arguments.min_eth_amount)
        self.max_sai_amount = Wad.from_number(self.arguments.max_sai_amount)
        self.min_sai_amount = Wad.from_number(self.arguments.min_sai_amount)
        self.eth_reserve = Wad.from_number(self.arguments.eth_reserve)
        self.min_margin = self.arguments.min_margin
        self.avg_margin = self.arguments.avg_margin
        self.max_margin = self.arguments.max_margin

        # We need to have the API server in order for this keeper to run.
        # Unfortunately it means it can be run only on Mainnet as there is no test server.
        if "apiServer" not in self.config.get_config()["etherDelta"]:
            raise Exception("Off-chain EtherDelta orders not supported on this chain")

        self.etherdelta = EtherDelta(web3=self.web3,
                                     address=Address(self.config.get_config()["etherDelta"]["contract"]))

        self.etherdelta_api = EtherDeltaApi(contract_address=self.etherdelta.address,
                                            api_server=self.config.get_config()["etherDelta"]["apiServer"],
                                            logger=self.logger)

        self.our_orders = set()

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--order-age", help="Age of created orders (in blocks)", type=int, required=True)
        parser.add_argument("--min-margin", help="Minimum margin allowed", type=float, required=True)
        parser.add_argument("--avg-margin", help="Target margin, used on new order creation", type=float, required=True)
        parser.add_argument("--max-margin", help="Maximum margin allowed", type=float, required=True)
        parser.add_argument("--eth-reserve", help="Minimum amount of ETH to keep in order to cover gas", type=float, required=True)
        parser.add_argument("--max-eth-amount", help="Maximum value of open ETH sell orders", type=float, required=True)
        parser.add_argument("--min-eth-amount", help="Minimum value of open ETH sell orders", type=float, required=True)
        parser.add_argument("--max-sai-amount", help="Maximum value of open SAI sell orders", type=float, required=True)
        parser.add_argument("--min-sai-amount", help="Minimum value of open SAI sell orders", type=float, required=True)

        parser.add_argument('--withdraw-on-shutdown', dest='withdraw_on_shutdown', action='store_true',
                            help="Whether should withdraw all tokens from EtherDelta on keeper shutdown")
        parser.set_defaults(withdraw_on_shutdown=False)

    def startup(self):
        self.approve()
        order = self.etherdelta.create_offchain_order(token_get=self.sai.address, amount_get=Wad.from_number(100),
                                             token_give=EtherDelta.ETH_TOKEN, amount_give=Wad.from_number(0.1),
                                             expires=self.web3.eth.blockNumber+self.order_age)
        self.place_order(order)
        # self.on_block(self.synchronize_orders)
        self.every(60*60, self.print_balances)
        self.every(20, self.publish_orders)

    def shutdown(self):
        self.cancel_all_orders()
        if self.arguments.withdraw_on_shutdown:
            self.withdraw_everything()

    def print_balances(self):
        sai_owned = self.sai.balance_of(self.our_address)
        sai_deposited = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        eth_owned = self.eth_balance(self.our_address)
        eth_deposited = self.etherdelta.balance_of(self.our_address)

        self.logger.info(f"Keeper balances are {sai_owned} + {sai_deposited} SAI, {eth_owned} + {eth_deposited} ETH")

    def approve(self):
        """Approve EtherDelta to access our SAI, so we can deposit it with the exchange"""
        self.etherdelta.approve([self.sai], directly())

    def place_order(self, order: OffChainOrder):
        self.logger.info(f"Placing off-chain EtherDelta order ('{order.token_get}',"
                         f" '{order.amount_get}', '{order.token_give}', '{order.amount_give}', '{order.expires}',"
                         f" '{order.nonce}') in progress...")

        self.our_orders.add(order)
        self.etherdelta_api.publish_offchain_order(order)

    def publish_orders(self):
        for order in self.our_orders:
            self.etherdelta_api.publish_offchain_order(order)

    def our_buy_orders(self):
        return list(filter(lambda order: order.token_get == self.sai.address and
                                         order.token_give == EtherDelta.ETH_TOKEN, self.our_orders))

    def our_sell_orders(self):
        return list(filter(lambda order: order.token_get == EtherDelta.ETH_TOKEN and
                                         order.token_give == self.sai.address, self.our_orders))

    def synchronize_orders(self):
        """Update our positions in the order book to reflect settings."""
        self.cancel_excessive_buy_orders()
        self.cancel_excessive_sell_orders()
        self.create_new_buy_order()
        self.create_new_sell_order()
        # TODO apparently deposits have to be made before we place orders, otherwise the EtherDelta backend
        # TODO seems to ignore new offchain orders. even if we deposit the tokens shortly afterwards, the orders
        # TODO will not reappear
        self.deposit_for_buy_orders()
        self.deposit_for_sell_orders()

    def cancel_excessive_buy_orders(self):
        """Cancel buy orders with rates outside allowed margin range."""
        for order in self.our_buy_orders():
            rate = self.rate_buy(order)
            rate_min = self.apply_buy_margin(self.target_rate(), self.min_margin)
            rate_max = self.apply_buy_margin(self.target_rate(), self.max_margin)
            if (rate < rate_max) or (rate > rate_min):
                self.etherdelta.cancel_order(order)

    def cancel_excessive_sell_orders(self):
        """Cancel sell orders with rates outside allowed margin range."""
        for order in self.our_sell_orders():
            rate = self.rate_sell(order)
            rate_min = self.apply_sell_margin(self.target_rate(), self.min_margin)
            rate_max = self.apply_sell_margin(self.target_rate(), self.max_margin)
            if (rate < rate_min) or (rate > rate_max):
                self.etherdelta.cancel_order(order).transact()

    def cancel_all_orders(self):
        """Cancel all our orders."""
        for order in self.our_orders():
            self.etherdelta.cancel_order(order).transact()

    def withdraw_everything(self):
        eth_balance = self.etherdelta.balance_of(self.our_address)
        if eth_balance > Wad(0):
            self.etherdelta.withdraw(eth_balance).transact()

        sai_balance = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        if sai_balance > Wad(0):
            self.etherdelta.withdraw_token(self.sai.address, sai_balance).transact()

    def create_new_buy_order(self):
        """If our ETH engagement is below the minimum amount, create a new offer up to the maximum amount"""
        total_amount = self.total_amount(self.our_buy_orders())
        if total_amount < self.min_eth_amount:
            our_balance = self.eth_balance(self.our_address) + self.etherdelta.balance_of(self.our_address) - self.eth_reserve
            have_amount = Wad.min(self.max_eth_amount, our_balance) - total_amount
            if have_amount > Wad(0):
                want_amount = have_amount / self.apply_buy_margin(self.target_rate(), self.avg_margin)
                self.etherdelta.create_offchain_order(token_get=self.sai.address, amount_get=want_amount,
                                                      token_give=EtherDelta.ETH_TOKEN, amount_give=have_amount,
                                                      expires=self.web3.eth.blockNumber+self.order_age)

    def create_new_sell_order(self):
        """If our SAI engagement is below the minimum amount, create a new offer up to the maximum amount"""
        total_amount = self.total_amount(self.our_sell_orders())
        if total_amount < self.min_sai_amount:
            our_balance = self.sai.balance_of(self.our_address) + self.etherdelta.balance_of_token(self.sai.address, self.our_address)
            have_amount = Wad.min(self.max_sai_amount, our_balance) - total_amount
            if have_amount > Wad(0):
                want_amount = have_amount * self.apply_sell_margin(self.target_rate(), self.avg_margin)
                self.etherdelta.create_offchain_order(token_get=EtherDelta.ETH_TOKEN, amount_get=want_amount,
                                                      token_give=self.sai.address, amount_give=have_amount,
                                                      expires=self.web3.eth.blockNumber+self.order_age)

    def deposit_for_buy_orders(self):
        order_total = self.total_amount(self.our_buy_orders())
        currently_deposited = self.etherdelta.balance_of(self.our_address)
        if order_total > currently_deposited:
            depositable_eth = Wad.max(self.eth_balance(self.our_address) - self.eth_reserve, Wad(0))
            additional_deposit = Wad.min(order_total - currently_deposited, depositable_eth)
            if additional_deposit > Wad(0):
                self.etherdelta.deposit(additional_deposit).transact()

    def deposit_for_sell_orders(self):
        order_total = self.total_amount(self.our_sell_orders())
        currently_deposited = self.etherdelta.balance_of_token(self.sai.address, self.our_address)
        if order_total > currently_deposited:
            additional_deposit = Wad.min(order_total - currently_deposited, self.sai.balance_of(self.our_address))
            if additional_deposit > Wad(0):
                self.etherdelta.deposit_token(self.sai.address, additional_deposit).transact()

    def target_rate(self) -> Wad:
        ref_per_gem = Wad(DSValue(web3=self.web3, address=self.tub.pip()).read_as_int())
        return self.tub.par() / ref_per_gem

    @staticmethod
    def rate_buy(order: Order) -> Wad:
        return order.amount_give / order.amount_get

    @staticmethod
    def rate_sell(order: Order) -> Wad:
        return order.amount_get / order.amount_give

    def total_amount(self, orders: List[Order]):
        give_available = lambda order: order.amount_give - (self.etherdelta.amount_filled(order) * order.amount_give / order.amount_get)
        return reduce(operator.add, map(give_available, orders), Wad(0))

    @staticmethod
    def apply_buy_margin(rate: Wad, margin: float) -> Wad:
        return rate * Wad.from_number(1 - margin)

    @staticmethod
    def apply_sell_margin(rate: Wad, margin: float) -> Wad:
        return rate * Wad.from_number(1 + margin)


if __name__ == '__main__':
    SaiMakerEtherDelta(sys.argv[1:]).start()
