"""Unit tests for RecoveryEngine and thermal mass calculations."""

import unittest

from hvac_crossover.config import CoordinatorConfig
from hvac_crossover.engines.recovery import RecoveryEngine
from hvac_crossover.models.enums import SystemPhase, ThermalProfile
from hvac_crossover.models.spaces import ComfortRange, Space, SpaceCondition
from hvac_crossover.models.topology import SystemTopology


class TestRecoveryEngine(unittest.TestCase):
    def setUp(self):
        self.config = CoordinatorConfig(
            recovery_delta_threshold_f=3.0,
            plateau_delta_threshold_f=1.0,
        )
        self.recovery = RecoveryEngine(self.config)
        self.space = Space(
            id="primary_br",
            name="Primary Bedroom",
            floor_area_sqft=250.0,
            thermal_profile=ThermalProfile.HISTORIC_VICTORIAN,
        )

    def test_recovery_vs_plateau_detection(self):
        comfort = ComfortRange(heat_to=68.0, cool_to=72.0)

        # 64.0°F -> 4.0°F deficit -> Recovery
        cond_recovery = SpaceCondition(current_temp=64.0, comfort=comfort)
        self.assertTrue(self.recovery.is_space_in_recovery(cond_recovery))
        self.assertFalse(self.recovery.is_space_at_plateau(cond_recovery))

        # 67.2°F -> 0.8°F deficit -> Plateau
        cond_plateau = SpaceCondition(current_temp=67.2, comfort=comfort)
        self.assertFalse(self.recovery.is_space_in_recovery(cond_plateau))
        self.assertTrue(self.recovery.is_space_at_plateau(cond_plateau))

    def test_thermal_energy_recovery_calculation(self):
        # 5°F deficit in Victorian plaster room
        comfort = ComfortRange(heat_to=68.0)
        cond = SpaceCondition(current_temp=63.0, comfort=comfort)
        btu_req = self.recovery.calculate_recovery_energy_btu(
            space=self.space,
            condition=cond,
            outdoor_temp_f=30.0,
            target_hours=1.0,
        )
        # Victorian 250 sqft with 9ft ceilings has significant thermal mass soak
        self.assertGreater(btu_req, 8000.0)

    def test_system_phase_determination(self):
        comfort = ComfortRange(heat_to=68.0)
        topology = SystemTopology(spaces=[self.space])

        # Active recovery in occupied space
        spaces_recovery = {"primary_br": SpaceCondition(current_temp=63.0, comfort=comfort, occupied=True)}
        phase1 = self.recovery.determine_system_phase(spaces_recovery, topology)
        self.assertEqual(phase1, SystemPhase.SETBACK_RECOVERY)

        # Plateau maintenance
        spaces_plateau = {"primary_br": SpaceCondition(current_temp=67.5, comfort=comfort, occupied=True)}
        phase2 = self.recovery.determine_system_phase(spaces_plateau, topology)
        self.assertEqual(phase2, SystemPhase.PLATEAU_MAINTENANCE)

        # Passive ventilation
        spaces_vent = {"primary_br": SpaceCondition(current_temp=68.0, comfort=comfort, window_open=True)}
        phase3 = self.recovery.determine_system_phase(spaces_vent, topology)
        self.assertEqual(phase3, SystemPhase.PASSIVE_VENTILATION)

    def test_envelope_score_and_thermal_mass_scaling(self):
        s_uninsulated = Space(
            id="s0", name="S0", floor_area_sqft=100.0, ceiling_height_ft=10.0, envelope_score=0, thermal_mass=1
        )
        s_mid = Space(
            id="s5", name="S5", floor_area_sqft=100.0, ceiling_height_ft=10.0, envelope_score=5, thermal_mass=3
        )
        s_passive = Space(
            id="s10", name="S10", floor_area_sqft=100.0, ceiling_height_ft=10.0, envelope_score=10, thermal_mass=5
        )

        self.assertAlmostEqual(s_uninsulated.ua_factor, 85.0, places=2)
        self.assertAlmostEqual(s_mid.ua_factor, 50.0, places=2)
        self.assertAlmostEqual(s_passive.ua_factor, 15.0, places=2)

        air_mass = 1000.0 * 0.018  # 18.0 BTU/°F
        self.assertAlmostEqual(s_uninsulated.thermal_capacitance_btu_per_f, air_mass * 6.0, places=2)
        self.assertAlmostEqual(s_mid.thermal_capacitance_btu_per_f, air_mass * 14.0, places=2)
        self.assertAlmostEqual(s_passive.thermal_capacitance_btu_per_f, air_mass * 24.0, places=2)


if __name__ == "__main__":
    unittest.main()
