"""Tests for generic conservative equipment profiles."""

import unittest

from hvac_crossover.models.enums import FuelType, NetMeteringMode
from hvac_crossover.models.producers import (
    AtmosphericBoilerProducer,
    FurnaceProducer,
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
        # Part-load COP
        self.assertAlmostEqual(hp.cop_at(47.0, part_load=True), 3.85)
        self.assertAlmostEqual(hp.cop_at(17.0, part_load=True), 2.75)
        self.assertAlmostEqual(hp.cop_at(5.0, part_load=True), 2.25)
        self.assertAlmostEqual(hp.cop_at(-4.0, part_load=True), 1.95)

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
        # Part-load COP
        self.assertAlmostEqual(hp.cop_at(47.0, part_load=True), 3.50)
        self.assertAlmostEqual(hp.cop_at(17.0, part_load=True), 2.35)
        self.assertAlmostEqual(hp.cop_at(5.0, part_load=True), 1.70)

    def test_haier_loads_and_has_part_load(self):
        hp = ProducerProfileFactory.load_from_json(f"{PROFILES_ROOT}/haier/xuxxms2vhb.json", "haier_hp")
        self.assertAlmostEqual(hp.cop_at(47.0), 3.19)
        self.assertAlmostEqual(hp.cop_at(47.0, part_load=True), 3.90)
        self.assertAlmostEqual(hp.cop_at(17.0, part_load=True), 2.80)

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

    def test_boiler_min_fire_computable_via_turndown_ratio(self):
        boiler = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/modulating_condensing_boiler.json", "gen_boiler", nominal_btu=80000.0
        )
        self.assertAlmostEqual(boiler.turndown_ratio, 4.0)
        # 80000 / 4.0 = 20000
        self.assertAlmostEqual(boiler.min_fire_btu, 20000.0)
        # Dynamic update of nominal capacity shifts min_fire
        boiler.nominal_btu = 60000.0
        self.assertAlmostEqual(boiler.min_fire_btu, 15000.0)
        # Explicit override
        boiler.min_fire_btu = 12000.0
        self.assertAlmostEqual(boiler.min_fire_btu, 12000.0)

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

    def test_atmospheric_cast_iron_gas_profile(self):
        boiler = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/atmospheric_cast_iron_boiler_gas.json",
            "cast_iron_gas",
        )
        self.assertIsInstance(boiler, AtmosphericBoilerProducer)
        self.assertEqual(boiler.fuel_type, FuelType.NATURAL_GAS)
        self.assertAlmostEqual(boiler.rated_afue, 0.83)
        cost = boiler.delivered_cost_per_mmbtu(35.0, self.tariffs_gas)
        self.assertAlmostEqual(cost, 1.88 / (0.83 * 0.100), places=1)

    def test_atmospheric_cast_iron_oil_profile(self):
        tariffs_oil = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            oil_cost_per_gal=3.80,
        )
        boiler = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/atmospheric_cast_iron_boiler_oil.json",
            "cast_iron_oil",
        )
        self.assertIsInstance(boiler, AtmosphericBoilerProducer)
        self.assertEqual(boiler.fuel_type, FuelType.OIL)
        self.assertAlmostEqual(boiler.rated_afue, 0.85)
        cost = boiler.delivered_cost_per_mmbtu(35.0, tariffs_oil)
        # $3.80/gal / 0.1385 / 0.85 AFUE
        self.assertAlmostEqual(cost, (3.80 / 0.1385) / 0.85, places=1)

    def test_furnace_profiles(self):
        furnace_std = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/furnace_gas_standard.json",
            "furnace_std",
        )
        self.assertIsInstance(furnace_std, FurnaceProducer)
        self.assertAlmostEqual(furnace_std.rated_afue, 0.80)
        self.assertEqual(furnace_std.fuel_type, FuelType.NATURAL_GAS)

        furnace_cond = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/furnace_gas_condensing.json",
            "furnace_cond",
        )
        self.assertIsInstance(furnace_cond, FurnaceProducer)
        self.assertAlmostEqual(furnace_cond.rated_afue, 0.95)

        tariffs_oil = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            oil_cost_per_gal=3.80,
        )
        furnace_oil = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/furnace_oil.json",
            "furnace_oil",
        )
        self.assertIsInstance(furnace_oil, FurnaceProducer)
        self.assertEqual(furnace_oil.fuel_type, FuelType.OIL)
        self.assertAlmostEqual(furnace_oil.rated_afue, 0.83)
        cost = furnace_oil.delivered_cost_per_mmbtu(35.0, tariffs_oil)
        self.assertAlmostEqual(cost, (3.80 / 0.1385) / 0.83, places=1)

    def test_profiles_omit_extraneous_fields(self):
        import json

        # Check all boiler profiles
        boiler_paths = [
            f"{PROFILES_ROOT}/generic/modulating_condensing_boiler.json",
            f"{PROFILES_ROOT}/burnham/alta.json",
            f"{PROFILES_ROOT}/bradford_white/bmfthw.json",
            f"{PROFILES_ROOT}/generic/atmospheric_cast_iron_boiler.json",
            f"{PROFILES_ROOT}/generic/atmospheric_cast_iron_boiler_gas.json",
            f"{PROFILES_ROOT}/generic/atmospheric_cast_iron_boiler_oil.json",
            f"{PROFILES_ROOT}/generic/steam_boiler_gas.json",
            f"{PROFILES_ROOT}/generic/steam_boiler_oil.json",
            f"{PROFILES_ROOT}/generic/furnace_gas_standard.json",
            f"{PROFILES_ROOT}/generic/furnace_gas_condensing.json",
            f"{PROFILES_ROOT}/generic/furnace_oil.json",
        ]
        for p in boiler_paths:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            self.assertNotIn("nominal_heating_btu", d, f"{p} still contains nominal_heating_btu")
            self.assertNotIn("min_fire_btu", d, f"{p} still contains min_fire_btu")
            self.assertNotIn("vintage", d, f"{p} still contains vintage")

        # Check all heat pump profiles
        hp_paths = [
            f"{PROFILES_ROOT}/generic/cold_climate_heat_pump.json",
            f"{PROFILES_ROOT}/generic/standard_heat_pump.json",
            f"{PROFILES_ROOT}/haier/xuxxms2vhb.json",
            f"{PROFILES_ROOT}/mitsubishi/mxz_xdxxnlhz.json",
            f"{PROFILES_ROOT}/mitsubishi/mxz_xcxxnahz2.json",
        ]
        for p in hp_paths:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            self.assertNotIn("nominal_heating_btu", d, f"{p} still contains nominal_heating_btu")
            self.assertNotIn("nominal_cooling_btu", d, f"{p} still contains nominal_cooling_btu")
            self.assertNotIn("refrigerant", d, f"{p} still contains refrigerant")
            self.assertNotIn("max_ports", d, f"{p} still contains max_ports")
            self.assertNotIn("vintage", d, f"{p} still contains vintage")
            self.assertNotIn(
                "max_capacity_btu", d.get("heating_performance", {}), f"{p} still contains max_capacity_btu"
            )
            self.assertIn("capacity_percentage", d.get("heating_performance", {}), f"{p} missing capacity_percentage")

    def test_heat_pump_derating_curve_scales_with_nominal_btu(self):
        # 36,000 BTU unit using cold-climate profile (100% at 47F, 75% at 17F, 62.5% at 5F, 50% at -4F)
        hp = ProducerProfileFactory.load_from_json(
            f"{PROFILES_ROOT}/generic/cold_climate_heat_pump.json",
            "cc_hp_36k",
            nominal_btu=36000.0,
        )
        self.assertEqual(hp.nominal_btu, 36000.0)
        self.assertAlmostEqual(hp.max_capacity_at(47.0), 36000.0)
        self.assertAlmostEqual(hp.max_capacity_at(17.0), 27000.0)  # 75%
        self.assertAlmostEqual(hp.max_capacity_at(5.0), 22500.0)  # 62.5%
        self.assertAlmostEqual(hp.max_capacity_at(-4.0), 18000.0)  # 50%
        self.assertEqual(hp.max_capacity_at(-10.0), 0.0)  # below -4.0F cutoff


if __name__ == "__main__":
    unittest.main()
