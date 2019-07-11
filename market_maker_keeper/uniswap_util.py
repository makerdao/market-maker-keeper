# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2019 grandizzy
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
import pandas as pd
from web3 import Web3
from time import gmtime, strftime
import calendar
import json
import ast
import logging

uniswap_abi = [{"name": "NewExchange", "inputs": [{"type": "address", "name": "token", "indexed": True}, {"type": "address", "name": "exchange", "indexed": True}], "anonymous": False, "type": "event"}]
exchangeABI ='[{"name": "TokenPurchase", "inputs": [{"type": "address", "name": "buyer", "indexed": true}, {"type": "uint256", "name": "eth_sold", "indexed": true}, {"type": "uint256", "name": "tokens_bought", "indexed": true}], "anonymous": false, "type": "event"}, {"name": "EthPurchase", "inputs": [{"type": "address", "name": "buyer", "indexed": true}, {"type": "uint256", "name": "tokens_sold", "indexed": true}, {"type": "uint256", "name": "eth_bought", "indexed": true}], "anonymous": false, "type": "event"}, {"name": "AddLiquidity", "inputs": [{"type": "address", "name": "provider", "indexed": true}, {"type": "uint256", "name": "eth_amount", "indexed": true}, {"type": "uint256", "name": "token_amount", "indexed": true}], "anonymous": false, "type": "event"}, {"name": "RemoveLiquidity", "inputs": [{"type": "address", "name": "provider", "indexed": true}, {"type": "uint256", "name": "eth_amount", "indexed": true}, {"type": "uint256", "name": "token_amount", "indexed": true}], "anonymous": false, "type": "event"}, {"name": "Transfer", "inputs": [{"type": "address", "name": "_from", "indexed": true}, {"type": "address", "name": "_to", "indexed": true}, {"type": "uint256", "name": "_value", "indexed": false}], "anonymous": false, "type": "event"}, {"name": "Approval", "inputs": [{"type": "address", "name": "_owner", "indexed": true}, {"type": "address", "name": "_spender", "indexed": true}, {"type": "uint256", "name": "_value", "indexed": false}], "anonymous": false, "type": "event"}, {"name": "setup", "outputs": [], "inputs": [{"type": "address", "name": "token_addr"}], "constant": false, "payable": false, "type": "function", "gas": 175875}, {"name": "addLiquidity", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "min_liquidity"}, {"type": "uint256", "name": "max_tokens"}, {"type": "uint256", "name": "deadline"}], "constant": false, "payable": true, "type": "function", "gas": 82605}, {"name": "removeLiquidity", "outputs": [{"type": "uint256", "name": "out"}, {"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "amount"}, {"type": "uint256", "name": "min_eth"}, {"type": "uint256", "name": "min_tokens"}, {"type": "uint256", "name": "deadline"}], "constant": false, "payable": false, "type": "function", "gas": 116814}, {"name": "__default__", "outputs": [], "inputs": [], "constant": false, "payable": true, "type": "function"}, {"name": "ethToTokenSwapInput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "min_tokens"}, {"type": "uint256", "name": "deadline"}], "constant": false, "payable": true, "type": "function", "gas": 12757}, {"name": "ethToTokenTransferInput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "min_tokens"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "recipient"}], "constant": false, "payable": true, "type": "function", "gas": 12965}, {"name": "ethToTokenSwapOutput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_bought"}, {"type": "uint256", "name": "deadline"}], "constant": false, "payable": true, "type": "function", "gas": 50455}, {"name": "ethToTokenTransferOutput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_bought"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "recipient"}], "constant": false, "payable": true, "type": "function", "gas": 50663}, {"name": "tokenToEthSwapInput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_sold"}, {"type": "uint256", "name": "min_eth"}, {"type": "uint256", "name": "deadline"}], "constant": false, "payable": false, "type": "function", "gas": 47503}, {"name": "tokenToEthTransferInput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_sold"}, {"type": "uint256", "name": "min_eth"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "recipient"}], "constant": false, "payable": false, "type": "function", "gas": 47712}, {"name": "tokenToEthSwapOutput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "eth_bought"}, {"type": "uint256", "name": "max_tokens"}, {"type": "uint256", "name": "deadline"}], "constant": false, "payable": false, "type": "function", "gas": 50175}, {"name": "tokenToEthTransferOutput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "eth_bought"}, {"type": "uint256", "name": "max_tokens"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "recipient"}], "constant": false, "payable": false, "type": "function", "gas": 50384}, {"name": "tokenToTokenSwapInput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_sold"}, {"type": "uint256", "name": "min_tokens_bought"}, {"type": "uint256", "name": "min_eth_bought"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "token_addr"}], "constant": false, "payable": false, "type": "function", "gas": 51007}, {"name": "tokenToTokenTransferInput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_sold"}, {"type": "uint256", "name": "min_tokens_bought"}, {"type": "uint256", "name": "min_eth_bought"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "recipient"}, {"type": "address", "name": "token_addr"}], "constant": false, "payable": false, "type": "function", "gas": 51098}, {"name": "tokenToTokenSwapOutput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_bought"}, {"type": "uint256", "name": "max_tokens_sold"}, {"type": "uint256", "name": "max_eth_sold"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "token_addr"}], "constant": false, "payable": false, "type": "function", "gas": 54928}, {"name": "tokenToTokenTransferOutput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_bought"}, {"type": "uint256", "name": "max_tokens_sold"}, {"type": "uint256", "name": "max_eth_sold"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "recipient"}, {"type": "address", "name": "token_addr"}], "constant": false, "payable": false, "type": "function", "gas": 55019}, {"name": "tokenToExchangeSwapInput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_sold"}, {"type": "uint256", "name": "min_tokens_bought"}, {"type": "uint256", "name": "min_eth_bought"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "exchange_addr"}], "constant": false, "payable": false, "type": "function", "gas": 49342}, {"name": "tokenToExchangeTransferInput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_sold"}, {"type": "uint256", "name": "min_tokens_bought"}, {"type": "uint256", "name": "min_eth_bought"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "recipient"}, {"type": "address", "name": "exchange_addr"}], "constant": false, "payable": false, "type": "function", "gas": 49532}, {"name": "tokenToExchangeSwapOutput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_bought"}, {"type": "uint256", "name": "max_tokens_sold"}, {"type": "uint256", "name": "max_eth_sold"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "exchange_addr"}], "constant": false, "payable": false, "type": "function", "gas": 53233}, {"name": "tokenToExchangeTransferOutput", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_bought"}, {"type": "uint256", "name": "max_tokens_sold"}, {"type": "uint256", "name": "max_eth_sold"}, {"type": "uint256", "name": "deadline"}, {"type": "address", "name": "recipient"}, {"type": "address", "name": "exchange_addr"}], "constant": false, "payable": false, "type": "function", "gas": 53423}, {"name": "getEthToTokenInputPrice", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "eth_sold"}], "constant": true, "payable": false, "type": "function", "gas": 5542}, {"name": "getEthToTokenOutputPrice", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_bought"}], "constant": true, "payable": false, "type": "function", "gas": 6872}, {"name": "getTokenToEthInputPrice", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "tokens_sold"}], "constant": true, "payable": false, "type": "function", "gas": 5637}, {"name": "getTokenToEthOutputPrice", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "uint256", "name": "eth_bought"}], "constant": true, "payable": false, "type": "function", "gas": 6897}, {"name": "tokenAddress", "outputs": [{"type": "address", "name": "out"}], "inputs": [], "constant": true, "payable": false, "type": "function", "gas": 1413}, {"name": "factoryAddress", "outputs": [{"type": "address", "name": "out"}], "inputs": [], "constant": true, "payable": false, "type": "function", "gas": 1443}, {"name": "balanceOf", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "address", "name": "_owner"}], "constant": true, "payable": false, "type": "function", "gas": 1645}, {"name": "transfer", "outputs": [{"type": "bool", "name": "out"}], "inputs": [{"type": "address", "name": "_to"}, {"type": "uint256", "name": "_value"}], "constant": false, "payable": false, "type": "function", "gas": 75034}, {"name": "transferFrom", "outputs": [{"type": "bool", "name": "out"}], "inputs": [{"type": "address", "name": "_from"}, {"type": "address", "name": "_to"}, {"type": "uint256", "name": "_value"}], "constant": false, "payable": false, "type": "function", "gas": 110907}, {"name": "approve", "outputs": [{"type": "bool", "name": "out"}], "inputs": [{"type": "address", "name": "_spender"}, {"type": "uint256", "name": "_value"}], "constant": false, "payable": false, "type": "function", "gas": 38769}, {"name": "allowance", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [{"type": "address", "name": "_owner"}, {"type": "address", "name": "_spender"}], "constant": true, "payable": false, "type": "function", "gas": 1925}, {"name": "name", "outputs": [{"type": "bytes32", "name": "out"}], "inputs": [], "constant": true, "payable": false, "type": "function", "gas": 1623}, {"name": "symbol", "outputs": [{"type": "bytes32", "name": "out"}], "inputs": [], "constant": true, "payable": false, "type": "function", "gas": 1653}, {"name": "decimals", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [], "constant": true, "payable": false, "type": "function", "gas": 1683}, {"name": "totalSupply", "outputs": [{"type": "uint256", "name": "out"}], "inputs": [], "constant": true, "payable": false, "type": "function", "gas": 1713}]'


