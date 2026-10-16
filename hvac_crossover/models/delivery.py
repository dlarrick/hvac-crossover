"""Terminal delivery units (Mini-split heads, baseboard loops, radiators, TRVs)."""

from abc import ABC
from dataclasses import dataclass

from .enums import EmitterType


@dataclass(kw_only=True)
class DeliveryUnit(ABC):
    """Abstract base class for heat/cool delivery hardware in a room."""

    id: str
    name: str = ""
    emitter_type: EmitterType
    producer_id: str


@dataclass(kw_only=True)
class MiniSplitHead(DeliveryUnit):
    """
    Ductless indoor head (wall, cassette, floor) or ducted air handler.
    Connected to a specific Inverter Heat Pump ODU.
    """

    emitter_type: EmitterType = EmitterType.DUCTLESS_HEAD
    nominal_btu: float = 12000.0
    unconditioned_line_length_ft: float = 0.0

    def effective_cop(self, base_cop: float, outdoor_temp_f: float, indoor_temp_f: float = 68.0) -> float:
        """
        Adjusts COP for convective thermal loss along unconditioned line runs
        (e.g., lines passing through freezing attics or exterior chases in winter).
        """
        if self.unconditioned_line_length_ft <= 0:
            return base_cop

        # Heat loss is proportional to delta between vapor line (~135°F) and unconditioned ambient
        temp_delta = max(0.0, 135.0 - outdoor_temp_f)
        # Approximate 0.06% loss per foot per 100°F delta for R-3/R-4 insulated lines
        loss_fraction = (self.unconditioned_line_length_ft * 0.0006) * (temp_delta / 100.0)
        derate_factor = max(0.80, 1.0 - loss_fraction)
        return base_cop * derate_factor


@dataclass(kw_only=True)
class HydronicEmitter(DeliveryUnit):
    """
    Hydronic water loop emitter (fin-tube baseboard, cast-iron radiator, in-floor radiant).
    Connected to a central boiler producer via a shared or dedicated zone valve.
    """

    emitter_type: EmitterType = EmitterType.FIN_TUBE_BASEBOARD
    zone_id: str = "z1"
    design_water_temp_f: float = 180.0


@dataclass(kw_only=True)
class SmartRadiatorValve(DeliveryUnit):
    """
    Individual Thermostatic Radiator Valve (TRV) providing 1:1 micro-zoning.
    """

    emitter_type: EmitterType = EmitterType.TRV
    zone_id: str = "z1"
    valve_id: str = "trv_1"
    design_water_temp_f: float = 180.0
