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
from pymaker.model import Token, TokenConfig
from pymaker import Address, get_pending_transactions, Wad, Receipt, web3_via_http
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.gas import add_gas_arguments, GasPriceFactory
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging
from market_maker_keeper.staking_rewards_factory import StakingRewardsFactory, StakingRewardsName


class UniswapV2MarketMakerKeeper:
    """Keeper acting as a market maker on UniswapV2.

    Adding or removing liquidity to arbitrary ETH-ERC20 or ERC20-ERC20 Pools.
    If one of the assets is ETH, it is assumed that it will be the second asset (token_b)

    """

    logger = logging.getLogger()

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='uniswap-market-maker-keeper')

        parser.add_argument("--endpoint-uri", type=str, default="http://localhost:8545",
                            help="JSON-RPC uri (default: `http://localhost:8545`)")

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

        parser.add_argument('--staking-rewards-name', type=StakingRewardsName, choices=StakingRewardsName,
                            help="Name of contract to stake liquidity tokens with")

        parser.add_argument("--staking-rewards-contract-address", type=str,
                            help="Address of contract to stake liquidity tokens with")

        parser.add_argument("--staking-rewards-target-reward-amount", type=float,
                            help="Address of contract to stake liquidity tokens with")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        add_gas_arguments(parser)
        self.arguments = parser.parse_args(args)

        setup_logging(self.arguments)

        self.web3: Web3 = web3_via_http(self.arguments.endpoint_uri, self.arguments.rpc_timeout)
        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.web3.eth.defaultAccount)
        if 'web3' not in kwargs:
            register_keys(self.web3, self.arguments.eth_key)

        self.gas_price = GasPriceFactory().create_gas_price(self.web3, self.arguments)

        # TODO: Add a more sophisticated regex for different variants of eth on the exchange
        # Record if eth is in pair, so can check which liquidity method needs to be used
        self.is_eth = 'ETH' in self.pair()

        # Identify which token is ETH, so we can provide the arguments to Uniswap Router in expected order
        self.eth_position = 1
        if self.is_eth:
            self.eth_position = 0 if self.pair().split('-')[0] == 'ETH' else 1

        self.reloadable_config = ReloadableConfig(self.arguments.token_config)
        self._last_config_dict = None
        self._last_config = None
        self.token_config = self.get_token_config().token_config

        self.token_a, self.token_b = self.instantiate_tokens(self.pair())

        self.uniswap = UniswapV2(self.web3, self.token_a, self.token_b, self.our_address, Address(self.arguments.router_address), Address(self.arguments.factory_address))

        # instantiate specific StakingRewards depending on arguments
        self.staking_rewards = StakingRewardsFactory().create_staking_rewards(self.arguments, self.web3)
        self.staking_rewards_target_reward_amount = self.arguments.staking_rewards_target_reward_amount

        # configure price feed
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments)
        self.price_feed_accepted_delay = self.arguments.price_feed_accepted_delay
        self.control_feed = create_control_feed(self.arguments)
        self.spread_feed = create_spread_feed(self.arguments)
        self.feed_price_null_counter = 0

        # testing_feed_price is used by the integration tests in tests/test_uniswapv2.py, to test different pricing scenarios
        # as the keeper consistently checks the price, some long running state variable is needed to
        self.testing_feed_price = False
        self.test_price = Wad.from_number(0)

        # initalize uniswap price
        self.uniswap_current_exchange_price = self.uniswap.get_exchange_rate()

        # set target min and max amounts for each side of the pair
        # balance doesnt exceed some level, as an effective stop loss against impermanent loss
        self.target_a_min_balance = Wad.from_number(self.arguments.target_a_min_balance)
        self.target_a_max_balance = Wad.from_number(self.arguments.target_a_max_balance)
        self.target_b_min_balance = Wad.from_number(self.arguments.target_b_min_balance)
        self.target_b_max_balance = Wad.from_number(self.arguments.target_b_max_balance)

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
        self.plunge()
        self.uniswap.approve(self.token_a)
        self.uniswap.approve(self.token_b)

    def shutdown(self):
        self.logger.info(f"Shutdown notification received, removing all available liquidity")
        self.remove_liquidity(True)

    def plunge(self):
        """
        Method to automatically plunge any pending transactions on keeper startup
        """

        pending_txes = get_pending_transactions(self.web3, self.our_address)
        self.logger.info(f"There are {len(pending_txes)} pending transactions in the queue")
        if len(pending_txes) > 0:
            for index, tx in enumerate(pending_txes):
                self.logger.warning(f"Cancelling {index+1} of {len(pending_txes)} pending transactions")
                # Note this can raise a "Transaction nonce is too low" error, stopping the service.
                # This means one of the pending TXes was mined, and the service can be restarted to either resume
                # plunging or normal operation.
                tx.cancel(gas_price=self.gas_price)

    def get_token_config(self):
        current_config = self.reloadable_config.get_token_config()
        if current_config != self._last_config_dict:
            self._last_config = TokenConfig(current_config)
            self._last_config_dict = current_config

            self.logger.info(f"Successfully parsed configuration")

        return self._last_config

    def instantiate_tokens(self, pair: str) -> Tuple[Token, Token]:
        assert (isinstance(pair, str))

        def get_address(value) -> Address:
            return Address(value['tokenAddress']) if 'tokenAddress' in value else None

        def get_decimals(value) -> int:
            return value['tokenDecimals'] if 'tokenDecimals' in value else 18

        token_a_name = 'WETH' if self.is_eth and self.eth_position == 0 else self.pair().split('-')[0]
        token_b_name = 'WETH' if self.is_eth and self.eth_position == 1 else self.pair().split('-')[1]

        token_a = Token(token_a_name, get_address(self.token_config[token_a_name]), get_decimals(self.token_config[token_a_name]))
        token_b = Token(token_b_name, get_address(self.token_config[token_b_name]), get_decimals(self.token_config[token_b_name]))

        return token_a, token_b

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

    def add_liquidity(self, should_stake: bool) -> Optional[Wad]:
        """ Send an addLiquidity or addLiquidityETH transaction to the UniswapV2 Router Contract.

        UniswapV2 differentiates between ETH and ERC20 token transactions.
        All operations are handled in WETH, but the Router Contract handles all wrapping and unwrapping.

        It is assumed that all availability should be added, except for 1 Eth for future Gas operations.

        Given available token balances and an exchange rate, calculations of amounts to add,
        and limits for price movement need to be calculated.

        Returns:
            A Wad representing our added liquidity_tokens
        """
        assert (isinstance(should_stake, bool))

        token_a_balance = self.get_balance(self.token_a)
        token_b_balance = self.get_balance(self.token_b)

        self.logger.info(f"Wallet {self.token_a.name} balance: {token_a_balance}; "
                         f"Wallet {self.token_b.name} balance: {token_b_balance}")

        add_liquidity_args = self.calculate_liquidity_args(token_a_balance, token_b_balance)
        self.logger.debug(f"Pair liquidity to add: {add_liquidity_args}")

        current_liquidity_tokens = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_current_liquidity()
        self.logger.info(f"Current liquidity tokens before adding: {current_liquidity_tokens}")

        staked_liquidity_tokens = self.staking_rewards.balance_of() if self.staking_rewards is not None else Wad(0)

        if add_liquidity_args is None:
            return None

        is_liquidity_to_add_positive = all(map(lambda x: x > Wad(0), add_liquidity_args.values()))
        if is_liquidity_to_add_positive and current_liquidity_tokens == Wad(0) and staked_liquidity_tokens == Wad(0):
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
                    self.uniswap.set_pair_token(self.uniswap.get_pair_address(self.token_a.address, self.token_b.address))

                liquidity_tokens = self.uniswap.get_current_liquidity()

                if should_stake:
                    self.stake_liquidity(liquidity_tokens)

                return liquidity_tokens
            else:
                self.logger.warning(f"Failed to add liquidity with: {add_liquidity_args}")
        else:
            self.logger.info(f"Not enough tokens to add liquidity or liquidity already added")

    def remove_liquidity(self, should_unstake: bool) -> Optional[Wad]:
        """ Send an removeLiquidity or removeLiquidityETH transaction to the UniswapV2 Router Contract.

        It is assumed that all liquidity should be removed.

        Returns:
            A Liquidity Token amount Wad  or a None
        """
        assert (isinstance(should_unstake, bool))

        # Store initial balances in order to log state changes resulting from transaction
        token_a_balance = self.get_balance(self.token_a)
        token_b_balance = self.get_balance(self.token_b)

        if should_unstake and self.staking_rewards:
            unstake_receipt = self.unstake_liquidity()
            if unstake_receipt is None:
                return None

        a_exchange_balance = self.uniswap.get_our_exchange_balance(self.token_a, self.uniswap.pair_address)
        b_exchange_balance = self.uniswap.get_our_exchange_balance(self.token_b, self.uniswap.pair_address)
        self.logger.info(f"exchange balance before removing {self.token_a.name}: {a_exchange_balance} {self.token_b.name}: {b_exchange_balance}")

        liquidity_to_remove = self.uniswap.get_current_liquidity()
        total_liquidity = self.uniswap.get_total_liquidity()
        self.logger.info(f"Current liquidity tokens before removing {liquidity_to_remove} from total liquidity of {total_liquidity}")

        remove_liquidity_args = {
            'liquidity': liquidity_to_remove,
            'amountAMin': Wad(0),
            'amountBMin': Wad(0)
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

                return liquidity_to_remove
            else:
                self.logger.warning(f"Failed to remove {liquidity_to_remove} liquidity of {self.uniswap.pair_address.address}")
        else:
            self.logger.info(f"No liquidity to remove")

    def stake_liquidity(self, liquidity_tokens) -> Optional[Receipt]:
        self.staking_rewards.approve(self.uniswap.pair_address)
        staking_receipt = self.staking_rewards.stake_liquidity(liquidity_tokens).transact(gas_price=self.gas_price)

        if staking_receipt is not None and staking_receipt.successful:
            gas_used = staking_receipt.gas_used
            gas_price = Wad(self.web3.eth.getTransaction(staking_receipt.transaction_hash.hex())['gasPrice'])
            tx_fee = Wad.from_number(gas_used) * gas_price

            self.logger.info(f"Staked {liquidity_tokens} liquidity tokens "
                                f"tx fee used {tx_fee} "
                                f"with tx hash {staking_receipt.transaction_hash.hex()}")
            return staking_receipt
        else:
            self.logger.error(f"Unable to stake liquidity tokens")
            return None

    def unstake_liquidity(self) -> Optional[Receipt]:
        staking_receipt = self.staking_rewards.withdraw_all_liquidity().transact(gas_price=self.gas_price)

        if staking_receipt is not None and staking_receipt.successful:
            gas_used = staking_receipt.gas_used
            gas_price = Wad(self.web3.eth.getTransaction(staking_receipt.transaction_hash.hex())['gasPrice'])
            tx_fee = Wad.from_number(gas_used) * gas_price

            self.logger.info(f"Withdrew all staked liquidity tokens "
                                f"tx fee used {tx_fee} "
                                f"with tx hash {staking_receipt.transaction_hash.hex()}")

            return staking_receipt
        else:
            self.logger.error(f"Unable to unstake liquidity tokens")
            return None

    def check_target_balance(self) -> bool:
        """
        Check current balance, see if its above or below target amounts. True results in liquidity removal; False liquidity addition or maintenance

        If staking_rewards is enabled, determine current liquidity holdings by querying the StakingRewards contract.
        """

        if self.staking_rewards:
            staked_tokens = self.staking_rewards.balance_of()

            if staked_tokens > Wad(0):
                total_liquidity = self.uniswap.get_total_liquidity()

                # Use staked_tokens to determine our portion of each side of the pool's reserves
                exchange_balance_a = staked_tokens * self.uniswap.get_exchange_balance(self.token_a, self.uniswap.pair_address) / total_liquidity
                exchange_balance_b = staked_tokens * self.uniswap.get_exchange_balance(self.token_b, self.uniswap.pair_address) / total_liquidity

                # Add account balance to pool balance
                current_token_a_balance = exchange_balance_a + self.get_balance(self.token_a)
                current_token_b_balance = exchange_balance_b + self.get_balance(self.token_b)
            else:
                current_token_a_balance = self.uniswap.get_our_exchange_balance(self.token_a, self.uniswap.pair_address) + self.get_balance(self.token_a)
                current_token_b_balance = self.uniswap.get_our_exchange_balance(self.token_b, self.uniswap.pair_address) + self.get_balance(self.token_b)
        else:
            current_token_a_balance = self.uniswap.get_our_exchange_balance(self.token_a, self.uniswap.pair_address) + self.get_balance(self.token_a)
            current_token_b_balance = self.uniswap.get_our_exchange_balance(self.token_b, self.uniswap.pair_address) + self.get_balance(self.token_b)

        if current_token_a_balance >= self.target_a_max_balance:
            self.logger.info(f"Keeper token A balance of {current_token_a_balance} exceeds max target balance of {self.target_a_max_balance}")
            return True
        elif current_token_b_balance >= self.target_b_max_balance:
            self.logger.info(f"Keeper token B balance of {current_token_b_balance} exceeds max target balance of {self.target_b_max_balance}")
            return True
        elif current_token_a_balance <= self.target_a_min_balance:
            self.logger.info(f"Keeper token A balance of {current_token_a_balance} is less than min target balance of {self.target_a_min_balance}")
            return True
        elif current_token_b_balance <= self.target_b_min_balance:
            self.logger.info(f"Keeper token B balance of {current_token_b_balance} is less than min target balance of {self.target_b_min_balance}")
            return True
        else:
            return False

    def check_prices(self, feed_price: Wad) -> Tuple[bool, bool]:
        # determine maximum accepted price difference up and down
        accepted_diff_up = feed_price * self.accepted_price_slippage_up
        accepted_diff_down = feed_price * self.accepted_price_slippage_down

        add_liquidity = False
        remove_liquidity = False

        # Check if external price feed has diverged above or below the Uniswap Price.
        # If the price has diverged, only add liquidity if the divergence is less than the maxmimum accepted
        # Remove liquidity if prices have diverged beyond maximum accepted
        if self.uniswap_current_exchange_price > feed_price:
            add_liquidity = accepted_diff_up > (self.uniswap_current_exchange_price - feed_price)
            remove_liquidity = accepted_diff_up < (self.uniswap_current_exchange_price - feed_price)
        elif self.uniswap_current_exchange_price < feed_price:
            add_liquidity = accepted_diff_down > (feed_price - self.uniswap_current_exchange_price)
            remove_liquidity = accepted_diff_down < (feed_price - self.uniswap_current_exchange_price) if remove_liquidity == False else True
        else:
            # prices match, add liquidity
            add_liquidity = True

        if remove_liquidity:
            self.logger.warning(f"Price feeds have diverged beyond accepted slippage, removing all available liquidity")

        return add_liquidity, remove_liquidity

    def determine_liquidity_action(self) -> Tuple[bool, bool]:
        """
        Add or remove liquidity depending upon the difference between Uniswap asset pool ratio and our external price feeds.

        Uniswap pricing is expressed as a function of token_a balance / token_b balance in the contract

        This function acts as a state machine that dynamically determines the liquidity actions to take

        First calculate the acceptable price movement limit based upon the
        difference between external price feeds and the accepted slippage.

        If Uniswap's price difference from external prices is less than the maximum accepted price difference (diff_up | diff_down)
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

        target_amounts_breached = self.check_target_balance()
        control_feed_value = self.control_feed.get()[0]

        if target_amounts_breached:
            self.logger.info(f"Target amounts breached, removing all available liquidity")
            add_liquidity = False
            remove_liquidity = True
            return add_liquidity, remove_liquidity

        elif control_feed_value['canBuy'] is False or control_feed_value['canSell'] is False:
            self.logger.info(f"Control feed instructing to stop trading, removing all available liquidity")
            add_liquidity = False
            remove_liquidity = True
            return add_liquidity, remove_liquidity

        elif control_feed_value['canBuy'] is True and control_feed_value['canSell'] is True:
            add_liquidity, remove_liquidity = self.check_prices(feed_price)
            return add_liquidity, remove_liquidity

        else:
            self.logger.info(f"No states triggered; Taking no action")
            return False, False

    def determine_staking_action(self, should_remove_liquidity: bool) -> Tuple[bool, bool]:
        """
            Determine whether to stake, withdraw, or maintain liquidity token staking operations.
            Returns [should_stake: bool, should_unstake: bool]
        """
        if self.staking_rewards:

            current_staked_tokens = self.staking_rewards.balance_of()
            current_staking_rewards = self.staking_rewards.earned()

            if current_staked_tokens == Wad(0) and not should_remove_liquidity:
                return True, False
            elif current_staked_tokens > Wad(0) and should_remove_liquidity:
                return False, True
            elif self.staking_rewards_target_reward_amount is not None:
                if current_staking_rewards > Wad.from_number(self.staking_rewards_target_reward_amount):
                    return False, True
                return False, False
            else:
                return False, False
        else:
            return False, False

    def place_liquidity(self) -> Optional[Wad]:
        """
        Main control function of Uniswap Keeper lifecycle.
        It will determine whether liquidity should be added, or removed
        and then create and submit transactions to the Uniswap Router Contract to update liquidity levels.

        It will return the liquidity_tokens minted, burned, staked, or unstaked.
        """

        exchange_token_a_balance = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_exchange_balance(self.token_a, self.uniswap.pair_address)
        exchange_token_b_balance = Wad.from_number(0) if self.uniswap.is_new_pool else self.uniswap.get_exchange_balance(self.token_b, self.uniswap.pair_address)

        self.logger.info(f"Exchange Contract {self.token_a.name} amount: {exchange_token_a_balance}; "
                         f"Exchange Contract {self.token_b.name} amount: {exchange_token_b_balance}")

        add_liquidity, remove_liquidity = self.determine_liquidity_action()
        self.logger.info(f"Add Liquidity: {add_liquidity}; Remove Liquidity: {remove_liquidity}")

        should_stake, should_unstake = self.determine_staking_action(remove_liquidity)
        self.logger.info(f"Should Stake Liquidity: {should_stake}; Should Unstake Liquidity: {should_unstake}")

        if add_liquidity:
            liquidity_tokens = self.add_liquidity(should_stake)
            if liquidity_tokens is not None:
                self.logger.info(f"Current liquidity tokens after adding {self.uniswap.get_current_liquidity()}")
                return liquidity_tokens

        if remove_liquidity:
            liquidity_tokens = self.remove_liquidity(should_unstake)
            if liquidity_tokens is not None:
                self.logger.info(f"Current liquidity tokens after removing {self.uniswap.get_current_liquidity()}")
                return liquidity_tokens

        if should_stake:
            stake_receipt = self.stake_liquidity(self.uniswap.get_current_liquidity())
            if stake_receipt is not None:
                staked_balance = self.staking_rewards.balance_of()
                self.logger.info(f"Staked {staked_balance} liquidity tokens")
                return staked_balance

        if should_unstake:
            unstake_receipt = self.unstake_liquidity()
            if unstake_receipt is not None:
                current_liquidity = self.uniswap.get_current_liquidity()
                self.logger.info(f"Unstaked {current_liquidity} liquidity tokens")
                return current_liquidity

if __name__ == '__main__':
    UniswapV2MarketMakerKeeper(sys.argv[1:]).main()
