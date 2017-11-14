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

import py

from keeper import Wad
from keeper.api.feed import DSValue
from keeper.api.token import DSToken, ERC20Token
from keeper.sai_maker_otc import SaiMakerOtc
from tests.conftest import SaiDeployment
from tests.helper import args


class TestSaiMakerOtc:
    @staticmethod
    def sample_config(tmpdir):
        file = tmpdir.join("sample_config.json")
        file.write("""{
            "buyBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minSaiAmount": 50.0,
                    "avgSaiAmount": 75.0,
                    "maxSaiAmount": 100.0,
                    "dustCutoff": 0.0
                }
            ],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 10.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")
        return file

    @staticmethod
    def two_adjacent_bands_config(tmpdir):
        file = tmpdir.join("two_adjacent_bands_config.json")
        file.write("""{
            "buyBands": [],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 8.5,
                    "dustCutoff": 0.0
                },
                {
                    "minMargin": 0.06,
                    "avgMargin": 0.08,
                    "maxMargin": 0.10,
                    "minWEthAmount": 7.0,
                    "avgWEthAmount": 9.5,
                    "maxWEthAmount": 12.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")
        return file

    @staticmethod
    def with_variables_config(tmpdir):
        file = tmpdir.join("with_variables_config.json")
        file.write("""{
            "variables": {
                "avgEthBook": 10
            },
            "buyBands": [],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minWEthAmount": $.variables.avgEthBook * 0.25,
                    "avgWEthAmount": $.variables.avgEthBook * 0.5,
                    "maxWEthAmount": $.variables.avgEthBook * 1.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")
        return file

    @staticmethod
    def bands_overlapping_invalid_config(tmpdir):
        file = tmpdir.join("bands_overlapping_invalid_config.json")
        file.write("""{
            "buyBands": [],
            "sellBands": [
                {
                    "minMargin": 0.02,
                    "avgMargin": 0.04,
                    "maxMargin": 0.06,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 10.0,
                    "dustCutoff": 0.0
                },
                {
                    "minMargin": 0.059,
                    "avgMargin": 0.07,
                    "maxMargin": 0.08,
                    "minWEthAmount": 5.0,
                    "avgWEthAmount": 7.5,
                    "maxWEthAmount": 10.0,
                    "dustCutoff": 0.0
                }
            ]
        }""")
        return file

    @staticmethod
    def mint_tokens(sai: SaiDeployment):
        DSToken(web3=sai.web3, address=sai.tub.gem()).mint(Wad.from_number(1000)).transact()
        DSToken(web3=sai.web3, address=sai.tub.sai()).mint(Wad.from_number(1000)).transact()

    @staticmethod
    def set_price(sai: SaiDeployment, price: Wad):
        DSValue(web3=sai.web3, address=sai.tub.pip()).poke_with_int(price.value).transact()

    @staticmethod
    def offers_by_token(sai: SaiDeployment, token: ERC20Token):
        return list(filter(lambda offer: offer.sell_which_token == token.address, sai.otc.active_offers()))

    @staticmethod
    def offers_sorted(offers: list) -> list:
        return sorted(offers, key=lambda offer: (offer.sell_how_much, offer.buy_how_much))

    def test_should_create_offers_on_startup(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.sample_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 2

        # and
        assert self.offers_by_token(sai, sai.sai)[0].owner == sai.our_address
        assert self.offers_by_token(sai, sai.sai)[0].sell_how_much == Wad.from_number(75)
        assert self.offers_by_token(sai, sai.sai)[0].sell_which_token == sai.sai.address
        assert self.offers_by_token(sai, sai.sai)[0].buy_how_much == Wad.from_number(0.78125)
        assert self.offers_by_token(sai, sai.sai)[0].buy_which_token == sai.gem.address

        # and
        assert self.offers_by_token(sai, sai.gem)[0].owner == sai.our_address
        assert self.offers_by_token(sai, sai.gem)[0].sell_how_much == Wad.from_number(7.5)
        assert self.offers_by_token(sai, sai.gem)[0].sell_which_token == sai.gem.address
        assert self.offers_by_token(sai, sai.gem)[0].buy_how_much == Wad.from_number(780)
        assert self.offers_by_token(sai, sai.gem)[0].buy_which_token == sai.sai.address

    def test_should_cancel_offers_on_shutdown(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.sample_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_offers()
        assert len(sai.otc.active_offers()) == 2

        # when
        keeper.shutdown()

        # then
        assert len(sai.otc.active_offers()) == 0

    def test_should_support_config_files_with_variables(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.with_variables_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 1

        # and
        assert self.offers_by_token(sai, sai.gem)[0].owner == sai.our_address
        assert self.offers_by_token(sai, sai.gem)[0].sell_how_much == Wad.from_number(5.0)
        assert self.offers_by_token(sai, sai.gem)[0].sell_which_token == sai.gem.address
        assert self.offers_by_token(sai, sai.gem)[0].buy_how_much == Wad.from_number(520)
        assert self.offers_by_token(sai, sai.gem)[0].buy_which_token == sai.sai.address

    def test_should_reload_config_file_if_changed(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.with_variables_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 1

        # when
        second_config_file = self.sample_config(tmpdir)
        shutil.copyfile(second_config_file, config_file)

        # and
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 2

    def test_should_fail_to_operate_if_bands_overlap(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.bands_overlapping_invalid_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 0

        # and
        assert keeper.terminated_internally

    def test_should_place_extra_offer_only_if_offer_brought_below_min(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.sample_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_offers()
        assert len(sai.otc.active_offers()) == 2
        sai_offer_id = self.offers_by_token(sai, sai.sai)[0].offer_id

        # when
        sai.otc.take(sai_offer_id, Wad.from_number(20)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 2

        # when
        sai.otc.take(sai_offer_id, Wad.from_number(5)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 2

        # when
        sai.otc.take(sai_offer_id, Wad.from_number(1)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 3
        assert sai.otc.active_offers()[2].sell_how_much == Wad.from_number(26)
        assert sai.otc.active_offers()[2].sell_which_token == sai.sai.address
        assert sai.otc.active_offers()[2].buy_how_much == Wad(270833333333333333)
        assert sai.otc.active_offers()[2].buy_which_token == sai.gem.address

    def test_should_cancel_all_buy_offers_and_place_a_new_one_if_above_max(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.sample_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_offers()
        assert len(sai.otc.active_offers()) == 2

        # when [75+20 = 95]
        sai.otc.make(sai.sai.address, Wad.from_number(20), sai.gem.address, Wad.from_number(0.20833)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 3

        # when [95+5 = 100]
        sai.otc.make(sai.sai.address, Wad.from_number(5), sai.gem.address, Wad.from_number(0.052)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 4

        # when [100+1 = 101] --> above max!
        sai.otc.make(sai.sai.address, Wad.from_number(1), sai.gem.address, Wad.from_number(0.010416)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 2
        assert self.offers_by_token(sai, sai.sai)[0].owner == sai.our_address
        assert self.offers_by_token(sai, sai.sai)[0].sell_how_much == Wad.from_number(75)
        assert self.offers_by_token(sai, sai.sai)[0].sell_which_token == sai.sai.address
        assert self.offers_by_token(sai, sai.sai)[0].buy_how_much == Wad.from_number(0.78125)
        assert self.offers_by_token(sai, sai.sai)[0].buy_which_token == sai.gem.address

    def test_should_cancel_all_sell_offers_and_place_a_new_one_if_above_max(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.sample_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_offers()
        assert len(sai.otc.active_offers()) == 2

        # when [7.5+2.0 = 9.5]
        sai.otc.make(sai.gem.address, Wad.from_number(2), sai.sai.address, Wad.from_number(208)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 3

        # when [9.5+0.5 = 10]
        sai.otc.make(sai.gem.address, Wad.from_number(0.5), sai.sai.address, Wad.from_number(52)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 4

        # when [10+0.1 = 10.1] --> above max!
        sai.otc.make(sai.gem.address, Wad.from_number(0.1), sai.sai.address, Wad.from_number(10.4)).transact()
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 2
        assert self.offers_by_token(sai, sai.gem)[0].owner == sai.our_address
        assert self.offers_by_token(sai, sai.gem)[0].sell_how_much == Wad.from_number(7.5)
        assert self.offers_by_token(sai, sai.gem)[0].sell_which_token == sai.gem.address
        assert self.offers_by_token(sai, sai.gem)[0].buy_how_much == Wad.from_number(780)
        assert self.offers_by_token(sai, sai.gem)[0].buy_which_token == sai.sai.address

    def test_should_cancel_all_offers_outside_bands(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.sample_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # and
        keeper.approve()
        keeper.synchronize_offers()
        assert len(sai.otc.active_offers()) == 2

        # when
        sai.otc.make(sai.sai.address, Wad.from_number(5), sai.gem.address, Wad.from_number(0.0538)).transact() #price=92.936802973977695
        sai.otc.make(sai.sai.address, Wad.from_number(5), sai.gem.address, Wad.from_number(0.0505)).transact() #price=99.0
        sai.otc.make(sai.gem.address, Wad.from_number(0.5), sai.sai.address, Wad.from_number(50.5)).transact() #price=101
        sai.otc.make(sai.gem.address, Wad.from_number(0.5), sai.sai.address, Wad.from_number(53.5)).transact() #price=107
        assert len(sai.otc.active_offers()) == 6
        # and
        keeper.synchronize_offers()
        # then
        assert len(sai.otc.active_offers()) == 2

    def test_should_create_offers_in_multiple_bands(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.two_adjacent_bands_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 2

        # and
        assert self.offers_sorted(sai.otc.active_offers())[0].owner == sai.our_address
        assert self.offers_sorted(sai.otc.active_offers())[0].sell_how_much == Wad.from_number(7.5)
        assert self.offers_sorted(sai.otc.active_offers())[0].sell_which_token == sai.gem.address
        assert self.offers_sorted(sai.otc.active_offers())[0].buy_how_much == Wad.from_number(780)
        assert self.offers_sorted(sai.otc.active_offers())[0].buy_which_token == sai.sai.address

        # and
        assert self.offers_sorted(sai.otc.active_offers())[1].owner == sai.our_address
        assert self.offers_sorted(sai.otc.active_offers())[1].sell_how_much == Wad.from_number(9.5)
        assert self.offers_sorted(sai.otc.active_offers())[1].sell_which_token == sai.gem.address
        assert self.offers_sorted(sai.otc.active_offers())[1].buy_how_much == Wad.from_number(1026)
        assert self.offers_sorted(sai.otc.active_offers())[1].buy_which_token == sai.sai.address

    def test_should_take_over_offer_from_adjacent_band_when_price_changes(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.two_adjacent_bands_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 2

        # and
        assert self.offers_sorted(sai.otc.active_offers())[0].owner == sai.our_address
        assert self.offers_sorted(sai.otc.active_offers())[0].sell_how_much == Wad.from_number(7.5)
        assert self.offers_sorted(sai.otc.active_offers())[0].sell_which_token == sai.gem.address
        assert self.offers_sorted(sai.otc.active_offers())[0].buy_how_much == Wad.from_number(780)
        assert self.offers_sorted(sai.otc.active_offers())[0].buy_which_token == sai.sai.address

        # and
        assert self.offers_sorted(sai.otc.active_offers())[1].owner == sai.our_address
        assert self.offers_sorted(sai.otc.active_offers())[1].sell_how_much == Wad.from_number(9.5)
        assert self.offers_sorted(sai.otc.active_offers())[1].sell_which_token == sai.gem.address
        assert self.offers_sorted(sai.otc.active_offers())[1].buy_how_much == Wad.from_number(1026)
        assert self.offers_sorted(sai.otc.active_offers())[1].buy_which_token == sai.sai.address

        # when
        self.set_price(sai, Wad.from_number(96))
        # and
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 2

        # and
        # ...new offer in the <0.02,0.06> band gets created
        assert self.offers_sorted(sai.otc.active_offers())[0].owner == sai.our_address
        assert self.offers_sorted(sai.otc.active_offers())[0].sell_how_much == Wad.from_number(7.5)
        assert self.offers_sorted(sai.otc.active_offers())[0].sell_which_token == sai.gem.address
        assert self.offers_sorted(sai.otc.active_offers())[0].buy_how_much == Wad.from_number(748.8)
        assert self.offers_sorted(sai.otc.active_offers())[0].buy_which_token == sai.sai.address

        # and
        # ...the offer from <0.02,0.06> ends up in the <0.06,0.10> band
        assert self.offers_sorted(sai.otc.active_offers())[1].owner == sai.our_address
        assert self.offers_sorted(sai.otc.active_offers())[1].sell_how_much == Wad.from_number(7.5)
        assert self.offers_sorted(sai.otc.active_offers())[1].sell_which_token == sai.gem.address
        assert self.offers_sorted(sai.otc.active_offers())[1].buy_how_much == Wad.from_number(780)
        assert self.offers_sorted(sai.otc.active_offers())[1].buy_which_token == sai.sai.address

    def test_should_cancel_all_orders_and_terminate_if_eth_balance_before_minimum(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.two_adjacent_bands_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"
                                       f" --min-eth-balance 100.0"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 2

        # when
        self.leave_only_some_eth(sai, Wad.from_number(10.0))  # there is a 5.0 ETH block reward even in testrpc,
                                                              # that's why `--min-eth-balance` is higher than 10.0

        # and
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 0
        assert keeper.terminated_internally

    def test_should_refuse_to_start_if_eth_balance_before_minimum(self, sai: SaiDeployment, tmpdir: py.path.local):
        # given
        config_file = self.two_adjacent_bands_config(tmpdir)

        # and
        keeper = SaiMakerOtc(args=args(f"--eth-from {sai.web3.eth.defaultAccount} --config {config_file}"
                                       f" --min-eth-balance 100.0"),
                             web3=sai.web3, config=sai.get_config())

        # and
        self.mint_tokens(sai)
        self.set_price(sai, Wad.from_number(100))

        # and
        self.leave_only_some_eth(sai, Wad.from_number(10.0))  # there is a 5.0 ETH block reward even in testrpc,
                                                              # that's why `--min-eth-balance` is higher than 10.0

        # when
        keeper.approve()
        keeper.synchronize_offers()

        # then
        assert len(sai.otc.active_offers()) == 0
        assert keeper.terminated_internally

    @staticmethod
    def leave_only_some_eth(sai: SaiDeployment, amount_of_eth_to_leave: Wad):
        balance = Wad(sai.web3.eth.getBalance(sai.our_address.address))
        sai.web3.eth.sendTransaction({'to': '0x0000011111000001111100000111110000011111',
                                      'value': (balance - amount_of_eth_to_leave).value})
