"""Main DualFuelCoordinator facade and central evaluation pipeline."""

from typing import Dict, Any, Optional, List, Set, Tuple
from .models.enums import HvacMode, HvacAction, SystemPhase, ProducerType
from .models.topology import SystemTopology
from .models.tariffs import TariffSchedule
from .models.snapshot import SystemSnapshot
from .models.spaces import SpaceCondition, ComfortRange
from .models.delivery import MiniSplitHead, HydronicEmitter
from .models.plan import DispatchPlan, SpaceDirective, ProducerDirective, Diagnostics
from .config import CoordinatorConfig
from .engines.economics import EconomicsEngine
from .engines.recovery import RecoveryEngine
from .engines.arbitrator import ArbitratorEngine


class DualFuelCoordinator:
    """
    Central coordination service and facade for dual-fuel HVAC systems.
    Evaluates ground-truth environmental and space conditions to produce
    actionable hardware directives and explainable telemetry.
    """

    def __init__(
        self,
        topology: SystemTopology,
        config: Optional[CoordinatorConfig] = None,
    ):
        self.topology = topology
        self.config = config or CoordinatorConfig()
        self.economics = EconomicsEngine()
        self.recovery = RecoveryEngine(self.config)
        self.arbitrator = ArbitratorEngine(self.config)

    def evaluate(self, snapshot: SystemSnapshot) -> DispatchPlan:
        """
        Evaluates the ground-truth snapshot and generates a DispatchPlan.
        """
        space_directives: Dict[str, SpaceDirective] = {}
        producer_directives: Dict[str, ProducerDirective] = {}
        explanations: List[str] = []

        # 1. Determine Macro System Phase
        system_phase = self.recovery.determine_system_phase(snapshot.spaces, self.topology)

        # 2. Find primary boiler for crossover calculations
        boiler_producers = [
            p for p in self.topology.producers
            if p.producer_type in (ProducerType.MODULATING_BOILER, ProducerType.ATMOSPHERIC_BOILER, ProducerType.STEAM_BOILER)
        ]
        primary_boiler = boiler_producers[0] if boiler_producers else None

        # Calculate general economic crossover temp for telemetry
        crossover_temp = 41.4  # Default fallback
        hp_producers = [p for p in self.topology.producers if p.producer_type == ProducerType.INVERTER_HEAT_PUMP]
        if primary_boiler and hp_producers:
            t_cross = self.economics.compute_crossover_temperature(
                tariffs=snapshot.tariffs,
                odu=hp_producers[0],
                boiler=primary_boiler,
                telemetry=snapshot.telemetry,
            )
            if t_cross is not None:
                crossover_temp = t_cross

        # 3. Initial Space Demand Assessment
        candidate_space_modes: Dict[str, HvacMode] = {}
        candidate_emitters: Dict[str, Optional[str]] = {}
        target_temps: Dict[str, Optional[float]] = {}
        requested_boiler_zones: Set[Tuple[str, str]] = set()

        space_costs: Dict[str, float] = {}
        space_cops: Dict[str, float] = {}

        for space in self.topology.spaces:
            cond = snapshot.spaces.get(space.id)
            if not cond:
                candidate_space_modes[space.id] = HvacMode.OFF
                candidate_emitters[space.id] = None
                target_temps[space.id] = None
                continue

            # Check window interlocks for passive ventilation
            if cond.window_open and self.config.passive_ventilation_override:
                candidate_space_modes[space.id] = HvacMode.OFF
                candidate_emitters[space.id] = None
                target_temps[space.id] = None
                explanations.append(f"Space '{space.name}' delivery units turned OFF due to open window/skylight.")
                continue

            demands_heat = False
            demands_cool = False
            if cond.comfort.demands_cool(cond.current_temp):
                demands_cool = True
            if cond.comfort.demands_heat(cond.current_temp):
                demands_heat = True
            if not demands_heat and not demands_cool:
                # Let emitters' own thermostats control cycling. Use a
                # reasonable mode depending on outdoor temperature.
                demands_cool = cond.comfort.demands_cool(snapshot.outdoor_temp)
                demands_heat = cond.comfort.demands_heat(snapshot.outdoor_temp)

            # Cooling Demand
            if demands_cool:
                candidate_space_modes[space.id] = HvacMode.COOL
                target_temps[space.id] = cond.comfort.cool_to
                cooling_heads = [e for e in space.emitters if isinstance(e, MiniSplitHead)]
                if cooling_heads:
                    candidate_emitters[space.id] = cooling_heads[0].id
                else:
                    candidate_emitters[space.id] = None
                continue

            # Heating Demand
            if demands_heat:
                candidate_space_modes[space.id] = HvacMode.HEAT
                target_temps[space.id] = cond.comfort.heat_to

                # Estimate space heating load
                load_btu = space.estimated_heat_loss_btu_per_hr(cond.current_temp, snapshot.outdoor_temp)

                # Check if space is in heavy setback recovery (>= 3.0°F deficit)
                in_recovery = self.recovery.is_space_in_recovery(cond)

                # Find available hydronic emitter
                hydronic_emitters = [e for e in space.emitters if isinstance(e, HydronicEmitter)]
                mini_split_heads = [e for e in space.emitters if isinstance(e, MiniSplitHead)]

                if in_recovery and hydronic_emitters and snapshot.outdoor_temp < 45.0:
                    # In deep recovery pull-up below 45°F, dispatch high-capacity boiler
                    selected = hydronic_emitters[0]
                    candidate_emitters[space.id] = selected.id
                    requested_boiler_zones.add((selected.producer_id, selected.zone_id))
                    explanations.append(
                        f"Space '{space.name}' in setback recovery (deficit {cond.comfort.heat_deficit(cond.current_temp):.1f}°F): "
                        f"dispatched high-capacity hydronic boiler for rapid pull-up."
                    )
                else:
                    # Steady-state plateau or mild weather: rank emitters economically
                    ranked = self.economics.rank_space_emitters_by_cost(
                        space=space,
                        outdoor_temp_f=snapshot.outdoor_temp,
                        tariffs=snapshot.tariffs,
                        topology=self.topology,
                        load_btu=load_btu,
                        telemetry=snapshot.telemetry,
                        part_load=True,
                    )
                    if ranked:
                        best_emitter, best_cost, best_eff = ranked[0]
                        candidate_emitters[space.id] = best_emitter.id
                        space_costs[space.id] = round(best_cost, 2)
                        space_cops[space.id] = round(best_eff, 2)

                        if isinstance(best_emitter, HydronicEmitter):
                            requested_boiler_zones.add((best_emitter.producer_id, best_emitter.zone_id))

        # 4. Multi-Split ODU Mode Conflict Arbitration
        if self.config.enforce_odu_mode_locks:
            resolved_modes, odu_explanations = self.arbitrator.arbitrate_odu_modes(
                requested_space_modes=candidate_space_modes,
                topology=self.topology,
                space_conditions=snapshot.spaces,
            )
            explanations.extend(odu_explanations)
        else:
            resolved_modes = candidate_space_modes

        # 5. Shared Hydronic Zone Valve Arbitration
        approved_boiler_zones, boiler_explanations = self.arbitrator.arbitrate_shared_boiler_zones(
            requested_boiler_zones=requested_boiler_zones,
            topology=self.topology,
            space_conditions=snapshot.spaces,
        )
        explanations.extend(boiler_explanations)

        # 6. Build Space Directives
        for space in self.topology.spaces:
            cond = snapshot.spaces.get(space.id)
            final_mode = resolved_modes.get(space.id, HvacMode.OFF)
            emitter_id = candidate_emitters.get(space.id)
            target = target_temps.get(space.id)

            if cond and cond.window_open and self.config.passive_ventilation_override:
                action = HvacAction.PASSIVE_VENTILATION
                final_mode = HvacMode.OFF
                emitter_id = None
            elif final_mode == HvacMode.HEAT:
                action = HvacAction.HEATING
            elif final_mode in (HvacMode.COOL, HvacMode.DRY):
                action = HvacAction.COOLING
            else:
                action = HvacAction.IDLE if cond and cond.comfort.is_in_deadband(cond.current_temp) else HvacAction.OFF

            active_producer_id = None
            if emitter_id:
                for e in space.emitters:
                    if e.id == emitter_id:
                        active_producer_id = e.producer_id
                        break

            space_directives[space.id] = SpaceDirective(
                space_id=space.id,
                action=action,
                hvac_mode=final_mode,
                target_temp=target,
                active_delivery_unit_id=emitter_id,
                active_producer_id=active_producer_id,
                explanation=f"Mode: {final_mode.value}, Action: {action.value}"
            )

        # 7. Build Producer Directives
        odu_operational_modes: Dict[str, str] = {}
        for producer in self.topology.producers:
            if producer.producer_type == ProducerType.INVERTER_HEAT_PUMP:
                # Find mode of active heads on this ODU
                active_modes = [
                    space_directives[s.id].hvac_mode
                    for s in self.topology.get_spaces_for_producer(producer.id)
                    if s.id in space_directives and space_directives[s.id].active_producer_id == producer.id
                ]
                active_modes = [m for m in active_modes if m != HvacMode.OFF]
                odu_mode = active_modes[0] if active_modes else HvacMode.OFF
                is_active = odu_mode != HvacMode.OFF
                odu_operational_modes[producer.id] = odu_mode.value

                producer_directives[producer.id] = ProducerDirective(
                    producer_id=producer.id,
                    operational_mode=odu_mode,
                    is_active=is_active,
                )
            else:
                # Boiler / Furnace producer
                active_zones = [
                    zone_id for b_id, zone_id in approved_boiler_zones
                    if b_id == producer.id
                ]
                is_active = len(active_zones) > 0
                producer_directives[producer.id] = ProducerDirective(
                    producer_id=producer.id,
                    operational_mode=HvacMode.HEAT if is_active else HvacMode.OFF,
                    is_active=is_active,
                    active_zones=active_zones,
                )

        # 8. Assemble Diagnostics
        diagnostics = Diagnostics(
            economic_crossover_temp=round(crossover_temp, 1),
            system_phase=system_phase,
            odu_modes=odu_operational_modes,
            space_costs_per_mmbtu=space_costs,
            space_cops=space_cops,
            explanations=explanations,
        )

        return DispatchPlan(
            space_directives=space_directives,
            producer_directives=producer_directives,
            diagnostics=diagnostics,
        )

    def evaluate_conditions(
        self,
        outdoor_temp: float,
        tariffs: TariffSchedule,
        spaces: Dict[str, Dict[str, Any]],
        solar_export_watts: float = 0.0,
        boiler_return_temp: Optional[float] = None,
        elec_rate_override: Optional[float] = None,
        current_hour: Optional[int] = None,
    ) -> DispatchPlan:
        """
        Convenience keyword-argument evaluation method that constructs a SystemSnapshot internally.
        """
        space_conditions: Dict[str, SpaceCondition] = {}
        for s_id, s_data in spaces.items():
            current_temp = float(s_data["current"])
            comfort = ComfortRange(
                heat_to=s_data.get("heat_to"),
                cool_to=s_data.get("cool_to"),
            )
            window_open = bool(s_data.get("window_open", False))
            occupied = bool(s_data.get("occupied", True))
            space_conditions[s_id] = SpaceCondition(
                current_temp=current_temp,
                comfort=comfort,
                window_open=window_open,
                occupied=occupied,
            )

        snapshot = SystemSnapshot(
            outdoor_temp=outdoor_temp,
            tariffs=tariffs,
            spaces=space_conditions,
            solar_export_watts=solar_export_watts,
            boiler_return_temp=boiler_return_temp,
            elec_rate_override=elec_rate_override,
            current_hour=current_hour,
        )
        return self.evaluate(snapshot)
