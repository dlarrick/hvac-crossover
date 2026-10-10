"""Thermal state and setback recovery evaluation engine."""

from typing import Dict, Tuple
from ..models.enums import SystemPhase
from ..models.spaces import Space, SpaceCondition
from ..models.topology import SystemTopology
from ..config import CoordinatorConfig


class RecoveryEngine:
    """
    Evaluates thermal error deltas to distinguish high-load setback recovery
    from steady-state plateau maintenance.
    """

    def __init__(self, config: CoordinatorConfig):
        self.config = config

    def is_space_in_recovery(self, condition: SpaceCondition) -> bool:
        """
        True if the space has a thermal deficit meeting or exceeding the recovery threshold.
        """
        deficit = condition.comfort.heat_deficit(condition.current_temp)
        return deficit >= self.config.recovery_delta_threshold_f

    def is_space_at_plateau(self, condition: SpaceCondition) -> bool:
        """
        True if the space is close enough to target to maintain steady-state plateau.
        """
        deficit = condition.comfort.heat_deficit(condition.current_temp)
        return deficit <= self.config.plateau_delta_threshold_f

    def calculate_recovery_energy_btu(
        self,
        space: Space,
        condition: SpaceCondition,
        outdoor_temp_f: float,
        target_hours: float = 1.0,
    ) -> float:
        """
        Calculates total BTUs required to recover this space from setback within target_hours:
        BTU = (Thermal Capacitance * Delta_T) + (Envelope Heat Loss Rate * target_hours)
        """
        delta_t = condition.comfort.heat_deficit(condition.current_temp)
        if delta_t <= 0:
            return 0.0

        mass_soak_btu = space.thermal_capacitance_btu_per_f * delta_t
        envelope_loss_btu = space.estimated_heat_loss_btu_per_hr(condition.current_temp, outdoor_temp_f) * target_hours
        return mass_soak_btu + envelope_loss_btu

    def determine_system_phase(
        self,
        spaces: Dict[str, SpaceCondition],
        topology: SystemTopology,
    ) -> SystemPhase:
        """
        Determines the dominant macro-system phase across all spaces.
        """
        active_occupied = [
            (s_id, cond) for s_id, cond in spaces.items()
            if cond.occupied and not cond.window_open
        ]

        if not active_occupied:
            # Check if passive ventilation is active anywhere
            if any(cond.window_open for cond in spaces.values()):
                return SystemPhase.PASSIVE_VENTILATION
            return SystemPhase.AWAY_COAST

        # If any active occupied space has a heavy deficit, system is in recovery
        if any(self.is_space_in_recovery(cond) for _, cond in active_occupied):
            return SystemPhase.SETBACK_RECOVERY

        return SystemPhase.PLATEAU_MAINTENANCE
