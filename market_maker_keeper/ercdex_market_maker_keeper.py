# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2018 bargst
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

import sys
import time

from market_maker_keeper.zrxv2_market_maker_keeper import ZrxV2MarketMakerKeeper
from pyexchange.ercdex import ErcdexApi


class ErcdexMarketMakerKeeper(ZrxV2MarketMakerKeeper):
    """Ercdex is SRAv2 except for canceling of orders"""

    def init_zrx(self):
        super().init_zrx()
        self.zrx_api = ErcdexApi(zrx_exchange=self.zrx_exchange, zrx_api=self.zrx_relayer_api)

    def cancel_order_function(self, order):
        return self.zrx_api.cancel_order(order)


if __name__ == '__main__':
    ErcdexMarketMakerKeeper(sys.argv[1:]).main()
