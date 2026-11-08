"""Unit tests for EconomicsEngine, tariffs, and boiler efficiency curves."""

import unittest

from hvac_crossover.engines.economics import EconomicsEngine
from hvac_crossover.models.enums import FuelType, NetMeteringMode
from hvac_crossover.models.producers import ModulatingBoilerProducer, ProducerProfileFactory
from hvac_crossover.models.tariffs import TariffSchedule


class TestEconomicsEngine(unittest.TestCase):
    def setUp(self):
        self.economics = EconomicsEngine()
        self.tariffs = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            solar_export_credit=0.05,
            net_metering_mode=NetMeteringMode.ONE_TO_ONE,
        )
        self.odu1 = ProducerProfileFactory.load_from_json(
            "hvac_crossover/profiles/producers/mitsubishi/mxz_xcxxnahz2.json", "odu1"
        )
        self.boiler = ProducerProfileFactory.load_from_json(
            "hvac_crossover/profiles/producers/burnham/alta.json", "alta150"
        )

    def test_crossover_cop_derivation(self):
        # Rated AFUE 95% at $0.25/kWh and $1.88/therm -> COP* = 3.70
        cop_star = self.economics.compute_crossover_cop(self.tariffs, self.boiler)
        self.assertAlmostEqual(cop_star, 3.70, places=2)

    def test_crossover_temperature_odu1(self):
        # Crossover temp for MXZ-3C30 should be ~40.3°F (at part-load rated COP 4.00@47F, 2.65@17F)
        t_cross = self.economics.compute_crossover_temperature(self.tariffs, self.odu1, self.boiler)
        self.assertIsNotNone(t_cross)
        self.assertAlmostEqual(t_cross, 40.3, delta=0.5)

    def test_net_metering_marginal_costs(self):
        # 1:1 NEM: full retail $0.25 even with solar surplus
        cost_1to1 = self.tariffs.marginal_electric_cost(instantaneous_solar_surplus_watts=3000)
        self.assertEqual(cost_1to1, 0.25)

        # Net Billing: drops to $0.05 export credit during surplus
        net_billing_tariffs = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            solar_export_credit=0.05,
            net_metering_mode=NetMeteringMode.NET_BILLING,
        )
        cost_nb_surplus = net_billing_tariffs.marginal_electric_cost(instantaneous_solar_surplus_watts=3000)
        self.assertEqual(cost_nb_surplus, 0.05)

    def test_boiler_short_cycling_penalty(self):
        # Load >= 20k -> full 95% AFUE
        eff_full = self.boiler.efficiency_at(load_btu=30000)
        self.assertEqual(eff_full, 0.95)

        # Load = 6k (single zone call) -> derated to ~85%
        eff_cycled = self.boiler.efficiency_at(load_btu=6000)
        self.assertLess(eff_cycled, 0.90)
        self.assertGreater(eff_cycled, 0.80)

    def test_boiler_dew_point_curve(self):
        # Cold return water (100°F) during recovery pull-up -> 96.5% deep condensing
        eff_cold = self.boiler.efficiency_at(load_btu=40000, return_water_temp_f=100.0)
        self.assertEqual(eff_cold, 0.965)

        # Hot return water (145°F) from fin-tube baseboard -> 86.5% non-condensing
        eff_hot = self.boiler.efficiency_at(load_btu=40000, return_water_temp_f=145.0)
        self.assertEqual(eff_hot, 0.865)

    def test_propane_boiler_dew_point_shift(self):
        propane_boiler = ModulatingBoilerProducer(
            id="alta_lp",
            name="Alta 150 Propane",
            nominal_btu=150000,
            fuel_type=FuelType.PROPANE,
            rated_afue=0.94,
        )
        # Cold return (95°F) -> 94.0% deep condensation
        eff_deep = propane_boiler.efficiency_at(load_btu=40000, return_water_temp_f=95.0)
        self.assertEqual(eff_deep, 0.940)

        # Return at 105°F -> active condensation (91.5%)
        eff_mid = propane_boiler.efficiency_at(load_btu=40000, return_water_temp_f=105.0)
        self.assertEqual(eff_mid, 0.915)

        # Return at 130°F (well above propane dew point) -> strictly non-condensing (86.0%)
        eff_dry = propane_boiler.efficiency_at(load_btu=40000, return_water_temp_f=130.0)
        self.assertEqual(eff_dry, 0.860)

        # Return at 120°F (near propane dew point) -> weak onset (88.0%)
        # Note: on natural gas at 120°F, condensing is 89.0%
        self.assertEqual(propane_boiler.efficiency_at(load_btu=40000, return_water_temp_f=120.0), 0.880)

    def test_propane_delivered_cost_and_crossover(self):
        # Propane at $3.20/gal, electricity at $0.25/kWh
        tariffs = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            propane_cost_per_gal=3.20,
        )
        propane_boiler = ModulatingBoilerProducer(
            id="alta_lp",
            name="Alta 150 Propane",
            nominal_btu=150000,
            fuel_type=FuelType.PROPANE,
            rated_afue=0.94,
        )

        # Raw fuel input cost: $3.20 / 0.0915 = $34.9727/MMBtu
        self.assertAlmostEqual(tariffs.propane_cost_per_mmbtu_input, 34.97, places=1)

        # Delivered cost at 94% AFUE: 34.9727 / 0.94 = $37.20/MMBtu
        cost_mmbtu = propane_boiler.delivered_cost_per_mmbtu(outdoor_temp_f=35.0, tariffs=tariffs, load_btu=40000)
        self.assertAlmostEqual(cost_mmbtu, 37.20, places=1)

        # With expensive propane, the crossover COP* drops significantly:
        # COP* = (elec_cost / 0.003412) / boiler_delivered_cost
        cop_star = self.economics.compute_crossover_cop(tariffs, propane_boiler)
        # Heat pump wins at much lower COP (and therefore lower outdoor temps)
        self.assertLess(cop_star, 2.1)

    def test_delivery_unit_cost_propane(self):
        tariffs = TariffSchedule(
            elec_cost_per_kwh=0.226,
            gas_cost_per_therm=1.88,
            propane_cost_per_gal=3.691,
        )
        propane_boiler = ModulatingBoilerProducer(
            id="bw_lp",
            name="Bradford White Propane",
            nominal_btu=140000,
            fuel_type=FuelType.PROPANE,
            rated_afue=0.95,
        )
        from hvac_crossover.models.delivery import HydronicEmitter
        from hvac_crossover.models.topology import SystemTopology

        emitter = HydronicEmitter(id="bb", producer_id="bw_lp", zone_id="z1")
        topo = SystemTopology(producers=[propane_boiler], spaces=[])

        # Without live return telemetry, single-zone baseboard estimates 154.2°F return
        # (above propane dew point ~120°F -> dry non-condensing 86.0% AFUE)
        cost, eff = self.economics.evaluate_delivery_unit_cost(
            delivery_unit=emitter,
            outdoor_temp_f=40.0,
            tariffs=tariffs,
            topology=topo,
        )
        # Fuel cost: 3.691 / 0.0915 = 40.3388 / 0.860 = 46.91/MMBtu
        self.assertAlmostEqual(cost, 46.91, places=1)
        self.assertEqual(eff, 0.860)

    def test_decoupler_mixing_and_condensing_prediction(self):
        from hvac_crossover.models.delivery import HydronicEmitter
        from hvac_crossover.models.enums import EmitterType

        boiler = ModulatingBoilerProducer(
            id="alta150",
            name="Burnham Alta 150",
            nominal_btu=150000,
            fuel_type=FuelType.NATURAL_GAS,
            rated_afue=0.95,
            primary_flow_gpm=12.0,
        )

        # 1. Single-zone baseboard call:
        # Supply = 160°F, Q_z = 3.5 GPM, Delta T = 20°F, Q_primary = 12 GPM
        # alpha = 3.5 / 12 = 0.2917 -> T_return = 160 - 0.2917 * 20 = 154.2°F (86.5% AFUE)
        z1_bb = HydronicEmitter(
            id="bb1", producer_id="alta150", zone_id="z1", emitter_type=EmitterType.FIN_TUBE_BASEBOARD
        )
        t_ret_single = boiler.estimate_return_temperature([z1_bb])
        self.assertAlmostEqual(t_ret_single, 154.2, places=1)
        self.assertEqual(boiler.condensing_state(t_ret_single), "dry")
        self.assertAlmostEqual(boiler.efficiency_at(return_water_temp_f=t_ret_single), 0.865, places=3)

        # 2. Multi-zone baseboard call (3 zones active):
        # Q_zones = 10.5 GPM, alpha = 10.5 / 12 = 0.875 -> T_return = 160 - 0.875 * 20 = 142.5°F
        z2_bb = HydronicEmitter(
            id="bb2", producer_id="alta150", zone_id="z2", emitter_type=EmitterType.FIN_TUBE_BASEBOARD
        )
        z3_bb = HydronicEmitter(
            id="bb3", producer_id="alta150", zone_id="z3", emitter_type=EmitterType.FIN_TUBE_BASEBOARD
        )
        t_ret_multi = boiler.estimate_return_temperature([z1_bb, z2_bb, z3_bb])
        self.assertAlmostEqual(t_ret_multi, 142.5, places=1)

        # 3. In-floor radiant (low temperature):
        # Supply = 110°F, Delta T = 10°F, Q_z = 4.5 GPM -> alpha = 4.5 / 12 = 0.375
        # T_return = 110 - 0.375 * 10 = 106.25°F (~93.1% AFUE)
        rad = HydronicEmitter(
            id="rad1", producer_id="alta150", zone_id="z_rad", emitter_type=EmitterType.IN_FLOOR_RADIANT
        )
        t_ret_rad = boiler.estimate_return_temperature([rad])
        self.assertAlmostEqual(t_ret_rad, 106.25, places=1)
        self.assertEqual(boiler.condensing_state(t_ret_rad), "condensing")
        eff_rad = boiler.efficiency_at(return_water_temp_f=t_ret_rad)
        self.assertGreater(eff_rad, 0.925)
        self.assertLess(eff_rad, 0.940)

        # 4. Outdoor reset enabled:
        boiler_odr = ModulatingBoilerProducer(
            id="alta_odr",
            name="Alta ODR",
            nominal_btu=150000,
            has_outdoor_reset=True,
            primary_flow_gpm=12.0,
        )
        # At 32.5°F outdoor (midpoint of 0°F->65°F):
        # Baseboard supply range is 180°F -> 120°F -> midpoint is 150°F
        # T_return = 150 - (3.5/12)*20 = 144.17°F
        t_ret_odr = boiler_odr.estimate_return_temperature([z1_bb], outdoor_temp_f=32.5)
        self.assertAlmostEqual(t_ret_odr, 144.2, places=1)

    def test_heating_oil_tariffs_and_economics(self):
        from hvac_crossover.models.producers import AtmosphericBoilerProducer

        tariffs = TariffSchedule(
            elec_cost_per_kwh=0.25,
            gas_cost_per_therm=1.88,
            oil_cost_per_gal=3.80,
        )
        # Raw fuel input cost: $3.80 / 0.1385 = $27.4368/MMBtu
        self.assertAlmostEqual(tariffs.oil_cost_per_mmbtu_input, 27.44, places=1)

        oil_boiler = AtmosphericBoilerProducer(
            id="wtgo3",
            name="Weil-McLain WTGO",
            nominal_btu=115000,
            fuel_type=FuelType.OIL,
            rated_afue=0.85,
        )
        cost_mmbtu = oil_boiler.delivered_cost_per_mmbtu(outdoor_temp_f=30.0, tariffs=tariffs)
        # Delivered cost: $27.4368 / 0.85 = $32.28/MMBtu
        self.assertAlmostEqual(cost_mmbtu, 32.28, places=1)

        # Crossover COP*:
        # COP* = (138,500 / 3,412.14) * 0.85 * (0.25 / 3.80) = ~2.27
        cop_star = self.economics.compute_crossover_cop(tariffs, oil_boiler)
        self.assertAlmostEqual(cop_star, 2.27, places=1)


if __name__ == "__main__":
    unittest.main()
