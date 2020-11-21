# This file is part of Maker Keeper Framework.
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

from argparse import Namespace
from web3 import Web3, HTTPProvider

from market_maker_keeper.band import Bands
from market_maker_keeper.control_feed import create_control_feed
from market_maker_keeper.limit import History
from market_maker_keeper.order_book import OrderBookManager
from market_maker_keeper.order_history_reporter import create_order_history_reporter
from market_maker_keeper.price_feed import PriceFeedFactory
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.spread_feed import create_spread_feed
from market_maker_keeper.util import setup_logging

from pymaker import Address, get_pending_transactions
from pymaker.lifecycle import Lifecycle
from pymaker.numeric import Wad
from pyexchange.api import PyexAPI
from pymaker.token import ERC20Token
from pymaker.keys import register_keys, _registered_accounts

class DEXKeeperAPI:
    """
    Define a common abstract API for keepers on decentralized exchanges
    """

    def __init__(self, arguments: Namespace, pyex_api: PyexAPI):

        setup_logging(arguments)

        if arguments.__contains__('web3'):
            self.web3 = arguments.web3
        else:
            web3_endpoint = f"http://{self.arguments.rpc_host}:{self.arguments.rpc_port}"
            web3_options = {"timeout": self.arguments.rpc_timeout}
            self.web3 = Web3(HTTPProvider(endpoint_uri=web3_endpoint, request_kwargs=web3_options))

        self.web3.eth.defaultAccount = self.arguments.eth_from
        self.our_address = Address(self.arguments.eth_from)
        register_keys(self.web3, self.arguments.eth_key)

        self.bands_config = ReloadableConfig(arguments.config)
        self.price_feed = PriceFeedFactory().create_price_feed(arguments)
        self.spread_feed = create_spread_feed(arguments)
        self.control_feed = create_control_feed(arguments)

        self.order_history_reporter = create_order_history_reporter(arguments)

        self.history = History()

        self.init_order_book_manager(arguments, pyex_api)

    def init_order_book_manager(self, arguments: Namespace, pyex_api: PyexAPI):
        self.order_book_manager = OrderBookManager(refresh_frequency=arguments.refresh_frequency)
        self.order_book_manager.get_orders_with(lambda: pyex_api.get_orders(self.pair()))
        self.order_book_manager.get_balances_with(lambda: pyex_api.get_balances())
        self.order_book_manager.cancel_orders_with(lambda order: pyex_api.cancel_order(order.order_id))
        self.order_book_manager.enable_history_reporting(self.order_history_reporter, self.our_buy_orders,
                                                        self.our_sell_orders)
        self.order_book_manager.start()

    def main(self):
        with Lifecycle() as lifecycle:
            lifecycle.initial_delay(10)

            if self.is_zrx:
                lifecycle.on_startup(self.startup)

            lifecycle.every(1, self.synchronize_orders)
            lifecycle.on_shutdown(self.shutdown)

    def plunge(self):
        """
        Method to automatically plunge any pending transactions on keeper startup
        """
        pending_txes = get_pending_transactions(self.web3, self.our_address)
        logging.info(f"There are {len(pending_txes)} pending transactions in the queue")
        if len(pending_txes) > 0:
            if not self.is_unlocked():
                logging.warning(f"{len(pending_txes)} transactions are pending")
                return
            for index, tx in enumerate(pending_txes):
                logging.warning(f"Cancelling {index+1} of {len(pending_txes)} pending transactions")
                # Note this can raise a "Transaction nonce is too low" error, stopping the service.
                # This means one of the pending TXes was mined, and the service can be restarted to either resume
                # plunging or normal operation.
                tx.cancel(gas_price=self.gas_price)

    def is_unlocked(self) -> bool:
        return (self.web3, self.our_address) in _registered_accounts

    def startup(self):
        self.approve()

    def shutdown(self):
        self.order_book_manager.cancel_all_orders()

    def approve(self):
        raise NotImplementedError()

    # Each exchange takes pair input as a different format
    def pair(self):
        raise NotImplementedError()

    def token_sell(self) -> str:
        raise NotImplementedError()

    def token_buy(self) -> str:
        raise NotImplementedError()

    # Different keys are used to access balance object for different exchanges
    def our_available_balance(self, our_balances: dict, token: str) -> Wad:
        raise NotImplementedError()

    def our_sell_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: order.is_sell, our_orders))

    def our_buy_orders(self, our_orders: list) -> list:
        return list(filter(lambda order: not order.is_sell, our_orders))

    def synchronize_orders(self):
        raise NotImplementedError()

    def place_orders(self, new_orders: list):
        raise NotImplementedError()
