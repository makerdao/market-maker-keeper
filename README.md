# market-maker-keeper

[![Build Status](https://travis-ci.org/makerdao/market-maker-keeper.svg?branch=master)](https://travis-ci.org/makerdao/market-maker-keeper)
[![codecov](https://codecov.io/gh/makerdao/market-maker-keeper/branch/master/graph/badge.svg)](https://codecov.io/gh/makerdao/market-maker-keeper)

The _DAI Stablecoin System_ incentivizes external agents, called _keepers_,
to automate certain operations around the Ethereum blockchain.

`market-maker-keeper` is actually a set of keepers that facilitate
market making on the following exchanges:
* OasisDEX (`oasis-market-maker-keeper`),
* EtherDelta (`etherdelta-market-maker-keeper`),
* RadarRelay and ERCdEX (`0x-market-maker-keeper`),
* Paradex (`paradex-market-maker-keeper`),
* DDEX (`ddex-market-maker-keeper`),
* Ethfinex (`ethfinex-market-maker-keeper`),
* GoPax (`gopax-market-maker-keeper`),
* HitBTC (`hitbtc-market-maker-keeper`),
* IDEX (`idex-market-maker-keeper`),
* Bibox (`bibox-market-maker-keeper`),
* OKEX (`okex-market-maker-keeper`),
* TheOcean (`theocean-market-maker-keeper`),
* gate.io (`gateio-market-maker-keeper`).

All these three keepers share some logic and operate in a similar way. They create
a series of orders in so called _bands_, which are configured with a JSON file
containing parameters like spreads, maximum engagement etc. Please see the
_"Bands configuration"_ section below for more details regarding keeper mechanics.

Provided an appropriate price feed is available, most of the market maker keepers
are capable of market-making on any token pair. The EtherDelta and IDEX keepers still are
to some extend bound to the DAI/ETH pair. This will either be changed at some point in the future,
or these two keepers will be discontinued.

This repo also contains an auxiliary tool called `oasis-market-maker-cancel`, which
may be used for emergency cancelling all market maker orders on OasisDEX if the
keeper gets stuck or dies for some reason, or if the network becomes congested.

<https://chat.makerdao.com/channel/keeper>


## Installation

This project uses *Python 3.6.6* and requires *virtualenv* to be installed.

In order to clone the project and install required third-party packages please execute:
```
git clone https://github.com/makerdao/market-maker-keeper.git
cd market-maker-keeper
git submodule update --init --recursive
./install.sh
```

For some known Ubuntu and macOS issues see the [pymaker](https://github.com/makerdao/pymaker) README.

### Installation of `etherdelta-client`

The `etherdelta-market-maker-keeper` keeper utilizes `etherdelta-client` (present in the `lib/pymaker/utils`
directory) to place orders on EtherDelta using _socket.io_. In order to use it, a `node` installation must
be present and `npm install` needs to be run in the `lib/pymaker/utils/etherdelta-client` folder.

This step is not necessary if you only want to use the other keepers from this project.

### Installation of `setzer`

`eth_dai-setzer` and `dai_eth-setzer` price feeds use `setzer` in order to prices Gemini and Kraken.
It is built on top of `setzer` so in order for it to work correctly, `setzer` and its dependencies
must be installed and available to the keepers. Please see: <https://github.com/makerdao/setzer>.


## Bands configuration

### Description

Bands configuration file is directly related to how market maker keepers work. They continuously
monitor and adjust their positions in the order book, maintaining open buy and sell orders
in multiple bands at the same time.

In each buy and sell band, the keepers aim to have open orders for at least `minAmount`.
In both cases, they will ensure the price of open orders stays within the <minMargin,maxMargin>
range from the current price.

When started, keepers places orders for the average amounts (`avgAmount`) in each band,
using use `avgMargin` to calculate the order price.

As long as the price of orders stays within the band (i.e. is in the <minMargin,maxMargin>
range from the current price, which can of course be moving constantly), the keepers
keep them open. If some orders leave the band, they either enter another adjacent band
or fall outside all bands. In case of the latter, they get immediately cancelled. In case of
the former, keepers can keep these orders open as long as their amount is within the
<minAmount,maxAmount>  ranges for the band they just entered. If it is above the maximum,
some open orders will get cancelled and potentially new one will be created to bring the total
amount back within the range. If it is below the minimum, a new order gets created for the remaining
amount so the total amount of orders in this band is equal to `avgAmount`.

The same thing will happen if the total amount of open orders in a band falls below either
`minAmount` as a result of other market participants taking these orders.
In this case also a new order gets created for the remaining amount so the total
amount of orders in this band is equal to `avgAmount`.

Some keepers will constantly use gas to cancel orders (OasisDEX, EtherDelta and 0x)
and create new ones (OasisDEX) as the price changes. Gas usage can be limited
by setting the margin and amount ranges wide enough and also by making sure that bands
are always adjacent to each other and that their <min,max> amount ranges overlap.

### File format
Bands configuration file consists of two main sections: *buyBands* and *sellBands*.
Each section is an array containing one object per each band.

The *minMargin* and *maxMargin* fields in each band object represent the margin (spread) range of that band.
These ranges may not overlap for bands of the same type (_buy_ or _sell_), and should be adjacent to each other
for better keeper performance (less orders will likely get cancelled if the bands are adjacent). The *avgMargin*
represents the margin (spread) of newly created orders within a band.

The next three fields (*minAmount*, *avgAmount* and *maxAmount*) are the minimum, target and maximum keeper
engagement per each band. The *dustCutoff* field is the minimum amount of each single order created in each
individual band, expressed in buy tokens for buy bands and in sell tokens for sell bands. Setting it to
a non-zero value prevents keepers from creating of lot of very tiny orders, which can cost a lot of gas
in case of OasisDEX or can result in too small orders being rejected by other exchanges.

Sample bands configuration file:

```json
{
    "_buyToken": "DAI",
    "buyBands": [
        {
            "minMargin": 0.005,
            "avgMargin": 0.01,
            "maxMargin": 0.02,
            "minAmount": 20.0,
            "avgAmount": 30.0,
            "maxAmount": 40.0,
            "dustCutoff": 0.0
        },
        {
            "minMargin": 0.02,
            "avgMargin": 0.025,
            "maxMargin": 0.03,
            "minAmount": 40.0,
            "avgAmount": 60.0,
            "maxAmount": 80.0,
            "dustCutoff": 0.0
        }
    ],
    "buyLimits": [],

    "_sellToken": "ETH",
    "sellBands": [
        {
            "minMargin": 0.005,
            "avgMargin": 0.01,
            "maxMargin": 0.02,
            "minAmount": 2.5,
            "avgAmount": 5.0,
            "maxAmount": 7.5,
            "dustCutoff": 0.0
        },
        {
            "minMargin": 0.02,
            "avgMargin": 0.025,
            "maxMargin": 0.05,
            "minAmount": 4.0,
            "avgAmount": 6.0,
            "maxAmount": 8.0,
            "dustCutoff": 0.0
        }
    ],
    "sellLimits": []
}
```
 ### Band examples
Let's create some example interactions using the bands above, suppose it is denominated in Dai and the price of 10 Dai is 1 ETH :
* If we look at the first buy band, the initial buy order will be 30 Dai ( *avgAmount* ) with a price of -> `price - (price * avgMargin)` -> `.1 - (.1 * 0.01)` -> 0.099 ETH per Dai.
* If our buy order above (30 DAI @ .099 ETH) gets partially filled (15 Dai are purchased), we will have (15 Dai remaining in the order). This number is below the band's *minAmount* (20 Dai), therefore another whole order of 15 Dai will be placed on the exchange each at the same price (0.099 ETH).
* In addition to buy orders, when the keeper starts up two sell orders will also be placed. For ease of explanation, lets say we are selling ETH priced at 100.00 DAI (5 ETH @ 101 DAI, 6 ETH @ 102.5 DAI). Now imagine the price of ETH suddenly drops to 97.50 DAI, which will push the bands down. The second band is now responsible for both sell orders since they fit inbetween band 2's *minMargin* and *maxMargin*. The keeper will now reset it's bands by:
    1. creating an order in band 1 (5 ETH @ 98.475 DAI) using *avgMargin* and *avgAmount*.
    2. cancelling the second order (5 ETH @ 102.5 DAI) (which is now in band 2) becuase *maxMargin* has been breached `price + (price * maxMargin) = orderPrice` -> `97.5 + (97.5 * 0.05)` -> 102.375 > 102.5
    2. keep the first order (5 ETH @ 101 DAI) (which is now in band 2) becuase it is within *minMargin* and *maxMargin* of band 2
    3. creating an order in band 2 (1 ETH @ 99.937 DAI) using *avgMargin* and *avgAmount*

Leaving us with 3 total orders:
  * band 1 -> (5 ETH @ 98.475 DAI)
  * band 2 -> (5 ETH @ 101 DAI) and (1 ETH @ 99.837 DAI)
 
### Order rate limitation

Two optional sections (*buyLimits* and *sendLimits*) can be used for limiting the maximum rate of orders
created by market maker keepers. Both use the same format:

```json
"buyLimits": [
    {
        "period": "1h",
        "amount": 50.0
    },
    {
        "period": "1d",
        "amount": 200.0
    }
]
```

The amounts are expressed either in terms of the buy or the sell token, depending on the section.
The above snippet imposes a limit of *50.0* buy token within each 60 minute window, and in addition
to that a maximum of *200.0* buy token within each 24h window.

Supported time units are: `s`, `m`, `h`, `d` and `w`.

### Data templating language

The [Jsonnet](https://github.com/google/jsonnet) data templating language can be used
for the configuration file.


## Price feed configuration

Each keeper takes a `--price-feed` commandline argument which determines the price used for market-making.
As of today these are the possible values of this argument:
* `eth_dai` - uses the price from the GDAX WebSocket ETH/USD price feed,
* `eth_dai-setzer` - uses the average of Kraken and Gemini ETH/USD prices,
* `eth_dai-tub` - uses the price feed from `Tub` (only works for keepers being able access an Ethereum node),
* `dai_eth` - inverse of the `eth_dai` price feed,
* `dai_eth-setzer` - inverse of the `eth_dai-setzer` price feed,
* `dai_eth-tub` - inverse of the `eth_dai-tub` price feed,
* `btc_dai` - uses the price from the GDAX WebSocket BTC/USD price feed;
* `dai_btc` - inverse of the `btc_dai` price feed,
* `fixed:1.56` - uses a fixed price, `1.56` in this example,
* `ws://...` or `wss://...` - uses a price feed advertised over a WebSocket connection (custom protocol).

The `--price-feed` commandline argument can also contain a comma-separated list of several different price feeds.
In this case, if one of them becomes unavailable, the next one in the list will be used instead. All listed price
feeds will be constantly running in background, the second one and following ones ready to take over
when the first one becomes unavailable.


## Running keepers

Each keeper is a commandline tool which takes some generic commandline arguments (like `--config`, `--price-feed`,
`--price-feed-expiry`, `--debug` etc.) and also some arguments which are specific to that particular keeper
(Ethereum node parameters and addresses, exchange API keys etc.). All accepted commandline arguments are listed\
in sections below, they can also be discovered by trying to start a keeper with the `--help` argument.

For example, in order to run `oasis-market-maker-keeper` you would first need to deploy an Ethereum node (we
recommend using Parity), generate an account in it, permanently unlock that account, transfer some tokens to it
and then run the keeper with:

```
bin/oasis-market-maker-keeper \
    --rpc-host 127.0.0.1 \
    --rpc-port 8180 \
    --rpc-timeout 30 \
    --eth-from [address of the generated Ethereum account] \
    --tub-address 0x448a5065aebb8e423f0896e6c5d525c040f59af3 \
    --oasis-address 0x14fbca95be7e99c15cc2996c6c9d841e54b79425 \
    --price-feed eth_dai \
    --buy-token-address [address of the quote token, could be DAI] \
    --sell-token-address [address of the base token, could be WETH] \
    --config [path to the json bands configuration file] \
    --smart-gas-price \
    --min-eth-balance 0.2
``` 

For the centralized exchanges, an account will need to be created with the exchange itself, a set of API keys
with trading permissions will usually need to be generated as well and also some tokens
will need to be deposited to the exchange, as the keepers do not handle deposits and withdrawals
themselves.


## `oasis-market-maker-keeper`

This keeper supports market-making on the [OasisDEX](https://oasisdex.com/) exchange.

### Usage

```
usage: oasis-market-maker-keeper [-h] [--rpc-host RPC_HOST]
                                 [--rpc-port RPC_PORT]
                                 [--rpc-timeout RPC_TIMEOUT] --eth-from
                                 ETH_FROM [--tub-address TUB_ADDRESS]
                                 --oasis-address OASIS_ADDRESS
                                 --buy-token-address BUY_TOKEN_ADDRESS
                                 --sell-token-address SELL_TOKEN_ADDRESS
                                 --config CONFIG --price-feed PRICE_FEED
                                 [--price-feed-expiry PRICE_FEED_EXPIRY]
                                 [--spread-feed SPREAD_FEED]
                                 [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                 [--round-places ROUND_PLACES]
                                 [--min-eth-balance MIN_ETH_BALANCE]
                                 [--gas-price GAS_PRICE] [--smart-gas-price]
                                 [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --rpc-timeout RPC_TIMEOUT
                        JSON-RPC timeout (in seconds, default: 10)
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --tub-address TUB_ADDRESS
                        Ethereum address of the Tub contract
  --oasis-address OASIS_ADDRESS
                        Ethereum address of the OasisDEX contract
  --buy-token-address BUY_TOKEN_ADDRESS
                        Ethereum address of the buy token
  --sell-token-address SELL_TOKEN_ADDRESS
                        Ethereum address of the sell token
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --round-places ROUND_PLACES
                        Number of decimal places to round order prices to
                        (default=2)
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper will cease
                        operation
  --gas-price GAS_PRICE
                        Gas price (in Wei)
  --smart-gas-price     Use smart gas pricing strategy, based on the
                        ethgasstation.info feed
  --debug               Enable debug output
```


## `oasis-market-maker-cancel`

This tool immediately cancels all our open orders on [OasisDEX](https://oasisdex.com/). 
It may be used if the `oasis-market-maker-keeper` gets stuck or dies for some reason,
or if the network becomes congested.

### Usage

```
usage: oasis-market-maker-cancel [-h] [--rpc-host RPC_HOST]
                                 [--rpc-port RPC_PORT]
                                 [--rpc-timeout RPC_TIMEOUT] --eth-from
                                 ETH_FROM --oasis-address OASIS_ADDRESS
                                 [--gas-price GAS_PRICE]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --rpc-timeout RPC_TIMEOUT
                        JSON-RPC timeout (in seconds, default: 10)
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --oasis-address OASIS_ADDRESS
                        Ethereum address of the OasisDEX contract
  --gas-price GAS_PRICE
                        Gas price in Wei (default: node default)
```


## `etherdelta-market-maker-keeper`

This keeper supports market-making on the [EtherDelta](https://etherdelta.com/) exchange.

### Usage

```
usage: etherdelta-market-maker-keeper [-h] [--rpc-host RPC_HOST]
                                      [--rpc-port RPC_PORT]
                                      [--rpc-timeout RPC_TIMEOUT] --eth-from
                                      ETH_FROM --tub-address TUB_ADDRESS
                                      --etherdelta-address ETHERDELTA_ADDRESS
                                      --etherdelta-socket ETHERDELTA_SOCKET
                                      [--etherdelta-number-of-attempts ETHERDELTA_NUMBER_OF_ATTEMPTS]
                                      [--etherdelta-retry-interval ETHERDELTA_RETRY_INTERVAL]
                                      [--etherdelta-timeout ETHERDELTA_TIMEOUT]
                                      --config CONFIG --price-feed PRICE_FEED
                                      [--price-feed-expiry PRICE_FEED_EXPIRY]
                                      [--spread-feed SPREAD_FEED]
                                      [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                      --order-age ORDER_AGE
                                      [--order-expiry-threshold ORDER_EXPIRY_THRESHOLD]
                                      [--order-no-cancel-threshold ORDER_NO_CANCEL_THRESHOLD]
                                      --eth-reserve ETH_RESERVE
                                      [--min-eth-balance MIN_ETH_BALANCE]
                                      --min-eth-deposit MIN_ETH_DEPOSIT
                                      --min-sai-deposit MIN_SAI_DEPOSIT
                                      [--cancel-on-shutdown]
                                      [--withdraw-on-shutdown]
                                      [--gas-price GAS_PRICE]
                                      [--smart-gas-price] [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --rpc-timeout RPC_TIMEOUT
                        JSON-RPC timeout (in seconds, default: 10)
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --tub-address TUB_ADDRESS
                        Ethereum address of the Tub contract
  --etherdelta-address ETHERDELTA_ADDRESS
                        Ethereum address of the EtherDelta contract
  --etherdelta-socket ETHERDELTA_SOCKET
                        Ethereum address of the EtherDelta API socket
  --etherdelta-number-of-attempts ETHERDELTA_NUMBER_OF_ATTEMPTS
                        Number of attempts of running the tool to talk to the
                        EtherDelta API socket
  --etherdelta-retry-interval ETHERDELTA_RETRY_INTERVAL
                        Retry interval for sending orders over the EtherDelta
                        API socket
  --etherdelta-timeout ETHERDELTA_TIMEOUT
                        Timeout for sending orders over the EtherDelta API
                        socket
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --order-age ORDER_AGE
                        Age of created orders (in blocks)
  --order-expiry-threshold ORDER_EXPIRY_THRESHOLD
                        Remaining order age (in blocks) at which order is
                        considered already expired, which means the keeper
                        will send a new replacement order slightly ahead
  --order-no-cancel-threshold ORDER_NO_CANCEL_THRESHOLD
                        Remaining order age (in blocks) below which keeper
                        does not try to cancel orders, assuming that they will
                        probably expire before the cancel transaction gets
                        mined
  --eth-reserve ETH_RESERVE
                        Amount of ETH which will never be deposited so the
                        keeper can cover gas
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper will cease
                        operation
  --min-eth-deposit MIN_ETH_DEPOSIT
                        Minimum amount of ETH that can be deposited in one
                        transaction
  --min-sai-deposit MIN_SAI_DEPOSIT
                        Minimum amount of SAI that can be deposited in one
                        transaction
  --cancel-on-shutdown  Whether should cancel all open orders on EtherDelta on
                        keeper shutdown
  --withdraw-on-shutdown
                        Whether should withdraw all tokens from EtherDelta on
                        keeper shutdown
  --gas-price GAS_PRICE
                        Gas price (in Wei)
  --smart-gas-price     Use smart gas pricing strategy, based on the
                        ethgasstation.info feed
  --debug               Enable debug output
```

### Known limitations

* Because of some random database errors, creating some orders randomly fails. This issue has been reported
  to the EtherDelta team (https://github.com/etherdelta/etherdelta.github.io/issues/275), but it
  hasn't been solved yet.

* There is no way to reliably get the current status of the EtherDelta order book, so the keeper
  relies on an assumption that if an order has been sent to EtherDelta it has actually made its way
  to the order book. If it doesn't happen (because of the error mentioned above for example),
  it will be missing from the exchange until its expiration time passes and it will get placed
  again (refreshed) by the keeper.

* Due to the same issue with retrieving the current order book status, the keeper starts with the
  assumption that the order book is empty. If there are already some keeper orders in it, they
  may get recreated again by the keeper so duplicates will exist until the older ones expire.
  That's why it is recommended to wait for the existing orders to expire before starting
  the keeper.

* There is a limit of 10 active orders per side
  (see: https://github.com/etherdelta/etherdelta.github.io/issues/274).


## `0x-market-maker-keeper`

This keeper supports market-making on any 0x exchange which implements the _0x Standard Relayer HTTP API_.

### Usage

```
usage: 0x-market-maker-keeper [-h] [--rpc-host RPC_HOST] [--rpc-port RPC_PORT]
                              [--rpc-timeout RPC_TIMEOUT] --eth-from ETH_FROM
                              --exchange-address EXCHANGE_ADDRESS
                              --relayer-api-server RELAYER_API_SERVER
                              [--relayer-per-page RELAYER_PER_PAGE]
                              --buy-token-address BUY_TOKEN_ADDRESS
                              --sell-token-address SELL_TOKEN_ADDRESS --config
                              CONFIG --price-feed PRICE_FEED
                              [--price-feed-expiry PRICE_FEED_EXPIRY]
                              [--spread-feed SPREAD_FEED]
                              [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                              --order-expiry ORDER_EXPIRY
                              [--order-expiry-threshold ORDER_EXPIRY_THRESHOLD]
                              [--min-eth-balance MIN_ETH_BALANCE]
                              [--cancel-on-shutdown] [--gas-price GAS_PRICE]
                              [--smart-gas-price] [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --rpc-timeout RPC_TIMEOUT
                        JSON-RPC timeout (in seconds, default: 10)
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --exchange-address EXCHANGE_ADDRESS
                        Ethereum address of the 0x Exchange contract
  --relayer-api-server RELAYER_API_SERVER
                        Address of the 0x Relayer API
  --relayer-per-page RELAYER_PER_PAGE
                        Number of orders to fetch per one page from the 0x
                        Relayer API (default: 100)
  --buy-token-address BUY_TOKEN_ADDRESS
                        Ethereum address of the buy token
  --sell-token-address SELL_TOKEN_ADDRESS
                        Ethereum address of the sell token
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --order-expiry ORDER_EXPIRY
                        Expiration time of created orders (in seconds)
  --order-expiry-threshold ORDER_EXPIRY_THRESHOLD
                        How long before order expiration it is considered
                        already expired (in seconds)
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper will cease
                        operation
  --cancel-on-shutdown  Whether should cancel all open orders on keeper
                        shutdown
  --gas-price GAS_PRICE
                        Gas price (in Wei)
  --smart-gas-price     Use smart gas pricing strategy, based on the
                        ethgasstation.info feed
  --debug               Enable debug output
```

### Known limitations

* This keeper is confirmed to work with RadarRelay and ERCdEX.

* In case of RadarRelay, expired and/or taken orders to not disappear from the UI immediately. Apparently they run
  a backend process called _chain watching service_, which for tokens with little liquidity kicks in only
  every 10 minutes and does order pruning. Because of that, if we configure the keeper to refresh
  the orders too frequently (i.e. if the `--order-expiry` will be too low), the exchange users will
  see two or even more duplicates of market maker orders.

* The _0x Standard Relayer HTTP API_ specifies 100 as the maximal page size for querying open orders.
  Having said that, some exchanges (e.g. RadarRelay) support more than that, so the `--relayer-per-page`
  argument can be used to increase this limit. Just bear in mind this is against the spec.

* Relayers tend to silently discard orders, for example if the ZRX token balance available in keeper account
  is too low. Even after successful order placement confirmation from the API the order may still disappear
  one or two seconds later.


## `paradex-market-maker-keeper`

This keeper supports market-making on the [Paradex](https://app.paradex.io/) exchange.

### Usage

```
usage: paradex-market-maker-keeper [-h] [--rpc-host RPC_HOST]
                                   [--rpc-port RPC_PORT]
                                   [--rpc-timeout RPC_TIMEOUT] --eth-from
                                   ETH_FROM --exchange-address
                                   EXCHANGE_ADDRESS
                                   [--paradex-api-server PARADEX_API_SERVER]
                                   --paradex-api-key PARADEX_API_KEY
                                   [--paradex-api-timeout PARADEX_API_TIMEOUT]
                                   --pair PAIR --buy-token-address
                                   BUY_TOKEN_ADDRESS --sell-token-address
                                   SELL_TOKEN_ADDRESS --config CONFIG
                                   --price-feed PRICE_FEED
                                   [--price-feed-expiry PRICE_FEED_EXPIRY]
                                   [--spread-feed SPREAD_FEED]
                                   [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                   --order-expiry ORDER_EXPIRY
                                   [--min-eth-balance MIN_ETH_BALANCE]
                                   [--gas-price GAS_PRICE] [--smart-gas-price]
                                   [--refresh-frequency REFRESH_FREQUENCY]
                                   [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --rpc-timeout RPC_TIMEOUT
                        JSON-RPC timeout (in seconds, default: 10)
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --exchange-address EXCHANGE_ADDRESS
                        Ethereum address of the 0x Exchange contract
  --paradex-api-server PARADEX_API_SERVER
                        Address of the Paradex API (default:
                        'https://api.paradex.io/consumer')
  --paradex-api-key PARADEX_API_KEY
                        API key for the Paradex API
  --paradex-api-timeout PARADEX_API_TIMEOUT
                        Timeout for accessing the Paradex API (in seconds,
                        default: 9.5)
  --pair PAIR           Token pair (sell/buy) on which the keeper will operate
  --buy-token-address BUY_TOKEN_ADDRESS
                        Ethereum address of the buy token
  --sell-token-address SELL_TOKEN_ADDRESS
                        Ethereum address of the sell token
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --order-expiry ORDER_EXPIRY
                        Expiration time of created orders (in seconds)
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper will cease
                        operation
  --gas-price GAS_PRICE
                        Gas price (in Wei)
  --smart-gas-price     Use smart gas pricing strategy, based on the
                        ethgasstation.info feed
  --refresh-frequency REFRESH_FREQUENCY
                        Order book refresh frequency (in seconds, default: 3)
  --debug               Enable debug output
```

### Known limitations

* The keeper needs access to an Ethereum node in order to grant token approvals to _0x_ contracts,
  and also to constantly monitor token balances so it knows the maximum amount of orders it can place.
  In addition to that, it uses the `eth_sign` JSON RPC call to sign all API requests.


## `ddex-market-maker-keeper`

This keeper supports market-making on the [DDEX](http://ddex.io/) exchange.

### Usage

```
usage: ddex-market-maker-keeper [-h] [--rpc-host RPC_HOST]
                                [--rpc-port RPC_PORT]
                                [--rpc-timeout RPC_TIMEOUT] --eth-from
                                ETH_FROM --exchange-address EXCHANGE_ADDRESS
                                [--ddex-api-server DDEX_API_SERVER]
                                [--ddex-api-timeout DDEX_API_TIMEOUT] --pair
                                PAIR --buy-token-address BUY_TOKEN_ADDRESS
                                --sell-token-address SELL_TOKEN_ADDRESS
                                --config CONFIG --price-feed PRICE_FEED
                                [--price-feed-expiry PRICE_FEED_EXPIRY]
                                [--spread-feed SPREAD_FEED]
                                [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                [--order-history ORDER_HISTORY]
                                [--order-history-every ORDER_HISTORY_EVERY]
                                [--min-eth-balance MIN_ETH_BALANCE]
                                [--gas-price GAS_PRICE] [--smart-gas-price]
                                [--refresh-frequency REFRESH_FREQUENCY]
                                [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --rpc-timeout RPC_TIMEOUT
                        JSON-RPC timeout (in seconds, default: 10)
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --exchange-address EXCHANGE_ADDRESS
                        Ethereum address of the 0x Exchange contract
  --ddex-api-server DDEX_API_SERVER
                        Address of the Ddex API (default:
                        'https://api.ddex.io')
  --ddex-api-timeout DDEX_API_TIMEOUT
                        Timeout for accessing the Ddex API (in seconds,
                        default: 9.5)
  --pair PAIR           Token pair (sell/buy) on which the keeper will operate
  --buy-token-address BUY_TOKEN_ADDRESS
                        Ethereum address of the buy token
  --sell-token-address SELL_TOKEN_ADDRESS
                        Ethereum address of the sell token
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --order-history ORDER_HISTORY
                        Endpoint to report active orders to
  --order-history-every ORDER_HISTORY_EVERY
                        Frequency of reporting active orders (in seconds,
                        default: 30)
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper will cease
                        operation
  --gas-price GAS_PRICE
                        Gas price (in Wei)
  --smart-gas-price     Use smart gas pricing strategy, based on the
                        ethgasstation.info feed
  --refresh-frequency REFRESH_FREQUENCY
                        Order book refresh frequency (in seconds, default: 3)
  --debug               Enable debug output
```


## `ethfinex-market-maker-keeper`

This keeper supports market-making on the [IDEX](http://ethfinex.com/) exchange.

### Usage

```
usage: ethfinex-market-maker-keeper [-h]
                                    [--ethfinex-api-server ETHFINEX_API_SERVER]
                                    --ethfinex-api-key ETHFINEX_API_KEY
                                    --ethfinex-api-secret ETHFINEX_API_SECRET
                                    [--ethfinex-timeout ETHFINEX_TIMEOUT]
                                    --pair PAIR --config CONFIG --price-feed
                                    PRICE_FEED
                                    [--price-feed-expiry PRICE_FEED_EXPIRY]
                                    [--spread-feed SPREAD_FEED]
                                    [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                    [--order-history ORDER_HISTORY]
                                    [--order-history-every ORDER_HISTORY_EVERY]
                                    [--refresh-frequency REFRESH_FREQUENCY]
                                    [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --ethfinex-api-server ETHFINEX_API_SERVER
                        Address of the Ethfinex API server (default:
                        'https://api.ethfinex.com')
  --ethfinex-api-key ETHFINEX_API_KEY
                        API key for the Ethfinex API
  --ethfinex-api-secret ETHFINEX_API_SECRET
                        API secret for the Ethfinex API
  --ethfinex-timeout ETHFINEX_TIMEOUT
                        Timeout for accessing the Ethfinex API (in seconds,
                        default: 9.5)
  --pair PAIR           Token pair (sell/buy) on which the keeper will operate
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --order-history ORDER_HISTORY
                        Endpoint to report active orders to
  --order-history-every ORDER_HISTORY_EVERY
                        Frequency of reporting active orders (in seconds,
                        default: 30)
  --refresh-frequency REFRESH_FREQUENCY
                        Order book refresh frequency (in seconds, default: 3)
  --debug               Enable debug output
```


## `gopax-market-maker-keeper`

This keeper supports market-making on the [GoPax](https://www.gopax.co.kr/) exchange.

### Usage

```
usage: gopax-market-maker-keeper [-h] [--gopax-api-server GOPAX_API_SERVER]
                                 --gopax-api-key GOPAX_API_KEY
                                 --gopax-api-secret GOPAX_API_SECRET
                                 [--gopax-timeout GOPAX_TIMEOUT] --pair PAIR
                                 --config CONFIG --price-feed PRICE_FEED
                                 [--price-feed-expiry PRICE_FEED_EXPIRY]
                                 [--spread-feed SPREAD_FEED]
                                 [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                 [--order-history ORDER_HISTORY]
                                 [--order-history-every ORDER_HISTORY_EVERY]
                                 [--refresh-frequency REFRESH_FREQUENCY]
                                 [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --gopax-api-server GOPAX_API_SERVER
                        Address of the GOPAX API server (default:
                        'https://api.gopax.co.kr')
  --gopax-api-key GOPAX_API_KEY
                        API key for the GOPAX API
  --gopax-api-secret GOPAX_API_SECRET
                        API secret for the GOPAX API
  --gopax-timeout GOPAX_TIMEOUT
                        Timeout for accessing the GOPAX API (in seconds,
                        default: 9.5)
  --pair PAIR           Token pair (sell/buy) on which the keeper will operate
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --order-history ORDER_HISTORY
                        Endpoint to report active orders to
  --order-history-every ORDER_HISTORY_EVERY
                        Frequency of reporting active orders (in seconds,
                        default: 30)
  --refresh-frequency REFRESH_FREQUENCY
                        Order book refresh frequency (in seconds, default: 3)
  --debug               Enable debug output
```

## `idex-market-maker-keeper`

This keeper supports market-making on the [IDEX](https://idex.market/) exchange.

### Usage

```
usage: idex-market-maker-keeper [-h] [--rpc-host RPC_HOST]
                                [--rpc-port RPC_PORT]
                                [--rpc-timeout RPC_TIMEOUT] --eth-from
                                ETH_FROM --tub-address TUB_ADDRESS
                                --idex-address IDEX_ADDRESS
                                [--idex-api-server IDEX_API_SERVER]
                                [--idex-timeout IDEX_TIMEOUT] --config CONFIG
                                --price-feed PRICE_FEED
                                [--price-feed-expiry PRICE_FEED_EXPIRY]
                                [--spread-feed SPREAD_FEED]
                                [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                --eth-reserve ETH_RESERVE
                                [--min-eth-balance MIN_ETH_BALANCE]
                                --min-eth-deposit MIN_ETH_DEPOSIT
                                --min-sai-deposit MIN_SAI_DEPOSIT
                                [--gas-price GAS_PRICE] [--smart-gas-price]
                                [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --rpc-timeout RPC_TIMEOUT
                        JSON-RPC timeout (in seconds, default: 10)
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --tub-address TUB_ADDRESS
                        Ethereum address of the Tub contract
  --idex-address IDEX_ADDRESS
                        Ethereum address of the IDEX contract
  --idex-api-server IDEX_API_SERVER
                        Address of the IDEX API server (default:
                        'https://api.idex.market')
  --idex-timeout IDEX_TIMEOUT
                        Timeout for accessing the IDEX API (in seconds,
                        default: 9.5)
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --eth-reserve ETH_RESERVE
                        Amount of ETH which will never be deposited so the
                        keeper can cover gas
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper will cease
                        operation
  --min-eth-deposit MIN_ETH_DEPOSIT
                        Minimum amount of ETH that can be deposited in one
                        transaction
  --min-sai-deposit MIN_SAI_DEPOSIT
                        Minimum amount of SAI that can be deposited in one
                        transaction
  --gas-price GAS_PRICE
                        Gas price (in Wei)
  --smart-gas-price     Use smart gas pricing strategy, based on the
                        ethgasstation.info feed
  --debug               Enable debug output
```

### Known limitations

* Due to a serious bug in the IDEX API (only half of the open orders are returned via the API),
  **this keeper should not be used yet**.


## `bibox-market-maker-keeper`

This keeper supports market-making on the [Bibox](https://www.bibox.com/exchange) centralized exchange.

### Usage

```
usage: bibox-market-maker-keeper [-h] [--bibox-api-server BIBOX_API_SERVER]
                                 --bibox-api-key BIBOX_API_KEY --bibox-secret
                                 BIBOX_SECRET [--bibox-timeout BIBOX_TIMEOUT]
                                 --pair PAIR --config CONFIG --price-feed
                                 PRICE_FEED
                                 [--price-feed-expiry PRICE_FEED_EXPIRY]
                                 [--spread-feed SPREAD_FEED]
                                 [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                 [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --bibox-api-server BIBOX_API_SERVER
                        Address of the Bibox API server (default:
                        'https://api.bibox.com')
  --bibox-api-key BIBOX_API_KEY
                        API key for the Bibox API
  --bibox-secret BIBOX_SECRET
                        Secret for the Bibox API
  --bibox-timeout BIBOX_TIMEOUT
                        Timeout for accessing the Bibox API (in seconds,
                        default: 9.5)
  --pair PAIR           Token pair (sell/buy) on which the keeper will operate
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --debug               Enable debug output
```


## `okex-market-maker-keeper`

This keeper supports market-making on the [OKEX](https://www.okex.com/) centralized exchange.

### Usage

```
usage: okex-market-maker-keeper [-h] [--okex-api-server OKEX_API_SERVER]
                                --okex-api-key OKEX_API_KEY --okex-secret-key
                                OKEX_SECRET_KEY [--okex-timeout OKEX_TIMEOUT]
                                --pair PAIR --config CONFIG --price-feed
                                PRICE_FEED
                                [--price-feed-expiry PRICE_FEED_EXPIRY]
                                [--spread-feed SPREAD_FEED]
                                [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --okex-api-server OKEX_API_SERVER
                        Address of the OKEX API server (default:
                        'https://www.okex.com')
  --okex-api-key OKEX_API_KEY
                        API key for the OKEX API
  --okex-secret-key OKEX_SECRET_KEY
                        Secret key for the OKEX API
  --okex-timeout OKEX_TIMEOUT
                        Timeout for accessing the OKEX API (in seconds,
                        default: 9.5)
  --pair PAIR           Token pair (sell/buy) on which the keeper will operate
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --debug               Enable debug output
```


## `gateio-market-maker-keeper`

This keeper supports market-making on the [gate.io](http://gate.io/) centralized exchange.

### Usage

```
usage: gateio-market-maker-keeper [-h] [--gateio-api-server GATEIO_API_SERVER]
                                  --gateio-api-key GATEIO_API_KEY
                                  --gateio-secret-key GATEIO_SECRET_KEY
                                  [--gateio-timeout GATEIO_TIMEOUT] --pair
                                  PAIR --config CONFIG --price-feed PRICE_FEED
                                  [--price-feed-expiry PRICE_FEED_EXPIRY]
                                  [--spread-feed SPREAD_FEED]
                                  [--spread-feed-expiry SPREAD_FEED_EXPIRY]
                                  [--debug]

optional arguments:
  -h, --help            show this help message and exit
  --gateio-api-server GATEIO_API_SERVER
                        Address of the Gate.io API server (default:
                        'https://data.gate.io')
  --gateio-api-key GATEIO_API_KEY
                        API key for the Gate.io API
  --gateio-secret-key GATEIO_SECRET_KEY
                        Secret key for the Gate.io API
  --gateio-timeout GATEIO_TIMEOUT
                        Timeout for accessing the Gate.io API (in seconds,
                        default: 9.5)
  --pair PAIR           Token pair (sell/buy) on which the keeper will operate
  --config CONFIG       Bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed
  --price-feed-expiry PRICE_FEED_EXPIRY
                        Maximum age of the price feed (in seconds, default:
                        120)
  --spread-feed SPREAD_FEED
                        Source of spread feed
  --spread-feed-expiry SPREAD_FEED_EXPIRY
                        Maximum age of the spread feed (in seconds, default:
                        3600)
  --debug               Enable debug output
```

### Known limitations

* The gate.io API sometimes does not acknowledge order creation, returning following error message:
  `Oops... reloading...<font color=white> 29.148 </font> <script> function
  r(){window.location.reload();}setTimeout('r()',3000);</script>`. This error
  seems to depend on the API address of the caller. Despite these errors, orders get properly
  created and registered in the backend, the keeper will find out about it the next time it
  queries the open orders list (which happens every few seconds).


## License

See [COPYING](https://github.com/makerdao/market-maker-keeper/blob/master/COPYING) file.

### Disclaimer

YOU (MEANING ANY INDIVIDUAL OR ENTITY ACCESSING, USING OR BOTH THE SOFTWARE INCLUDED IN THIS GITHUB REPOSITORY) EXPRESSLY UNDERSTAND AND AGREE THAT YOUR USE OF THE SOFTWARE IS AT YOUR SOLE RISK.
THE SOFTWARE IN THIS GITHUB REPOSITORY IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
YOU RELEASE AUTHORS OR COPYRIGHT HOLDERS FROM ALL LIABILITY FOR YOU HAVING ACQUIRED OR NOT ACQUIRED CONTENT IN THIS GITHUB REPOSITORY. THE AUTHORS OR COPYRIGHT HOLDERS MAKE NO REPRESENTATIONS CONCERNING ANY CONTENT CONTAINED IN OR ACCESSED THROUGH THE SERVICE, AND THE AUTHORS OR COPYRIGHT HOLDERS WILL NOT BE RESPONSIBLE OR LIABLE FOR THE ACCURACY, COPYRIGHT COMPLIANCE, LEGALITY OR DECENCY OF MATERIAL CONTAINED IN OR ACCESSED THROUGH THIS GITHUB REPOSITORY. 
