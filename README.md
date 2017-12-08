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
_"Bands configuration"_ section below fore more details.

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

### Installation of `etherdelta-socket`

The `etherdelta-market-maker-keeper` keeper utilizes `etherdelta-socket` (present in the `utils` directory)
to place orders on EtherDelta using _socket.io_. In order to use it, a `node` installation must be present
and `npm install` needs to be run in the `utils/etherdelta-socket` folder.

This step is not necessary if you only want to use the other keepers from this project.

### Installation of `setzer`

All market maker keepers use `setzer` in order to access price feeds like GDAX, Kraken etc. This interface
is built on top of `setzer` so in order for it to work correctly, `setzer` and its dependencies
must be installed and available to the keepers. Please see: <https://github.com/makerdao/setzer>.

Without `setzer` installed, the `--price-feed` argument can not be used and only the default price feed
(provided by `Tub`) will be available.


## Running keepers

An individual script in the `bin` directory is present for each keeper. For example, `keeper-sai-bite`
can be run with:
```bash
bin/keeper-sai-bite --eth-from 0x0101010101010101010101010101010101010101
```

### Restarting dying keepers

As keepers tend to die at times, in any serious environment they should be run by a tool
which can restart them if they fail. It could be _systemd_, but if you don't want to set it up,
a simple `bin/run-forever` script has been provided. Its job is to simply restart the
specified program as long as it's return code is non-zero.

For example you could run the same `keeper-sai-bite` keeper like that:
```bash
bin/run-forever bin/keeper-sai-bite --eth-from 0x0101010101010101010101010101010101010101
```
so it gets automatically restarted every time it fails.

### Individual keeper accounts

**It is advised to run each keeper on their own Ethereum account**

### Unlocking accounts

Keepers will fail to start if the Ethereum accounts they are configured to operate on are not unlocked.
This post <https://ethereum.stackexchange.com/questions/15349/parity-unlock-multiple-accounts-at-startup/15351#15351>
describes how to unlock multiple accounts in Parity on startup.

## Reference keepers

This sections lists and briefly describes a set of reference keepers present in this project.

### `keeper-sai-maker-otc`

Keeper to act as a market maker on OasisDEX, on the W-ETH/SAI pair.

Keeper continuously monitors and adjusts its positions in order to act as a market maker.
It maintains buy and sell orders in multiple bands at the same time. In each buy band,
it aims to have open SAI sell orders for at least `minSaiAmount`. In each sell band
it aims to have open WETH sell orders for at least `minWEthAmount`. In both cases,
it will ensure the price of open orders stays within the <minMargin,maxMargin> range
from the current SAI/W-ETH price.

When started, the keeper places orders for the average amounts (`avgSaiAmount`
and `avgWEthAmount`) in each band and uses `avgMargin` to calculate the order price.

As long as the price of orders stays within the band (i.e. is in the <minMargin,maxMargin>
range from the current SAI/W-ETH price, which is of course constantly moving), the keeper
keeps them open. If they leave the band, they either enter another adjacent band
or fall outside all bands. In case of the latter, they get immediately cancelled. In case of
the former, the keeper can keep these orders open as long as their amount is within the
<minSaiAmount,maxSaiAmount> (for buy bands) or <minWEthAmount,maxWEthAmount> (for sell bands)
ranges for the band they just entered. If it is above the maximum, all open orders will get
cancelled and a new one will be created (for the `avgSaiAmount` / `avgWEthAmount`). If it is below
the minimum, a new order gets created for the remaining amount so the total amount of orders
in this band is equal to `avgSaiAmount` or `avgWEthAmount`.

The same thing will happen if the total amount of open orders in a band falls below either
`minSaiAmount` or `minWEthAmount` as a result of other market participants taking these orders.
In this case also a new order gets created for the remaining amount so the total
amount of orders in this band is equal to `avgSaiAmount` / `avgWEthAmount`.

This keeper will constantly use gas to move orders as the SAI/GEM price changes. Gas usage
can be limited by setting the margin and amount ranges wide enough and also by making
sure that bands are always adjacent to each other and that their <min,max> amount ranges
overlap.

Usage:
```
usage: keeper-sai-maker-otc [-h] [--rpc-host RPC_HOST] [--rpc-port RPC_PORT]
                            --eth-from ETH_FROM [--gas-price GAS_PRICE]
                            [--initial-gas-price INITIAL_GAS_PRICE]
                            [--increase-gas-price-by INCREASE_GAS_PRICE_BY]
                            [--increase-gas-price-every INCREASE_GAS_PRICE_EVERY]
                            [--debug] [--trace] --config CONFIG
                            [--round-places ROUND_PLACES]

optional arguments:
  -h, --help            show this help message and exit
  --rpc-host RPC_HOST   JSON-RPC host (default: `localhost')
  --rpc-port RPC_PORT   JSON-RPC port (default: `8545')
  --eth-from ETH_FROM   Ethereum account from which to send transactions
  --gas-price GAS_PRICE
                        Static gas pricing: Gas price in Wei
  --initial-gas-price INITIAL_GAS_PRICE
                        Increasing gas pricing: Initial gas price in Wei
  --increase-gas-price-by INCREASE_GAS_PRICE_BY
                        Increasing gas pricing: Gas price increase in Wei
  --increase-gas-price-every INCREASE_GAS_PRICE_EVERY
                        Increasing gas pricing: Gas price increase interval in
                        seconds
  --debug               Enable debug output
  --trace               Enable trace output
  --config CONFIG       Buy/sell bands configuration file
  --round-places ROUND_PLACES
                        Number of decimal places to round order prices to
                        (default=2)
```



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

### `keeper-sai-maker-etherdelta`

Keeper to act as a market maker on EtherDelta, on the ETH/SAI pair.

Due to limitations of EtherDelta, **the development of this keeper has been
discontinued**. It works most of the time, but due to the fact that EtherDelta
was a bit unpredictable in terms of placing orders at the time this keeper
was developed, we abandoned it and decided to stick to SaiMakerOtc for now.










There is also a _Setzer_ class which provides a simple interface to the `setzer` commandline
tool (<https://github.com/makerdao/setzer>).



**Beware!** This is the first version of the APIs and they will definitely change
and/or evolve in the future.







## Disclaimer

This set of reference keepers is provided for demonstration purposes only. If you,
by any chance, want to run them on the production network or provide them
with any real money or tokens, you do it on your own responsibility only.

As stated in the _GNU Affero General Public License_:

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU Affero General Public License for more details.




