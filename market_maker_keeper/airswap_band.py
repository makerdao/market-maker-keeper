# This file is part of Maker Keeper Framework.
#
# Copyright (C) 2017-2018 reverendus
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import itertools
import logging
import operator
from functools import reduce
from pprint import pformat
from typing import Tuple, Optional

import time

from market_maker_keeper.feed import Feed
from market_maker_keeper.limit import SideLimits, History
from market_maker_keeper.price_feed import Price
from market_maker_keeper.reloadable_config import ReloadableConfig
from pymaker.numeric import Wad


class Band:
    def __init__(self,
                 min_margin: float,
                 avg_margin: float,
                 max_margin: float,
                 min_amount: Wad,
                 avg_amount: Wad,
                 max_amount: Wad,
                 dust_cutoff: Wad,
                 params: dict):
        assert(isinstance(min_margin, float))
        assert(isinstance(avg_margin, float))
        assert(isinstance(max_margin, float))
        assert(isinstance(min_amount, Wad))
        assert(isinstance(avg_amount, Wad))
        assert(isinstance(max_amount, Wad))
        assert(isinstance(dust_cutoff, Wad))
        assert(isinstance(params, dict))

        self.min_margin = min_margin
        self.avg_margin = avg_margin
        self.max_margin = max_margin
        self.min_amount = min_amount
        self.avg_amount = avg_amount
        self.max_amount = max_amount
        self.dust_cutoff = dust_cutoff
        self.params = params

        assert(self.min_amount >= Wad(0))
        assert(self.avg_amount >= Wad(0))
        assert(self.max_amount >= Wad(0))
        assert(self.min_amount <= self.avg_amount)
        assert(self.avg_amount <= self.max_amount)

        assert(self.min_margin <= self.avg_margin)
        assert(self.avg_margin <= self.max_margin)
        assert(self.min_margin < self.max_margin)

    def order_price(self, order) -> Wad:
        raise NotImplemented()

    def includes(self, order, target_price: Wad) -> bool:
        raise NotImplemented()

    def type(self) -> str:
        raise NotImplemented()

    def excessive_orders(self, orders: list, target_price: Wad, is_first_band: bool, is_last_band: bool):
        """Return orders which need to be cancelled to bring the total order amount in the band below maximum."""

        # Get all orders which are currently present in the band.
        orders_in_band = [order for order in orders if self.includes(order, target_price)]
        orders_total = Bands.total_amount(orders_in_band)

        # The sorting in which we remove orders depends on which band we are in.
        # * In the first band we start cancelling with orders closest to the target price.
        # * In the last band we start cancelling with orders furthest from the target price.
        # * In remaining cases we remove orders starting from the smallest one.
        if is_first_band:
            sorting = lambda order: abs(self.order_price(order) - target_price)
            reverse = True

        elif is_last_band:
            sorting = lambda order: abs(self.order_price(order) - target_price)
            reverse = False

        else:
            sorting = lambda order: order.remaining_sell_amount
            reverse = True

        # Keep removing orders until their total amount stops being greater than `maxAmount`.
        orders_to_leave = sorted(orders_in_band, key=sorting, reverse=reverse)
        while Bands.total_amount(orders_to_leave) > self.max_amount:
            orders_to_leave.pop()

        result = set(orders_in_band) - set(orders_to_leave)

        if len(result) > 0:
            logger = logging.getLogger()
            logger.info(f"{self.type().capitalize()} band (spread <{self.min_margin}, {self.max_margin}>,"
                        f" amount <{self.min_amount}, {self.max_amount}>) has amount {orders_total}, scheduling"
                        f" {len(result)} order(s) for cancellation: {', '.join(map(lambda o: '#' + str(o.order_id), result))}")

        return result


class BuyBand(Band):
    def __init__(self, dictionary: dict):
        super().__init__(min_margin=float(dictionary['minMargin']),
                         avg_margin=float(dictionary['avgMargin']),
                         max_margin=float(dictionary['maxMargin']),
                         min_amount=Wad.from_number(dictionary['minAmount']),
                         avg_amount=Wad.from_number(dictionary['avgAmount']),
                         max_amount=Wad.from_number(dictionary['maxAmount']),
                         dust_cutoff=Wad.from_number(dictionary['dustCutoff']),
                         params=dictionary.get('params', {}))

    def order_price(self, order) -> Wad:
        return order.sell_to_buy_price

    def includes(self, order, target_price: Wad) -> bool:
        price = self.order_price(order)
        price_min = self._apply_margin(target_price, self.min_margin)
        price_max = self._apply_margin(target_price, self.max_margin)
        return (price > price_max) and (price <= price_min)

    def type(self) -> str:
        return "buy"

    def avg_price(self, target_price: Wad) -> Wad:
        return self._apply_margin(target_price, self.avg_margin)

    @staticmethod
    def _apply_margin(price: Wad, margin: float) -> Wad:
        return price * Wad.from_number(1 - margin)


