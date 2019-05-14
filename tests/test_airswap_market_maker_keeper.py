
import unittest

from pymaker.numeric import Wad


from tests.band_config import BandConfig
from tests.test_band import TestBands
from tests.test_price_feed import FakeFeed

from market_maker_keeper.airswap_market_maker_keeper import AirswapMarketMakerKeeper, AirswapBands, min_price, max_price
from market_maker_keeper.airswap_market_maker_keeper import closest_margin_to_amount, _amount_to_margin, _find_closest
from market_maker_keeper.price_feed import PriceFeed, BackupPriceFeed, AveragePriceFeed, Price, WebSocketPriceFeed, ReversePriceFeed
from market_maker_keeper.reloadable_config import ReloadableConfig
from market_maker_keeper.feed import EmptyFeed, FixedFeed
from market_maker_keeper.limit import History
from market_maker_keeper.price_feed import PriceFeedFactory

# test getOrder route


# test new_sell_orders

# test new_buy_orders


def test_airswap_read_bands(tmpdir):
    bands_file = BandConfig.sample_config(tmpdir)
    bands_config = ReloadableConfig(str(bands_file))
    airswap_bands = AirswapBands.read(bands_config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())
    assert isinstance(airswap_bands, AirswapBands)


def test_airswap_read_no_adjacent_bands(tmpdir):
    sell_bands_file = BandConfig.two_adjacent_sell_bands_config(tmpdir)
    sell_bands_config = ReloadableConfig(str(sell_bands_file))
    airswap_sell_bands = AirswapBands.read(sell_bands_config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())
    assert len(airswap_sell_bands.sell_bands) == 0

    buy_bands_file = BandConfig.two_adjacent_buy_bands_config(tmpdir)
    buy_bands_config = ReloadableConfig(str(buy_bands_file))
    airswap_buy_bands = AirswapBands.read(buy_bands_config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())
    assert len(airswap_buy_bands.buy_bands) == 0

def test_new_buy_orders_maker_amount_success_case(tmpdir):
    bands_file = BandConfig.sample_config(tmpdir)
    bands_config = ReloadableConfig(str(bands_file))
    airswap_bands = AirswapBands.read(bands_config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())

    maker_amount = Wad(156200000000000000)
    taker_amount = Wad(0)
    our_buy_balance = Wad(1562000000000000000000)
    target_price = WebSocketPriceFeed(FakeFeed({"buyPrice": "120", "sellPrice": "130"})).get_price()

    new_order = airswap_bands._new_buy_orders(maker_amount, taker_amount, our_buy_balance, target_price.buy_price)

    # -- pricing logic --
    # buyPrice = 120 * minMargin = 0.02 = 117.6
    # maker_amount = .1562000 * 117.6 = 18.36912000

    assert new_order['taker_amount'].__float__() == 18.36912000
    assert new_order['maker_amount'].__float__() == 0.1562000

def test_new_buy_orders_taker_amount_success_case(tmpdir):
    bands_file = BandConfig.sample_config(tmpdir)
    bands_config = ReloadableConfig(str(bands_file))
    airswap_bands = AirswapBands.read(bands_config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())

    maker_amount = Wad(0)
    taker_amount = Wad(11360000000000000000)
    our_buy_balance = Wad(1562000000000000000000)
    target_price = WebSocketPriceFeed(FakeFeed({"buyPrice": "120", "sellPrice": "130"})).get_price()

    new_order = airswap_bands._new_buy_orders(maker_amount, taker_amount, our_buy_balance, target_price.buy_price)

    # -- pricing logic --
    # buyPrice = 120 * minMargin = 0.02 = 117.6
    # maker_amount = 11.36000 / 117.6 = 0.09659863945

    assert new_order['taker_amount'].__float__() == 11.36000
    assert new_order['maker_amount'].__float__() == 0.09659863945578231


def test_new_buy_orders_taker_amount_exceed_buy_balance_fail_case(tmpdir):
    bands_file = BandConfig.sample_config(tmpdir)
    bands_config = ReloadableConfig(str(bands_file))
    airswap_bands = AirswapBands.read(bands_config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())

    maker_amount = Wad(0)
    taker_amount = Wad(11360000000000000000)
    our_buy_balance = Wad(50000000000000000)
    target_price = WebSocketPriceFeed(FakeFeed({"buyPrice": "120", "sellPrice": "130"})).get_price()

    new_order = airswap_bands._new_buy_orders(maker_amount, taker_amount, our_buy_balance, target_price.buy_price)

    # -- pricing logic --
    # buyPrice = 120 * minMargin = 0.02 = 117.6
    # maker_amount = 11.36000 / 117.6 = 0.09659863945
    # our_buy_balance = 0.050000000000000000 !!BREAK!!

    assert new_order == {}


def test_new_buy_orders_maker_amount_exceed_buy_balance_fail_case(tmpdir):
    bands_file = BandConfig.sample_config(tmpdir)
    bands_config = ReloadableConfig(str(bands_file))
    airswap_bands = AirswapBands.read(bands_config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())

    maker_amount = Wad(156200000000000000)
    taker_amount = Wad(0)
    our_buy_balance = Wad(50000000000000000)
    target_price = WebSocketPriceFeed(FakeFeed({"buyPrice": "120", "sellPrice": "130"})).get_price()

    new_order = airswap_bands._new_buy_orders(maker_amount, taker_amount, our_buy_balance, target_price.buy_price)

    # -- pricing logic --
    # buyPrice = 120 * minMargin = 0.02 = 117.6
    # maker_amount = .1562000 * 117.6 = 18.36912000
    # our_buy_balance = 0.050000000000000000 !!BREAK!!

    assert new_order == {}


#def test_new_sell_orders_maker_amount_success_case(tmpdir):
#    bands_file = BandConfig.sample_config(tmpdir)
#    bands_config = ReloadableConfig(str(bands_file))
#    airswap_bands = AirswapBands.read(bands_config, EmptyFeed(), FixedFeed({'canBuy': True, 'canSell': True}), History())
#
#    maker_amount = Wad(156200000000000000000)
#    taker_amount = Wad(0)
#    our_sell_balance = Wad(1562000000000000000000)
#    target_price = WebSocketPriceFeed(FakeFeed({"buyPrice": "120", "sellPrice": "130"})).get_price()
#
#    print(f'maker_amount {maker_amount.__float__()}')
#
#    new_order = airswap_bands._new_sell_orders(maker_amount, taker_amount, our_sell_balance, target_price.sell_price)
#
#    # -- pricing logic --
#    # sellPrice = 130 * maxMargin = 0.06 = 7.8
#    # 130 + 7.8 = 137.8
#    # maker_amount = .1562000 * 117.6 = 18.36912000
#
#    assert new_order == 0


if __name__ == '__main__':
    unittest.main()



