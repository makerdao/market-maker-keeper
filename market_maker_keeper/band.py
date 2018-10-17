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
                 dust_cutoff: Wad):
        assert(isinstance(min_margin, float))
        assert(isinstance(avg_margin, float))
        assert(isinstance(max_margin, float))
        assert(isinstance(min_amount, Wad))
        assert(isinstance(avg_amount, Wad))
        assert(isinstance(max_amount, Wad))
        assert(isinstance(dust_cutoff, Wad))

        self.min_margin = min_margin
        self.avg_margin = avg_margin
        self.max_margin = max_margin
        self.min_amount = min_amount
        self.avg_amount = avg_amount
        self.max_amount = max_amount
        self.dust_cutoff = dust_cutoff

        assert(self.min_amount >= Wad(0))
        assert(self.avg_amount >= Wad(0))
        assert(self.max_amount >= Wad(0))
        assert(self.min_amount <= self.avg_amount)
        assert(self.avg_amount <= self.max_amount)

        assert(self.min_margin <= self.avg_margin)
        assert(self.avg_margin <= self.max_margin)
        assert(self.min_margin < self.max_margin)

    def includes(self, order, target_price: Wad) -> bool:
        raise NotImplemented()

    def excessive_orders(self, orders: list, target_price: Wad):
        """Return orders which need to be cancelled to bring the total order amount in the band below maximum."""

        # Get all orders which are currently present in the band.
        orders_in_band = [order for order in orders if self.includes(order, target_price)]

        # Keep removing orders starting from the smallest one until their total amount
        # stops being greater than `maxAmount`.
        orders_to_leave = sorted(orders_in_band, key=lambda order: order.remaining_sell_amount, reverse=True)
        while Bands.total_amount(orders_to_leave) > self.max_amount:
            orders_to_leave.pop()

        return set(orders_in_band) - set(orders_to_leave)


class BuyBand(Band):
    def __init__(self, dictionary: dict):
        super().__init__(min_margin=float(dictionary['minMargin']),
                         avg_margin=float(dictionary['avgMargin']),
                         max_margin=float(dictionary['maxMargin']),
                         min_amount=Wad.from_number(dictionary['minAmount']),
                         avg_amount=Wad.from_number(dictionary['avgAmount']),
                         max_amount=Wad.from_number(dictionary['maxAmount']),
                         dust_cutoff=Wad.from_number(dictionary['dustCutoff']))

    def includes(self, order, target_price: Wad) -> bool:
        price = order.sell_to_buy_price
        price_min = self._apply_margin(target_price, self.min_margin)
        price_max = self._apply_margin(target_price, self.max_margin)
        return (price > price_max) and (price <= price_min)

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
                         dust_cutoff=Wad.from_number(dictionary['dustCutoff']))

    def includes(self, order, target_price: Wad) -> bool:
        price = order.buy_to_sell_price
        price_min = self._apply_margin(target_price, self.min_margin)
        price_max = self._apply_margin(target_price, self.max_margin)
        return (price > price_min) and (price <= price_max)

    def avg_price(self, target_price: Wad) -> Wad:
        return self._apply_margin(target_price, self.avg_margin)

    @staticmethod
    def _apply_margin(price: Wad, margin: float) -> Wad:
        return price * Wad.from_number(1 + margin)


class NewOrder:
    def __init__(self, is_sell: bool, price: Wad, amount: Wad, pay_amount: Wad, buy_amount: Wad, confirm_function):
        assert(isinstance(is_sell, bool))
        assert(isinstance(price, Wad))
        assert(isinstance(amount, Wad))
        assert(isinstance(pay_amount, Wad))
        assert(isinstance(buy_amount, Wad))
        assert(callable(confirm_function))

        self.is_sell = is_sell
        self.price = price
        self.amount = amount
        self.pay_amount = pay_amount
        self.buy_amount = buy_amount
        self.confirm_function = confirm_function

    def confirm(self):
        self.confirm_function()

    def __repr__(self):
        return pformat(vars(self))


