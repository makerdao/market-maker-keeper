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

import argparse
import logging
import sys

from argparse import Namespace
from typing import Optional, Tuple
from web3 import Web3, HTTPProvider

from pymaker import Address
from pyexchange.staking_rewards import StakingRewards
from pyexchange.uniswap_staking_rewards import UniswapStakingRewards


class StakingRewardsFactory:
    @staticmethod
    def create_staking_rewards(arguments: Namespace, web3: Web3) -> StakingRewards:
        if arguments.staking_rewards_name == "UniswapStakingRewards":
            return UniswapStakingRewards(
                web3,
                Address(arguments.eth_from), 
                Address(arguments.staking_rewards_contract_address), 
                arguments.staking_rewards_name)
        else:
            return None
