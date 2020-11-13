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
            # TODO: determine default return
            return None

    # TODO: standardize
    # def stake_liquidity(self) -> Optional[Receipt]:
    #         staking_receipt = self.staking_rewards.withdraw_all_liquidity().transact(gas_price=self.gas_price)

    #         if staking_receipt is not None and staking_receipt.successful:
    #             gas_used = staking_receipt.gas_used
    #             gas_price = Wad(self.web3.eth.getTransaction(staking_receipt.transaction_hash.hex())['gasPrice'])
    #             tx_fee = Wad.from_number(gas_used) * gas_price

    #             self.logger.info(f"Withdrew all staked liquidity tokens "
    #                                 f"tx fee used {tx_fee} "
    #                                 f"with tx hash {staking_receipt.transaction_hash.hex()}")
                
    #             return staking_receipt
    #         else:
    #             self.logger.error(f"Unable to unstake liquidity tokens")
    #             return None

    # def unstake_liquidity(self) -> Optional[Receipt]:
    #         staking_receipt = self.staking_rewards.withdraw_all_liquidity().transact(gas_price=self.gas_price)

    #         if staking_receipt is not None and staking_receipt.successful:
    #             gas_used = staking_receipt.gas_used
    #             gas_price = Wad(self.web3.eth.getTransaction(staking_receipt.transaction_hash.hex())['gasPrice'])
    #             tx_fee = Wad.from_number(gas_used) * gas_price

    #             self.logger.info(f"Withdrew all staked liquidity tokens "
    #                                 f"tx fee used {tx_fee} "
    #                                 f"with tx hash {staking_receipt.transaction_hash.hex()}")
                
    #             return staking_receipt
    #         else:
    #             self.logger.error(f"Unable to unstake liquidity tokens")
    #             return None