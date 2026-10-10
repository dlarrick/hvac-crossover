"""Unit tests for EconomicsEngine, tariffs, and boiler efficiency curves."""

import unittest
from hvac_crossover.models.tariffs import TariffSchedule
from hvac_crossover.models.enums import NetMeteringMode
from hvac_crossover.models.producers import ProducerProfileFactory, ModulatingBoilerProducer
from hvac_crossover.engines.economics import EconomicsEngine


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
            "hvac_crossover/profiles/producers/mitsubishi/mxz_3c30nahz2.json", "odu1"
        )
        self.boiler = ProducerProfileFactory.load_from_json(
            "hvac_crossover/profiles/producers/burnham/alta_150.json", "alta150"
        )

    def test_crossover_cop_derivation(self):
        # Rated AFUE 95% at $0.25/kWh and $1.88/therm -> COP* = 3.70
        cop_star = self.economics.compute_crossover_cop(self.tariffs, self.boiler)
        self.assertAlmostEqual(cop_star, 3.70, places=2)

    def test_crossover_temperature_odu1(self):
        # Crossover temp for MXZ-3C30 should be ~41.4°F
        t_cross = self.economics.compute_crossover_temperature(self.tariffs, self.odu1, self.boiler)
        self.assertIsNotNone(t_cross)
        self.assertAlmostEqual(t_cross, 41.4, delta=0.5)

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


if __name__ == "__main__":
    unittest.main()
