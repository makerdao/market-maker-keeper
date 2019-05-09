#!/usr/bin/env bash

dir="$(dirname "$0")"
echo $dir

source $dir/_virtualenv/bin/activate || exit

export PYTHONPATH=$PYTHONPATH:$dir:$dir/lib/pymaker:$dir/lib/pyexchange:$dir/lib/ethgasstation-client:$dir/lib/gdax-client py.test --cov=market_maker_keeper --cov-report=term --cov-append tests/

exec python3 -m pytest tests/test_airswap_market_maker_keeper.py
