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

from keeper import ERC20Token
from keeper.api import Address
from keeper.api.approval import directly
from keeper.api.radarrelay import RadarRelay
from keeper.sai import SaiKeeper


class SaiMakerRadarRelay(SaiKeeper):
    """SAI keeper to act as a market maker on RadarRelay, on the WETH/SAI pair."""
    def __init__(self, args: list, **kwargs):
        super().__init__(args, **kwargs)

        self.ether_token = ERC20Token(web3=self.web3, address=Address(self.config.get_config()["0x"]["etherToken"]))
        self.radar_relay = RadarRelay(web3=self.web3, address=Address(self.config.get_config()["0x"]["exchange"]))

        # so the token names are printed nicer
        ERC20Token.register_token(self.radar_relay.zrx_token(), 'ZRX')
        ERC20Token.register_token(self.ether_token.address, '0x-WETH')

    def args(self, parser: argparse.ArgumentParser):
        parser.add_argument("--config", type=str, required=True,
                            help="Buy/sell bands configuration file")

    def startup(self):
        self.approve()

    def shutdown(self):
        pass

    def approve(self):
        """Approve 0x to access our 0x-WETH and SAI, so we can sell it on the exchange."""
        self.radar_relay.approve([self.ether_token, self.sai], directly())


if __name__ == '__main__':
    SaiMakerRadarRelay(sys.argv[1:]).start()
