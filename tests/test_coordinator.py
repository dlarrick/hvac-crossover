"""End-to-end integration tests for DualFuelCoordinator and full Victorian home setup."""

import unittest
from hvac_crossover import (
    ProducerProfileFactory, Space, MiniSplitHead, HydronicEmitter,
    SystemTopology, TariffSchedule, DualFuelCoordinator, CoordinatorConfig,
    ThermalProfile, SharedBoilerRole, SystemPhase, HvacMode, HvacAction
)


class TestDualFuelCoordinator(unittest.TestCase):

    def setUp(self):
        # Setup Doug's exact Victorian hardware topology
        self.odu1 = ProducerProfileFactory.load_from_json(
            "hvac_crossover/profiles/producers/mitsubishi/mxz_3c30nahz2.json", "odu1"
        )
        self.odu2 = ProducerProfileFactory.load_from_json(
            "hvac_crossover/profiles/producers/mitsubishi/mxz_2d20nlhz.json", "odu2"
        )
        self.boiler = ProducerProfileFactory.load_from_json(
            "hvac_crossover/profiles/producers/burnham/alta_150.json", "alta150"
        )

        self.loft = Space(
            id="loft", name="3rd Floor Loft",
            emitters=[
                MiniSplitHead(id="loft_head", producer_id="odu1", nominal_btu=12000),
                HydronicEmitter(id="loft_baseboard", producer_id="alta150", zone_id="z3")
            ],
            floor_area_sqft=400, thermal_profile=ThermalProfile.HISTORIC_VICTORIAN
        )

        self.primary_br = Space(
            id="primary_br", name="Primary Bedroom",
            emitters=[
                MiniSplitHead(id="master_head", producer_id="odu1", nominal_btu=6000),
                HydronicEmitter(id="master_baseboard", producer_id="alta150", zone_id="z2")
            ],
            floor_area_sqft=250, priority=10, shared_boiler_role=SharedBoilerRole.MASTER
        )

        self.guest_room = Space(
            id="guest_room", name="Guest Room",
            emitters=[
                MiniSplitHead(id="guest_head", producer_id="odu2", nominal_btu=6000),
                HydronicEmitter(id="guest_baseboard", producer_id="alta150", zone_id="z2")
            ],
            floor_area_sqft=200, priority=5, shared_boiler_role=SharedBoilerRole.SLAVE
        )

        self.first_floor = Space(
            id="first_floor", name="First Floor",
            emitters=[
                MiniSplitHead(id="f1_primary", producer_id="odu1", nominal_btu=15000),
                MiniSplitHead(id="f1_cassette", producer_id="odu2", nominal_btu=12000),
                HydronicEmitter(id="f1_baseboard", producer_id="alta150", zone_id="z1")
            ],
            floor_area_sqft=900, priority=8
        )

        self.topology = SystemTopology(
            producers=[self.odu1, self.odu2, self.boiler],
            spaces=[self.loft, self.primary_br, self.guest_room, self.first_floor]
        )
        self.coordinator = DualFuelCoordinator(topology=self.topology)
        self.tariffs = TariffSchedule(elec_cost_per_kwh=0.25, gas_cost_per_therm=1.88)

    def test_cold_morning_recovery_dispatches_boiler(self):
        # 30°F outdoor, large thermal deficit (4.0°F - 6.0°F) across floors
        plan = self.coordinator.evaluate_conditions(
            outdoor_temp=30.0,
            tariffs=self.tariffs,
            spaces={
                "loft": {"current": 63.0, "heat_to": 68.0},
                "primary_br": {"current": 64.0, "heat_to": 68.0},
                "guest_room": {"current": 62.0, "heat_to": 60.0},
                "first_floor": {"current": 62.0, "heat_to": 68.0},
            }
        )
        self.assertEqual(plan.diagnostics.system_phase, SystemPhase.SETBACK_RECOVERY)
        # All calling zones engaged on the high-capacity Alta 150
        self.assertIn("z1", plan.producer_directives["alta150"].active_zones)
        self.assertIn("z2", plan.producer_directives["alta150"].active_zones)
        self.assertIn("z3", plan.producer_directives["alta150"].active_zones)
        self.assertEqual(plan.space_directives["primary_br"].active_delivery_unit_id, "master_baseboard")

    def test_warm_plateau_dispatches_heat_pumps(self):
        # 46°F outdoor (>41.4°F crossover), small deficit (<= 1.0°F)
        plan = self.coordinator.evaluate_conditions(
            outdoor_temp=46.0,
            tariffs=self.tariffs,
            spaces={
                "loft": {"current": 67.5, "heat_to": 68.0},
                "primary_br": {"current": 67.2, "heat_to": 68.0},
                "guest_room": {"current": 63.0, "heat_to": 60.0},
                "first_floor": {"current": 67.0, "heat_to": 68.0},
            }
        )
        self.assertEqual(plan.diagnostics.system_phase, SystemPhase.PLATEAU_MAINTENANCE)
        # Boiler rests
        self.assertEqual(len(plan.producer_directives["alta150"].active_zones), 0)
        # Primary bedroom uses mini-split head at high COP
        self.assertEqual(plan.space_directives["primary_br"].active_delivery_unit_id, "master_head")
        self.assertEqual(plan.producer_directives["odu1"].operational_mode, HvacMode.HEAT)

    def test_passive_ventilation_shuts_off_space(self):
        # Open skylight on Loft
        plan = self.coordinator.evaluate_conditions(
            outdoor_temp=58.0,
            tariffs=self.tariffs,
            spaces={
                "loft": {"current": 72.0, "heat_to": 68.0, "window_open": True},
                "primary_br": {"current": 68.0, "heat_to": 68.0},
                "guest_room": {"current": 65.0, "heat_to": 60.0},
                "first_floor": {"current": 68.0, "heat_to": 68.0},
            }
        )
        self.assertEqual(plan.space_directives["loft"].action, HvacAction.PASSIVE_VENTILATION)
        self.assertIsNone(plan.space_directives["loft"].active_delivery_unit_id)


if __name__ == "__main__":
    unittest.main()
