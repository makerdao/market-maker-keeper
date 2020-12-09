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
import sys

from web3 import Web3, HTTPProvider

from pymaker import Address, web3_via_http
from pymaker.gas import FixedGasPrice, DefaultGasPrice
from pymaker.keys import register_keys
from pymaker.oasis import MatchingMarket
from pymaker.util import synchronize


class OasisMarketMakerCancel:
    """Tool to cancel all our open orders on OasisDEX."""

    def __init__(self, args: list, **kwargs):
        parser = argparse.ArgumentParser(prog='oasis-market-maker-cancel')
        parser.add_argument("--endpoint-uri", type=str,
                            help="JSON-RPC uri (example: `http://localhost:8545`)")
        parser.add_argument("--rpc-host", default="localhost", type=str, help="[DEPRECATED] JSON-RPC host (default: `localhost')")
        parser.add_argument("--rpc-port", default=8545, type=int, help="[DEPRECATED] JSON-RPC port (default: `8545')")
        parser.add_argument("--rpc-timeout", help="JSON-RPC timeout (in seconds, default: 10)", default=10, type=int)
        parser.add_argument("--eth-from", help="Ethereum account from which to send transactions", required=True, type=str)
        parser.add_argument("--eth-key", type=str, nargs='*', help="Ethereum private key(s) to use")
        parser.add_argument("--oasis-address", help="Ethereum address of the OasisDEX contract", required=True, type=str)
        parser.add_argument("--gas-price", help="Gas price in Wei (default: node default)", default=0, type=int)
        self.arguments = parser.parse_args(args)

        if 'web3' in kwargs:
            self.web3 = kwargs['web3']
        elif self.arguments.endpoint_uri:
            self.web3: Web3 = web3_via_http(self.arguments.endpoint_uri, self.arguments.rpc_timeout)
        else:
            self.web3 = Web3(HTTPProvider(endpoint_uri=f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}",
                                          request_kwargs={"timeout": self.arguments.rpc_timeout}))

        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        register_keys(self.web3, self.arguments.eth_key)
        self.otc = MatchingMarket(web3=self.web3, address=Address(self.arguments.oasis_address))

        logging.basicConfig(format='%(asctime)-15s %(levelname)-8s %(message)s', level=logging.INFO)

    def main(self):
        self.cancel_orders(self.our_orders(self.otc.get_orders()))

    def our_orders(self, orders: list):
        return list(filter(lambda order: order.maker == self.our_address, orders))

    def cancel_orders(self, orders: list):
        synchronize([self.otc.kill(order.order_id).transact_async(gas_price=self.gas_price()) for order in orders])

    def gas_price(self):
        if self.arguments.gas_price > 0:
            return FixedGasPrice(self.arguments.gas_price)
        else:
            return DefaultGasPrice()


if __name__ == '__main__':
    OasisMarketMakerCancel(sys.argv[1:]).main()
