#!/bin/sh

PYTHONPATH=$PYTHONPATH:./lib/pymaker:./lib/pyexchange:./lib/gdax-client py.test --cov=market_maker_keeper --cov-report=term --cov-append tests/
