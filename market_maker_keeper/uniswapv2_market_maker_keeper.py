#
# Copyright (C) 2020 MikeHathaway
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
import sys

from typing import Optional, Tuple
from web3 import Web3, HTTPProvider
from pymaker.lifecycle import Lifecycle
from pyexchange.uniswapv2 import UniswapV2
from pymaker.keys import register_keys
from pymaker.model import Token
from pymaker import Address, Wad, Receipt
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.model import TokenConfig
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging



class UniswapV2MarketMakerKeeper:
    """Keeper acting as a market maker on UniswapV2.

    Adding or removing liquidity to arbitrary ETH-ERC20 or ERC20-ERC20 Pools.
    If one of the assets is ETH, it is assumed that it will be the second asset (token_b)

    """

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='uniswap-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--rpc-timeout", type=int, default=10,
                            help="JSON-RPC timeout (in seconds, default: 10)")

        parser.add_argument("--eth-from", type=str, required=True,
                            help="Ethereum account from which to send transactions")

        parser.add_argument("--eth-key", type=str, nargs='*',
                            help="Ethereum private key(s) to use (e.g. 'key_file=aaa.json,pass_file=aaa.pass')")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--token-config", type=str, required=True,
                            help="Token configuration file")

        parser.add_argument("--price-feed", type=str, required=True,
                            help="Source of price feed")

        parser.add_argument("--price-feed-accepted-delay", type=int, default=60,
                            help="Number of seconds the keeper will tolerate the price feed being null before removing liquidity")

        parser.add_argument("--price-feed-expiry", type=int, default=86400,
                            help="Maximum age of the price feed (in seconds, default: 86400)")

        parser.add_argument("--ethgasstation-api-key", type=str, default=None, help="ethgasstation API key")

        parser.add_argument("--gas-price", type=int, default=9000000000,
                            help="Gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--max-add-liquidity-slippage", type=int, default=2,
                            help="Maximum percentage off the desired amount of liquidity to add in add_liquidity()")

        parser.add_argument("--accepted-price-slippage-up", type=float, required=True,
                            help="Percentage difference between Uniswap exchange rate and aggregated price above which liquidity would be added")

        parser.add_argument("--accepted-price-slippage-down", type=float, required=True,
                            help="Percentage difference between Uniswap exchange rate and aggregated price below which liquidity would be added")

        parser.add_argument("--target-a-min-balance", type=float, required=True,
                            help="Minimum balance of token A to maintain.")

        parser.add_argument("--target-a-max-balance", type=float, required=True,
                            help="Minimum balance of token A to maintain.")

        parser.add_argument("--target-b-min-balance", type=float, required=True,
                            help="Minimum balance of token B to maintain.")

        parser.add_argument("--target-b-max-balance", type=float, required=True,
                            help="Minimum balance of token B to maintain.")

        parser.add_argument("--factory-address", type=str, default="0x5C69bEe701ef814a2B6a3EDD4B1652CB9cc5aA6f",
                            help="Address of the UniswapV2 Factory smart contract used to create new pools")

        parser.add_argument("--router-address", type=str, default="0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
                            help="Address of the UniswapV2 RouterV2 smart contract used to handle liquidity management")

        parser.add_argument("--initial-delay", type=int, default=10,
                            help="Initial number of seconds to wait before placing liquidity")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"https://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))

        self.web3.eth.defaultAccount = self.arguments.eth_from
        if 'web3' not in kwargs:
            register_keys(self.web3, self.arguments.eth_key)

        # TODO: Add a more sophisticated regex for different variants of eth on the exchange 
        # Record if eth is in pair, so can check which liquidity method needs to be used
        self.is_eth = 'ETH' in self.pair()

        # Identify which token is ETH, so we can provide the arguments to Uniswap Router in expected order
        self.eth_position = 1
        if self.is_eth:
            self.eth_position = 0 if self.pair().split('-')[0] == 'ETH' else 1

        token_a_name = 'WETH' if self.is_eth and self.eth_position == 0 else self.pair().split('-')[0]
        token_b_name = 'WETH' if self.is_eth and self.eth_position == 1 else self.pair().split('-')[1]

        self.reloadable_config = ReloadableConfig(self.arguments.token_config)
        self._last_config_dict = None
        self._last_config = None
        token_config = self.get_token_config().tokens
        self.token_a = list(filter(lambda token: token.name == token_a_name, token_config))[0]
        self.token_b = list(filter(lambda token: token.name == token_b_name, token_config))[0]

        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)

        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.price_feed_accepted_delay = self.arguments.price_feed_accepted_delay
        self.control_feed = create_control_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)

        # testing_feed_price is used by the integration tests in tests/test_uniswapv2.py, to test different pricing scenarios
        # as the keeper consistently checks the price, some long running state variable is needed to 
        self.testing_feed_price = False
        self.test_price = Wad.from_number(0)

        self.uniswap = UniswapV2(self.web3, self.token_a, self.token_b, Address(self.arguments.router_address), Address(self.arguments.factory_address))

        self.uniswap_current_exchange_price = self.uniswap.get_exchange_rate()

        self.feed_price_null_counter = 0

        # set target min and max amounts for each side of the pair
        # balance doesnt exceed some level, as an effective stop loss against impermanent loss
        self.target_a_min_balance = self.token_a.unnormalize_amount(Wad.from_number(self.arguments.target_a_min_balance))
        self.target_a_max_balance = self.token_a.unnormalize_amount(Wad.from_number(self.arguments.target_a_max_balance))
        self.target_b_min_balance = self.token_b.unnormalize_amount(Wad.from_number(self.arguments.target_b_min_balance))
        self.target_b_max_balance = self.token_b.unnormalize_amount(Wad.from_number(self.arguments.target_b_max_balance))

        self.accepted_price_slippage_up = Wad.from_number(self.arguments.accepted_price_slippage_up / 100)
        self.accepted_price_slippage_down = Wad.from_number(self.arguments.accepted_price_slippage_down / 100)
        self.max_add_liquidity_slippage = Wad.from_number(self.arguments.max_add_liquidity_slippage / 100)

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(self.arguments.initial_delay)
            lifecycle.on_startup(self.startup)
            lifecycle.every(10, self.place_liquidity)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        self.uniswap.approve(self.token_a)
        self.uniswap.approve(self.token_b)

    def shutdown(self):
        self.logger.info(f"Shutdown notification received, removing all available liquidity")
        self.remove_liquidity()

    def get_token_config(self):
        current_config = self.reloadable_config.get_token_config()
        if current_config != self._last_config_dict:
            self._last_config = TokenConfig(current_config)
            self._last_config_dict = current_config

            self.logger.info(f"Successfully parsed configuration")

        return self._last_config

    def pair(self) -> str:
        return self.arguments.pair

    def get_balance(self, token: Token) -> Wad:
        if token.name == "WETH":
            return self.uniswap.get_account_eth_balance()
        else:
            return self.uniswap.get_account_token_balance(token)
        
    def calculate_liquidity_args(self, token_a_balance: Wad, token_b_balance: Wad) -> Optional[dict]:
        """ Returns dictionary containing arguments for addLiquidity transactions

        Calculate amount of both tokens, given the current reserve ratio on uniswap
        Use accepted_slippage to calculate min off of available balance + liquidity

        If eth is in the pair, at least 1 eth should be left for gas
        """

        if self.is_eth:
            if self.eth_position == 0:
                token_a_balance = token_a_balance - Wad.from_number(1)
                if token_a_balance < Wad.from_number(0):
                    self.logger.info(f"Insufficient Eth balance.")
                    return
            elif self.eth_position == 1:
                token_b_balance = token_b_balance - Wad.from_number(1)
                if token_b_balance < Wad.from_number(0):
                    self.logger.info(f"Insufficient Eth balance.")
                    return

        token_a_desired = min(token_a_balance, token_b_balance / self.uniswap_current_exchange_price)
        token_a_min = token_a_desired - (token_a_desired * self.max_add_liquidity_slippage)
        token_b_desired = min(token_b_balance, token_a_desired * self.uniswap_current_exchange_price)
        token_b_min = token_b_desired - (token_b_desired * self.max_add_liquidity_slippage)

        add_liquidity_args = {
            'amount_a_desired': self.token_a.unnormalize_amount(token_a_desired),
            'amount_b_desired': self.token_b.unnormalize_amount(token_b_desired),
            'amount_a_min': self.token_a.unnormalize_amount(token_a_min),
            'amount_b_min': self.token_b.unnormalize_amount(token_b_min)
        }
        return add_liquidity_args

    def add_liquidity(self) -> Optional[Receipt]:
        """ Send an addLiquidity or addLiquidityETH transaction to the UniswapV2 Router Contract.

        UniswapV2 differentiates between ETH and ERC20 token transactions.
        All operations are handled in WETH, but the Router Contract handles all wrapping and unwrapping.

        It is assumed that all availability should be added, except for 1 Eth for future Gas operations.

        Given available token balances and an exchange rate, calculations of amounts to add,
        and limits for price movement need to be calculated.

        Returns:
            A Pymaker Receipt object or a None
        """

        token_a_balance = self.get_balance(self.token_a)
        token_b_balance = self.get_balance(self.token_b)

        self.logger.info(f"Wallet {self.token_a.name} balance: {token_a_balance}; "
                         f"Wallet {self.token_b.name} balance: {token_b_balance}")

        add_liquidity_args = self.calculate_liquidity_args(token_a_balance, token_b_balance)
        self.logger.debug(f"Pair liquidity to add: {add_liquidity_args}")

        current_liquidity_tokens = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_current_liquidity()
        self.logger.info(f"Current liquidity tokens before adding: {current_liquidity_tokens}")

        if add_liquidity_args is None:
            return None

        is_liquidity_to_add_positive = all(map(lambda x: x > Wad(0), add_liquidity_args.values()))
        if is_liquidity_to_add_positive and current_liquidity_tokens == Wad(0):
            self.logger.info(
                    f"Add {self.token_a.name} liquidity of amount: {self.token_a.normalize_amount(add_liquidity_args['amount_a_desired'])}")
            self.logger.info(
                    f"Add {self.token_b.name} liquidity of: {self.token_b.normalize_amount(add_liquidity_args['amount_b_desired'])}")
            
            if self.is_eth:
                token = self.token_b if self.eth_position == 0 else self.token_a
                transact = self.uniswap.add_liquidity_eth(add_liquidity_args, token, self.eth_position).transact(
                    gas_price=self.gas_price)
            else:
                transact = self.uniswap.add_liquidity(add_liquidity_args, self.token_a, self.token_b).transact(
                    gas_price=self.gas_price)

            if transact is not None and transact.successful:
                gas_used = transact.gas_used
                gas_price = Wad(self.web3.eth.getTransaction(transact.transaction_hash.hex())['gasPrice'])
                tx_fee = Wad.from_number(gas_used) * gas_price

                token_a_balance_after_add = self.get_balance(self.token_a)
                token_a_added = token_a_balance - token_a_balance_after_add
                token_b_balance_after_add = self.get_balance(self.token_b)
                token_b_added = token_b_balance - token_b_balance_after_add

                self.logger.info(f"Real {self.token_a.name} amount added: {token_a_added} "
                                    f"Real {self.token_b.name} amount added: {token_b_added} "
                                    f"tx fee used {tx_fee} "
                                    f"with tx hash {transact.transaction_hash.hex()}")

                if self.uniswap.is_new_pool:
                    self.uniswap.set_and_approve_pair_token(self.uniswap.get_pair_address(self.token_a.address, self.token_b.address))

                return transact
            else:
                self.logger.warning(f"Failed to add liquidity with: {add_liquidity_args}")
        else:
            self.logger.info(f"Not enough tokens to add liquidity or liquidity already added")

    def remove_liquidity(self) -> Optional[Receipt]:
        """ Send an removeLiquidity or removeLiquidityETH transaction to the UniswapV2 Router Contract.

        It is assumed that all liquidity should be removed.

        Returns:
            A Pymaker Receipt object or a None
        """

        liquidity_to_remove = self.uniswap.get_current_liquidity()
        total_liquidity = self.uniswap.get_total_liquidity()
        self.logger.info(f"Current liquidity tokens before removing {liquidity_to_remove} from total liquidity of {total_liquidity}")

        # Store initial balances in order to log state changes resulting from transaction
        token_a_balance = self.get_balance(self.token_a)
        token_b_balance = self.get_balance(self.token_b)
        eth_balance = self.uniswap.get_account_eth_balance()

        amount_a_min = self.uniswap.get_our_exchange_balance(self.token_a, self.uniswap.pair_address)
        amount_b_min = self.uniswap.get_our_exchange_balance(self.token_b, self.uniswap.pair_address)

        remove_liquidity_args = {
            'liquidity': liquidity_to_remove,
            'amountAMin': self.token_a.unnormalize_amount(amount_a_min),
            'amountBMin': self.token_b.unnormalize_amount(amount_b_min)
        }

        if liquidity_to_remove > Wad(0):
            self.logger.debug(f"Removing {remove_liquidity_args} from Uniswap pool {self.uniswap.pair_address}")

            if self.is_eth:
                token = self.token_b if self.eth_position == 0 else self.token_a
                transact = self.uniswap.remove_liquidity_eth(remove_liquidity_args, token, self.eth_position).transact(
                    gas_price=self.gas_price)
            else:
                transact = self.uniswap.remove_liquidity(remove_liquidity_args, self.token_a, self.token_b).transact(
                    gas_price=self.gas_price)

            if transact is not None and transact.successful:
                gas_used = transact.gas_used
                gas_price = Wad(self.web3.eth.getTransaction(transact.transaction_hash.hex())['gasPrice'])
                tx_fee = Wad.from_number(gas_used) * gas_price

                token_a_balance_after_remove = self.get_balance(self.token_a)
                token_a_removed = token_a_balance_after_remove - token_a_balance

                token_b_balance_after_remove = self.get_balance(self.token_b)
                token_b_removed = token_b_balance_after_remove - token_b_balance
                self.logger.info(f"Real {self.token_a.name} amount removed: {token_a_removed} "
                                    f"Real {self.token_b.name} amount removed: {token_b_removed} "
                                    f"tx fee used {tx_fee} "
                                    f"with tx hash {transact.transaction_hash.hex()}")

                return transact
            else:
                self.logger.warning(f"Failed to remove {liquidity_to_remove} liquidity of {self.uniswap.pair_address.address}")
        else:
            self.logger.info(f"No liquidity to remove")


    def check_target_balance(self) -> Tuple[bool, bool]:
        # check current balance, see if its above or below target amounts
        # True results in liquidity removal; False liquidity adding

        # TODO: check to ensure that target amounts aren't breached when creating a new pool
        current_token_a_balance = self.uniswap.get_our_exchange_balance(self.token_a, self.uniswap.pair_address) + self.get_balance(self.token_a)
        current_token_b_balance = self.uniswap.get_our_exchange_balance(self.token_b, self.uniswap.pair_address) + self.get_balance(self.token_b)

        a_exceeds_target_balance = current_token_a_balance >= self.target_a_max_balance
        b_exceeds_target_balance = current_token_b_balance >= self.target_b_max_balance
        a_below_target_balance = current_token_a_balance <= self.target_a_min_balance
        b_below_target_balance = current_token_b_balance <= self.target_b_min_balance

        token_a_should_remove = a_below_target_balance or a_exceeds_target_balance
        token_b_should_remove = b_below_target_balance or b_exceeds_target_balance

        return token_a_should_remove, token_b_should_remove

    def check_price_feed_diverged(self, feed_price: Wad) -> bool:
        diff_up = feed_price * self.accepted_price_slippage_up
        diff_down = feed_price * self.accepted_price_slippage_down
        remove_liquidity = False

        if self.uniswap_current_exchange_price > feed_price:
            remove_liquidity = diff_up < (self.uniswap_current_exchange_price - feed_price)
        elif self.uniswap_current_exchange_price < feed_price:
            remove_liquidity = diff_down < (feed_price - self.uniswap_current_exchange_price) if remove_liquidity == False else True

        return remove_liquidity

    def determine_liquidity_action(self) -> Tuple[bool, bool]:
        """
        Add or remove liquidity depending upon the difference between Uniswap asset pool ratio and our external price feeds.

        Uniswap pricing is expressed as a function of token_a balance / token_b balance in the contract

        This function acts as a state machine that dynamically determines the liquidity actions to take

        First calculate the acceptable price movement limit based upon the
        difference between external price feeds and the accepted slippage.

        If the minimum accepted price difference is less than the distance of uniswaps price from external prices
        add liquidity to the pool, otherwise remove it.
        """
        
        if self.testing_feed_price is False:
            feed_price = (self.price_feed.get_price().buy_price + self.price_feed.get_price().sell_price) / Wad.from_number(2)
        else:
            feed_price = self.test_price

        if feed_price is None:
            self.feed_price_null_counter += 1
            if self.feed_price_null_counter >= self.price_feed_accepted_delay:
                self.logger.warning(f"Price feed has returned null for {self.price_feed_accepted_delay} seconds, removing all available' liquidity")
                self.feed_price_null_counter = 0
                add_liquidity = False
                remove_liquidity = True
                return add_liquidity, remove_liquidity
            return False, False
        else:
            self.feed_price_null_counter = 0

        self.uniswap_current_exchange_price = self.uniswap.get_exchange_rate() if self.uniswap.get_exchange_rate() != Wad.from_number(0) else feed_price

        self.logger.info(f"Feed price: {feed_price} Uniswap price: {self.uniswap_current_exchange_price}")

        control_feed_value = self.control_feed.get()[0]

        token_a_target_amount_breach, token_b_target_amount_breach = self.check_target_balance()
        prices_diverged = self.check_price_feed_diverged(feed_price)

        if token_a_target_amount_breach is True or token_b_target_amount_breach is True:
            self.logger.info(f"Target amounts breached, removing all available liquidity")
            add_liquidity = False
            remove_liquidity = True
            return add_liquidity, remove_liquidity

        elif prices_diverged is True:
            self.logger.info(f"Price feeds have diverged beyond accepted slippage, removing all available liquidity")
            add_liquidity = False
            remove_liquidity = True
            return add_liquidity, remove_liquidity

        elif control_feed_value['canBuy'] is False or control_feed_value['canSell'] is False:
            self.logger.info(f"Control feed instructing to sell, removing all available liquidity")
            add_liquidity = False
            remove_liquidity = True
            return add_liquidity, remove_liquidity

        elif control_feed_value['canBuy'] is True and control_feed_value['canSell'] is True:
            diff_up = feed_price * self.accepted_price_slippage_up
            diff_down = feed_price * self.accepted_price_slippage_down

            add_liquidity = False
            remove_liquidity = False

            # check if external price feed is showing lower prices
            if self.uniswap_current_exchange_price > feed_price:
                add_liquidity = diff_up > (self.uniswap_current_exchange_price - feed_price)
            # check if external price feed is showing higher prices
            elif self.uniswap_current_exchange_price < feed_price:
                add_liquidity = diff_down > (feed_price - self.uniswap_current_exchange_price) if add_liquidity == False else True
            else:
                add_liquidity = True

            return add_liquidity, remove_liquidity

        else:
            self.logger.info(f"No states triggered; Taking no action")
            return False, False

    def place_liquidity(self):
        """
        Main control function of Uniswap Keeper lifecycle. 
        It will determine whether liquidity should be added, or removed
        and then create and submit transactions to the Uniswap Router Contract to update liquidity levels.
        """

        exchange_token_a_balance = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_exchange_balance(self.token_a, self.uniswap.pair_address)
        exchange_token_b_balance = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_exchange_balance(self.token_b, self.uniswap.pair_address)

        self.logger.info(f"Exchange Contract {self.token_a.name} amount: {exchange_token_a_balance}; "
                         f"Exchange Contract {self.token_b.name} amount: {exchange_token_b_balance}")

        add_liquidity, remove_liquidity = self.determine_liquidity_action()

        self.logger.info(f"Add Liquidity: {add_liquidity}; Remove Liquidity: {remove_liquidity}")
        
        if add_liquidity:
            receipt = self.add_liquidity()
            if receipt is not None:
                self.logger.info(f"Current liquidity tokens after adding {self.uniswap.get_current_liquidity()}")

        if remove_liquidity:
            receipt = self.remove_liquidity()
            if receipt is not None:
                self.logger.info(f"Current liquidity tokens after removing {self.uniswap.get_current_liquidity()}")


if __name__ == '__main__':
    UniswapV2MarketMakerKeeper(sys.argv[1:]).main()
