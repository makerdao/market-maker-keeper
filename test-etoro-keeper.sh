#!/bin/bash
    
    set -eu

    bin/etoro-market-maker-keeper \
        --etoro-api-server \
        --etoro-api-key "apikey" \
    	--etoro-secret-key "./keyfile" \ 
        --price-feed "GdaxMidpointPriceFeed" \
        --config [path to the json bands configuration file, e.g. bands.json]