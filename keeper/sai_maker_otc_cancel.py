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
        self.cancel_offers(self.our_offers(self.otc.active_offers()))

    def our_offers(self, active_offers: list):
        """Return list of offers owned by us."""
        return list(filter(lambda offer: offer.owner == self.our_address, active_offers))

    def cancel_offers(self, offers: list):
        """Cancel offers asynchronously."""
        synchronize([self.otc.kill(offer.offer_id).transact_async(gas_price=self.gas_price) for offer in offers])


if __name__ == '__main__':
    SaiMakerOtcCancel(sys.argv[1:]).start()
