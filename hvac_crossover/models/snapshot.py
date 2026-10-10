"""System snapshot model representing the live state passed into the evaluator."""

from dataclasses import dataclass, field
from typing import Dict, Optional, Any
from .tariffs import TariffSchedule
from .spaces import SpaceCondition


@dataclass(frozen=True)
class SystemSnapshot:
    """
    Complete ground-truth state of the environment and spaces at a single point in time.
    """
    outdoor_temp: float
    tariffs: TariffSchedule
    spaces: Dict[str, SpaceCondition]

    # Optional Telemetry Overrides & Renewable Inputs
    solar_export_watts: float = 0.0
    boiler_return_temp: Optional[float] = None
    outdoor_humidity: Optional[float] = None
    elec_rate_override: Optional[float] = None
    current_hour: Optional[int] = None
    telemetry: Dict[str, Any] = field(default_factory=dict)
