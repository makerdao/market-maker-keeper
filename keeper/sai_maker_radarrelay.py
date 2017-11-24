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
import sys

from keeper import ERC20Token, Wad
from keeper.api import Address, synchronize
from keeper.api.approval import directly
from keeper.api.radarrelay import RadarRelay, RadarRelayApi, Order
from keeper.sai import SaiKeeper


class SaiMakerRadarRelay(SaiKeeper):
    """SAI keeper to act as a market maker on RadarRelay, on the WETH/SAI pair."""
    def __init__(self, args: list, **kwargs):
        super().__init__(args, **kwargs)

        self.ether_token = ERC20Token(web3=self.web3, address=Address(self.config.get_config()["0x"]["etherToken"]))
        self.radar_relay = RadarRelay(web3=self.web3, address=Address(self.config.get_config()["0x"]["exchange"]))
        self.radar_relay_api = RadarRelayApi(contract_address=self.radar_relay.address,
                                             api_server=self.config.get_config()["radarRelay"]["apiServer"])

        # so the token names are printed nicer
        ERC20Token.register_token(self.radar_relay.zrx_token(), 'ZRX')
        ERC20Token.register_token(self.ether_token.address, '0x-WETH')

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

    def startup(self):
        self.approve()

    def shutdown(self):
        order = self.radar_relay.create_order(Wad.from_number(1),
                                              Wad.from_number(0.0030),
                                              self.sai.address,
                                              self.ether_token.address,
                                              1511496715)

        order_with_fees = self.radar_relay_api.calculate_fees(order)
        signed_order = self.radar_relay.sign_order(order_with_fees)

        print(self.radar_relay_api.submit_order(signed_order))

        orders = self.radar_relay_api.get_orders_by_maker(self.our_address)
        orders = list(filter(lambda order: self.radar_relay.get_unavailable_taker_token_amount(order) < order.taker_token_amount, orders))
        synchronize(list(map(lambda order: self.radar_relay.cancel_order(order).transact_async(), orders)))




        # print(repr(signed_order))

        # order = Order(maker=self.our_address,
        #               taker=Address("0x0000000000000000000000000000000000000000"),
        #               maker_token_address=Address("0x323b5d4c32345ced77393b3530b1eed0f346429d"),
        #               taker_token_address=Address("0xef7fff64389b814a946f3e92105513705ca6b990"),
        #               maker_token_amount=Wad(10000000000000000),
        #               taker_token_amount=Wad(20000000000000000),
        #               expiration_unix_timestamp_sec=42,
        #               salt=67006738228878699843088602623665307406148487219438534730168799356281242528500,
        #               exchange_contract_address=self.radar_relay.address,
        #               maker_fee=Wad(0),
        #               taker_fee=Wad(0),
        #               fee_recipient=Address("0x0000000000000000000000000000000000000000"))
        #
        # print(repr(self.radar_relay_api.calculate_fees(order)))
        # print("---")
        # print(self.radar_relay_api.get_orders_by_maker(self.our_address))

    def approve(self):
        """Approve 0x to access our 0x-WETH and SAI, so we can sell it on the exchange."""
        self.radar_relay.approve([self.ether_token, self.sai], directly())


if __name__ == '__main__':
    SaiMakerRadarRelay(sys.argv[1:]).start()
