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

from market_maker_keeper.util import setup_logging
from pymaker.lifecycle import Lifecycle
from pymaker.model import Token
from pyexchange.uniswap import UniswapV2
from market_maker_keeper.feed import ExpiringFeed, WebSocketFeed
from web3 import Web3, HTTPProvider
from pymaker.keys import register_keys
from pymaker import Address, Wad
from market_maker_keeper.gas import GasPriceFactory
from market_maker_keeper.uniswap_util import UniswapUtil


class UniswapV2MarketMakerKeeper:
    """Keeper acting as a market maker on UniswapV2.
    Adding or removing liquidity"""

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

        parser.add_argument("--graph-url", type=str, required=True,
                            help="Graph Protocol host")

        parser.add_argument("--pair", type=str, required=True,
                            help="Token pair (sell/buy) on which the keeper will operate")

        parser.add_argument("--token-config", type=str, required=True,
                            help="Token configuration file")

        parser.add_argument("--token-a-address", type=str, required=True,
                            help="Ethereum address of the first token in the pool")

        parser.add_argument("--token-b-address", type=str, required=True,
                            help="Ethereum address of the second token in the pool")

        parser.add_argument("--exchange-address", type=str, required=True,
                            help="Uniswap Exchange address")

        parser.add_argument("--uniswap-feed", type=str, required=True,
                            help="Source of liquidity feed")

        parser.add_argument("--uniswap-feed-expiry", type=int, default=86400,
                            help="Maximum age of the liquidity feed (in seconds, default: 86400)")

        parser.add_argument("--gas-price", type=int, default=0,
                            help="Gas price (in Wei)")

        parser.add_argument("--smart-gas-price", dest='smart_gas_price', action='store_true',
                            help="Use smart gas pricing strategy, based on the ethgasstation.info feed")

        parser.add_argument("--percentage-difference", type=float, default=2,
                            help="Percentage difference between Uniswap exchange rate and aggregated price"
                                 "(default: 2)")

        parser.add_argument("--uniswap-percentage-difference", type=float, default=5,
                            help="Percentage difference between future Uniswap exchange rate and aggregated price"
                                 "(default: 5)")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)
        setup_logging(self.arguments)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                                                              request_kwargs={"timeout": self.arguments.rpc_timeout}))

        self.web3.eth.defaultAccount = self.arguments.eth_from
        register_keys(self.web3, self.arguments.eth_key)

        # TODO: account for reverse ordering of ETH in pair
        token_a_name = 'WETH' if self.pair().split('-')[0] == 'ETH' else self.pair().split('-')[0]
        token_b_name = 'WETH' if self.pair().split('-')[1] == 'ETH' else self.pair().split('-')[1]
        # TODO: add support for reloadable config to provide info on Tokens
        self.token_a = Token(token_a_name, self.arguments.token_a_address, 18)
        self.token_b = Token(token_b_name, self.arguments.token_b_address, 18)

        self.gas_price = GasPriceFactory().create_gas_price(self.arguments)

        self.uniswap = UniswapV2(self.web3, self.arguments.graph_url, self.token_a, self.token_b)
        # self.utils = UniswapUtil(web3=self.web3,
        #                          dai_contract_address='0x09cabEC1eAd1c0Ba254B09efb3EE13841712bE14',
        #                          dai_address='0x89d24A6b4CcB1B6fAA2625fE562bDD9a23260359',
        #                          factory_contract='0xc0a47dFe034B400B47bDaD5FecDa2621de6c4d95')

        # TODO: add approval check

        if self.arguments.uniswap_feed:
            web_socket_feed = WebSocketFeed(self.arguments.uniswap_feed, 5)
            expiring_web_socket_feed = ExpiringFeed(web_socket_feed, self.arguments.uniswap_feed_expiry)

            self.feed = expiring_web_socket_feed

    def main(self):
        with Lifecycle(self.web3) as lifecycle:
            lifecycle.initial_delay(5)
            lifecycle.every(1, self.place_liquidity)

    def pair(self) -> str:
        return self.arguments.pair

    def place_liquidity(self):

        feed_price = self.feed.get()[0]['price']

        uniswap_price = Wad.from_number(1 / self.utils.get_future_price())
        self.logger.info(f"Uniswap future price is {uniswap_price}")

        uniswap_current_exchange_price = self.uniswap.get_exchange_rate()
        uniswap_price_move = abs(uniswap_price - uniswap_current_exchange_price) / uniswap_current_exchange_price

        if uniswap_price_move > Wad.from_number(self.arguments.uniswap_percentage_difference):
            self.logger.info(f"Uniswap price move: {uniswap_price_move}")
            add_liquidity = False
            remove_liquidity = True

            self.logger.info(f"Uniswap price move triggered add liquidity: {add_liquidity}; remove liquidity: {remove_liquidity}")

        else:
            diff = Wad.from_number(feed_price * self.arguments.percentage_difference / 100)
            self.logger.info(f"Feed price: {feed_price} Uniswap price: {uniswap_current_exchange_price} Diff: {diff}")

            add_liquidity = diff > abs(Wad.from_number(feed_price) - uniswap_current_exchange_price)
            remove_liquidity = diff < abs(Wad.from_number(feed_price) - uniswap_current_exchange_price)

            self.logger.info(f"Feed price / Uniswap price diff triggered add liquidity: {add_liquidity}; remove liquidity: {remove_liquidity}")

        token_a_balance = self.uniswap.get_account_token_balance(self.token_a) if not self.token_a.is_eth() else self.uniswap.get_account_eth_balance()
        token_b_balance = self.uniswap.get_account_token_balance(self.token_b) if not self.token_b.is_eth() else self.uniswap.get_account_eth_balance()

        self.logger.info(f"Wallet {self.token_a.name} balance: {token_a_balance}; "
                         f"Wallet {self.token_b.name} balance: {token_b_balance}")
        self.logger.info(f"Exchange Contract ETH amount: {self.uniswap.get_eth_exchange_balance()}; "
                         f"Exchange Contract DAI amount: {self.uniswap.get_exchange_balance()}")

        if add_liquidity:
            eth_balance_no_gas = eth_balance - Wad.from_number(1)
            liquidity_to_add = eth_balance_no_gas

            self.logger.info(f"Wallet liquidity {liquidity_to_add}")
            self.logger.info(f"Calculated liquidity {token_balance / uniswap_current_exchange_price}")
            eth_amount_to_add = min(liquidity_to_add, (token_balance * Wad(95)/Wad(100)) / uniswap_current_exchange_price )

            current_liquidity_tokens = self.uniswap.get_current_liquidity()
            self.logger.info(f"Current liquidity tokens before adding {current_liquidity_tokens}")

            if liquidity_to_add > Wad(0) and current_liquidity_tokens == Wad(0):
                self.logger.info(f"{self.token_address.address} add liquidity of {eth_amount_to_add} ETH amount")
                transact = self.uniswap.add_liquidity(eth_amount_to_add).transact(gas_price=self.gas_price)

                if transact is not None and transact.successful:
                    token_balance_after_add = self.uniswap.get_account_token_balance()
                    eth_balance_after_add = self.uniswap.get_account_eth_balance()
                    gas_used = transact.gas_used
                    gas_price = Wad(self.web3.eth.getTransaction(transact.transaction_hash.hex())['gasPrice'])
                    tx_fee = Wad.from_number(gas_used) * gas_price

                    eth_real_added = eth_balance - eth_balance_after_add - tx_fee
                    token_real_added = token_balance - token_balance_after_add
                    self.logger.info(f"Real Eth amount added {eth_real_added} Real token amount "
                                     f"added {token_real_added} at price {eth_real_added / token_real_added}; "
                                     f"tx fee used {tx_fee}")
                    self.logger.info(f"Successfully added {eth_amount_to_add} liquidity "
                                     f"of {self.token_address.address} with {transact.transaction_hash.hex()}")
                else:
                    self.logger.warning(f"Failed to add {eth_amount_to_add} liquidity of {self.token_address.address}")
            else:
                self.logger.info(f"Not enough tokens to add liquidity")
            self.logger.info(f"Current liquidity tokens after adding {self.uniswap.get_current_liquidity()}")

        if remove_liquidity:
            liquidity_to_remove = self.uniswap.get_current_liquidity()
            self.logger.info(f"Current liquidity tokens before removing {liquidity_to_remove}")

            if liquidity_to_remove > Wad(0):
                self.logger.info(f"Removing {liquidity_to_remove} from Uniswap pool")

                transact = self.uniswap.remove_liquidity(liquidity_to_remove).transact(gas_price=self.gas_price)

                if transact is not None and transact.successful:
                    token_balance_after_remove = self.uniswap.get_account_token_balance()
                    eth_balance_after_remove = self.uniswap.get_account_eth_balance()
                    gas_used = transact.gas_used
                    gas_price = Wad(self.web3.eth.getTransaction(transact.transaction_hash.hex())['gasPrice'])
                    tx_fee = Wad.from_number(gas_used) * gas_price
                    eth_real_removed = eth_balance_after_remove - eth_balance + tx_fee
                    token_real_removed = token_balance_after_remove - token_balance
                    self.logger.info(f"Real Eth amount removed {eth_real_removed} Real token amount "
                                     f"removed {token_real_removed} at price {eth_real_removed / token_real_removed}; "
                                     f"tx fee used {tx_fee}")
                    self.logger.info(f"Removed {liquidity_to_remove} liquidity "
                                     f"of {self.token_address.address} with transaction {transact.transaction_hash.hex()}")
                else:
                    self.logger.warning(f"Failed to remove {liquidity_to_remove} liquidity of {self.token_address.address}")
            else:
                self.logger.info(f"No liquidity to remove")

            self.logger.info(f"Current liquidity tokens after removing {self.uniswap.get_current_liquidity()}")


if __name__ == '__main__':
    UniswapV2MarketMakerKeeper(sys.argv[1:]).main()