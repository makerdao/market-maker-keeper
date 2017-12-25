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
import logging
import sys

from web3 import Web3, HTTPProvider

from market_maker_keeper.bibox_api import BiboxApi
from market_maker_keeper.price import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from pymaker import Address
from pymaker.lifecycle import Web3Lifecycle
from pymaker.sai import Tub, Vox


class BiboxMarketMakerKeeper:
    """Keeper acting as a market maker on Bibox, on the ETH/DAI pair."""

    logger = logging.getLogger('bibox-market-maker-keeper')

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='bibox-market-maker-keeper')

        parser.add_argument("--rpc-host", type=str, default="localhost",
                            help="JSON-RPC host (default: `localhost')")

        parser.add_argument("--rpc-port", type=int, default=8545,
                            help="JSON-RPC port (default: `8545')")

        parser.add_argument("--tub-address", type=str, required=True,
                            help="Ethereum address of the Tub contract")

        parser.add_argument("--bibox-api-server", type=str, default="https://api.bibox.com",
                            help="Address of the Bibox API server (default: 'https://api.bibox.com')")

        parser.add_argument("--bibox-api-key", type=str, required=True,
                            help="API key for the Bibox API")

        parser.add_argument("--bibox-secret", type=str, required=True,
                            help="Secret for the Bibox API")

        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

        parser.add_argument("--price-feed", type=str,
                            help="Source of price feed. Tub price feed will be used if not specified")

        parser.add_argument("--debug", dest='debug', action='store_true',
                            help="Enable debug output")

        self.arguments = parser.parse_args(args)

        self.web3 = kwargs['web3'] if 'web3' in kwargs else Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}"))
        self.tub = Tub(web3=self.web3, address=Address(self.arguments.tub_address))
        self.vox = Vox(web3=self.web3, address=self.tub.vox())

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s',
                            level=(logging.DEBUG if self.arguments.debug else logging.INFO))

        self.bands_config = ReloadableConfig(self.arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(self.arguments.price_feed, self.tub, self.vox)

        self.bibox_api = BiboxApi(api_server=self.arguments.bibox_api_server,
                                  api_key=self.arguments.bibox_api_key,
                                  secret=self.arguments.bibox_secret)

    def main(self):
        with Web3Lifecycle(self.web3) as lifecycle:
            self.lifecycle = lifecycle
            lifecycle.on_startup(self.startup)
            lifecycle.every(15*60, self.print_balances)
            lifecycle.on_shutdown(self.shutdown)

    def startup(self):
        user_info = self.bibox_api.user_info()

        self.logger.info("Bibox API key seems to be valid")
        self.logger.info(f"Accessing Bibox as user_id: '{user_info['user_id']}', email: '{user_info['email']}'")

    def shutdown(self):
        pass
        # self.cancel_orders(self.our_orders())

    def print_balances(self):
        pass
        # sai_owned = self.sai.balance_of(self.our_address)
        # weth_owned = self.ether_token.balance_of(self.our_address)
        #
        # self.logger.info(f"Keeper balances are {sai_owned} SAI, {weth_owned} 0x-WETH")


if __name__ == '__main__':
    BiboxMarketMakerKeeper(sys.argv[1:]).main()
