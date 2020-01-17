#!/bin/bash

set -eu

cd "$(dirname "$0")"
# source ../env
export CURRENT_DIR=$(pwd)

# cd ../../market-maker-keeper

# Being exported as shell variables, not as environment variables
# ./.dev.sh

export ORDER_HISTORY_SERVICE_POST="0.0.0.0:9494"
export STREAMER_PRICE_FEED_SOCKET="0.0.0.0:9595"
export ETORO_API_KEY="f3a8c406-e569-402b-85b6-0bcd102ec856"
export ETORO_SECRET_KEY=`cat .key`
export LOGS_DIR="./"


bin/etoro-market-maker-keeper \
    --etoro-api-key ${ETORO_API_KEY?:} \
    --etoro-account "mm@liquidityproviders.io" \
    --etoro-secret-key ${ETORO_SECRET_KEY?:} \
    --price-feed ${STREAMER_PRICE_FEED_SOCKET?:}/ETH_DAI_PRICE/socket,eth_usdc-pair-midpoint \
    --order-history ${ORDER_HISTORY_SERVICE_POST?:}/etoro_server2/ETH-DAI \
    --pair ETH-DAI \
    --config ${CURRENT_DIR?:}/etoro-bands.json \
    $@ 2> >(tee -a ${LOGS_DIR?:}/etoro_server2-ethdai.log >&2)