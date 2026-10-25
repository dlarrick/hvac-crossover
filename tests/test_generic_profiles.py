"""Tests for generic conservative equipment profiles."""

import unittest

from hvac_crossover.models.enums import FuelType, NetMeteringMode
from hvac_crossover.models.producers import (
    InverterHeatPumpProducer,
    ModulatingBoilerProducer,
    ProducerProfileFactory,
    SteamBoilerProducer,
)
from hvac_crossover.models.tariffs import TariffSchedule

PROFILES_ROOT = "hvac_crossover/profiles/producers"


class TestGenericHeatPumpProfiles(unittest.TestCase):
    def setUp(self):
        self.tariffs = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            solar_export_credit=0.05,
            net_metering_mode=NetMeteringMode.ONE_TO_ONE,
        )

    def test_cold_climate_hp_loads_as_inverter(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/cold_climate_heat_pump.json", "cc_hp")
        self.assertIsInstance(hp, InverterHeatPumpProducer)
        self.assertAlmostEqual(hp.min_operational_temp_f, -4.0)

    def test_cold_climate_hp_cop_at_design_points(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/cold_climate_heat_pump.json", "cc_hp")
        self.assertAlmostEqual(hp.cop_at(47.0), 3.30)
        self.assertAlmostEqual(hp.cop_at(17.0), 2.40)
        self.assertAlmostEqual(hp.cop_at(5.0), 2.00)
        self.assertAlmostEqual(hp.cop_at(-4.0), 1.75)

    def test_cold_climate_hp_interpolates(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/cold_climate_heat_pump.json", "cc_hp")
        cop_30 = hp.cop_at(30.0)
        self.assertGreater(cop_30, 2.40)
        self.assertLess(cop_30, 3.30)

    def test_cold_climate_hp_within_envelope(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/cold_climate_heat_pump.json", "cc_hp")
        self.assertTrue(hp.is_within_envelope(0.0))
        self.assertTrue(hp.is_within_envelope(-4.0))
        self.assertFalse(hp.is_within_envelope(-5.0))
        # With 5°F safety band: effective cutoff is 1°F
        self.assertFalse(hp.is_within_envelope(0.0, safety_band_f=5.0))

    def test_standard_hp_loads_and_min_temp(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/standard_heat_pump.json", "std_hp")
        self.assertIsInstance(hp, InverterHeatPumpProducer)
        self.assertAlmostEqual(hp.min_operational_temp_f, 5.0)

    def test_standard_hp_cop_at_design_points(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/standard_heat_pump.json", "std_hp")
        self.assertAlmostEqual(hp.cop_at(47.0), 3.00)
        self.assertAlmostEqual(hp.cop_at(17.0), 2.00)
        self.assertAlmostEqual(hp.cop_at(5.0), 1.50)

    def test_standard_hp_extrapolates_flat_below_min(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/standard_heat_pump.json", "std_hp")
        # Below the lowest data point (5°F), flat-extrapolates to 1.50
        self.assertAlmostEqual(hp.cop_at(0.0), 1.50)

    def test_standard_hp_not_within_envelope_below_5f(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/standard_heat_pump.json", "std_hp")
        self.assertTrue(hp.is_within_envelope(10.0))
        self.assertFalse(hp.is_within_envelope(4.9))

    def test_cold_climate_hp_delivered_cost(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/generic/cold_climate_heat_pump.json", "cc_hp")
        cost = hp.delivered_cost_per_mmbtu(17.0, self.tariffs)
        # At COP 2.40: $0.25 / (2.40 * 0.003412) = ~$30.50/MMBtu
        self.assertGreater(cost, 20.0)
        self.assertLess(cost, 50.0)


class TestGenericBoilerProfiles(unittest.TestCase):
    def setUp(self):
        self.tariffs_gas = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            net_metering_mode=NetMeteringMode.ONE_TO_ONE,
        )
        self.tariffs_propane = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            propane_cost_per_gal=3.50,
            net_metering_mode=NetMeteringMode.ONE_TO_ONE,
        )

    def test_mod_con_boiler_loads_as_modulating(self):
        boiler = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/modulating_condensing_boiler.json", "gen_boiler"
        )
        self.assertIsInstance(boiler, ModulatingBoilerProducer)
        self.assertEqual(boiler.fuel_type, FuelType.NATURAL_GAS)
        self.assertAlmostEqual(boiler.rated_afue, 0.95)
        self.assertAlmostEqual(boiler.min_fire_btu, 25000.0)
        self.assertAlmostEqual(boiler.turndown_ratio, 4.0)

    def test_mod_con_boiler_delivered_cost(self):
        boiler = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/modulating_condensing_boiler.json", "gen_boiler"
        )
        cost = boiler.delivered_cost_per_mmbtu(35.0, self.tariffs_gas)
        # $1.88/therm at 95% AFUE: ~$19.79/MMBtu
        self.assertAlmostEqual(cost, 1.88 / (0.95 * 0.100), places=1)

    def test_mod_con_boiler_propane_override(self):
        boiler = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/modulating_condensing_boiler.json",
            "gen_boiler_lp",
            fuel_type=FuelType.PROPANE,
        )
        self.assertEqual(boiler.fuel_type, FuelType.PROPANE)
        cost = boiler.delivered_cost_per_mmbtu(35.0, self.tariffs_propane)
        # $3.50/gal at 91500 BTU/gal, 95% AFUE
        expected = (3.50 / 0.0915) / 0.95
        self.assertAlmostEqual(cost, expected, places=0)

    def test_atmospheric_cast_iron_loads_as_steam_boiler(self):
        boiler = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/atmospheric_cast_iron_boiler.json",
            "cast_iron",
        )
        self.assertIsInstance(boiler, SteamBoilerProducer)
        self.assertAlmostEqual(boiler.rated_afue, 0.82)
        self.assertEqual(boiler.min_run_time_minutes, 20)

    def test_atmospheric_cast_iron_delivered_cost(self):
        boiler = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/atmospheric_cast_iron_boiler.json",
            "cast_iron",
        )
        cost = boiler.delivered_cost_per_mmbtu(35.0, self.tariffs_gas)
        # $1.88/therm at 82% AFUE: ~$22.93/MMBtu
        self.assertAlmostEqual(cost, 1.88 / (0.82 * 0.100), places=1)


if __name__ == "__main__":
    unittest.main()
