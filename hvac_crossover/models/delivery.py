"""Terminal delivery units (Mini-split heads, baseboard loops, radiators, TRVs, air handlers)."""

from abc import ABC
from dataclasses import dataclass
from typing import Any

from .enums import EmitterType

HYDRONIC_EMITTER_CHARACTERISTICS: dict[EmitterType, dict[str, Any]] = {
    EmitterType.FIN_TUBE_BASEBOARD: {
        "design_water_temp_f": 160.0,
        "design_delta_t_f": 20.0,
        "design_gpm": 3.5,
        "odr_temp_range": (180.0, 120.0),  # at 0°F -> 65°F outdoor
    },
    EmitterType.CAST_IRON_RADIATOR: {
        "design_water_temp_f": 150.0,
        "design_delta_t_f": 20.0,
        "design_gpm": 3.5,
        "odr_temp_range": (160.0, 110.0),
    },
    EmitterType.IN_FLOOR_RADIANT: {
        "design_water_temp_f": 110.0,
        "design_delta_t_f": 10.0,
        "design_gpm": 4.5,
        "odr_temp_range": (110.0, 85.0),
    },
    EmitterType.KICKSPACE: {
        "design_water_temp_f": 140.0,
        "design_delta_t_f": 15.0,
        "design_gpm": 3.0,
        "odr_temp_range": (150.0, 115.0),
    },
    EmitterType.HYDRO_AIR: {
        "design_water_temp_f": 130.0,
        "design_delta_t_f": 25.0,
        "design_gpm": 4.0,
        "odr_temp_range": (140.0, 110.0),
    },
}


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
    Hydronic water loop emitter (fin-tube baseboard, cast-iron radiator, in-floor radiant, etc.).
    Connected to a central boiler producer via a shared or dedicated zone valve.
    """

    emitter_type: EmitterType = EmitterType.FIN_TUBE_BASEBOARD
    zone_id: str = "z1"
    design_water_temp_f: float = 160.0
    design_delta_t_f: float = 20.0
    design_gpm: float = 3.5

    def __post_init__(self) -> None:
        if self.emitter_type in HYDRONIC_EMITTER_CHARACTERISTICS:
            specs = HYDRONIC_EMITTER_CHARACTERISTICS[self.emitter_type]
            if self.design_water_temp_f == 160.0 and self.emitter_type != EmitterType.FIN_TUBE_BASEBOARD:
                self.design_water_temp_f = specs["design_water_temp_f"]
            if self.design_delta_t_f == 20.0 and self.emitter_type != EmitterType.FIN_TUBE_BASEBOARD:
                self.design_delta_t_f = specs["design_delta_t_f"]
            if self.design_gpm == 3.5 and self.emitter_type != EmitterType.FIN_TUBE_BASEBOARD:
                self.design_gpm = specs["design_gpm"]


@dataclass(kw_only=True)
class FurnaceRegister(DeliveryUnit):
    """
    Central ducted warm-air supply register or zone.
    Connected to a central FurnaceProducer.
    """

    emitter_type: EmitterType = EmitterType.DUCTED_AIR_HANDLER


@dataclass(kw_only=True)
class SmartRadiatorValve(DeliveryUnit):
    """
    Individual Thermostatic Radiator Valve (TRV) providing 1:1 micro-zoning.
    """

    emitter_type: EmitterType = EmitterType.TRV
    zone_id: str = "z1"
    valve_id: str = "trv_1"
    design_water_temp_f: float = 180.0
