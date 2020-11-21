#!/bin/sh
source _virtualenv/bin/activate

# Remove existing container if tests not gracefully stopped
docker-compose down

# Create environment file
touch .env

# Start ganache
docker-compose up -d ganache

# Wait for initalization
sleep 2

PYTHONPATH=$PYTHONPATH:./lib/pymaker:./lib/pyexchange:./lib/ethgasstation-client:./lib/gdax-client:./lib/pygasprice-client py.test -x --cov=market_maker_keeper --cov-report=term --cov-append tests/

# Cleanup local node
docker-compose down
