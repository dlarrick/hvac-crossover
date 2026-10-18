"""System snapshot model representing the live state passed into the evaluator."""

from dataclasses import dataclass, field
from typing import Any

from .spaces import SpaceCondition
from .tariffs import TariffSchedule


@dataclass(frozen=True)
class SystemSnapshot:
    """
    Complete ground-truth state of the environment and spaces at a single point in time.
    """

    outdoor_temp: float
    tariffs: TariffSchedule
    spaces: dict[str, SpaceCondition]

    # Optional Telemetry Overrides & Renewable Inputs
    solar_export_watts: float = 0.0
    boiler_return_temp: float | None = None
    outdoor_humidity: float | None = None
    elec_rate_override: float | None = None
    current_hour: int | None = None
    telemetry: dict[str, Any] = field(default_factory=dict)
