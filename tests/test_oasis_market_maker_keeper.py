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

import pytest

from market_maker_keeper.oasis_market_maker_keeper import OasisMarketMakerKeeper
from pymaker.deployment import Deployment
from pymaker.feed import DSValue
from pymaker.lifecycle import Web3Lifecycle
from pymaker.numeric import Wad
from pymaker.token import DSToken, ERC20Token
from tests.band_config import BandConfig
from tests.helper import args


class TestOasisMarketMakerKeeper:
    @staticmethod
    def mint_tokens(deployment: Deployment):
        DSToken(web3=deployment.web3, address=deployment.tub.gem()).mint(Wad.from_number(1000)).transact()
        DSToken(web3=deployment.web3, address=deployment.tub.sai()).mint(Wad.from_number(1000)).transact()

    @staticmethod
    def set_price(deployment: Deployment, price: Wad):
        DSValue(web3=deployment.web3, address=deployment.tub.pip()).poke_with_int(price.value).transact()

    @staticmethod
    def orders_by_token(deployment: Deployment, token: ERC20Token):
        return list(filter(lambda order: order.pay_token == token.address, deployment.otc.get_orders()))

    @staticmethod
    def orders_sorted(orders: list) -> list:
        return sorted(orders, key=lambda order: (order.pay_amount, order.buy_amount))

    @staticmethod
    def synchronize_orders_twice(keeper: OasisMarketMakerKeeper):
        keeper.synchronize_orders()
        keeper.synchronize_orders()

    def test_should_create_orders_on_startup(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "  
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 2

        # and
        assert self.orders_by_token(deployment, deployment.sai)[0].maker == deployment.our_address
        assert self.orders_by_token(deployment, deployment.sai)[0].pay_amount == Wad.from_number(75)
        assert self.orders_by_token(deployment, deployment.sai)[0].pay_token == deployment.sai.address
        assert self.orders_by_token(deployment, deployment.sai)[0].buy_amount == Wad.from_number(0.78125)
        assert self.orders_by_token(deployment, deployment.sai)[0].buy_token == deployment.gem.address

        # and
        assert self.orders_by_token(deployment, deployment.gem)[0].maker == deployment.our_address
        assert self.orders_by_token(deployment, deployment.gem)[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_by_token(deployment, deployment.gem)[0].pay_token == deployment.gem.address
        assert self.orders_by_token(deployment, deployment.gem)[0].buy_amount == Wad.from_number(780)
        assert self.orders_by_token(deployment, deployment.gem)[0].buy_token == deployment.sai.address

    def test_should_cancel_orders_on_shutdown(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        self.synchronize_orders_twice(keeper)
        assert len(deployment.otc.get_orders()) == 2

        # when
        keeper.shutdown()

        # then
        assert len(deployment.otc.get_orders()) == 0

    def test_should_support_config_files_with_variables(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.with_variables_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 1

        # and
        assert self.orders_by_token(deployment, deployment.gem)[0].maker == deployment.our_address
        assert self.orders_by_token(deployment, deployment.gem)[0].pay_amount == Wad.from_number(5.0)
        assert self.orders_by_token(deployment, deployment.gem)[0].pay_token == deployment.gem.address
        assert self.orders_by_token(deployment, deployment.gem)[0].buy_amount == Wad.from_number(520)
        assert self.orders_by_token(deployment, deployment.gem)[0].buy_token == deployment.sai.address

    def test_should_reload_config_file_if_changed(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.with_variables_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 1

        # when
        second_config_file = BandConfig.sample_config(tmpdir)
        shutil.copyfile(second_config_file, config_file)

        # and
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 2

    def test_should_fail_to_operate_if_bands_overlap(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.bands_overlapping_invalid_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()

        # expect
        with pytest.raises(Exception):
            self.synchronize_orders_twice(keeper)

    def test_should_place_extra_order_only_if_order_brought_below_min(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        self.synchronize_orders_twice(keeper)
        assert len(deployment.otc.get_orders()) == 2
        sai_order_id = self.orders_by_token(deployment, deployment.sai)[0].order_id

        # when
        deployment.otc.take(sai_order_id, Wad.from_number(20)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 2

        # when
        deployment.otc.take(sai_order_id, Wad.from_number(5)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 2

        # when
        deployment.otc.take(sai_order_id, Wad.from_number(1)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 3
        assert deployment.otc.get_orders()[2].pay_amount == Wad.from_number(26)
        assert deployment.otc.get_orders()[2].pay_token == deployment.sai.address
        assert deployment.otc.get_orders()[2].buy_amount == Wad(270833333333333333)
        assert deployment.otc.get_orders()[2].buy_token == deployment.gem.address

    def test_should_cancel_selected_buy_orders_to_bring_the_band_total_below_max_and_closest_to_it(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        self.synchronize_orders_twice(keeper)
        assert len(deployment.otc.get_orders()) == 2

        # when [75+17 = 92]
        deployment.otc.make(deployment.sai.address, Wad.from_number(17), deployment.gem.address, Wad.from_number(0.1770805)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 3

        # when [92+2 = 94]
        deployment.otc.make(deployment.sai.address, Wad.from_number(2), deployment.gem.address, Wad.from_number(0.020833)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 4

        # when [94+7 = 101] --> above max!
        deployment.otc.make(deployment.sai.address, Wad.from_number(7), deployment.gem.address, Wad.from_number(0.072912)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 4
        assert reduce(Wad.__add__, map(lambda order: order.pay_amount, self.orders_by_token(deployment, deployment.sai)), Wad(0)) \
               == Wad.from_number(99)

    def test_should_cancel_the_only_buy_order_and_place_a_new_one_if_above_max(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                             web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()

        # and
        # [one artificially created order above the max band threshold]
        deployment.otc.make(deployment.sai.address, Wad.from_number(170), deployment.gem.address, Wad.from_number(1.770805)).transact()

        # when
        self.synchronize_orders_twice(keeper)

        # then
        # [the artificial order gets cancelled, a new one gets created instead]
        assert len(deployment.otc.get_orders()) == 2
        assert self.orders_by_token(deployment, deployment.sai)[0].maker == deployment.our_address
        assert self.orders_by_token(deployment, deployment.sai)[0].pay_amount == Wad.from_number(75)
        assert self.orders_by_token(deployment, deployment.sai)[0].pay_token == deployment.sai.address
        assert self.orders_by_token(deployment, deployment.sai)[0].buy_amount == Wad.from_number(0.78125)
        assert self.orders_by_token(deployment, deployment.sai)[0].buy_token == deployment.gem.address

    def test_should_cancel_selected_sell_orders_to_bring_the_band_total_below_max_and_closest_to_it(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        self.synchronize_orders_twice(keeper)
        assert len(deployment.otc.get_orders()) == 2

        # when [7.5+2.0 = 9.5]
        deployment.otc.make(deployment.gem.address, Wad.from_number(2), deployment.sai.address, Wad.from_number(208)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 3

        # when [9.5+0.5 = 10]
        deployment.otc.make(deployment.gem.address, Wad.from_number(0.5), deployment.sai.address, Wad.from_number(52)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 4

        # when [10+0.1 = 10.1] --> above max!
        deployment.otc.make(deployment.gem.address, Wad.from_number(0.1), deployment.sai.address, Wad.from_number(10.4)).transact()
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 4
        assert reduce(Wad.__add__, map(lambda order: order.pay_amount, self.orders_by_token(deployment, deployment.gem)), Wad(0)) \
               == Wad.from_number(10.0)

    def test_should_cancel_the_only_sell_order_and_place_a_new_one_if_above_max(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()

        # and
        # [one artificially created order above the max band threshold]
        deployment.otc.make(deployment.gem.address, Wad.from_number(20), deployment.sai.address, Wad.from_number(2080)).transact()

        # when
        self.synchronize_orders_twice(keeper)

        # then
        # [the artificial order gets cancelled, a new one gets created instead]
        assert len(deployment.otc.get_orders()) == 2
        assert self.orders_by_token(deployment, deployment.gem)[0].maker == deployment.our_address
        assert self.orders_by_token(deployment, deployment.gem)[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_by_token(deployment, deployment.gem)[0].pay_token == deployment.gem.address
        assert self.orders_by_token(deployment, deployment.gem)[0].buy_amount == Wad.from_number(780)
        assert self.orders_by_token(deployment, deployment.gem)[0].buy_token == deployment.sai.address

    def test_should_cancel_all_orders_outside_bands(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.sample_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        keeper.approve()
        self.synchronize_orders_twice(keeper)
        assert len(deployment.otc.get_orders()) == 2

        # when
        deployment.otc.make(deployment.sai.address, Wad.from_number(5), deployment.gem.address, Wad.from_number(0.0538)).transact() #price=92.936802973977695
        deployment.otc.make(deployment.sai.address, Wad.from_number(5), deployment.gem.address, Wad.from_number(0.0505)).transact() #price=99.0
        deployment.otc.make(deployment.gem.address, Wad.from_number(0.5), deployment.sai.address, Wad.from_number(50.5)).transact() #price=101
        deployment.otc.make(deployment.gem.address, Wad.from_number(0.5), deployment.sai.address, Wad.from_number(53.5)).transact() #price=107
        assert len(deployment.otc.get_orders()) == 6
        # and
        self.synchronize_orders_twice(keeper)
        # then
        assert len(deployment.otc.get_orders()) == 2

    def test_should_create_orders_in_multiple_bands(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 2

        # and
        assert self.orders_sorted(deployment.otc.get_orders())[0].maker == deployment.our_address
        assert self.orders_sorted(deployment.otc.get_orders())[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_sorted(deployment.otc.get_orders())[0].pay_token == deployment.gem.address
        assert self.orders_sorted(deployment.otc.get_orders())[0].buy_amount == Wad.from_number(780)
        assert self.orders_sorted(deployment.otc.get_orders())[0].buy_token == deployment.sai.address

        # and
        assert self.orders_sorted(deployment.otc.get_orders())[1].maker == deployment.our_address
        assert self.orders_sorted(deployment.otc.get_orders())[1].pay_amount == Wad.from_number(9.5)
        assert self.orders_sorted(deployment.otc.get_orders())[1].pay_token == deployment.gem.address
        assert self.orders_sorted(deployment.otc.get_orders())[1].buy_amount == Wad.from_number(1026)
        assert self.orders_sorted(deployment.otc.get_orders())[1].buy_token == deployment.sai.address

    def test_should_take_over_order_from_adjacent_band_when_price_changes(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file}"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 2

        # and
        assert self.orders_sorted(deployment.otc.get_orders())[0].maker == deployment.our_address
        assert self.orders_sorted(deployment.otc.get_orders())[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_sorted(deployment.otc.get_orders())[0].pay_token == deployment.gem.address
        assert self.orders_sorted(deployment.otc.get_orders())[0].buy_amount == Wad.from_number(780)
        assert self.orders_sorted(deployment.otc.get_orders())[0].buy_token == deployment.sai.address

        # and
        assert self.orders_sorted(deployment.otc.get_orders())[1].maker == deployment.our_address
        assert self.orders_sorted(deployment.otc.get_orders())[1].pay_amount == Wad.from_number(9.5)
        assert self.orders_sorted(deployment.otc.get_orders())[1].pay_token == deployment.gem.address
        assert self.orders_sorted(deployment.otc.get_orders())[1].buy_amount == Wad.from_number(1026)
        assert self.orders_sorted(deployment.otc.get_orders())[1].buy_token == deployment.sai.address

        # when
        self.set_price(deployment, Wad.from_number(96))
        # and
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 2

        # and
        # ...new order in the <0.02,0.06> band gets created
        assert self.orders_sorted(deployment.otc.get_orders())[0].maker == deployment.our_address
        assert self.orders_sorted(deployment.otc.get_orders())[0].pay_amount == Wad.from_number(7.5)
        assert self.orders_sorted(deployment.otc.get_orders())[0].pay_token == deployment.gem.address
        assert self.orders_sorted(deployment.otc.get_orders())[0].buy_amount == Wad.from_number(748.8)
        assert self.orders_sorted(deployment.otc.get_orders())[0].buy_token == deployment.sai.address

        # and
        # ...the order from <0.02,0.06> ends up in the <0.06,0.10> band
        assert self.orders_sorted(deployment.otc.get_orders())[1].maker == deployment.our_address
        assert self.orders_sorted(deployment.otc.get_orders())[1].pay_amount == Wad.from_number(7.5)
        assert self.orders_sorted(deployment.otc.get_orders())[1].pay_token == deployment.gem.address
        assert self.orders_sorted(deployment.otc.get_orders())[1].buy_amount == Wad.from_number(780)
        assert self.orders_sorted(deployment.otc.get_orders())[1].buy_token == deployment.sai.address

    def test_should_cancel_all_orders_but_not_terminate_if_eth_balance_below_minimum(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file} "
                                                  f"--min-eth-balance 100.0"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # when
        keeper.approve()
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 2

        # when
        self.leave_only_some_eth(deployment, Wad.from_number(10.0))  # there is a 5.0 ETH block reward even in testrpc,
                                                                     # that's why `--min-eth-balance` is higher than 10

        # and
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 0
        assert not keeper.lifecycle.terminated_internally

    def test_should_not_create_any_orders_but_not_terminate_if_eth_balance_before_minimum(self, deployment: Deployment, tmpdir):
        # given
        config_file = BandConfig.two_adjacent_bands_config(tmpdir)

        # and
        keeper = OasisMarketMakerKeeper(args=args(f"--eth-from {deployment.our_address} "
                                                  f"--tub-address {deployment.tub.address} "
                                                  f"--oasis-address {deployment.otc.address} "
                                                  f"--config {config_file} "
                                                  f"--min-eth-balance 100.0"),
                                        web3=deployment.web3)
        keeper.lifecycle = Web3Lifecycle(web3=keeper.web3, logger=keeper.logger)

        # and
        self.mint_tokens(deployment)
        self.set_price(deployment, Wad.from_number(100))

        # and
        self.leave_only_some_eth(deployment, Wad.from_number(10.0))  # there is a 5.0 ETH block reward even in testrpc,
                                                                     # that's why `--min-eth-balance` is higher than 10

        # when
        keeper.approve()
        self.synchronize_orders_twice(keeper)

        # then
        assert len(deployment.otc.get_orders()) == 0
        assert not keeper.lifecycle.terminated_internally

    @staticmethod
    def leave_only_some_eth(deployment: Deployment, amount_of_eth_to_leave: Wad):
        balance = Wad(deployment.web3.eth.getBalance(deployment.our_address.address))
        deployment.web3.eth.sendTransaction({'to': '0x0000011111000001111100000111110000011111',
                                             'value': (balance - amount_of_eth_to_leave).value})
