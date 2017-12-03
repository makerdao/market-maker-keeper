#!/bin/sh

py.test --cov=keeper --cov=pymaker --cov-report=term --cov-append tests/
