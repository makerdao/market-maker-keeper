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

import shutil
from functools import reduce

import py
import pytest
from mock import MagicMock

from market_maker_keeper.etherdelta_market_maker_keeper import EtherDeltaMarketMakerKeeper
from pymaker import Address
from pymaker.deployment import Deployment
from pymaker.etherdelta import EtherDelta
from pymaker.feed import DSValue
from pymaker.lifecycle import Web3Lifecycle
from pymaker.numeric import Wad
from pymaker.token import DSToken
from tests.band_config import BandConfig
from tests.helper import args


class TestEtherDeltaMarketMakerKeeper:
    @staticmethod
    def mint_tokens(deployment: Deployment):
        DSToken(web3=deployment.web3, address=deployment.tub.gem()).mint(Wad.from_number(1000)).transact()
        DSToken(web3=deployment.web3, address=deployment.tub.sai()).mint(Wad.from_number(1000)).transact()

    @staticmethod
    def set_price(deployment: Deployment, price: Wad):
        DSValue(web3=deployment.web3, address=deployment.tub.pip()).poke_with_int(price.value).transact()

    def orders(self, keeper: EtherDeltaMarketMakerKeeper):
        return list(filter(lambda order: order.remaining_sell_amount > Wad(0), keeper.our_orders))

    def orders_by_token(self, keeper: EtherDeltaMarketMakerKeeper, token_address: Address):
        return list(filter(lambda order: order.pay_token == token_address, self.orders(keeper)))

    @staticmethod
    def orders_sorted(orders: list) -> list:
        return sorted(orders, key=lambda order: (order.pay_amount, order.buy_amount))

    def test_should_deposit_and_create_orders_on_startup(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert deployment.etherdelta.balance_of(deployment.our_address) > Wad(0)
        assert deployment.etherdelta.balance_of_token(deployment.sai.address, deployment.our_address) > Wad(0)

        # and
        assert len(self.orders(keeper)) == 2
        assert keeper.etherdelta_api.publish_order.call_count == 2

        # and
        assert self.orders_by_token(keeper, deployment.sai.address)[0].maker == deployment.our_address
        assert self.orders_by_token(keeper, deployment.sai.address)[0].pay_amount == Wad.from_number(75)
        assert self.orders_by_token(keeper, deployment.sai.address)[0].pay_token == deployment.sai.address
        assert self.orders_by_token(keeper, deployment.sai.address)[0].buy_amount == Wad.from_number(0.78125)
        assert self.orders_by_token(keeper, deployment.sai.address)[0].buy_token == EtherDelta.ETH_TOKEN

        # and
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].maker == deployment.our_address
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].buy_amount == Wad.from_number(780)
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].buy_token == deployment.sai.address

    def test_should_not_cancel_orders_on_shutdown_if_not_asked_to_do_so(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        assert len(self.orders(keeper)) == 2

        # when
        keeper.shutdown()

        # then
        assert len(self.orders(keeper)) == 2

        # and
        assert deployment.etherdelta.balance_of(deployment.our_address) > Wad(0)
        assert deployment.etherdelta.balance_of_token(deployment.sai.address, deployment.our_address) > Wad(0)

    def test_should_cancel_orders_on_shutdown_if_asked_to_do_so(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"
                                                       f" --cancel-on-shutdown"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        assert len(self.orders(keeper)) == 2

        # when
        keeper.shutdown()

        # then
        assert len(self.orders(keeper)) == 0

        # and
        assert deployment.etherdelta.balance_of(deployment.our_address) > Wad(0)
        assert deployment.etherdelta.balance_of_token(deployment.sai.address, deployment.our_address) > Wad(0)

    def test_should_cancel_orders_on_shutdown_and_withdraw_if_asked_to_do_so(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"
                                                       f" --cancel-on-shutdown --withdraw-on-shutdown"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        assert len(self.orders(keeper)) == 2

        # when
        keeper.shutdown()

        # then
        assert len(self.orders(keeper)) == 0

        # and
        assert deployment.etherdelta.balance_of(deployment.our_address) == Wad(0)
        assert deployment.etherdelta.balance_of_token(deployment.sai.address, deployment.our_address) == Wad(0)

    def test_should_support_config_files_with_variables(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.with_variables_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"
                                                       f" --cancel-on-shutdown --withdraw-on-shutdown"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert len(self.orders(keeper)) == 1

        # and
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].maker == deployment.our_address
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].pay_amount == Wad.from_number(5.0)
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].buy_amount == Wad.from_number(520)
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].buy_token == deployment.sai.address

    def test_should_reload_config_file_if_changed(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.with_variables_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert len(self.orders(keeper)) == 1

        # when
        second_config_file = BandConfig.sample_config(tmpdir)
        shutil.copyfile(second_config_file, config_file)

        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert len(self.orders(keeper)) == 2

    def test_should_fail_to_operate_if_bands_overlap(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.bands_overlapping_invalid_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()

        # expect
        with pytest.raises(Exception):
            keeper.synchronize_orders()

    def test_should_place_extra_order_only_if_order_brought_below_min(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        assert len(self.orders(keeper)) == 2
        sai_order = self.orders_by_token(keeper, deployment.sai.address)[0]

        # when
        print(sai_order.sell_to_buy_price)
        print(sai_order.buy_to_sell_price)
        deployment.etherdelta.trade(sai_order, Wad.from_number(20)/Wad.from_number(96)).transact()
        # and
        keeper.synchronize_orders()
        # then
        assert len(self.orders(keeper)) == 2

        # when
        deployment.etherdelta.trade(sai_order, Wad.from_number(5)/Wad.from_number(96)).transact()
        # and
        keeper.synchronize_orders()
        # then
        assert len(self.orders(keeper)) == 2

        # when
        deployment.etherdelta.trade(sai_order, Wad.from_number(1)/Wad.from_number(96)).transact()
        # and
        keeper.synchronize_orders()
        # then
        assert len(self.orders(keeper)) == 3
        assert self.orders(keeper)[2].pay_amount == Wad.from_number(26)
        assert self.orders(keeper)[2].pay_token == deployment.sai.address
        assert self.orders(keeper)[2].buy_amount == Wad(270833333000000000)
        assert self.orders(keeper)[2].buy_token == EtherDelta.ETH_TOKEN

    def test_should_cancel_selected_buy_orders_to_bring_the_band_total_below_max_and_closest_to_it(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        assert len(self.orders(keeper)) == 2

        # when [75+17 = 92]
        keeper.our_orders.append(deployment.etherdelta.create_order(deployment.sai.address, Wad.from_number(17),
                                                                    EtherDelta.ETH_TOKEN, Wad.from_number(0.1770805),
                                                                    1000000))
        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        # then
        assert len(self.orders(keeper)) == 3

        # when [92+2 = 94]
        keeper.our_orders.append(deployment.etherdelta.create_order(deployment.sai.address, Wad.from_number(2),
                                                                    EtherDelta.ETH_TOKEN, Wad.from_number(0.020833),
                                                                    1000000))
        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        # then
        assert len(self.orders(keeper)) == 4

        # when [94+7 = 101] --> above max!
        keeper.our_orders.append(deployment.etherdelta.create_order(deployment.sai.address, Wad.from_number(7),
                                                                    EtherDelta.ETH_TOKEN, Wad.from_number(0.072912),
                                                                    1000000))
        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        # then
        assert len(self.orders(keeper)) == 4
        assert reduce(Wad.__add__, map(lambda order: order.pay_amount, self.orders_by_token(keeper, deployment.sai.address)), Wad(0)) \
               == Wad.from_number(99)

    def test_should_cancel_the_only_buy_order_and_place_a_new_one_if_above_max(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()

        # and
        # [one artificially created order above the max band threshold]
        keeper.our_orders.append(deployment.etherdelta.create_order(deployment.sai.address, Wad.from_number(170),
                                                                    EtherDelta.ETH_TOKEN, Wad.from_number(1.770805),
                                                                    1000000))

        # when
        keeper.synchronize_orders()  # ... first call is so it can cancel the order
        keeper.synchronize_orders()  # ... second call is so it can made deposits
        keeper.synchronize_orders()  # ... third call is so the actual orders can get placed

        # then
        # [the artificial order gets cancelled, a new one gets created instead]
        assert len(self.orders(keeper)) == 2
        assert self.orders_by_token(keeper, deployment.sai.address)[0].maker == deployment.our_address
        assert self.orders_by_token(keeper, deployment.sai.address)[0].pay_amount == Wad.from_number(75)
        assert self.orders_by_token(keeper, deployment.sai.address)[0].pay_token == deployment.sai.address
        assert self.orders_by_token(keeper, deployment.sai.address)[0].buy_amount == Wad.from_number(0.78125)
        assert self.orders_by_token(keeper, deployment.sai.address)[0].buy_token == EtherDelta.ETH_TOKEN

    def test_should_cancel_selected_sell_orders_to_bring_the_band_total_below_max_and_closest_to_it(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        assert len(self.orders(keeper)) == 2

        # when [7.5+2.0 = 9.5]
        keeper.our_orders.append(deployment.etherdelta.create_order(EtherDelta.ETH_TOKEN, Wad.from_number(2),
                                                                    deployment.sai.address, Wad.from_number(208),
                                                                    1000000))
        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        # then
        assert len(self.orders(keeper)) == 3

        # when [9.5+0.5 = 10]
        keeper.our_orders.append(deployment.etherdelta.create_order(EtherDelta.ETH_TOKEN, Wad.from_number(0.5),
                                                                    deployment.sai.address, Wad.from_number(52),
                                                                    1000000))
        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        # then
        assert len(self.orders(keeper)) == 4

        # when [10+0.1 = 10.1] --> above max!
        keeper.our_orders.append(deployment.etherdelta.create_order(EtherDelta.ETH_TOKEN, Wad.from_number(0.1),
                                                                    deployment.sai.address, Wad.from_number(10.4),
                                                                    1000000))
        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        # then
        assert len(self.orders(keeper)) == 4
        assert reduce(Wad.__add__, map(lambda order: order.pay_amount, self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)), Wad(0)) \
               == Wad.from_number(10.0)

    def test_should_cancel_the_only_sell_order_and_place_a_new_one_if_above_max(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()

        # and
        # [one artificially created order above the max band threshold]
        keeper.our_orders.append(deployment.etherdelta.create_order(EtherDelta.ETH_TOKEN, Wad.from_number(20),
                                                                    deployment.sai.address, Wad.from_number(2080),
                                                                    1000000))

        # when
        keeper.synchronize_orders()  # ... first call is so it can cancel the order
        keeper.synchronize_orders()  # ... second call is so it can made deposits
        keeper.synchronize_orders()  # ... third call is so the actual orders can get placed

        # then
        # [the artificial order gets cancelled, a new one gets created instead]
        assert len(self.orders(keeper)) == 2
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].maker == deployment.our_address
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].buy_amount == Wad.from_number(780)
        assert self.orders_by_token(keeper, EtherDelta.ETH_TOKEN)[0].buy_token == deployment.sai.address

    def test_should_cancel_all_orders_outside_bands(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        assert len(self.orders(keeper)) == 2

        # when
        keeper.our_orders.append(deployment.etherdelta.create_order(deployment.sai.address, Wad.from_number(5), EtherDelta.ETH_TOKEN, Wad.from_number(0.0538), 1000000)) #price=92.936802973977695
        keeper.our_orders.append(deployment.etherdelta.create_order(deployment.sai.address, Wad.from_number(5), EtherDelta.ETH_TOKEN, Wad.from_number(0.0505), 1000000)) #price=99.0
        keeper.our_orders.append(deployment.etherdelta.create_order(EtherDelta.ETH_TOKEN, Wad.from_number(0.5), deployment.sai.address, Wad.from_number(50.5), 1000000)) #price=101
        keeper.our_orders.append(deployment.etherdelta.create_order(EtherDelta.ETH_TOKEN, Wad.from_number(0.5), deployment.sai.address, Wad.from_number(53.5), 1000000)) #price=107
        assert len(self.orders(keeper)) == 6
        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        # then
        assert len(self.orders(keeper)) == 2

    def test_should_create_orders_in_multiple_bands(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert len(self.orders(keeper)) == 2

        # and
        assert self.orders_sorted(self.orders(keeper))[0].maker == deployment.our_address
        assert self.orders_sorted(self.orders(keeper))[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_sorted(self.orders(keeper))[0].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_sorted(self.orders(keeper))[0].buy_amount == Wad.from_number(780)
        assert self.orders_sorted(self.orders(keeper))[0].buy_token == deployment.sai.address

        # and
        assert self.orders_sorted(self.orders(keeper))[1].maker == deployment.our_address
        assert self.orders_sorted(self.orders(keeper))[1].pay_amount == Wad.from_number(9.5)
        assert self.orders_sorted(self.orders(keeper))[1].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_sorted(self.orders(keeper))[1].buy_amount == Wad.from_number(1026)
        assert self.orders_sorted(self.orders(keeper))[1].buy_token == deployment.sai.address

    def test_should_take_over_order_from_adjacent_band_when_price_changes(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert len(self.orders(keeper)) == 2

        # and
        assert self.orders_sorted(self.orders(keeper))[0].maker == deployment.our_address
        assert self.orders_sorted(self.orders(keeper))[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_sorted(self.orders(keeper))[0].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_sorted(self.orders(keeper))[0].buy_amount == Wad.from_number(780)
        assert self.orders_sorted(self.orders(keeper))[0].buy_token == deployment.sai.address

        # and
        assert self.orders_sorted(self.orders(keeper))[1].maker == deployment.our_address
        assert self.orders_sorted(self.orders(keeper))[1].pay_amount == Wad.from_number(9.5)
        assert self.orders_sorted(self.orders(keeper))[1].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_sorted(self.orders(keeper))[1].buy_amount == Wad.from_number(1026)
        assert self.orders_sorted(self.orders(keeper))[1].buy_token == deployment.sai.address

        # when
        self.set_price(deployment, Wad.from_number(96))
        # and
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert len(self.orders(keeper)) == 2

        # and
        # ...new order in the <0.02,0.06> band gets created
        assert self.orders_sorted(self.orders(keeper))[0].maker == deployment.our_address
        assert self.orders_sorted(self.orders(keeper))[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_sorted(self.orders(keeper))[0].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_sorted(self.orders(keeper))[0].buy_amount == Wad.from_number(748.8)
        assert self.orders_sorted(self.orders(keeper))[0].buy_token == deployment.sai.address

        # and
        # ...the order from <0.02,0.06> ends up in the <0.06,0.10> band
        assert self.orders_sorted(self.orders(keeper))[1].maker == deployment.our_address
        assert self.orders_sorted(self.orders(keeper))[1].pay_amount == Wad.from_number(7.5)
        assert self.orders_sorted(self.orders(keeper))[1].pay_token == EtherDelta.ETH_TOKEN
        assert self.orders_sorted(self.orders(keeper))[1].buy_amount == Wad.from_number(780)
        assert self.orders_sorted(self.orders(keeper))[1].buy_token == deployment.sai.address

    def test_should_cancel_all_orders_but_not_terminate_if_eth_balance_before_minimum(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 200"
                                                       f" --min-eth-balance 100.0"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert len(self.orders(keeper)) == 2

        # when
        self.leave_only_some_eth(deployment, Wad.from_number(10.0))  # there is a 5.0 ETH block reward even in testrpc,
                                                                     # that's why `--min-eth-balance` is higher than 10

        # and
        keeper.synchronize_orders()

        # then
        assert len(self.orders(keeper)) == 0
        assert not keeper.lifecycle.terminated_internally

    def test_should_use_specified_gas_price_for_all_transactions(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 10"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"
                                                       f" --cancel-on-shutdown"
                                                       f" --gas-price 69000000000"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        start_block_number = deployment.web3.eth.blockNumber

        # when
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed
        keeper.shutdown()

        # then
        for block_number in range(start_block_number+1, deployment.web3.eth.blockNumber+1):
            for transaction in deployment.web3.eth.getBlock(block_number, full_transactions=True).transactions:
                assert transaction.gasPrice == 69000000000

    def test_should_not_create_any_orders_but_not_terminate_if_eth_balance_before_minimum(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # and
        keeper = EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                       f" --tub-address {deployment.tub.address}"
                                                       f" --etherdelta-address {deployment.etherdelta.address}"
                                                       f" --etherdelta-socket https://127.0.0.1:99999/"
                                                       f" --order-age 3600 --eth-reserve 200"
                                                       f" --min-eth-balance 100.0"
                                                       f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3)
        keeper.etherdelta_api.publish_order = MagicMock()

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        self.leave_only_some_eth(deployment, Wad.from_number(10.0))  # there is a 5.0 ETH block reward even in testrpc,
                                                                     # that's why `--min-eth-balance` is higher than 10

        # when
        keeper.approve()
        keeper.synchronize_orders()  # ... first call is so it can made deposits
        keeper.synchronize_orders()  # ... second call is so the actual orders can get placed

        # then
        assert len(self.orders(keeper)) == 0
        assert not keeper.lifecycle.terminated_internally

    def test_should_refuse_to_start_if_eth_reserve_lower_than_min_eth_balance(self, deployment: Deployment, tmpdir: py.path.local):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # expect
        with pytest.raises(Exception, match="--eth-reserve must be higher than --min-eth-balance"):
            EtherDeltaMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} --config {config_file}"
                                                  f" --tub-address {deployment.tub.address}"
                                                  f" --etherdelta-address {deployment.etherdelta.address}"
                                                  f" --etherdelta-socket https://127.0.0.1:99999/"
                                                  f" --order-age 3600 --eth-reserve 99.9"
                                                  f" --min-eth-balance 100.0"
                                                  f" --min-eth-deposit 1 --min-sai-deposit 400"),
                                        web3=deployment.web3)

    @staticmethod
    def leave_only_some_eth(deployment: Deployment, amount_of_eth_to_leave: Wad):
        balance = Wad(deployment.web3.eth.getBalance(deployment.our_address.address))
        deployment.web3.eth.sendTransaction({'to': '0x0000011111000001111100000111110000011111',
                                             'value': (balance - amount_of_eth_to_leave).value})
