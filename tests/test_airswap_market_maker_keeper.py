
import unittest
from tests.band_config import BandConfig
from market_maker_keeper.airswap_market_maker_keeper import AirswapMarketMakerKeeper, AirswapBands, min_price, max_price

from market_maker_keeper.airswap_market_maker_keeper import closest_margin_to_amount, _amount_to_margin, _find_closest


# test getOrder route

# test new_orders

# test new_sell_orders

# test new_buy_orders

class TestAirswapKeeper(unittest.TestCase):

    def test_read_bands(self):
        config_file = BandConfig.sample_config()
        print(f"testing read_bands")

#    def test_isupper(self):
#        self.assertTrue('FOO'.isupper())
#        self.assertFalse('Foo'.isupper())
#
#    def test_split(self):
#        s = 'hello world'
#        self.assertEqual(s.split(), ['hello', 'world'])
#        # check that s.split fails when the separator is not a string
#        with self.assertRaises(TypeError):
#            s.split(2)

if __name__ == '__main__':
    unittest.main()



