#!/usr/bin/env bash

cd "$(dirname "$0")"

set -e

rm -rf _virtualenv
virtualenv _virtualenv
source _virtualenv/bin/activate

# Downgrade pip to avoid issues with new 20.3.0 dependency resolver's handling of conflicting dependencies
pip install pip==20.2.4

# The advantage of using this method, in contrary to just calling `pip install -r requirements.txt` several times,
# is that it can detect different versions of the same dependency and fail with a "Double requirement given"
# error message.
pip install $(cat requirements.txt $(find lib -name requirements.txt | sort) | sort | uniq | sed 's/ *== */==/g')
