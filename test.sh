#!/bin/sh
source _virtualenv/bin/activate

# Pull the docker image
docker pull makerdao/testchain-pymaker:unit-testing

# Remove existing container if tests not gracefully stopped
docker-compose down

# Start ganache
docker-compose up -d ganache

# Start parity and wait to initialize
docker-compose up -d parity
sleep 2

PYTHONPATH=$PYTHONPATH:./lib/pymaker:./lib/pyexchange:./lib/ethgasstation-client:./lib/gdax-client py.test -x --cov=market_maker_keeper --cov-report=term --cov-append tests/test_uniswapv2_market_maker_keeper.py

# Cleanup local node
docker-compose down
