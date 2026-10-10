"""Coordinator configuration, policies, and operational thresholds."""

from dataclasses import dataclass


@dataclass
class CoordinatorConfig:
    """
    Configurable policies, thresholds, and safety parameters for the DualFuelCoordinator.
    """
    # Thermal Recovery vs Plateau Boundaries
    recovery_delta_threshold_f: float = 3.0   # Deficit >= 3.0°F -> engage high-capacity boiler recovery
    plateau_delta_threshold_f: float = 1.0    # Deficit <= 1.0°F -> hand off to heat pump plateau

    # Economic Switching Buffer (Hysteresis)
    # E.g. 1.05 means heat pump cost ($/MMBtu) must be at least 5% cheaper than boiler
    # to switch from boiler to heat pump, preventing flip-flopping right at the crossover line.
    economic_switching_buffer: float = 1.05

    # Absolute Outdoor Temperature Safeguards
    min_heat_pump_temp_f: float = -13.0       # Minimum rated operational temperature for cold-climate inverters

    # Operational Interlocks
    passive_ventilation_override: bool = True # Open window/skylight forces space delivery units to OFF
    enforce_odu_mode_locks: bool = True       # Enforce single-refrigerant mode lock across heads on same ODU
    short_cycling_protection: bool = True     # Apply burner cycling derate when load is below boiler min fire
