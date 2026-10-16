"""Refrigerant mode safety and shared-zone arbitration engine."""

from ..config import CoordinatorConfig
from ..models.delivery import HydronicEmitter, MiniSplitHead
from ..models.enums import HvacMode, SharedBoilerRole
from ..models.producers import InverterHeatPumpProducer
from ..models.spaces import Space, SpaceCondition
from ..models.topology import SystemTopology


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
        requested_space_modes: dict[str, HvacMode],
        topology: SystemTopology,
        space_conditions: dict[str, SpaceCondition],
        candidate_emitters: dict[str, list[str]] | None = None,
        requested_boiler_zones: set[tuple[str, str]] | None = None,
    ):
        """
        Ensures all heads on each ODU operate in a compatible refrigerant mode.
        If a conflict occurs (e.g. Head A wants COOL, Head B wants HEAT on same ODU):
        - If the ODU supports simultaneous heat/cool (VRF Heat Recovery), allow both.
        - If one side is only in deadband float while the other has active demand, deadband yields.
        - If heating spaces have alternative emitters (other ODUs), conflicting head yields.
        - If heating space has mild deficit (< recovery threshold), avoid boiler fallback; head yields.
        - If heating space is in recovery (>= recovery threshold) with hydronic fallback, divert to boiler.
        - If no alternate exists and in recovery, highest priority Space wins.
        Returns:
            If candidate_emitters is None: (resolved_modes, explanations)
            Else: (resolved_modes, resolved_emitters, resolved_boiler_zones, explanations)
        """
        resolved_modes = dict(requested_space_modes)
        resolved_emitters = (
            {k: list(v) for k, v in candidate_emitters.items()} if candidate_emitters is not None else None
        )
        resolved_boiler_zones = set(requested_boiler_zones) if requested_boiler_zones is not None else set()
        explanations = []

        odus = [p for p in topology.producers if isinstance(p, InverterHeatPumpProducer)]

        for odu in odus:
            if odu.supports_simultaneous_heat_cool:
                continue  # VRF Heat Recovery relaxes the single-mode lock

            # Collect active requested heads on this ODU
            heating_heads: list[tuple[Space, MiniSplitHead]] = []
            cooling_heads: list[tuple[Space, MiniSplitHead]] = []

            for s in topology.spaces:
                mode = resolved_modes.get(s.id, HvacMode.OFF)
                if mode == HvacMode.OFF:
                    continue

                for d in s.emitters:
                    if not (isinstance(d, MiniSplitHead) and d.producer_id == odu.id):
                        continue

                    # If candidate_emitters was provided, only consider heads currently candidate
                    if resolved_emitters is not None and d.id not in resolved_emitters.get(s.id, []):
                        continue

                    if mode == HvacMode.HEAT:
                        heating_heads.append((s, d))
                    elif mode in (HvacMode.COOL, HvacMode.DRY):
                        cooling_heads.append((s, d))

            if not heating_heads or not cooling_heads:
                continue  # No conflict on this ODU

            # Conflict detected!
            def is_in_deadband(sp: Space) -> bool:
                c = space_conditions.get(sp.id)
                return c.comfort.is_in_deadband(c.current_temp) if c else False

            def get_heat_deficit(sp: Space) -> float:
                c = space_conditions.get(sp.id)
                return c.comfort.heat_deficit(c.current_temp) if c else 0.0

            all_heaters_in_deadband = all(is_in_deadband(s) for s, _ in heating_heads)
            any_cooler_active = any(not is_in_deadband(s) for s, _ in cooling_heads)

            all_coolers_in_deadband = all(is_in_deadband(s) for s, _ in cooling_heads)
            any_heater_active = any(not is_in_deadband(s) for s, _ in heating_heads)

            if all_heaters_in_deadband and any_cooler_active:
                # Cooling wins ODU; heating heads yield because they are in deadband float
                for s, head in heating_heads:
                    if resolved_emitters is not None:
                        if head.id in resolved_emitters.get(s.id, []):
                            resolved_emitters[s.id].remove(head.id)
                        if not resolved_emitters[s.id]:
                            resolved_modes[s.id] = HvacMode.OFF
                    else:
                        resolved_modes[s.id] = HvacMode.OFF
                    explanations.append(
                        f"ODU Mode Conflict on '{odu.id}': Head '{head.id}' in Space '{s.name}' turned OFF "
                        f"(space in deadband float) to allow cooling on active space(s)."
                    )
                continue

            if all_coolers_in_deadband and any_heater_active:
                # Heating wins ODU; cooling heads yield because they are in deadband float
                for s, head in cooling_heads:
                    if resolved_emitters is not None:
                        if head.id in resolved_emitters.get(s.id, []):
                            resolved_emitters[s.id].remove(head.id)
                        if not resolved_emitters[s.id]:
                            resolved_modes[s.id] = HvacMode.OFF
                    else:
                        resolved_modes[s.id] = HvacMode.OFF
                    explanations.append(
                        f"ODU Mode Conflict on '{odu.id}': Head '{head.id}' in Space '{s.name}' turned OFF "
                        f"(space in deadband float) to allow heating on active space(s)."
                    )
                continue

            # Both sides have active demands or similar float states.
            remaining_heaters = []
            threshold_f = getattr(self.config, "boiler_conflict_recovery_threshold_f", 2.0)
            for s, head in heating_heads:
                deficit = get_heat_deficit(s)
                in_recovery = deficit >= threshold_f

                # Check if space has another active emitter on another producer (e.g. ODU1)
                has_other_active_emitter = False
                if resolved_emitters is not None:
                    has_other_active_emitter = any(e_id != head.id for e_id in resolved_emitters.get(s.id, []))

                hydronic_emitters = [e for e in s.emitters if isinstance(e, HydronicEmitter)]

                if in_recovery and hydronic_emitters:
                    # Space is in deep recovery -> divert to boiler!
                    h_emitter = hydronic_emitters[0]
                    if resolved_emitters is not None:
                        # Remove mini-split heads for this space from candidate emitters
                        resolved_emitters[s.id] = [
                            e_id
                            for e_id in resolved_emitters.get(s.id, [])
                            if not any(isinstance(e, MiniSplitHead) and e.id == e_id for e in s.emitters)
                        ]
                        if h_emitter.id not in resolved_emitters[s.id]:
                            resolved_emitters[s.id].append(h_emitter.id)
                    resolved_boiler_zones.add((h_emitter.producer_id, h_emitter.zone_id))
                    resolved_modes[s.id] = HvacMode.HEAT
                    explanations.append(
                        f"ODU Mode Conflict on '{odu.id}': Space '{s.name}' in setback recovery "
                        f"(deficit {deficit:.1f}°F >= {threshold_f:.1f}°F) "
                        "diverted heating to hydronic boiler to allow cooling on other space(s)."
                    )
                elif has_other_active_emitter:
                    # Space still has heating coverage from another producer -> yield this head
                    if resolved_emitters is not None:
                        if head.id in resolved_emitters.get(s.id, []):
                            resolved_emitters[s.id].remove(head.id)
                    explanations.append(
                        f"ODU Mode Conflict on '{odu.id}': Head '{head.id}' in Space '{s.name}' "
                        "paused to allow cooling; "
                        f"Space '{s.name}' maintains heating via other delivery unit(s)."
                    )
                elif not in_recovery:
                    # Small deficit (< threshold) -> avoid boiler fallback, head yields
                    if resolved_emitters is not None:
                        if head.id in resolved_emitters.get(s.id, []):
                            resolved_emitters[s.id].remove(head.id)
                        if not resolved_emitters[s.id]:
                            resolved_modes[s.id] = HvacMode.OFF
                    else:
                        resolved_modes[s.id] = HvacMode.OFF
                    explanations.append(
                        f"ODU Mode Conflict on '{odu.id}': Space '{s.name}' heating paused on '{head.id}' "
                        f"(deficit {deficit:.1f}°F < {threshold_f:.1f}°F); cooling prioritized without boiler fallback."
                    )
                else:
                    # In recovery but NO hydronic alternative exists
                    remaining_heaters.append((s, head))

            if remaining_heaters:
                max_cooling_priority = max(s.priority for s, _ in cooling_heads)
                max_heating_priority = max(s.priority for s, _ in remaining_heaters)

                if max_cooling_priority > max_heating_priority:
                    for s, head in remaining_heaters:
                        if resolved_emitters is not None:
                            if head.id in resolved_emitters.get(s.id, []):
                                resolved_emitters[s.id].remove(head.id)
                            if not resolved_emitters[s.id]:
                                resolved_modes[s.id] = HvacMode.OFF
                        else:
                            resolved_modes[s.id] = HvacMode.OFF
                        explanations.append(
                            f"ODU Mode Conflict on '{odu.id}': Space '{s.name}' heating paused; "
                            f"cooling prioritized on higher-priority space."
                        )
                else:
                    for s, head in cooling_heads:
                        if resolved_emitters is not None:
                            if head.id in resolved_emitters.get(s.id, []):
                                resolved_emitters[s.id].remove(head.id)
                            if not resolved_emitters[s.id]:
                                resolved_modes[s.id] = HvacMode.OFF
                        else:
                            resolved_modes[s.id] = HvacMode.OFF
                        explanations.append(
                            f"ODU Mode Conflict on '{odu.id}': Space '{s.name}' cooling paused; "
                            f"heating prioritized on higher-priority space."
                        )

        if candidate_emitters is None and requested_boiler_zones is None:
            return resolved_modes, explanations

        return resolved_modes, resolved_emitters, resolved_boiler_zones, explanations

    def arbitrate_shared_boiler_zones(
        self,
        requested_boiler_zones: set[tuple[str, str]],
        topology: SystemTopology,
        space_conditions: dict[str, SpaceCondition],
    ) -> tuple[set[tuple[str, str]], list[str]]:
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
                for m in masters
                if m.id in space_conditions
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
