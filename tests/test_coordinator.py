"""End-to-end integration tests for DualFuelCoordinator and full Victorian home setup."""

import unittest

from hvac_crossover import (
    DualFuelCoordinator,
    HvacAction,
    HvacMode,
    HydronicEmitter,
    MiniSplitHead,
    ProducerProfileFactory,
    SharedBoilerRole,
    Space,
    SystemPhase,
    SystemTopology,
    TariffSchedule,
    ThermalProfile,
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
            id="loft",
            name="3rd Floor Loft",
            emitters=[
                MiniSplitHead(id="loft_head", producer_id="odu1", nominal_btu=12000),
                HydronicEmitter(id="loft_baseboard", producer_id="alta150", zone_id="z3"),
            ],
            floor_area_sqft=400,
            thermal_profile=ThermalProfile.HISTORIC_VICTORIAN,
        )

        self.primary_br = Space(
            id="primary_br",
            name="Primary Bedroom",
            emitters=[
                MiniSplitHead(id="master_head", producer_id="odu1", nominal_btu=6000),
                HydronicEmitter(id="master_baseboard", producer_id="alta150", zone_id="z2"),
            ],
            floor_area_sqft=250,
            priority=10,
            shared_boiler_role=SharedBoilerRole.MASTER,
        )

        self.guest_room = Space(
            id="guest_room",
            name="Guest Room",
            emitters=[
                MiniSplitHead(id="guest_head", producer_id="odu2", nominal_btu=6000),
                HydronicEmitter(id="guest_baseboard", producer_id="alta150", zone_id="z2"),
            ],
            floor_area_sqft=200,
            priority=5,
            shared_boiler_role=SharedBoilerRole.SLAVE,
        )

        self.first_floor = Space(
            id="first_floor",
            name="First Floor",
            emitters=[
                MiniSplitHead(id="f1_primary", producer_id="odu1", nominal_btu=15000),
                MiniSplitHead(id="f1_cassette", producer_id="odu2", nominal_btu=12000),
                HydronicEmitter(id="f1_baseboard", producer_id="alta150", zone_id="z1"),
            ],
            floor_area_sqft=900,
            priority=8,
        )

        self.topology = SystemTopology(
            producers=[self.odu1, self.odu2, self.boiler],
            spaces=[self.loft, self.primary_br, self.guest_room, self.first_floor],
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
            },
        )
        self.assertEqual(plan.diagnostics.system_phase, SystemPhase.SETBACK_RECOVERY)
        # All calling zones engaged on the high-capacity Alta 150
        self.assertIn("z1", plan.producer_directives["alta150"].active_zones)
        self.assertIn("z2", plan.producer_directives["alta150"].active_zones)
        self.assertIn("z3", plan.producer_directives["alta150"].active_zones)
        self.assertEqual(plan.space_directives["primary_br"].active_delivery_unit_ids, ["master_baseboard"])

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
            },
        )
        self.assertEqual(plan.diagnostics.system_phase, SystemPhase.PLATEAU_MAINTENANCE)
        # Boiler rests
        self.assertEqual(len(plan.producer_directives["alta150"].active_zones), 0)
        # Primary bedroom uses mini-split head at high COP
        self.assertEqual(plan.space_directives["primary_br"].active_delivery_unit_ids, ["master_head"])
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
            },
        )
        self.assertEqual(plan.space_directives["loft"].action, HvacAction.PASSIVE_VENTILATION)
        self.assertEqual([], plan.space_directives["loft"].active_delivery_unit_ids)

    def test_odu2_conflict_first_floor_mild_heat_and_guest_room_cool(self):
        """
        Doug's real-life scenario:
        - First Floor calls for heat (67°F vs 68°F, deficit 1.0°F < 3.0°F recovery threshold).
        - Guest Room calls for cool (72°F vs 70°F, cool deficit 2.0°F).
        - ODU2 must NOT conflict. First Floor f1_cassette yields on ODU2.
        - First Floor continues heating via f1_primary on ODU1.
        - Boiler is NOT fired for this mild plateau deficit!
        """
        plan = self.coordinator.evaluate_conditions(
            outdoor_temp=55.0,
            tariffs=self.tariffs,
            spaces={
                "loft": {"current": 68.0, "heat_to": 68.0},
                "primary_br": {"current": 68.0, "heat_to": 68.0},
                "guest_room": {"current": 72.0, "cool_to": 70.0},
                "first_floor": {"current": 67.0, "heat_to": 68.0},
            },
        )
        # ODU2 must run COOL for Guest Room
        self.assertEqual(plan.producer_directives["odu2"].operational_mode, HvacMode.COOL)
        self.assertEqual(plan.space_directives["guest_room"].hvac_mode, HvacMode.COOL)
        self.assertEqual(plan.space_directives["guest_room"].active_delivery_unit_ids, ["guest_head"])

        # First Floor keeps heating via ODU1 f1_primary; f1_cassette on ODU2 is turned OFF
        self.assertEqual(plan.space_directives["first_floor"].hvac_mode, HvacMode.HEAT)
        self.assertEqual(plan.space_directives["first_floor"].active_delivery_unit_ids, ["f1_primary"])
        self.assertEqual(plan.producer_directives["odu1"].operational_mode, HvacMode.HEAT)

        # Crucial: Boiler is NOT engaged for a mild 1.0°F deficit
        self.assertEqual(len(plan.producer_directives["alta150"].active_zones), 0)

    def test_odu2_conflict_deep_recovery_diverts_to_boiler(self):
        """
        When First Floor is in heavy setback recovery (>= 3.0°F deficit) and conflicts
        with Guest Room cooling on ODU2, it DOES fall back to the boiler.
        """
        plan = self.coordinator.evaluate_conditions(
            outdoor_temp=50.0,
            tariffs=self.tariffs,
            spaces={
                "loft": {"current": 68.0, "heat_to": 68.0},
                "primary_br": {"current": 68.0, "heat_to": 68.0},
                "guest_room": {"current": 72.0, "cool_to": 70.0},
                "first_floor": {"current": 64.0, "heat_to": 68.0},  # 4.0°F deficit >= 3.0°F
            },
        )
        # Guest room gets cooling on ODU2
        self.assertEqual(plan.producer_directives["odu2"].operational_mode, HvacMode.COOL)
        self.assertEqual(plan.space_directives["guest_room"].active_delivery_unit_ids, ["guest_head"])

        # First Floor diverted to boiler for rapid recovery
        self.assertIn("f1_baseboard", plan.space_directives["first_floor"].active_delivery_unit_ids)
        self.assertNotIn("f1_cassette", plan.space_directives["first_floor"].active_delivery_unit_ids)
        self.assertIn("z1", plan.producer_directives["alta150"].active_zones)

    def test_odu_conflict_deadband_float_yields_to_active_cooling(self):
        """
        When First Floor is in deadband float (comfort satisfied) and Guest Room
        has active cooling demand, First Floor yields on ODU2 without boiler fallback.
        """
        plan = self.coordinator.evaluate_conditions(
            outdoor_temp=55.0,
            tariffs=self.tariffs,
            spaces={
                "loft": {"current": 68.0, "heat_to": 68.0},
                "primary_br": {"current": 68.0, "heat_to": 68.0},
                "guest_room": {"current": 73.0, "cool_to": 70.0},
                "first_floor": {"current": 70.0, "heat_to": 68.0, "cool_to": 74.0},  # deadband
            },
        )
        self.assertEqual(plan.producer_directives["odu2"].operational_mode, HvacMode.COOL)
        self.assertNotIn("f1_cassette", plan.space_directives["first_floor"].active_delivery_unit_ids)
        self.assertEqual(len(plan.producer_directives["alta150"].active_zones), 0)

    def test_odu_conflict_single_emitter_mild_heat_pauses_without_boiler(self):
        """
        When Guest Room (single mini-split head) has a mild heat deficit (67°F vs 68°F)
        and First Floor has an active cool demand, Guest Room pauses heating without
        firing the boiler.
        """
        plan = self.coordinator.evaluate_conditions(
            outdoor_temp=68.0,
            tariffs=self.tariffs,
            spaces={
                "loft": {"current": 70.0, "heat_to": 68.0},
                "primary_br": {"current": 70.0, "heat_to": 68.0},
                "guest_room": {"current": 67.0, "heat_to": 68.0},  # 1.0°F deficit, single head
                "first_floor": {"current": 74.0, "cool_to": 71.0},  # active cool
            },
        )
        self.assertEqual(plan.producer_directives["odu2"].operational_mode, HvacMode.COOL)
        self.assertEqual(plan.space_directives["guest_room"].active_delivery_unit_ids, [])
        self.assertEqual(plan.space_directives["guest_room"].hvac_mode, HvacMode.OFF)
        self.assertEqual(len(plan.producer_directives["alta150"].active_zones), 0)

    def test_heat_pump_low_ambient_lockout_with_hydronic_fallback(self):
        """
        When outdoor temp is below the heat pump operational envelope + safety band,
        the heat pump is locked out and heating falls back to hydronics.
        """
        haier = ProducerProfileFactory.load_from_json("haier/3u24ms2vhb.json", "odu_haier")
        boiler = ProducerProfileFactory.load_from_json("bradford_white/bwmfthw140p.json", "bw_boiler")
        condo = Space(
            id="condo",
            name="Condo",
            emitters=[
                MiniSplitHead(id="lr_head", producer_id="odu_haier", nominal_btu=12000),
                HydronicEmitter(id="baseboard", producer_id="bw_boiler", zone_id="z1"),
            ],
            floor_area_sqft=1000,
        )
        topo = SystemTopology(producers=[haier, boiler], spaces=[condo])
        coord = DualFuelCoordinator(topo)

        # Haier envelope: -4°F, safety band: 5°F -> cutoff is 1°F
        # At 0°F (below 1°F cutoff), heat pump is locked out even though COP is theoretically positive
        plan = coord.evaluate_conditions(
            outdoor_temp=0.0,
            tariffs=TariffSchedule(elec_cost_per_kwh=0.226, gas_cost_per_therm=1.88, propane_cost_per_gal=3.691),
            spaces={"condo": {"current": 66.0, "heat_to": 68.0}},
        )
        self.assertEqual(plan.space_directives["condo"].active_delivery_unit_ids, ["baseboard"])
        self.assertEqual(plan.producer_directives["odu_haier"].operational_mode, HvacMode.OFF)
        self.assertFalse(plan.producer_directives["odu_haier"].is_active)
        self.assertTrue(plan.producer_directives["bw_boiler"].is_active)
        self.assertIn("z1", plan.producer_directives["bw_boiler"].active_zones)
        self.assertTrue(any("locked out" in exp for exp in plan.diagnostics.explanations))

    def test_heat_pump_low_ambient_lockout_only_heat_pump(self):
        """
        When outdoor temp is below envelope + safety band and space has only heat pump,
        heating is safely disabled.
        """
        haier = ProducerProfileFactory.load_from_json("haier/3u24ms2vhb.json", "odu_haier")
        space = Space(
            id="cabin",
            name="Cabin",
            emitters=[
                MiniSplitHead(id="cabin_head", producer_id="odu_haier", nominal_btu=12000),
            ],
            floor_area_sqft=500,
        )
        topo = SystemTopology(producers=[haier], spaces=[space])
        coord = DualFuelCoordinator(topo)

        plan = coord.evaluate_conditions(
            outdoor_temp=-2.0,
            tariffs=TariffSchedule(elec_cost_per_kwh=0.226, gas_cost_per_therm=1.88, propane_cost_per_gal=3.691),
            spaces={"cabin": {"current": 60.0, "heat_to": 68.0}},
        )
        self.assertEqual(plan.space_directives["cabin"].hvac_mode, HvacMode.OFF)
        self.assertEqual(plan.space_directives["cabin"].active_delivery_unit_ids, [])
        self.assertFalse(plan.producer_directives["odu_haier"].is_active)


if __name__ == "__main__":
    unittest.main()
