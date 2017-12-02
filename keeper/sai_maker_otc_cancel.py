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

import sys

from pymaker.util import synchronize
from keeper.sai import SaiKeeper


class SaiMakerOtcCancel(SaiKeeper):
    """Tool to cancel all our open orders on OasisDEX."""

    def startup(self):
        self.cancel_orders(self.our_orders(self.otc.get_orders()))

    def our_orders(self, orders: list):
        """Return list of orders owned by us."""
        return list(filter(lambda order: order.owner == self.our_address, orders))

    def cancel_orders(self, orders: list):
        """Cancel orders asynchronously."""
        synchronize([self.otc.kill(order.order_id).transact_async(gas_price=self.gas_price) for order in orders])


if __name__ == '__main__':
    SaiMakerOtcCancel(sys.argv[1:]).start()
