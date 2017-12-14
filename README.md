# market-maker-keeper

[![Build Status](https://travis-ci.org/makerdao/market-maker-keeper.svg?branch=master)](https://travis-ci.org/makerdao/market-maker-keeper)
[![codecov](https://codecov.io/gh/makerdao/market-maker-keeper/branch/master/graph/badge.svg)](https://codecov.io/gh/makerdao/market-maker-keeper)

The _DAI Stablecoin System_ incentivizes external agents, called _keepers_,
to automate certain operations around the Ethereum blockchain.

`market-maker-keeper` is actually a set of keepers that facilitate SAI/W-ETH and SAI/ETH
market making of the following exchanges:
* OasisDEX (`oasis-market-maker-keeper`),
* EtherDelta (`etherdelta-market-maker-keeper`),
* RadarRelay (`radarrelay-market-maker-keeper`).

All these three keepers share some logic and operate in a similar way. They create
a series of orders in so called _bands_, which are configured with a JSON file
containing parameters like spreads, maximum engagement etc. Please see the
_"Bands configuration"_ section below for more details regarding keeper mechanics.

All these keepers are currently only capable of market-making on the SAI/W-ETH
(for OasisDEX and RadarRelay) and SAI/ETH (for EtherDelta) pairs. Changing it
would require making some changes to their source code. Having said that,
that change seems to be pretty trivial.

This repo also contains an auxiliary tool called `oasis-market-maker-cancel`, which
may be used for emergency cancelling all market maker orders on OasisDEX if the
keeper gets stuck or dies for some reason, or if the network becomes congested.

<https://chat.makerdao.com/channel/keeper>


## Installation

This project uses *Python 3.6.2*.

In order to clone the project and install required third-party packages please execute:
```
git clone https://github.com/makerdao/market-maker-keeper.git
git submodule update --init --recursive
pip3 install -r requirements.txt
```

### Known macOS issues

In order for the Python requirements to install correctly on _macOS_, please install
`openssl`, `libtool` and `pkg-config` using [Homebrew](https://brew.sh/):
```
brew install openssl libtool pkg-config
```

and set the `LDFLAGS` environment variable before you run `pip3 install -r requirements.txt`:
```
export LDFLAGS="-L$(brew --prefix openssl)/lib" CFLAGS="-I$(brew --prefix openssl)/include" 
```

### Installation of `etherdelta-client`

The `etherdelta-market-maker-keeper` keeper utilizes `etherdelta-client` (present in the `lib/pymaker/utils`
directory) to place orders on EtherDelta using _socket.io_. In order to use it, a `node` installation must
be present and `npm install` needs to be run in the `lib/pymaker/utils/etherdelta-client` folder.

This step is not necessary if you only want to use the other keepers from this project.

### Installation of `setzer`

All market maker keepers use `setzer` in order to access price feeds like GDAX, Kraken etc. This interface
is built on top of `setzer` so in order for it to work correctly, `setzer` and its dependencies
must be installed and available to the keepers. Please see: <https://github.com/makerdao/setzer>.

Without `setzer` installed, the `--price-feed` argument can not be used and only the default price feed
(provided by `Tub`) will be available.


## Bands configuration

### Description

Bands configuration file is directly related to how market maker keepers work. They continuously
monitor and adjusts its positions in the order to book, maintaining open buy and sell orders
in multiple bands at the same time.

In each buy band, the keepers aim to have open SAI sell orders for at least `minSaiAmount`.
In each sell band they aim to have open WETH (or ETH) sell orders for at least `minWEthAmount`.
In both cases, they will ensure the price of open orders stays within the <minMargin,maxMargin>
range from the current SAI/ETH price.

When started, keepers places orders for the average amounts (`avgSaiAmount`
and `avgWEthAmount`) in each band, using use `avgMargin` to calculate the order price.

As long as the price of orders stays within the band (i.e. is in the <minMargin,maxMargin>
range from the current SAI/ETH price, which is of course constantly moving), the keepers
keep them open. If some orders leave the band, they either enter another adjacent band
or fall outside all bands. In case of the latter, they get immediately cancelled. In case of
the former, keepers can keep these orders open as long as their amount is within the
<minSaiAmount,maxSaiAmount> (for buy bands) or <minWEthAmount,maxWEthAmount> (for sell bands)
ranges for the band they just entered. If it is above the maximum, some open orders will get
cancelled and potentially new one will be created to bring the total amount back within the
range. If it is below the minimum, a new order gets created for the remaining amount so the
total amount of orders in this band is equal to `avgSaiAmount` or `avgWEthAmount`.

The same thing will happen if the total amount of open orders in a band falls below either
`minSaiAmount` or `minWEthAmount` as a result of other market participants taking these orders.
In this case also a new order gets created for the remaining amount so the total
amount of orders in this band is equal to `avgSaiAmount` / `avgWEthAmount`.

Keeper will constantly use gas to cancel orders (for OasisDEX, EtherDelta and RadarRelay)
and create new ones (OasisDEX only) as the SAI/ETH price changes. Gas usage can be limited
by setting the margin and amount ranges wide enough and also by making sure that bands
are always adjacent to each other and that their <min,max> amount ranges overlap.

### File format

Bands configuration file consists of two main sections: *buyBands* (configuration determining how the keeper
buys WETH (or ETH) with SAI) and *sellBands* (configuration determining how the keeper sells WETH (or ETH) for SAI).
Each section is an array containing one object per each band.

The *minMargin* and *maxMargin* fields in each band object represent the margin (spread) range of that band.
These ranges may not overlap for bands of the same type (_buy_ or _sell_), and should be adjacent to each other
for better keeper performance (less orders will likely get cancelled if they are adjacent). The *avgMargin*
represents the margin (spread) of newly created orders within a band.

The next three fields (*minSaiAmount*, *avgSaiAmount* and *maxSaiAmount* for buy bands, or *minWEthAmount*,
*avgWEthAmount* and *maxWEthAmount* for sell bands) are the minimum, target and maximum keeper engagement
per each band. The *dustCutoff* field is the minimum value of order created in each individual band,
expressed in SAI for buy bands and in WETH (or ETH) for sell bands. Setting it to a non-zero value prevents
keepers from creating of lot of very tiny orders, which can cost a lot of gas in case of OasisDEX.  

Sample bands configuration file:

```json
{
    "buyBands": [
        {
            "minMargin": 0.005,
            "avgMargin": 0.01,
            "maxMargin": 0.02,
            "minSaiAmount": 20.0,
            "avgSaiAmount": 30.0,
            "maxSaiAmount": 40.0,
            "dustCutoff": 0.0
        },
        {
            "minMargin": 0.02,
            "avgMargin": 0.025,
            "maxMargin": 0.03,
            "minSaiAmount": 40.0,
            "avgSaiAmount": 60.0,
            "maxSaiAmount": 80.0,
            "dustCutoff": 0.0
        }
    ],
    "sellBands": [
        {
            "minMargin": 0.005,
            "avgMargin": 0.01,
            "maxMargin": 0.02,
            "minWEthAmount": 2.5,
            "avgWEthAmount": 5.0,
            "maxWEthAmount": 7.5,
            "dustCutoff": 0.0
        },
        {
            "minMargin": 0.02,
            "avgMargin": 0.025,
            "maxMargin": 0.03,
            "minWEthAmount": 4.0,
            "avgWEthAmount": 6.0,
            "maxWEthAmount": 8.0,
            "dustCutoff": 0.0
        }
    ]
}
```

### Data templating language

The [Jsonnet](https://github.com/google/jsonnet) data templating language can be used
for the bands config file.


## `oasis-market-maker-keeper`

This keeper supports market-making on the [OasisDEX](https://oasisdex.com/) exchange.

### Usage

```
usage: oasis-market-maker-keeper [-h] [--rpc-host RPC_HOST]
                                 [--rpc-port RPC_PORT] --eth-from ETH_FROM
                                 --tub-address TUB_ADDRESS --oasis-address
                                 OASIS_ADDRESS --config CONFIG
                                 [--price-feed PRICE_FEED]
                                 [--round-places ROUND_PLACES]
                                 [--min-eth-balance MIN_ETH_BALANCE]
                                 [--gas-price GAS_PRICE]
                                 [--gas-price-increase GAS_PRICE_INCREASE]
                                 [--gas-price-increase-every GAS_PRICE_INCREASE_EVERY]
                                 [--gas-price-max GAS_PRICE_MAX]
                                 [--cancel-gas-price CANCEL_GAS_PRICE]
                                 [--cancel-gas-price-increase CANCEL_GAS_PRICE_INCREASE]
                                 [--cancel-gas-price-increase-every CANCEL_GAS_PRICE_INCREASE_EVERY]
                                 [--cancel-gas-price-max CANCEL_GAS_PRICE_MAX]
                                 [--debug] [--trace]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --tub-address TUB_ADDRESS
                        Ethereum address of the Tub contract
  --oasis-address OASIS_ADDRESS
                        Ethereum address of the OasisDEX contract
  --config CONFIG       Buy/sell bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed. Tub price feed will be used if
                        not specified
  --round-places ROUND_PLACES
                        Number of decimal places to round order prices to
                        (default=2)
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper with either
                        terminate or not start at all
  --gas-price GAS_PRICE
                        Gas price (in Wei)
  --gas-price-increase GAS_PRICE_INCREASE
                        Gas price increase (in Wei) if no confirmation within
                        --gas-price-increase-every seconds
  --gas-price-increase-every GAS_PRICE_INCREASE_EVERY
                        Gas price increase frequency (in seconds, default:
                        120)
  --gas-price-max GAS_PRICE_MAX
                        Maximum gas price (in Wei)
  --cancel-gas-price CANCEL_GAS_PRICE
                        Gas price (in Wei) for order cancellation
  --cancel-gas-price-increase CANCEL_GAS_PRICE_INCREASE
                        Gas price increase (in Wei) for order cancellation if
                        no confirmation within --cancel-gas-price-increase-
                        every seconds
  --cancel-gas-price-increase-every CANCEL_GAS_PRICE_INCREASE_EVERY
                        Gas price increase frequency for order cancellation
                        (in seconds, default: 120)
  --cancel-gas-price-max CANCEL_GAS_PRICE_MAX
                        Maximum gas price (in Wei) for order cancellation
  --debug               Enable debug output
  --trace               Enable trace output
```


## `oasis-market-maker-cancel`

This tool immediately cancels all our open orders on [OasisDEX](https://oasisdex.com/). 
It may be used if the `oasis-market-maker-keeper` gets stuck or dies for some reason,
or if the network becomes congested.

### Usage

```
usage: oasis-market-maker-cancel [-h] [--rpc-host RPC_HOST]
                                 [--rpc-port RPC_PORT] --eth-from ETH_FROM
                                 --oasis-address OASIS_ADDRESS
                                 [--gas-price GAS_PRICE]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
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
                                      [--rpc-port RPC_PORT] --eth-from
                                      ETH_FROM --tub-address TUB_ADDRESS
                                      --etherdelta-address ETHERDELTA_ADDRESS
                                      --etherdelta-socket ETHERDELTA_SOCKET
                                      --config CONFIG
                                      [--price-feed PRICE_FEED] --order-age
                                      ORDER_AGE
                                      [--order-expiry-threshold ORDER_EXPIRY_THRESHOLD]
                                      --eth-reserve ETH_RESERVE
                                      [--min-eth-balance MIN_ETH_BALANCE]
                                      --min-eth-deposit MIN_ETH_DEPOSIT
                                      --min-sai-deposit MIN_SAI_DEPOSIT
                                      [--cancel-on-shutdown]
                                      [--withdraw-on-shutdown]
                                      [--gas-price GAS_PRICE] [--debug]
                                      [--trace]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --tub-address TUB_ADDRESS
                        Ethereum address of the Tub contract
  --etherdelta-address ETHERDELTA_ADDRESS
                        Ethereum address of the EtherDelta contract
  --etherdelta-socket ETHERDELTA_SOCKET
                        Ethereum address of the EtherDelta API socket
  --config CONFIG       Buy/sell bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed. Tub price feed will be used if
                        not specified
  --order-age ORDER_AGE
                        Age of created orders (in blocks)
  --order-expiry-threshold ORDER_EXPIRY_THRESHOLD
                        Order age at which order is considered already expired
                        (in blocks)
  --eth-reserve ETH_RESERVE
                        Amount of ETH which will never be deposited so the
                        keeper can cover gas
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper with either
                        terminate or not start at all
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
  --debug               Enable debug output
  --trace               Enable trace output
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


## `radarrelay-market-maker-keeper`

This keeper supports market-making on the [RadarRelay](https://app.radarrelay.com/) exchange.
As _RadarRelay_ is a regular 0x Exchange implementing the _0x Standard Relayer API_, this
keeper can be easily adapted to market-make on other 0x exchanges as well.

### Usage

```
usage: radarrelay-market-maker-keeper [-h] [--rpc-host RPC_HOST]
                                      [--rpc-port RPC_PORT] --eth-from
                                      ETH_FROM --tub-address TUB_ADDRESS
                                      --exchange-address EXCHANGE_ADDRESS
                                      --weth-address WETH_ADDRESS
                                      --relayer-api-server RELAYER_API_SERVER
                                      --config CONFIG
                                      [--price-feed PRICE_FEED] --order-expiry
                                      ORDER_EXPIRY
                                      [--order-expiry-threshold ORDER_EXPIRY_THRESHOLD]
                                      [--min-eth-balance MIN_ETH_BALANCE]
                                      [--cancel-on-shutdown]
                                      [--gas-price GAS_PRICE] [--debug]
                                      [--trace]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --tub-address TUB_ADDRESS
                        Ethereum address of the Tub contract
  --exchange-address EXCHANGE_ADDRESS
                        Ethereum address of the 0x Exchange contract
  --weth-address WETH_ADDRESS
                        Ethereum address of the WETH token
  --relayer-api-server RELAYER_API_SERVER
                        Address of the 0x Relayer API
  --config CONFIG       Buy/sell bands configuration file
  --price-feed PRICE_FEED
                        Source of price feed. Tub price feed will be used if
                        not specified
  --order-expiry ORDER_EXPIRY
                        Expiration time of created orders (in seconds)
  --order-expiry-threshold ORDER_EXPIRY_THRESHOLD
                        Order expiration time at which order is considered
                        already expired (in seconds)
  --min-eth-balance MIN_ETH_BALANCE
                        Minimum ETH balance below which keeper with either
                        terminate or not start at all
  --cancel-on-shutdown  Whether should cancel all open orders on RadarRelay on
                        keeper shutdown
  --gas-price GAS_PRICE
                        Gas price (in Wei)
  --debug               Enable debug output
  --trace               Enable trace output
```

### Known limitations

* Expired and/or taken orders to not disappear from the RadarRelay UI immediately. Apparently they run a
  backend process called _chain watching service_, which for tokens with little liquidity kicks in only
  every 10 minutes and does order pruning. Because of that, if we configure the keeper to refresh
  the orders too frequently (i.e. if the `--order-expiry` will be too low), the exchange users will
  see two or even more duplicates of market maker orders.


## License

See [COPYING](https://github.com/makerdao/market-maker-keeper/blob/master/COPYING) file.
