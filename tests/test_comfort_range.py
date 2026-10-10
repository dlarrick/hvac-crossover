"""Unit tests for ComfortRange deadband, deficits, and open-ended floating."""

import unittest
from hvac_crossover.models.spaces import ComfortRange


class TestComfortRange(unittest.TestCase):

    def test_standard_deadband(self):
        # 68°F to 72°F comfort band
        comfort = ComfortRange(heat_to=68.0, cool_to=72.0)
        self.assertTrue(comfort.demands_heat(66.0))
        self.assertFalse(comfort.demands_heat(69.0))
        self.assertTrue(comfort.is_in_deadband(70.0))
        self.assertFalse(comfort.demands_cool(71.0))
        self.assertTrue(comfort.demands_cool(73.5))

    def test_deficits(self):
        comfort = ComfortRange(heat_to=68.0, cool_to=72.0)
        self.assertEqual(comfort.heat_deficit(64.5), 3.5)
        self.assertEqual(comfort.heat_deficit(69.0), 0.0)
        self.assertEqual(comfort.cool_deficit(74.5), 2.5)
        self.assertEqual(comfort.cool_deficit(70.0), 0.0)

    def test_open_ended_uncontrolled_float(self):
        # Passive thermal banking (100% uncontrolled float)
        comfort = ComfortRange(heat_to=None, cool_to=None)
        self.assertFalse(comfort.demands_heat(50.0))
        self.assertFalse(comfort.demands_cool(90.0))
        self.assertTrue(comfort.is_in_deadband(65.0))
        self.assertEqual(comfort.heat_deficit(40.0), 0.0)

    def test_solar_cap_cooling_only(self):
        # Upper-floor solar cap: no heating, cool if above 75.0°F
        comfort = ComfortRange(heat_to=None, cool_to=75.0)
        self.assertFalse(comfort.demands_heat(60.0))
        self.assertTrue(comfort.is_in_deadband(72.0))
        self.assertTrue(comfort.demands_cool(76.5))
        self.assertEqual(comfort.cool_deficit(77.0), 2.0)

    def test_freeze_protection_heating_only(self):
        # Away freeze protection: heat if below 55.0°F, no cooling
        comfort = ComfortRange(heat_to=55.0, cool_to=None)
        self.assertTrue(comfort.demands_heat(52.0))
        self.assertFalse(comfort.demands_heat(56.0))
        self.assertFalse(comfort.demands_cool(85.0))


if __name__ == "__main__":
    unittest.main()