class UniswapUtil:
    logger = logging.getLogger()
    pd.options.mode.chained_assignment = None
    pd.set_option('display.max_columns', 21)
    pd.set_option('display.max_colwidth', -1)

    def __init__(self, web3: Web3, dai_contract_address: str, dai_address: str, factory_contract: str):
        assert(isinstance(web3, Web3))

        self.web3 = web3

        self.dai_contract_address = dai_contract_address
        self.contract_address_lower = dai_contract_address.lower()
        self.dai_address = dai_address
        uniswap = web3.eth.contract(factory_contract, abi=uniswap_abi)

        events = uniswap.events.NewExchange.createFilter(fromBlock=6627917).get_all_entries()
        self.token_exchange = {e.args.token: e.args.exchange.lower() for e in events}
        self.exchange_token = {e.args.exchange.lower(): e.args.token for e in events}
        self.lowerExch_CheckSumExchAddr = {e.args.exchange.lower(): e.args.exchange for e in events}
        self.uniswap_exch_addr = set(self.exchange_token.keys())
        self.exch_abi = json.loads(exchangeABI)
        self.exchange_dai_contract = web3.eth.contract(address=dai_address, abi=self.exch_abi)

    def exchange_Token_contract(self, token_address):
        return self.web3.eth.contract(address=token_address, abi=self.exch_abi)

    #translates a hexidecimal input string into what uniswap functions are being called along with what inputs
    def translate_input(self, input_str):
        try:
            return str(self.exchange_dai_contract.decode_function_input(input_str))
        except:
            self.logger.error("----- problem input string is below -----")
            self.logger.error(input_str)
            return "something wrong with input_str"

    @staticmethod
    def to_int(x):
        return int(x, 16)

    @staticmethod
    def get_epoch_time():
        return calendar.timegm(gmtime())

    #get live price if you pass in the exchange pair contract address, so MKR-ETH would return a price around 2.9 currently as 2.9 ETH equals a MKR
    def getTokenPriceInETH(self, to_address):
        checkSumAddr = self.lowerExch_CheckSumExchAddr[to_address]
        contract = self.exchange_Token_contract(self.exchange_token[to_address])
        ETH_Reserve = self.web3.eth.getBalance(checkSumAddr)
        balanceOf = contract.functions.balanceOf(checkSumAddr).call()
        return ETH_Reserve/balanceOf

    #this is used in lambda functions a lot below, take in a string which looks like a dictionary and get value from particular key
    @staticmethod
    def str_to_dict(x, key_value):
        return ast.literal_eval(x[:-1].lstrip())[key_value]

    #look at following website https://docs.uniswap.io/smart-contract-integration/interface
    #for a particular set of input functions, it is getting all the ETH being sent into the ETH-DAI uniswap contract
    def get_eth_from_df(self, df):
        function_text = {'(<Function ethToTokenSwapInput(uint256,uint256)', '(<Function ethToTokenTransferInput(uint256,uint256,address)'}
        eth_df = df[df['Input_1st'].isin(function_text)]
        if eth_df.empty:
            return 0
        eth_df["deadline"] = eth_df.loc[:,"Input_2nd"].apply(lambda x: int(self.str_to_dict(x, 'deadline')))
        epoch_time_now = self.get_epoch_time()
        filter_eth_df = eth_df[eth_df["deadline"] > epoch_time_now]
        total_eth = filter_eth_df['readable_value'].sum()/10**18
        return total_eth

    #below functions are rarely called but uniswap taker users specifies token output so we are trying to determine the ETH input
    def get_eth_from_df_output(self, df):
        function_text = {'(<Function ethToTokenSwapOutput(uint256,uint256)', '(<Function ethToTokenTransferOutput(uint256,uint256,address)'}
        eth_df = df[df['Input_1st'].isin(function_text)]
        if eth_df.empty:
            return 0
        eth_df["tokens_bought"] = eth_df.loc[:,"Input_2nd"].apply(lambda x: float(self.str_to_dict(x, 'tokens_bought')))
        eth_df["price_in_eth"] = eth_df.loc[:,"to"].apply(lambda x: self.getTokenPriceInETH(x))
        eth_df["ETH_sold"] = eth_df["tokens_bought"] * eth_df["price_in_eth"]
        eth_df["deadline"] = eth_df.loc[:,"Input_2nd"].apply(lambda x: int(self.str_to_dict(x, 'deadline')))
        epoch_time_now = self.get_epoch_time()
        filter_eth_df = eth_df[eth_df["deadline"] > epoch_time_now]
        total_eth = filter_eth_df['ETH_sold'].sum()/10**18
        return total_eth

    #here we are determining token amount input, when user sends in tokens to get ETH
    def get_token_from_df(self, df):
        function_text = {'(<Function tokenToEthSwapInput(uint256,uint256,uint256)', '(<Function ethToTokenTransferInput(uint256,uint256,uint256,address)'}
        token_df = df[df['Input_1st'].isin(function_text)]
        if token_df.empty:
            return 0
        #you need to use float here, ran into an interesting overflow problem because Ethereum uses such large numbers
        token_df["tokens_sold"] = token_df.loc[:,"Input_2nd"].apply(lambda x: float(self.str_to_dict(x, 'tokens_sold')))
        token_df["deadline"] = token_df.loc[:,"Input_2nd"].apply(lambda x: int(self.str_to_dict(x, 'deadline')))
        epoch_time_now = self.get_epoch_time()
        filter_token_df = token_df[token_df["deadline"] > epoch_time_now]
        total_tokens = filter_token_df["tokens_sold"].sum()/10**18
        return total_tokens

    #below functions are rarely used, so here user is trying input tokens but specifies how much ETH they want to get ahead of time, note ETH is an output
    def get_token_from_df_output(self, df):
        function_text = {'(<Function tokenToEthSwapOutput(uint256,uint256,uint256)', '(<Function ethToTokenTransferOutput(uint256,uint256,uint256,address)'}
        token_df = df[df['Input_1st'].isin(function_text)]
        if token_df.empty:
            return 0
        token_df["eth_bought"] = token_df.loc[:,"Input_2nd"].apply(lambda x: float(self.str_to_dict(x, 'eth_bought')))
        token_df["price_in_eth"] = token_df.loc[:,"to"].apply(lambda x: self.getTokenPriceInETH(x))
        token_df["tokens_sold"] = token_df["eth_bought"] / token_df["price_in_eth"]
        token_df["deadline"] = token_df.loc[:,"Input_2nd"].apply(lambda x: int(self.str_to_dict(x, 'deadline')))
        epoch_time_now = self.get_epoch_time()
        filter_token_df = token_df[token_df["deadline"] > epoch_time_now]
        total_tokens = filter_token_df["tokens_sold"].sum()/10**18
        return total_tokens

    #here we are sending tokens to get other tokens but in this particular example its how many tokens we send to the DAI address, so we just need to keep track of tokens sent in
    def get_token_fromTokenToToken(self, df):
        function_text = {'(<Function tokenToTokenSwapInput(uint256,uint256,uint256,uint256,address)', '(<Function tokenToTokenTransferInput(uint256,uint256,uint256,uint256,address,address)'}
        token_df = df[df['Input_1st'].isin(function_text)]
        if token_df.empty:
            return 0
        token_df["tokens_sold"] = token_df.loc[:,"Input_2nd"].apply(lambda x: float(self.str_to_dict(x, 'tokens_sold')))
        token_df["deadline"] = token_df.loc[:,"Input_2nd"].apply(lambda x: int(self.str_to_dict(x, 'deadline')))
        epoch_time_now = self.get_epoch_time()
        filter_token_df = token_df[token_df["deadline"] > epoch_time_now]
        total_tokens = filter_token_df["tokens_sold"].sum()/10**18
        return total_tokens

    #here the user is specifying the tokens bought so we need to work backwards and figure out the tokens sent in or sold
    def get_token_fromTokenToToken_output(self, df):
        function_text = {'(<Function tokenToTokenSwapOutput(uint256,uint256,uint256,uint256,address)', '(<Function tokenToTokenTransferOutput(uint256,uint256,uint256,uint256,address,address)'}
        token_df = df[df['Input_1st'].isin(function_text)]
        if token_df.empty:
            return 0
        token_df["tokens_bought"] = token_df.loc[:,"Input_2nd"].apply(lambda x: float(self.str_to_dict(x, 'tokens_bought')))
        token_df["tokens_final_addr"] = token_df.loc[:,"Input_2nd"].apply(lambda x: self.str_to_dict(x, 'token_addr'))
        token_df["tokens_final_exch_addr"] = token_df["tokens_final_addr"].apply(lambda x: self.token_exchange[x])
        token_df["price_in_eth_output"] = token_df.loc[:,"tokens_final_exch_addr"].apply(lambda x: self.getTokenPriceInETH(x))
        token_df["ETH_sold"] = token_df["tokens_bought"] * token_df["price_in_eth_output"]
        token_df["price_in_eth_input"] = token_df.loc[:,"to"].apply(lambda x: self.getTokenPriceInETH(x))
        token_df["tokens_sold"] = token_df["ETH_sold"] /token_df["price_in_eth_input"]
        #need to really think here
        token_df["deadline"] = token_df.loc[:,"Input_2nd"].apply(lambda x: int(self.str_to_dict(x, 'deadline')))
        epoch_time_now = self.get_epoch_time()
        filter_token_df = token_df[token_df["deadline"] > epoch_time_now]
        total_tokens = filter_token_df["tokens_sold"].sum()/10**18
        return total_tokens

    #here the end token is DAI in our particular example, and we are trying to determine how much ETH is being sent to the ETH-DAI pair, but we are given input token which can be any token
    def get_eth_fromTokenToToken(self, df, final_token_addr):
        function_text = {'(<Function tokenToTokenSwapInput(uint256,uint256,uint256,uint256,address)', '(<Function tokenToTokenTransferInput(uint256,uint256,uint256,uint256,address,address)'}
        if df.empty:
            return 0
        token_df = df[df['Input_1st'].isin(function_text)]
        if token_df.empty:
            return 0
        token_df["tokens_final_addr"] = token_df.loc[:,"Input_2nd"].apply(lambda x: self.str_to_dict(x, 'token_addr'))
        filtered_df =  token_df[token_df["tokens_final_addr"]== final_token_addr]
        if filtered_df.empty:
            return 0
        filtered_df["tokens_sold"] = filtered_df.loc[:,"Input_2nd"].apply(lambda x: float(self.str_to_dict(x, 'tokens_sold')))
        filtered_df["deadline"] = filtered_df.loc[:,"Input_2nd"].apply(lambda x: int(self.str_to_dict(x, 'deadline')))
        filtered_df["input_token_address"] = filtered_df.loc[:,"to"].apply(lambda x: self.exchange_token[x])
        filtered_df["price_in_eth"] = filtered_df.loc[:,"to"].apply(lambda x: self.getTokenPriceInETH(x))
        filtered_df["ETH_bought"] = filtered_df["tokens_sold"] * filtered_df["price_in_eth"]
        epoch_time_now = self.get_epoch_time()
        filter_token_df = filtered_df[filtered_df["deadline"] > epoch_time_now]

        self.logger.debug(filter_token_df[["to","from","readable_gas","readable_gasPrice","readable_input","Input_1st","Input_2nd","ETH_bought","price_in_eth" ,"input_token_address","hash","deadline"]])
        total_eth = filter_token_df["ETH_bought"].sum()/10**18
        return total_eth

    #here we are given output token  or tokens_bought and we need to know input ETH
    def get_eth_fromTokenToToken_output(self, df, final_token_addr):
        function_text = {'(<Function tokenToTokenSwapOutput(uint256,uint256,uint256,uint256,address)', '(<Function tokenToTokenTransferOutput(uint256,uint256,uint256,uint256,address,address)'}
        if df.empty:
            return 0
        token_df = df[df['Input_1st'].isin(function_text)]
        if token_df.empty:
            return 0
        token_df["tokens_final_addr"] = token_df.loc[:,"Input_2nd"].apply(lambda x: self.str_to_dict(x, 'token_addr'))
        filtered_df =  token_df[token_df["tokens_final_addr"]== final_token_addr]
        if filtered_df.empty:
            return 0
        filtered_df["tokens_bought"] = filtered_df.loc[:,"Input_2nd"].apply(lambda x: float(self.str_to_dict(x, 'tokens_bought')))
        filtered_df["deadline"] = filtered_df.loc[:,"Input_2nd"].apply(lambda x: int(self.str_to_dict(x, 'deadline')))
        filtered_df["input_token_address"] = filtered_df.loc[:,"to"].apply(lambda x: self.exchange_token[x])
        filtered_df["tokens_final_exch_addr"] = filtered_df["tokens_final_addr"].apply(lambda x: self.token_exchange[x])
        filtered_df["price_in_eth_output"] = filtered_df.loc[:,"tokens_final_exch_addr"].apply(lambda x: self.getTokenPriceInETH(x))
        filtered_df["ETH_sold"] = filtered_df["tokens_bought"] * filtered_df["price_in_eth_output"]
        epoch_time_now = self.get_epoch_time()
        filter_token_df = filtered_df[filtered_df["deadline"] > epoch_time_now]

        self.logger.debug(filter_token_df[["to","from","readable_gas","readable_gasPrice","readable_input","Input_1st","Input_2nd","ETH_sold","price_in_eth_output" ,"input_token_address","hash","deadline"]])
        total_eth = filter_token_df["ETH_sold"].sum()/10**18
        return total_eth

    def pool_liquidity(self, ETH_in, Token_in):
        Token_start = self.exchange_dai_contract.functions.balanceOf(self.dai_contract_address).call()/10**18
        ETH_start = self.web3.eth.getBalance(self.dai_contract_address)/10**18

        ETH_end = (ETH_start + ETH_in) * Token_start / (Token_start + Token_in)
        Token_end = (Token_start + Token_in) * ETH_start / (ETH_start + ETH_in)
        ETH_out = ETH_end - ETH_start - ETH_in
        Token_out = Token_end - Token_start - Token_in
        return (ETH_end, Token_end, ETH_out, Token_out)

    def get_future_price(self):

        #getting parity pending transactions
        a = self.web3.manager.request_blocking('parity_pendingTransactions', [])
        #converting the pending to a pandas dataframe
        df = pd.DataFrame(a)
        #pruning the data frame so we are only dealing with pending transactions where the to address is to a uniswap exchange contract addess
        uni_df1 = df[df["to"].isin(self.uniswap_exch_addr)]

        #checking if we have a non empty datafame
        if len(uni_df1.index) != 0:
            uni_df1.loc[:,'readable_input'] = uni_df1['input'].apply(self.translate_input)
            uni_df  = uni_df1[uni_df1['readable_input'] != "something wrong with input_str"]
            bad_data_df = uni_df1[uni_df1['readable_input'] == "something wrong with input_str"]
            if len(bad_data_df.index) != 0:
                self.logger.info(f'----there is some bad input data for below dataframe ------ the time is {strftime("%Y-%m-%d %H:%M:%S", gmtime())}')
                self.logger.info(bad_data_df)
            #splitting the readable input into the functions being called vs the input numbers being sent in
            if len(uni_df.index) != 0:
                new = uni_df.loc[:,'readable_input'].str.split(">,", n=1, expand = True )
                uni_df.loc[:,"Input_1st"] = new[0]
                uni_df.loc[:,"Input_2nd"] = new[1]
                uni_df.loc[:,'readable_gas'] = uni_df['gas'].apply(self.to_int)
                uni_df.loc[:,'readable_gasPrice'] = uni_df['gasPrice'].apply(self.to_int)
                uni_df.loc[:,'readable_value'] = uni_df['value'].apply(self.to_int).astype(float)
                #printing all pending transactions that are being sent to any uniswap exchange address
                self.logger.debug(uni_df[["to","from","readable_gas","readable_gasPrice","readable_input","Input_1st","Input_2nd","readable_value","hash"]])
                #here we are pruning the dataframe to only pending being sent to the DAI-ETH uniswap exchange contract
                to_df = uni_df[uni_df["to"]==self.contract_address_lower]

                self.logger.info(f'total number of ETH being sent in and sold in a token to token swap '
                                  f'where DAI is final output {self.get_eth_fromTokenToToken(uni_df, self.dai_address)}')
                self.logger.info(f'total number of ETH (output function) being sent in and sold in a token to token swap '
                                  f'where DAI is final output {self.get_eth_fromTokenToToken_output(uni_df, self.dai_address)}')
                self.logger.info(f'total number of ETH being sent in is {self.get_eth_from_df(to_df)}')
                self.logger.info(f'total number of ETH (output function) being sent in is {self.get_eth_from_df_output(to_df)}')
                self.logger.info(f'total number of DAI being sent in is {self.get_token_from_df(to_df)}')
                self.logger.info(f'total number of DAI (output function) being sent in is {self.get_token_from_df_output(to_df)}')
                self.logger.info(f'total number of DAI being sent in and sold in a token to token swap is {self.get_token_fromTokenToToken(to_df)}')
                self.logger.info(f'total number of DAI (output function) being sent in and sold in a token to token swap is {self.get_token_fromTokenToToken_output(to_df)}')
                ETH_in = self.get_eth_fromTokenToToken(uni_df, self.dai_address) + self.get_eth_fromTokenToToken_output(uni_df, self.dai_address) \
                         + self.get_eth_from_df(to_df) + self.get_eth_from_df_output(to_df)
                Token_in = self.get_token_from_df(to_df) + self.get_token_from_df_output(to_df) \
                           + self.get_token_fromTokenToToken(to_df) + self.get_token_fromTokenToToken_output(to_df)
                ETH_end, Token_end, ETH_out, Token_out = self.pool_liquidity(ETH_in, Token_in)
                self.logger.info(f'current price inside util function is {self.getTokenPriceInETH(self.dai_contract_address.lower())} and future price is {ETH_end/Token_end} ')
                return ETH_end/Token_end

        return self.getTokenPriceInETH(self.dai_contract_address.lower())



