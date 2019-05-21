#!/bin/bash

cd "$(dirname "$0")"
#source ../env
source ./env
export CURRENT_DIR=$(pwd)

#cd ../../market-maker-keeper

#    --spread-feed ${STREAMER_SOCKET:?}/ETHDAI_spread/socket \
#    --order-history ${ORDER_HISTORY_SERVICE_POST:?}/ddex_server1/WETH-DAI \

bin/airswap-market-maker-keeper \
    --rpc-host ${RPC_HOST:?} \
    --rpc-port ${RPC_PORT:?} \
    --eth-from ${V3_AIRSWAP_SERVER1_ADDRESS:?} \
    --eth-key ${V3_AIRSWAP_SERVER1_KEY?:} \
    --exchange-address ${EXCHANGE_ADDRESS:?} \
    --localhost-orderserver-port ${ORDERSERVER_PORT:?} \
    --airswap-api-server ${AIRSWAP_API_SERVER} \
    --pair WETH-DAI \
    --buy-token-address ${ETH_ADDRESS:?} \
    --sell-token-address ${SCD_ADDRESS:?} \
    --config ${CURRENT_DIR?:}/airswap-ethdai-bands.json \
    --price-feed eth_dai \
    $@ 2> >(tee -a ${CURRENT_DIR?:}/airswap-ethdai.log >&2)
