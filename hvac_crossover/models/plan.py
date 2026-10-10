"""Dispatch plan and diagnostic outputs produced by the coordinator evaluation."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from .enums import HvacMode, HvacAction, SystemPhase


@dataclass
class SpaceDirective:
    """Actionable directive for a single space (room)."""
    space_id: str
    action: HvacAction
    hvac_mode: HvacMode
    target_temp: Optional[float]
    active_delivery_unit_id: Optional[str] = None
    active_producer_id: Optional[str] = None
    explanation: str = ""


@dataclass
class ProducerDirective:
    """Actionable directive for an energy producer (ODU, Boiler, Furnace)."""
    producer_id: str
    operational_mode: HvacMode
    is_active: bool
    active_zones: List[str] = field(default_factory=list)
    estimated_load_btu: float = 0.0


@dataclass
class Diagnostics:
    """Rich telemetry and explanations for user dashboards and energy reporting."""
    economic_crossover_temp: float
    system_phase: SystemPhase
    odu_modes: Dict[str, str] = field(default_factory=dict)
    space_costs_per_mmbtu: Dict[str, float] = field(default_factory=dict)
    space_cops: Dict[str, float] = field(default_factory=dict)
    explanations: List[str] = field(default_factory=list)


@dataclass
class DispatchPlan:
    """
    Complete output returned by DualFuelCoordinator.evaluate().
    Contains both the hardware actuation commands and diagnostic explanations.
    """
    space_directives: Dict[str, SpaceDirective]
    producer_directives: Dict[str, ProducerDirective]
    diagnostics: Diagnostics