class SellBand(Band):
    def __init__(self, dictionary: dict):
        super().__init__(min_margin=float(dictionary['minMargin']),
                         avg_margin=float(dictionary['avgMargin']),
                         max_margin=float(dictionary['maxMargin']),
                         min_amount=Wad.from_number(dictionary['minAmount']),
                         avg_amount=Wad.from_number(dictionary['avgAmount']),
                         max_amount=Wad.from_number(dictionary['maxAmount']),
                         dust_cutoff=Wad.from_number(dictionary['dustCutoff']),
                         params=dictionary.get('params', {}))

    def order_price(self, order) -> Wad:
        return order.buy_to_sell_price

    def includes(self, order, target_price: Wad) -> bool:
        price = self.order_price(order)
        price_min = self._apply_margin(target_price, self.min_margin)
        price_max = self._apply_margin(target_price, self.max_margin)
        return (price > price_min) and (price <= price_max)

    def type(self) -> str:
        return "sell"

    def avg_price(self, target_price: Wad) -> Wad:
        return self._apply_margin(target_price, self.avg_margin)

    @staticmethod
    def _apply_margin(price: Wad, margin: float) -> Wad:
        return price * Wad.from_number(1 + margin)


class NewOrder:
    def __init__(self, is_sell: bool, price: Wad, amount: Wad, pay_amount: Wad, buy_amount: Wad, band: Band, confirm_function):
        assert(isinstance(is_sell, bool))
        assert(isinstance(price, Wad))
        assert(isinstance(amount, Wad))
        assert(isinstance(pay_amount, Wad))
        assert(isinstance(buy_amount, Wad))
        assert(isinstance(band, Band))
        assert(callable(confirm_function))

        self.is_sell = is_sell
        self.price = price
        self.amount = amount
        self.pay_amount = pay_amount
        self.buy_amount = buy_amount
        self.band = band
        self._confirm_function = confirm_function

    def confirm(self):
        self._confirm_function()

    def __repr__(self):
        return pformat(vars(self))


