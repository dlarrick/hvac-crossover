"""Unit tests for ArbitratorEngine constraint solving and safety locks."""

import unittest

from hvac_crossover.config import CoordinatorConfig
from hvac_crossover.engines.arbitrator import ArbitratorEngine
from hvac_crossover.models.delivery import HydronicEmitter, MiniSplitHead
from hvac_crossover.models.enums import HvacMode, SharedBoilerRole
from hvac_crossover.models.producers import InverterHeatPumpProducer, ModulatingBoilerProducer
from hvac_crossover.models.spaces import ComfortRange, Space, SpaceCondition
from hvac_crossover.models.topology import SystemTopology


class TestArbitratorEngine(unittest.TestCase):
    def setUp(self):
        self.config = CoordinatorConfig()
        self.arbitrator = ArbitratorEngine(self.config)

        self.odu1 = InverterHeatPumpProducer(id="odu1", name="ODU 1", nominal_btu=30000)
        self.boiler = ModulatingBoilerProducer(id="boiler", name="Boiler", nominal_btu=150000)

        # Space 1: Loft on ODU1
        self.loft = Space(
            id="loft",
            name="Loft",
            emitters=[MiniSplitHead(id="loft_head", producer_id="odu1")],
            priority=5,
        )

        # Space 2: First Floor has head on ODU1 AND baseboard on boiler
        self.first_floor = Space(
            id="first_floor",
            name="First Floor",
            emitters=[
                MiniSplitHead(id="f1_head", producer_id="odu1"),
                HydronicEmitter(id="f1_baseboard", producer_id="boiler", zone_id="z1"),
            ],
            priority=8,
        )

        self.topology = SystemTopology(
            producers=[self.odu1, self.boiler],
            spaces=[self.loft, self.first_floor],
        )

    def test_odu_mode_conflict_diverts_heating_to_boiler(self):
        # Loft requests COOL, First Floor requests HEAT on same ODU
        requested_modes = {"loft": HvacMode.COOL, "first_floor": HvacMode.HEAT}
        conditions = {
            "loft": SpaceCondition(current_temp=74.0, comfort=ComfortRange(cool_to=70.0)),
            "first_floor": SpaceCondition(current_temp=65.0, comfort=ComfortRange(heat_to=68.0)),
        }

        resolved_modes, explanations = self.arbitrator.arbitrate_odu_modes(
            requested_space_modes=requested_modes,
            topology=self.topology,
            space_conditions=conditions,
        )

        # Both remain active: Loft gets COOL on ODU1, First Floor gets diverted to boiler
        self.assertEqual(resolved_modes["loft"], HvacMode.COOL)
        self.assertEqual(resolved_modes["first_floor"], HvacMode.HEAT)
        self.assertTrue(any("diverted heating to hydronic boiler" in exp for exp in explanations))

    def test_odu_mode_conflict_with_emitters_mild_deficit_avoids_boiler(self):
        # First Floor at 67°F vs 68°F (deficit 1.0°F < 3.0°F)
        requested_modes = {"loft": HvacMode.COOL, "first_floor": HvacMode.HEAT}
        conditions = {
            "loft": SpaceCondition(current_temp=74.0, comfort=ComfortRange(cool_to=70.0)),
            "first_floor": SpaceCondition(current_temp=67.0, comfort=ComfortRange(heat_to=68.0)),
        }
        candidate_emitters = {"loft": ["loft_head"], "first_floor": ["f1_head"]}
        requested_boiler = set()

        modes, emitters, boiler_zones, exp = self.arbitrator.arbitrate_odu_modes(
            requested_space_modes=requested_modes,
            topology=self.topology,
            space_conditions=conditions,
            candidate_emitters=candidate_emitters,
            requested_boiler_zones=requested_boiler,
        )

        # Loft gets COOL; First Floor paused without boiler fallback
        self.assertEqual(modes["loft"], HvacMode.COOL)
        self.assertEqual(emitters["loft"], ["loft_head"])
        self.assertEqual(modes["first_floor"], HvacMode.OFF)
        self.assertEqual(emitters["first_floor"], [])
        self.assertEqual(len(boiler_zones), 0)

    def test_odu_mode_conflict_with_emitters_deep_recovery_diverts(self):
        # First Floor at 64°F vs 68°F (deficit 4.0°F >= 3.0°F)
        requested_modes = {"loft": HvacMode.COOL, "first_floor": HvacMode.HEAT}
        conditions = {
            "loft": SpaceCondition(current_temp=74.0, comfort=ComfortRange(cool_to=70.0)),
            "first_floor": SpaceCondition(current_temp=64.0, comfort=ComfortRange(heat_to=68.0)),
        }
        candidate_emitters = {"loft": ["loft_head"], "first_floor": ["f1_head"]}
        requested_boiler = set()

        modes, emitters, boiler_zones, exp = self.arbitrator.arbitrate_odu_modes(
            requested_space_modes=requested_modes,
            topology=self.topology,
            space_conditions=conditions,
            candidate_emitters=candidate_emitters,
            requested_boiler_zones=requested_boiler,
        )

        # Loft gets COOL; First Floor diverted to boiler
        self.assertEqual(modes["loft"], HvacMode.COOL)
        self.assertEqual(emitters["first_floor"], ["f1_baseboard"])
        self.assertIn(("boiler", "z1"), boiler_zones)

    def test_vrf_heat_recovery_relaxes_mode_lock(self):
        vrf_odu = InverterHeatPumpProducer(
            id="odu1", name="VRF ODU", nominal_btu=30000, supports_simultaneous_heat_cool=True
        )
        vrf_topology = SystemTopology(producers=[vrf_odu, self.boiler], spaces=[self.loft, self.first_floor])

        requested_modes = {"loft": HvacMode.COOL, "first_floor": HvacMode.HEAT}
        conditions = {
            "loft": SpaceCondition(current_temp=74.0, comfort=ComfortRange(cool_to=70.0)),
            "first_floor": SpaceCondition(current_temp=65.0, comfort=ComfortRange(heat_to=68.0)),
        }

        resolved_modes, explanations = self.arbitrator.arbitrate_odu_modes(
            requested_space_modes=requested_modes,
            topology=vrf_topology,
            space_conditions=conditions,
        )
        self.assertEqual(resolved_modes["loft"], HvacMode.COOL)
        self.assertEqual(resolved_modes["first_floor"], HvacMode.HEAT)
        # Zero conflict explanations generated
        self.assertEqual(len(explanations), 0)

    def test_shared_boiler_zone_master_slave_arbitration(self):
        # Primary BR (Master) and Guest Room (Slave) share Z2
        primary = Space(
            id="p_br",
            name="Primary BR",
            emitters=[HydronicEmitter(id="p_base", producer_id="boiler", zone_id="z2")],
            shared_boiler_role=SharedBoilerRole.MASTER,
        )
        guest = Space(
            id="g_rm",
            name="Guest Room",
            emitters=[HydronicEmitter(id="g_base", producer_id="boiler", zone_id="z2")],
            shared_boiler_role=SharedBoilerRole.SLAVE,
        )
        shared_topology = SystemTopology(producers=[self.boiler], spaces=[primary, guest])

        # Case 1: Guest is unoccupied and calls for heat, Master is satisfied -> Call cancelled
        requested_zones = {("boiler", "z2")}
        conditions = {
            "p_br": SpaceCondition(current_temp=68.0, comfort=ComfortRange(heat_to=68.0)),
            "g_rm": SpaceCondition(current_temp=60.0, comfort=ComfortRange(heat_to=65.0), occupied=False),
        }
        approved, exp = self.arbitrator.arbitrate_shared_boiler_zones(
            requested_boiler_zones=requested_zones,
            topology=shared_topology,
            space_conditions=conditions,
        )
        self.assertNotIn(("boiler", "z2"), approved)

        # Case 2: Master calls for heat -> Approved
        conditions_master_calls = {
            "p_br": SpaceCondition(current_temp=64.0, comfort=ComfortRange(heat_to=68.0)),
            "g_rm": SpaceCondition(current_temp=60.0, comfort=ComfortRange(heat_to=65.0), occupied=False),
        }
        approved2, exp2 = self.arbitrator.arbitrate_shared_boiler_zones(
            requested_boiler_zones=requested_zones,
            topology=shared_topology,
            space_conditions=conditions_master_calls,
        )
        self.assertIn(("boiler", "z2"), approved2)


if __name__ == "__main__":
    unittest.main()
