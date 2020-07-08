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

from pprint import pformat
from pymaker import Address
from pymaker.model import Token

class TokenConfig:
    def __init__(self, data: dict):
        assert (isinstance(data, dict))

        self.tokens = [Token(name=key,
                             address=Address(value['tokenAddress']) if 'tokenAddress' in value else None,
                             decimals=value['tokenDecimals'] if 'tokenDecimals' in value else 18) for key, value in
                       data['tokens'].items()]

    def __repr__(self):
        return pformat(vars(self))