class Bands:
    logger = logging.getLogger()

    @staticmethod
    def read(reloadable_config: ReloadableConfig, spread_feed: Feed, control_feed: Feed, history: History):
        assert(isinstance(reloadable_config, ReloadableConfig))
        assert(isinstance(spread_feed, Feed))
        assert(isinstance(control_feed, Feed))
        assert(isinstance(history, History))

        try:
            config = reloadable_config.get_config(spread_feed.get()[0])
            control_feed_value = control_feed.get()[0]

            buy_bands = list(map(BuyBand, config['buyBands']))
            buy_limits = SideLimits(config['buyLimits'] if 'buyLimits' in config else [], history.buy_history)
            sell_bands = list(map(SellBand, config['sellBands']))
            sell_limits = SideLimits(config['sellLimits'] if 'sellLimits' in config else [], history.sell_history)

            if len(buy_bands) != 1:
                logging.getLogger().warning("You must only have one buy band. This is required for airswap compatability.")
                buy_bands = []

            if len(sell_bands) != 1:
                logging.getLogger().warning("You must only have one sell band. This is required for airswap compatability.")
                sell_bands = []

            if 'canBuy' not in control_feed_value or 'canSell' not in control_feed_value:
                logging.getLogger().warning("Control feed expired. Assuming no buy bands and no sell bands.")

                buy_bands = []
                sell_bands = []

            else:
                if not control_feed_value['canBuy']:
                    logging.getLogger().warning("Control feed says we shall not buy. Assuming no buy bands.")
                    buy_bands = []

                if not control_feed_value['canSell']:
                    logging.getLogger().warning("Control feed says we shall not sell. Assuming no sell bands.")
                    sell_bands = []

        except Exception as e:
            logging.getLogger().exception(f"Config file is invalid ({e}). Treating the config file as it has no bands.")

            buy_bands = []
            buy_limits = SideLimits([], history.buy_history)
            sell_bands = []
            sell_limits = SideLimits([], history.buy_history)

        return Bands(buy_bands=buy_bands, buy_limits=buy_limits, sell_bands=sell_bands, sell_limits=sell_limits)

    def __init__(self, buy_bands: list, buy_limits: SideLimits, sell_bands: list, sell_limits: SideLimits):
        assert(isinstance(buy_bands, list))
        assert(isinstance(buy_limits, SideLimits))
        assert(isinstance(sell_bands, list))
        assert(isinstance(sell_limits, SideLimits))

        self.buy_bands = buy_bands
        self.buy_limits = buy_limits
        self.sell_bands = sell_bands
        self.sell_limits = sell_limits

        if self._bands_overlap(self.buy_bands) or self._bands_overlap(self.sell_bands):
            self.logger.warning("Bands in the config file overlap. Treating the config file as it has no bands.")

            self.buy_bands = []
            self.sell_bands = []

    def _excessive_sell_orders(self, our_sell_orders: list, target_price: Wad):
        """Return sell orders which need to be cancelled to bring total amounts within all sell bands below maximums."""
        assert(isinstance(our_sell_orders, list))
        assert(isinstance(target_price, Wad))

        bands = self.sell_bands

        for band in bands:
            for order in band.excessive_orders(our_sell_orders, target_price, band == bands[0], band == bands[-1]):
                yield order

    def _excessive_buy_orders(self, our_buy_orders: list, target_price: Wad):
        """Return buy orders which need to be cancelled to bring total amounts within all buy bands below maximums."""
        assert(isinstance(our_buy_orders, list))
        assert(isinstance(target_price, Wad))

        bands = self.buy_bands

        for band in bands:
            for order in band.excessive_orders(our_buy_orders, target_price, band == bands[0], band == bands[-1]):
                yield order

    def _outside_any_band_orders(self, orders: list, bands: list, target_price: Wad):
        """Return buy or sell orders which need to be cancelled as they do not fall into any buy or sell band."""
        assert(isinstance(orders, list))
        assert(isinstance(bands, list))
        assert(isinstance(target_price, Wad))

        for order in orders:
            if not any(band.includes(order, target_price) for band in bands):
                self.logger.info(f"Order #{order.order_id} doesn't belong to any band, scheduling it for cancellation")

                yield order


    def new_order(self, token_amount: Wad, side_amount: str, our_buy_balance: Wad, our_sell_balance: Wad, target_price: Price) -> Tuple[list, Wad, Wad]:
        assert(isinstance(side_amount, str))
        assert(isinstance(token_amount, Wad))
        assert(isinstance(our_buy_balance, Wad))
        assert(isinstance(our_sell_balance, Wad))
        assert(isinstance(target_price, Price))

        if target_price is not None and token_amount is not None:

            new_buy_order = self._new_buy_order(token_amount, our_buy_balance, target_price.buy_price) \
                if target_price.buy_price is not None \
                else {}

            new_sell_order = self._new_sell_orders(token_amount, our_sell_balance, target_price.sell_price) \
                if target_price.sell_price is not None \
                else {}

            return new_buy_order

        else:
            return "No orders"

    def _new_sell_orders(self, token_amount, our_sell_balance: Wad, target_price: Wad):
        """Return sell orders which need to be placed to bring total amounts within all sell bands above minimums."""
        assert(isinstance(our_sell_balance, Wad))
        assert(isinstance(target_price, Wad))

       # new_orders = []
       # limit_amount = self.sell_limits.available_limit(time.time())
       # missing_amount = Wad(0)

       # for band in self.sell_bands:
       #     if total_amount < band.min_amount:
       #         price = band.avg_price(target_price)
       #         pay_amount = Wad.min(band.avg_amount - total_amount, our_sell_balance, limit_amount)
       #         buy_amount = pay_amount * price
       #         missing_amount += Wad.max((band.avg_amount - total_amount) - our_sell_balance, Wad(0))
       #         if (price > Wad(0)) and (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
       #             self.logger.info(f"Sell band (spread <{band.min_margin}, {band.max_margin}>,"
       #                              f" amount <{band.min_amount}, {band.max_amount}>) has amount {total_amount},"
       #                              f" creating new sell order with price {price}")

       #             our_sell_balance = our_sell_balance - pay_amount
       #             limit_amount = limit_amount - pay_amount

       #             new_orders.append(NewOrder(is_sell=True,
       #                                        price=price,
       #                                        amount=pay_amount,
       #                                        pay_amount=pay_amount,
       #                                        buy_amount=buy_amount,
       #                                        band=band,
       #                                        confirm_function=lambda: self.sell_limits.use_limit(time.time(), pay_amount)))

        return "hey"

    def _new_buy_order(self, token_amount, our_buy_balance: Wad, target_price: Wad):
        """Return buy orders which need to be placed to bring total amounts within all buy bands above minimums."""
        assert(isinstance(token_amount, Wad))
        assert(isinstance(our_buy_balance, Wad))
        assert(isinstance(target_price, Wad))

        new_order = {}
        limit_amount = self.buy_limits.available_limit(time.time())
        missing_amount = Wad(0)
        band = self.buy_bands[0]

        price = band.avg_price(target_price)
        buy_amount = token_amount / price

        if (price > Wad(0)) and (token_amount > Wad(0)) and (buy_amount > Wad(0)):
            self.logger.info(f"Buy band (spread <{band.min_margin}, {band.max_margin}>,"
                             f" amount <{band.min_amount}, {band.max_amount}>) has amount {token_amount},"
                             f" creating new buy order with price {price}")

#           our_buy_balance = our_buy_balance - pay_amount
#           limit_amount = limit_amount - pay_amount

            new_order = {
                "maker_amount": token_amount,
                "taker_amount": buy_amount
            }

        return new_order

    @staticmethod
    def total_amount(orders):
        return reduce(operator.add, map(lambda order: order.remaining_sell_amount, orders), Wad(0))


    @staticmethod
    def _one_band_per_side(bands: list):
        for band in bands:
            print(f"band here - {band}")

    @staticmethod
    def _bands_overlap(bands: list):
        def two_bands_overlap(band1, band2):
            return band1.min_margin < band2.max_margin and band2.min_margin < band1.max_margin

        for band1 in bands:
            if len(list(filter(lambda band2: two_bands_overlap(band1, band2), bands))) > 1:
                return True

        return False
