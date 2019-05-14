#!/usr/bin/env bash

dir="$(dirname "$0")"

source $dir/_virtualenv/bin/activate || exit

export PYTHONPATH=$PYTHONPATH:$dir:$dir/lib/pymaker:$dir/lib/pyexchange:$dir/lib/ethgasstation-client:$dir/lib/gdax-client

exec python3 -m pytest --cov=market_maker_keeper --cov-report=term --cov-append tests/
