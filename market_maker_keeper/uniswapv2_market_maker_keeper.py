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
from market_maker_keeper.util import setup_logging
from pymaker.lifecycle import Lifecycle
from pymaker.model import Token
from pyexchange.uniswapv2 import UniswapV2
from market_maker_keeper.feed import ExpiringFeed, WebSocketFeed
from pymaker.keys import register_keys
from pymaker import Address, Wad, Receipt
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.model import TokenConfig
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig

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

        parser.add_argument("--price-feed-expiry", type=int, default=86400,
                            help="Maximum age of the price feed (in seconds, default: 86400)")

        parser.add_argument("--gas-price", type=int, default=9000000000,
                            help="Gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--initial-exchange-rate", type=float, default=None,
                            help="Used to determine the initial ratio to be used in a newly created pool")

        parser.add_argument("--accepted-slippage", type=float, default=2,
                            help="Percentage difference between Uniswap exchange rate and aggregated price"
                                 "(default: 2)")

        parser.add_argument("--factory-address", type=str,
                            help="Optional address used to test locally deployed contracts")

        parser.add_argument("--router-address", type=str,
                            help="Optional address used to test locally deployed contracts")

        parser.add_argument("--initial-delay", type=int, default=10,
                            help="Initial number of seconds to wait before placing liquidity")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))

        self.web3.eth.defaultAccount = self.arguments.eth_from
        if 'web3' not in kwargs:
            register_keys(self.web3, self.arguments.eth_key)

        # Record if eth is in pair, so can check which liquidity method needs to be used
        self.is_eth = 'ETH' in self.pair()

        # Assume token b is always ETH if ETH is in pair
        token_a_name = self.pair().split('-')[0]
        token_b_name = 'WETH' if self.pair().split('-')[1] == 'ETH' or self.pair().split('-')[1] == 'WETH' else self.pair().split('-')[1]

        self.reloadable_config = ReloadableConfig(self.arguments.token_config)
        self._last_config_dict = None
        self._last_config = None
        token_config = self.get_token_config().tokens

        self.token_a = list(filter(lambda token: token.name == token_a_name, token_config))[0]
        self.token_b = list(filter(lambda token: token.name == token_b_name, token_config))[0]

        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)
        self.initial_exchange_rate = self.arguments.initial_exchange_rate
        self.accepted_slippage = Wad.from_number(self.arguments.accepted_slippage / 100)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.testing_feed_price = None

        # used for local testing
        if self.arguments.factory_address is None and self.arguments.router_address is None:
            self.uniswap = UniswapV2(self.web3, self.token_a, self.token_b)
        else:
            self.uniswap = UniswapV2(self.web3, self.token_a, self.token_b, Address(self.arguments.router_address), Address(self.arguments.factory_address))

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(self.arguments.initial_delay)
            lifecycle.on_startup(self.startup)
            lifecycle.every(10, self.place_liquidity)

    def startup(self):
        self.uniswap.approve(self.token_a)
        self.uniswap.approve(self.token_b)

    def get_token_config(self):
        current_config = self.reloadable_config.get_token_config()
        if current_config != self._last_config_dict:
            self._last_config = TokenConfig(current_config)
            self._last_config_dict = current_config

            self.logger.info(f"Successfully parsed configuration")

        return self._last_config

    def pair(self) -> str:
        return self.arguments.pair

    def _is_weth(self, token: Token) -> bool:
        assert (isinstance(token, Token))

        return True if token.name == 'WETH' else False

    # returns dictionary containing arguments for addLiquidityETH call
    def _calculate_liquidity_eth(self, token_a_balance: Wad, eth_balance: Wad, uniswap_current_exchange_price: Wad, accepted_slippage: Wad) -> Optional[dict]:
        assert (isinstance(token_a_balance, Wad))
        assert (isinstance(eth_balance, Wad))
        assert (isinstance(uniswap_current_exchange_price, Wad))
        assert (isinstance(accepted_slippage, Wad))

        # Subtract Wad.from_number(1) to leave eth for gas
        eth_desired = eth_balance - Wad.from_number(1.0)
        if eth_desired < Wad.from_number(0):
            self.logger.info(f"Insufficient Eth balance.")
            return

        # calculate amount of token desired taking into account equivalent eth should leave some gas
        # ensure there is an equivalent amount available for both balances
        token_desired = min(token_a_balance, eth_desired * uniswap_current_exchange_price)
        amount_token_min = token_desired - (token_desired * accepted_slippage)
        eth_desired = min(eth_desired, token_desired / uniswap_current_exchange_price)
        amount_eth_min = eth_desired - (eth_desired * accepted_slippage)

        add_liquidity_eth_args = {
            'amount_token_desired': self.token_a.unnormalize_amount(token_desired),
            'amount_eth_desired': self.token_b.unnormalize_amount(eth_desired),
            'amount_token_min': self.token_a.unnormalize_amount(amount_token_min),
            'amount_eth_min': self.token_b.unnormalize_amount(amount_eth_min)
        }

        return add_liquidity_eth_args

    # returns dictionary containing arguments for addLiquidity call
    def _calculate_liquidity_tokens(self, token_a_balance: Wad, token_b_balance: Wad, uniswap_current_exchange_price: Wad, accepted_slippage: Wad) -> dict:
        assert (isinstance(token_a_balance, Wad))
        assert (isinstance(token_b_balance, Wad))
        assert (isinstance(uniswap_current_exchange_price, Wad))
        assert (isinstance(accepted_slippage, Wad))

        # Need to calculate the equivalent amount of the other token given available balance and exchange rate
        token_a_to_add = min(token_a_balance, token_b_balance * uniswap_current_exchange_price)
        token_b_to_add = min(token_b_balance, token_a_to_add / Wad.from_number(uniswap_current_exchange_price))
        token_a_to_add = min(token_a_to_add, token_b_to_add * uniswap_current_exchange_price)

        # Use Supplied percentage difference args to calculate min off of available balance + liquidity
        amount_a_min = token_a_to_add - (token_a_to_add * accepted_slippage)
        amount_b_min = token_b_to_add - (token_b_to_add * accepted_slippage)

        add_liquidity_args = {
            'amount_a_desired': self.token_a.unnormalize_amount(token_a_to_add),
            'amount_b_desired': self.token_b.unnormalize_amount(token_b_to_add),
            'amount_a_min': self.token_a.unnormalize_amount(amount_a_min),
            'amount_b_min': self.token_b.unnormalize_amount(amount_b_min)
        }
        return add_liquidity_args

    def add_liquidity(self, uniswap_current_exchange_price: Wad) -> Optional[Receipt]:
        """ Send an addLiquidity or addLiquidityETH transaction to the UniswapV2 Router Contract.

        UniswapV2 differentiates between ETH and ERC20 token transactions.
        All operations are handled in WETH, but the Router Contract handles all wrapping and unwrapping.

        It is assumed that all availability should be added, except for 1 Eth for future Gas operations.

        Given available token balances and an exchange rate, calculations of amounts to add,
        and limits for price movement need to be calculated.

        Args:
            uniswap_current_exchange_price: The current Product of the pool
        Returns:
            A Pymaker Receipt object or a None
        """
        assert (isinstance(uniswap_current_exchange_price, Wad))

        # Variables shared across both eth and token pools
        accepted_slippage = self.accepted_slippage
        token_a_balance = self.uniswap.get_account_token_balance(self.token_a) if not self._is_weth(self.token_a) else self.uniswap.get_account_eth_balance()
        token_b_balance = self.uniswap.get_account_token_balance(self.token_b) if not self._is_weth(self.token_b) else self.uniswap.get_account_eth_balance()

        self.logger.info(f"Wallet {self.token_a.name} balance: {token_a_balance}; "
                         f"Wallet {self.token_b.name} balance: {token_b_balance}")

        if not self.is_eth:
            add_liquidity_args = self._calculate_liquidity_tokens(token_a_balance, token_b_balance, uniswap_current_exchange_price, accepted_slippage)
            self.logger.info(f"Token Pair liquidity to add: {add_liquidity_args}")

        if self.is_eth:
            add_liquidity_eth_args = self._calculate_liquidity_eth(token_a_balance, token_b_balance, uniswap_current_exchange_price, accepted_slippage)
            if add_liquidity_eth_args is None:
                return
            self.logger.info(f"ETH Pair liquidity to add: {add_liquidity_eth_args}")

        current_liquidity_tokens = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_current_liquidity()
        self.logger.info(f"Current liquidity tokens before adding: {current_liquidity_tokens}")

        dict_to_check = add_liquidity_eth_args if self.is_eth else add_liquidity_args
        is_liquidity_to_add_positive = all(map(lambda x: x > Wad(0), dict_to_check.values()))
        if is_liquidity_to_add_positive and current_liquidity_tokens == Wad(0):
            if self.is_eth:
                self.logger.info(
                    f"Add {self.token_a.name} liquidity of amount: {self.token_a.normalize_amount(add_liquidity_eth_args['amount_token_desired'])}")
                self.logger.info(
                    f"Add {self.token_b.name} liquidity of: {add_liquidity_eth_args['amount_eth_desired']}")

                transact = self.uniswap.add_liquidity_eth(add_liquidity_eth_args, self.token_a).transact(
                    gas_price=self.gas_price)
            else:
                self.logger.info(
                    f"Add {self.token_a.name} liquidity of amount: {self.token_a.normalize_amount(add_liquidity_args['amount_a_desired'])}")
                self.logger.info(
                    f"Add {self.token_b.name} liquidity of: {self.token_b.normalize_amount(add_liquidity_args['amount_b_desired'])}")
                transact = self.uniswap.add_liquidity(add_liquidity_args, self.token_a, self.token_b).transact(
                    gas_price=self.gas_price)

            if transact is not None and transact.successful:
                gas_used = transact.gas_used
                gas_price = Wad(self.web3.eth.getTransaction(transact.transaction_hash.hex())['gasPrice'])
                tx_fee = Wad.from_number(gas_used) * gas_price

                if self.is_eth:
                    eth_balance_after_add = self.uniswap.get_account_eth_balance()
                    eth_real_added = token_b_balance - eth_balance_after_add - tx_fee
                    self.logger.info(f"Real Eth amount added {eth_real_added} Real token amount "
                                     f"added {add_liquidity_eth_args['amount_token_desired']} at price {eth_real_added / add_liquidity_eth_args['amount_token_desired']}; " 
                                     f"tx fee used {tx_fee} "
                                     f"with tx hash {transact.transaction_hash.hex()}")
                else:
                    token_a_balance_after_add = self.uniswap.get_account_token_balance(self.token_a)
                    token_a_added = token_a_balance - token_a_balance_after_add
                    token_b_balance_after_add = self.uniswap.get_account_token_balance(self.token_b)
                    token_b_added = token_b_balance - token_b_balance_after_add

                    self.logger.info(f"Real {self.token_a.name} amount added: {token_a_added} "
                                     f"Real {self.token_b.name} amount added: {token_b_added} "
                                     f"tx fee used {tx_fee} "
                                     f"with tx hash {transact.transaction_hash.hex()}")

                if self.uniswap.is_new_pool:
                    self.uniswap.set_and_approve_pair_token(self.uniswap.get_pair_address(self.token_a.address, self.token_b.address))
                    self.initial_exchange_rate = None

                return transact
            else:
                if self.is_eth:
                    self.logger.warning(f"Failed to add liquidity with: {add_liquidity_eth_args}")
                else:
                    self.logger.warning(f"Failed to add liquidity with: {add_liquidity_args}")
        else:
            self.logger.info(f"Not enough tokens to add liquidity or liquidity already added")

    def remove_liquidity(self, exchange_token_a_balance: Wad, exchange_token_b_balance: Wad) -> Optional[Receipt]:
        """ Send an removeLiquidity or removeLiquidityETH transaction to the UniswapV2 Router Contract.

        It is assumed that all liquidity should be removed.

        Args:
            exchange_token_a_balance:
            exchange_token_b_balance:
        Returns:
            A Pymaker Receipt object or a None
        """
        assert (isinstance(exchange_token_a_balance, Wad))
        assert (isinstance(exchange_token_b_balance, Wad))

        liquidity_to_remove = self.uniswap.get_current_liquidity()
        total_liquidity = self.uniswap.get_total_liquidity()

        self.logger.info(f"Current liquidity tokens before removing {liquidity_to_remove} from total liquidity of {total_liquidity}")

        # Store initial balances in order to log state changes resulting from transaction
        token_a_balance = self.uniswap.get_account_token_balance(self.token_a) if not self._is_weth(self.token_a) else self.uniswap.get_account_eth_balance()
        token_b_balance = self.uniswap.get_account_token_balance(self.token_b) if not self._is_weth(self.token_b) else self.uniswap.get_account_eth_balance()
        eth_balance = self.uniswap.get_account_eth_balance()

        amount_a_min = self.token_a.unnormalize_amount(liquidity_to_remove * exchange_token_a_balance / total_liquidity)
        amount_b_min = self.token_b.unnormalize_amount(liquidity_to_remove * exchange_token_b_balance / total_liquidity)

        remove_liquidity_args = {
            'liquidity': liquidity_to_remove,
            'amountAMin': amount_a_min,
            'amountBMin': amount_b_min
        }

        remove_liquidity_eth_args = {
            'liquidity': liquidity_to_remove,
            'amountTokenMin': amount_a_min,
            'amountETHMin': amount_b_min
        }

        if liquidity_to_remove > Wad(0):
            self.logger.info(f"Removing {liquidity_to_remove} from Uniswap pool {self.uniswap.pair_address}")

            if self.is_eth:
                transact = self.uniswap.remove_liquidity_eth(remove_liquidity_eth_args, self.token_a).transact(
                    gas_price=self.gas_price)
            else:
                transact = self.uniswap.remove_liquidity(remove_liquidity_args, self.token_a, self.token_b).transact(
                    gas_price=self.gas_price)

            if transact is not None and transact.successful:
                gas_used = transact.gas_used
                gas_price = Wad(self.web3.eth.getTransaction(transact.transaction_hash.hex())['gasPrice'])
                tx_fee = Wad.from_number(gas_used) * gas_price

                token_a_balance_after_remove = self.uniswap.get_account_token_balance(self.token_a)
                token_a_removed = token_a_balance_after_remove - token_a_balance
                if self.is_eth:
                    eth_balance_after_remove = self.uniswap.get_account_eth_balance()
                    eth_real_added = eth_balance_after_remove - eth_balance + tx_fee
                    self.logger.info(f"Real Eth amount removed {eth_real_added} Real token amount "
                                     f"removed {token_a_removed} at price {eth_real_added / token_a_removed}; "
                                     f"tx fee used {tx_fee}"
                                     f"with tx hash {transact.transaction_hash.hex()}")
                else:
                    token_b_balance_after_remove = self.uniswap.get_account_token_balance(self.token_b)
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

    def determine_liquidity_action(self, uniswap_current_exchange_price: Wad) -> Optional[Tuple]:
        assert isinstance(uniswap_current_exchange_price, Wad)

        feed_price = self.price_feed.get_price().buy_price if self.testing_feed_price is None else self.testing_feed_price

        self.logger.info(f"Feed price: {feed_price} Uniswap price: {uniswap_current_exchange_price}")

        # calculate the acceptable price movement limit based upon the
        # difference between external price feeds and the accepted slippage
        diff = feed_price * self.accepted_slippage

        print("diff", diff, feed_price, uniswap_current_exchange_price,
              Wad.from_number(feed_price) - uniswap_current_exchange_price)
        # if the accepted difference is greater than the distance of uniswaps price from external prices
        # add liquidity to the pool, otherwise remove it
        add_liquidity = diff > abs(Wad.from_number(feed_price) - uniswap_current_exchange_price)
        remove_liquidity = diff < abs(Wad.from_number(feed_price) - uniswap_current_exchange_price)
        print(add_liquidity, remove_liquidity)
        self.logger.info(
            f"Feed price / Uniswap price diff {diff} triggered add liquidity: {add_liquidity}; remove liquidity: {remove_liquidity}")

        # TODO: programmatically determine limit
        if diff < Wad.from_number(.000000001) and feed_price is not None:
            self.logger.info(f"Price moves are minimal; maintaining existing liquidity")
            return None, None

        if feed_price is None:
            self.logger.warning(f"Price feed is returning null, removing all available liquidity")
            add_liquidity = False
            remove_liquidity = True

        return add_liquidity, remove_liquidity

    def place_liquidity(self):
        """
        Add or remove liquidity depending upon the difference between Uniswap ratio and our external price feeds.

        Pricing is expressed as a function of token_a balance / token_b balance
        """

        uniswap_current_exchange_price = Wad.from_number(self.initial_exchange_rate) if self.initial_exchange_rate is not None else self.uniswap.get_exchange_rate()

        exchange_token_a_balance = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_exchange_balance(self.token_a, self.uniswap.pair_address)
        exchange_token_b_balance = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_exchange_balance(self.token_b, self.uniswap.pair_address)

        self.logger.info(f"Exchange Contract {self.token_a.name} amount: {exchange_token_a_balance}; "
                         f"Exchange Contract {self.token_b.name} amount: {exchange_token_b_balance}")

        add_liquidity, remove_liquidity = self.determine_liquidity_action(uniswap_current_exchange_price)

        if add_liquidity:
            receipt = self.add_liquidity(uniswap_current_exchange_price)
            if receipt is not None:
                self.logger.info(f"Current liquidity tokens after adding {self.uniswap.get_current_liquidity()}")

        if remove_liquidity:
            receipt = self.remove_liquidity(exchange_token_a_balance, exchange_token_b_balance)
            if receipt is not None:
                self.logger.info(f"Current liquidity tokens after removing {self.uniswap.get_current_liquidity()}")


if __name__ == '__main__':
    UniswapV2MarketMakerKeeper(sys.argv[1:]).main()