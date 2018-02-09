# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017-2018 reverendus
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
import logging
import operator
import sys
from functools import reduce

import itertools
from typing import Iterable

from retry import retry
from web3 import Web3, HTTPProvider

from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.gas import GasPriceFactory
from pyexchange.idex import IDEX, IDEXApi
from pymaker import Address, synchronize
from pymaker.approval import directly
from pymaker.etherdelta import EtherDelta, EtherDeltaApi, Order
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from market_maker_keeper.band import Bands
from market_maker_keeper.price import PriceFeedFactory
from pymaker.sai import Tub, Vox
from pymaker.token import ERC20Token
from pymaker.util import eth_balance


class IdexMarketMakerKeeper:
    """Keeper acting as a market maker on IDEX, on the ETH/SAI pair."""

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='iex-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--tub-address", type=str, required=True,
                            help="Ethereum address of the Tub contract")

        parser.add_argument("--idex-address", type=str, required=True,
                            help="Ethereum address of the IDEX contract")

        parser.add_argument("--idex-api-server", type=str, default='https://api.idex.market',
                            help="Address of the IDEX API server (default: 'https://api.idex.market')")

        parser.add_argument("--idex-timeout", type=float, default=9.5,
                            help="Timeout for accessing the IDEX API (in seconds, default: 9.5)")

        parser.add_argument("--config", type=str, required=True,
                            help="Bands configuration file")

        parser.add_argument("--price-feed", type=str, required=True,
                            help="Source of price feed")

        parser.add_argument("--price-feed-expiry", type=int, default=120,
                            help="Maximum age of the price feed (in seconds, default: 120)")

        parser.add_argument("--eth-reserve", type=float, required=True,
                            help="Amount of ETH which will never be deposited so the keeper can cover gas")

        parser.add_argument("--min-eth-balance", type=float, default=0,
                            help="Minimum ETH balance below which keeper will cease operation")

        parser.add_argument("--min-eth-deposit", type=float, required=True,
                            help="Minimum amount of ETH that can be deposited in one transaction")

        parser.add_argument("--min-sai-deposit", type=float, required=True,
                            help="Minimum amount of SAI that can be deposited in one transaction")

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--gas-price-increase", type=int,
                            help="Gas price increase (in Wei) if no confirmation within"
                                 " `--gas-price-increase-every` seconds")

        parser.add_argument("--gas-price-increase-every", type=int, default=120,
                            help="Gas price increase frequency (in seconds, default: 120)")

        parser.add_argument("--gas-price-max", type=int,
                            help="Maximum gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        parser.set_defaults(cancel_on_shutdown=False, withdraw_on_shutdown=False)

        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        self.tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address))
        self.sai = ERC20Token(web3=self.web3, address=self.tub.sai())
        self.gem = ERC20Token(web3=self.web3, address=self.tub.gem())

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))
        logging.getLogger('urllib3.connectionpool').setLevel(logging.INFO)
        logging.getLogger('requests.packages.urllib3.connectionpool').setLevel(logging.INFO)

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.eth_reserve = Wad.from_number(self.arguments.eth_reserve)
        self.min_eth_balance = Wad.from_number(self.arguments.min_eth_balance)
        self.min_eth_deposit = Wad.from_number(self.arguments.min_eth_deposit)
        self.min_sai_deposit = Wad.from_number(self.arguments.min_sai_deposit)
        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed,
                                                               self.arguments.price_feed_expiry, self.tub)

        if self.eth_reserve <= self.min_eth_balance:
            raise Exception("--eth-reserve must be higher than --min-eth-balance")

        self.idex = IDEX(self.web3, Address(self.arguments.idex_address))
        self.idex_api = IDEXApi(self.idex, self.arguments.idex_api_server, self.arguments.idex_timeout)

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(10)
            lifecycle.on_startup(self.startup)
            lifecycle.on_block(self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.approve()

    @retry(delay=5, logger=logger)
    def shutdown(self):
        self.cancel_all_orders()

    def approve(self):
        """Approve IEEX to access our tokens, so we can deposit them with the exchange"""
        token_addresses = filter(lambda address: address != IDEX.ETH_TOKEN, [self.token_sell(), self.token_buy()])
        tokens = list(map(lambda address: ERC20Token(web3=self.web3, address=address), token_addresses))

        self.idex.approve(tokens, directly(gas_price=self.gas_price))

    def price(self) -> Wad:
        return self.price_feed.get_price()

    def pair(self):
        # IDEX is inconsistent here. They call the pair `DAI_ETH`, but in reality all prices are
        # calculated like it was an `ETH/DAI` pair.
        return 'DAI_ETH'

    def token_sell(self) -> Address:
        return IDEX.ETH_TOKEN

    def token_buy(self) -> Address:
        return self.sai.address

    def our_balances(self):
        return self.idex_api.get_balances()

    def our_available_balance(self, our_balances, token: Address) -> Wad:
        if token == EtherDelta.ETH_TOKEN:
            try:
                return Wad.from_number(our_balances['ETH']['available'])
            except KeyError:
                return Wad(0)
        elif token == self.sai.address:
            try:
                return Wad.from_number(our_balances['DAI']['available'])
            except KeyError:
                return Wad(0)
        else:
            raise Exception("Unknown token")

    def our_orders(self) -> list:
        return self.idex_api.get_orders(self.pair())

    def our_sell_orders(self, our_orders: list):
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list):
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        # If keeper balance is below `--min-eth-balance`, cancel all orders but do not terminate
        # the keeper, keep processing blocks as the moment the keeper gets a top-up it should
        # resume activity straight away, without the need to restart it.
        if eth_balance(self.web3, self.our_address) < self.min_eth_balance:
            self.logger.warning(f"Keeper ETH balance below minimum, cancelling all orders.")
            self.cancel_all_orders()

            return

        bands = Bands(self.bands_config)
        our_balances = self.our_balances()
        our_orders = self.our_orders()
        target_price = self.price()

        # If the is no target price feed, cancel all orders but do not terminate the keeper.
        # The moment the price feed comes back, the keeper will resume placing orders.
        if target_price is None:
            self.logger.warning("Cancelling all orders as no price feed available.")
            self.cancel_all_orders()
            return

        # Cancel orders
        cancellable_orders = bands.cancellable_orders(our_buy_orders=self.our_buy_orders(our_orders),
                                                      our_sell_orders=self.our_sell_orders(our_orders),
                                                      target_price=target_price)
        if len(cancellable_orders) > 0:
            self.cancel_orders(cancellable_orders)
            return

        # If we detect that our total balance reported by the API is not equal to the
        # total balance reported by the Ethereum contract, it probably means that some
        # deposits are still pending being credited to our account. In this case
        # we also do not create any new orders, but at the same time existing orders
        # can still be cancelled.
        if not self.balances_match(our_balances):
            self.logger.info("Balances do not match, probably deposits are in progress, waiting.")
            return

        # Evaluate if we need to create new orders, and how much do we need to deposit
        new_orders, missing_buy_amount, missing_sell_amount = bands.new_orders(our_buy_orders=self.our_buy_orders(our_orders),
                                                                               our_sell_orders=self.our_sell_orders(our_orders),
                                                                               our_buy_balance=self.our_available_balance(our_balances, self.token_buy()),
                                                                               our_sell_balance=self.our_available_balance(our_balances, self.token_sell()),
                                                                               target_price=target_price)

        # If deposited amount too low for placing buy orders, try to deposit.
        # If deposited amount too low for placing sell orders, try to deposit.
        made_deposit = False

        if missing_buy_amount > Wad(0):
            if self.deposit_for_buy_order(missing_buy_amount):
                made_deposit = True

        if missing_sell_amount > Wad(0):
            if missing_sell_amount > Wad(0):
                if self.deposit_for_sell_order(missing_sell_amount):
                    made_deposit = True

        # If we managed to deposit something, do not do anything so we can reevaluate new orders to be created.
        # Otherwise, create new orders.
        if not made_deposit:
            self.create_orders(new_orders)

    def cancel_orders(self, orders: list):
        for order in orders:
            self.idex_api.cancel_order(order)

    def cancel_all_orders(self):
        self.cancel_orders(self.our_orders())

    def create_orders(self, new_orders):
        for new_order in new_orders:
            if new_order.is_sell:
                self.idex_api.place_order(pay_token=self.token_sell(),
                                          pay_amount=new_order.pay_amount,
                                          buy_token=self.token_buy(),
                                          buy_amount=new_order.buy_amount)
            else:
                self.idex_api.place_order(pay_token=self.token_buy(),
                                          pay_amount=new_order.pay_amount,
                                          buy_token=self.token_sell(),
                                          buy_amount=new_order.buy_amount)

    def deposit_for_sell_order(self, missing_sell_amount: Wad):
        # We always want to deposit at least `min_eth_deposit`. If `missing_sell_amount` is less
        # than that, we deposit `min_eth_deposit` anyway.
        if Wad(0) < missing_sell_amount < self.min_eth_deposit:
            missing_sell_amount = self.min_eth_deposit

        # We can never deposit more than our available ETH balance minus `eth_reserve` (reserve for gas).
        depositable_eth = Wad.max(eth_balance(self.web3, self.our_address) - self.eth_reserve, Wad(0))
        missing_sell_amount = Wad.min(missing_sell_amount, depositable_eth)

        # If we still can deposit something, and it's at least `min_eth_deposit`, then we do deposit.
        if missing_sell_amount > Wad(0) and missing_sell_amount >= self.min_eth_deposit:
            receipt = self.idex.deposit(missing_sell_amount).transact(gas_price=self.gas_price)
            return receipt is not None and receipt.successful
        else:
            return False

    def deposit_for_buy_order(self, missing_buy_amount: Wad):
        # We always want to deposit at least `min_sai_deposit`. If `missing_buy_amount` is less
        # than that, we deposit `min_sai_deposit` anyway.
        if Wad(0) < missing_buy_amount < self.min_sai_deposit:
            missing_buy_amount = self.min_sai_deposit

        # We can never deposit more than our available SAI balance.
        depositable_sai = self.sai.balance_of(self.our_address)
        missing_buy_amount = Wad.min(missing_buy_amount, depositable_sai)

        # If we still can deposit something, and it's at least `min_sai_deposit`, then we do deposit.
        if missing_buy_amount > Wad(0) and missing_buy_amount >= self.min_sai_deposit:
            receipt = self.idex.deposit_token(self.sai.address, missing_buy_amount).transact(gas_price=self.gas_price)
            return receipt is not None and receipt.successful
        else:
            return False

    def balances_match(self, our_balances) -> bool:
        try:
            eth_available = Wad.from_number(our_balances['ETH']['available'])
        except KeyError:
            eth_available = Wad(0)

        try:
            eth_on_orders = Wad.from_number(our_balances['ETH']['onOrders'])
        except KeyError:
            eth_on_orders = Wad(0)

        try:
            dai_available = Wad.from_number(our_balances['DAI']['available'])
        except KeyError:
            dai_available = Wad(0)

        try:
            dai_on_orders = Wad.from_number(our_balances['DAI']['onOrders'])
        except KeyError:
            dai_on_orders = Wad(0)

        return self.idex.balance_of(self.our_address) == eth_available + eth_on_orders and \
               self.idex.balance_of_token(self.sai.address, self.our_address) == dai_available + dai_on_orders


if __name__ == '__main__':
    IdexMarketMakerKeeper(sys.argv[1:]).main()
