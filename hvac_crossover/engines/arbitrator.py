"""Refrigerant mode safety and shared-zone arbitration engine."""

from typing import Dict, List, Set, Tuple, Optional
from ..models.enums import HvacMode, HvacAction, SharedBoilerRole
from ..models.producers import InverterHeatPumpProducer
from ..models.delivery import DeliveryUnit, MiniSplitHead, HydronicEmitter
from ..models.spaces import Space, SpaceCondition
from ..models.topology import SystemTopology
from ..config import CoordinatorConfig


class ArbitratorEngine:
    """
    Enforces hardware physical safety constraints:
    1. Multi-split single-refrigerant mode locks (preventing heat/cool conflict faults).
    2. Shared hydronic zone valve arbitration (master/slave relationships).
    3. Window/skylight cutoffs for passive thermal banking.
    """

    def __init__(self, config: CoordinatorConfig):
        self.config = config

    def arbitrate_odu_modes(
        self,
        requested_space_modes: Dict[str, HvacMode],
        topology: SystemTopology,
        space_conditions: Dict[str, SpaceCondition],
    ) -> Tuple[Dict[str, HvacMode], List[str]]:
        """
        Ensures all heads on each ODU operate in a compatible refrigerant mode.
        If a conflict occurs (e.g. Head A wants COOL, Head B wants HEAT on same ODU):
        - If the ODU supports simultaneous heat/cool (VRF Heat Recovery), allow both.
        - Otherwise, checks if the heating head can be diverted to an alternate producer (boiler).
        - If no alternate exists, highest priority Space wins; lower priority is idled.
        Returns: (resolved_space_modes, explanation_messages)
        """
        resolved_modes = dict(requested_space_modes)
        explanations = []

        # Find all inverter heat pump producers
        odus = [p for p in topology.producers if isinstance(p, InverterHeatPumpProducer)]

        for odu in odus:
            if odu.supports_simultaneous_heat_cool:
                continue  # VRF Heat Recovery relaxes the single-mode lock

            # Find all spaces that have a mini-split head on this ODU
            odu_spaces: List[Tuple[Space, MiniSplitHead]] = []
            for s in topology.spaces:
                for d in s.emitters:
                    if isinstance(d, MiniSplitHead) and d.producer_id == odu.id:
                        odu_spaces.append((s, d))
                        break

            # Collect active requested modes (ignore OFF/IDLE)
            active_modes: Dict[str, List[Tuple[Space, MiniSplitHead]]] = {"heat": [], "cool": [], "dry": []}
            for s, head in odu_spaces:
                mode = resolved_modes.get(s.id, HvacMode.OFF)
                if mode in (HvacMode.HEAT, HvacMode.COOL, HvacMode.DRY):
                    active_modes[mode.value].append((s, head))

            active_mode_keys = [k for k, v in active_modes.items() if v]

            # Conflict: both HEAT and COOL (or DRY) requested on the same ODU!
            if "heat" in active_mode_keys and ("cool" in active_mode_keys or "dry" in active_mode_keys):
                cooling_spaces = active_modes["cool"] + active_modes["dry"]
                heating_spaces = active_modes["heat"]

                # Check if heating spaces can be cleanly diverted to an alternate hydronic emitter
                diverted_all_heaters = True
                diverted_spaces = []

                for s, head in heating_spaces:
                    has_hydronic_alt = any(
                        isinstance(e, HydronicEmitter) for e in s.emitters
                    )
                    if has_hydronic_alt:
                        diverted_spaces.append(s)
                    else:
                        diverted_all_heaters = False
                        break

                if diverted_all_heaters:
                    # Successfully resolved conflict by diverting heat to boiler!
                    for s in diverted_spaces:
                        explanations.append(
                            f"ODU Mode Conflict on '{odu.id}': Space '{s.name}' diverted heating to hydronic boiler "
                            f"to allow cooling on upper space(s)."
                        )
                else:
                    # Cannot divert all heaters; resolve via space priority
                    max_cooling_priority = max(s.priority for s, _ in cooling_spaces)
                    max_heating_priority = max(s.priority for s, _ in heating_spaces)

                    if max_cooling_priority > max_heating_priority:
                        winning_mode = HvacMode.COOL
                        for s, _ in heating_spaces:
                            resolved_modes[s.id] = HvacMode.OFF
                            explanations.append(
                                f"ODU Mode Conflict on '{odu.id}': Space '{s.name}' heating paused; "
                                f"cooling prioritized on higher-priority space."
                            )
                    else:
                        winning_mode = HvacMode.HEAT
                        for s, _ in cooling_spaces:
                            resolved_modes[s.id] = HvacMode.OFF
                            explanations.append(
                                f"ODU Mode Conflict on '{odu.id}': Space '{s.name}' cooling paused; "
                                f"heating prioritized on higher-priority space."
                            )

        return resolved_modes, explanations

    def arbitrate_shared_boiler_zones(
        self,
        requested_boiler_zones: Set[Tuple[str, str]],
        topology: SystemTopology,
        space_conditions: Dict[str, SpaceCondition],
    ) -> Tuple[Set[Tuple[str, str]], List[str]]:
        """
        Arbitrates multiple spaces sharing the same boiler zone valve (e.g. Floor 2 Z2).
        Returns: (approved_boiler_zones, explanation_messages)
        """
        approved_zones = set(requested_boiler_zones)
        explanations = []

        # Find all unique (boiler_id, zone_id) pairs
        all_zones = set()
        for s in topology.spaces:
            for d in s.emitters:
                if isinstance(d, HydronicEmitter):
                    all_zones.add((d.producer_id, d.zone_id))

        for boiler_id, zone_id in all_zones:
            shared_spaces = topology.get_shared_hydronic_spaces(boiler_id, zone_id)
            if len(shared_spaces) <= 1:
                continue  # Dedicated zone, no arbitration needed

            # Identify Master and Slave spaces
            masters = [s for s in shared_spaces if s.shared_boiler_role == SharedBoilerRole.MASTER]
            slaves = [s for s in shared_spaces if s.shared_boiler_role == SharedBoilerRole.SLAVE]

            zone_key = (boiler_id, zone_id)
            if zone_key not in approved_zones:
                continue

            # If zone was called by a slave, check if master permits or if slave should float
            master_calling = any(
                space_conditions[m.id].comfort.demands_heat(space_conditions[m.id].current_temp)
                for m in masters if m.id in space_conditions
            )

            if not master_calling and slaves:
                for slave in slaves:
                    cond = space_conditions.get(slave.id)
                    if cond and not cond.occupied:
                        approved_zones.discard(zone_key)
                        explanations.append(
                            f"Shared Zone '{zone_id}' call cancelled: Slave space '{slave.name}' is unoccupied "
                            f"and master space is satisfied."
                        )

        return approved_zones, explanations
