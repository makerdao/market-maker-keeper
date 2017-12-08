#!/bin/sh

PYTHONPATH=$PYTHONPATH:./lib/pymaker py.test --cov=keeper --cov=market_maker_keeper --cov-report=term --cov-append tests/