class Bands:
    logger = logging.getLogger()

    @staticmethod
    def read(reloadable_config: ReloadableConfig, spread_feed: Feed, history: History):
        assert(isinstance(reloadable_config, ReloadableConfig))
        assert(isinstance(history, History))

        try:
            config = reloadable_config.get_config(spread_feed.get()[0])

            buy_bands = list(map(BuyBand, config['buyBands']))
            buy_limits = SideLimits(config['buyLimits'] if 'buyLimits' in config else [], history.buy_history)
            sell_bands = list(map(SellBand, config['sellBands']))
            sell_limits = SideLimits(config['sellLimits'] if 'sellLimits' in config else [], history.sell_history)
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

        for band in self.sell_bands:
            for order in band.excessive_orders(our_sell_orders, target_price):
                yield order

    def _excessive_buy_orders(self, our_buy_orders: list, target_price: Wad):
        """Return buy orders which need to be cancelled to bring total amounts within all buy bands below maximums."""
        assert(isinstance(our_buy_orders, list))
        assert(isinstance(target_price, Wad))

        for band in self.buy_bands:
            for order in band.excessive_orders(our_buy_orders, target_price):
                yield order

    def _outside_any_band_orders(self, orders: list, bands: list, target_price: Wad):
        """Return buy or sell orders which need to be cancelled as they do not fall into any buy or sell band."""
        assert(isinstance(orders, list))
        assert(isinstance(bands, list))
        assert(isinstance(target_price, Wad))

        for order in orders:
            if not any(band.includes(order, target_price) for band in bands):
                yield order

    def cancellable_orders(self, our_buy_orders: list, our_sell_orders: list, target_price: Price) -> list:
        assert(isinstance(our_buy_orders, list))
        assert(isinstance(our_sell_orders, list))
        assert(isinstance(target_price, Price))

        if target_price.buy_price is None:
            self.logger.warning("Cancelling all buy orders as no buy price is available.")
            buy_orders_to_cancel = our_buy_orders

        else:
            buy_orders_to_cancel = list(itertools.chain(self._excessive_buy_orders(our_buy_orders, target_price.buy_price),
                                                        self._outside_any_band_orders(our_buy_orders, self.buy_bands, target_price.buy_price)))

        if target_price.sell_price is None:
            self.logger.warning("Cancelling all sell orders as no sell price is available.")
            sell_orders_to_cancel = our_sell_orders

        else:
            sell_orders_to_cancel = list(itertools.chain(self._excessive_sell_orders(our_sell_orders, target_price.sell_price),
                                                         self._outside_any_band_orders(our_sell_orders, self.sell_bands, target_price.sell_price)))

        return buy_orders_to_cancel + sell_orders_to_cancel

    def new_orders(self, our_buy_orders: list, our_sell_orders: list, our_buy_balance: Wad, our_sell_balance: Wad, target_price: Price) -> Tuple[list, Wad, Wad]:
        assert(isinstance(our_buy_orders, list))
        assert(isinstance(our_sell_orders, list))
        assert(isinstance(our_buy_balance, Wad))
        assert(isinstance(our_sell_balance, Wad))
        assert(isinstance(target_price, Price))

        if target_price is not None:
            new_buy_orders, missing_buy_amount = self._new_buy_orders(our_buy_orders, our_buy_balance, target_price.buy_price) \
                if target_price.buy_price is not None \
                else ([], Wad(0))

            new_sell_orders, missing_sell_amount = self._new_sell_orders(our_sell_orders, our_sell_balance, target_price.sell_price) \
                if target_price.sell_price is not None \
                else ([], Wad(0))

            return new_buy_orders + new_sell_orders, missing_buy_amount, missing_sell_amount

        else:
            return [], Wad(0), Wad(0)

    def _new_sell_orders(self, our_sell_orders: list, our_sell_balance: Wad, target_price: Wad):
        """Return sell orders which need to be placed to bring total amounts within all sell bands above minimums."""
        assert(isinstance(our_sell_orders, list))
        assert(isinstance(our_sell_balance, Wad))
        assert(isinstance(target_price, Wad))

        new_orders = []
        limit_amount = self.sell_limits.available_limit(time.time())
        missing_amount = Wad(0)

        for band in self.sell_bands:
            orders = [order for order in our_sell_orders if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = band.avg_price(target_price)
                pay_amount = Wad.min(band.avg_amount - total_amount, our_sell_balance, limit_amount)
                buy_amount = pay_amount * price
                missing_amount += Wad.max((band.avg_amount - total_amount) - our_sell_balance, Wad(0))
                if (price > Wad(0)) and (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new sell order")

                    our_sell_balance = our_sell_balance - pay_amount
                    limit_amount = limit_amount - pay_amount

                    new_orders.append(NewOrder(is_sell=True,
                                               price=price,
                                               amount=pay_amount,
                                               pay_amount=pay_amount,
                                               buy_amount=buy_amount,
                                               confirm_function=lambda: self.sell_limits.use_limit(time.time(), pay_amount)))

        return new_orders, missing_amount

    def _new_buy_orders(self, our_buy_orders: list, our_buy_balance: Wad, target_price: Wad):
        """Return buy orders which need to be placed to bring total amounts within all buy bands above minimums."""
        assert(isinstance(our_buy_orders, list))
        assert(isinstance(our_buy_balance, Wad))
        assert(isinstance(target_price, Wad))

        new_orders = []
        limit_amount = self.buy_limits.available_limit(time.time())
        missing_amount = Wad(0)

        for band in self.buy_bands:
            orders = [order for order in our_buy_orders if band.includes(order, target_price)]
            total_amount = self.total_amount(orders)
            if total_amount < band.min_amount:
                price = band.avg_price(target_price)
                pay_amount = Wad.min(band.avg_amount - total_amount, our_buy_balance, limit_amount)
                buy_amount = pay_amount / price
                missing_amount += Wad.max((band.avg_amount - total_amount) - our_buy_balance, Wad(0))
                if (price > Wad(0)) and (pay_amount >= band.dust_cutoff) and (pay_amount > Wad(0)) and (buy_amount > Wad(0)):
                    self.logger.debug(f"Using price {price} for new buy order")

                    our_buy_balance = our_buy_balance - pay_amount
                    limit_amount = limit_amount - pay_amount

                    new_orders.append(NewOrder(is_sell=False,
                                               price=price,
                                               amount=buy_amount,
                                               pay_amount=pay_amount,
                                               buy_amount=buy_amount,
                                               confirm_function=lambda: self.buy_limits.use_limit(time.time(), pay_amount)))

        return new_orders, missing_amount

    @staticmethod
    def total_amount(orders):
        return reduce(operator.add, map(lambda order: order.remaining_sell_amount, orders), Wad(0))

    @staticmethod
    def _bands_overlap(bands: list):
        def two_bands_overlap(band1, band2):
            return band1.min_margin < band2.max_margin and band2.min_margin < band1.max_margin

        for band1 in bands:
            if len(list(filter(lambda band2: two_bands_overlap(band1, band2), bands))) > 1:
                return True

        return False
