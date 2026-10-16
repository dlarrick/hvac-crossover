"""Dispatch plan and diagnostic outputs produced by the coordinator evaluation."""

from dataclasses import dataclass, field

from .enums import HvacAction, HvacMode, SystemPhase


@dataclass
class SpaceDirective:
    """Actionable directive for a single space (room)."""

    space_id: str
    action: HvacAction
    hvac_mode: HvacMode
    target_temp: float | None
    active_delivery_unit_ids: list[str] = field(default_factory=list)
    active_producer_id: str | None = None
    explanation: str = ""


@dataclass
class ProducerDirective:
    """Actionable directive for an energy producer (ODU, Boiler, Furnace)."""

    producer_id: str
    operational_mode: HvacMode
    is_active: bool
    active_zones: list[str] = field(default_factory=list)
    estimated_load_btu: float = 0.0


@dataclass
class Diagnostics:
    """Rich telemetry and explanations for user dashboards and energy reporting."""

    economic_crossover_temp: float
    system_phase: SystemPhase
    odu_modes: dict[str, str] = field(default_factory=dict)
    space_costs_per_mmbtu: dict[str, float] = field(default_factory=dict)
    space_cops: dict[str, float] = field(default_factory=dict)
    explanations: list[str] = field(default_factory=list)


@dataclass
class DispatchPlan:
    """
    Complete output returned by DualFuelCoordinator.evaluate().
    Contains both the hardware actuation commands and diagnostic explanations.
    """

    space_directives: dict[str, SpaceDirective]
    producer_directives: dict[str, ProducerDirective]
    diagnostics: Diagnostics
