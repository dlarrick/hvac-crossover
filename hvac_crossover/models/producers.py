"""Equipment producer models (Heat pumps, Mod-Con boilers, Steam boilers)."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import json
import os
from typing import Dict, Any, Optional
from .enums import ProducerType, FuelType


def interpolate_points(points: Dict[float, float], x: float) -> float:
    """Linearly interpolate or flat-extrapolate between given x->y points."""
    if not points:
        return 0.0
    sorted_pts = sorted(points.items(), key=lambda item: item[0])
    xs = [p[0] for p in sorted_pts]
    ys = [p[1] for p in sorted_pts]

    if x <= xs[0]:
        return ys[0]
    if x >= xs[-1]:
        return ys[-1]

    for i in range(len(xs) - 1):
        x1, x2 = xs[i], xs[i + 1]
        if x1 <= x <= x2:
            y1, y2 = ys[i], ys[i + 1]
            return y1 + (y2 - y1) * (x - x1) / (x2 - x1)

    return ys[-1]


@dataclass
class Producer(ABC):
    """Abstract base class for all energy conversion producers."""
    id: str
    name: str
    producer_type: ProducerType
    nominal_btu: float
    fuel_type: FuelType

    @abstractmethod
    def delivered_cost_per_mmbtu(
        self,
        outdoor_temp_f: float,
        tariffs: Any,
        load_btu: Optional[float] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        part_load: bool = False,
    ) -> float:
        """Returns the cost per 1,000,000 BTU of delivered heat under given conditions."""
        pass


@dataclass
class InverterHeatPumpProducer(Producer):
    """
    Multi-split inverter heat pump outdoor unit (ODU).
    Enforces single-refrigerant mode lock and multi-point COP/capacity interpolation.
    """
    producer_type: ProducerType = field(default=ProducerType.INVERTER_HEAT_PUMP, init=False)
    fuel_type: FuelType = field(default=FuelType.ELECTRICITY, init=False)

    cop_curve_full_load: Dict[float, float] = field(default_factory=dict)
    cop_curve_part_load: Optional[Dict[float, float]] = None
    capacity_curve_btu: Dict[float, float] = field(default_factory=dict)

    refrigerant: str = "R410A"
    max_ports: int = 3
    supports_simultaneous_heat_cool: bool = False  # True for VRF Heat Recovery (e.g. City Multi with BC controller)

    def cop_at(self, outdoor_temp_f: float, part_load: bool = False) -> float:
        """Interpolates COP at given outdoor ambient temperature."""
        if part_load and self.cop_curve_part_load:
            return interpolate_points(self.cop_curve_part_load, outdoor_temp_f)
        return interpolate_points(self.cop_curve_full_load, outdoor_temp_f)

    def max_capacity_at(self, outdoor_temp_f: float) -> float:
        """Interpolates maximum heating capacity in BTU/h at outdoor ambient temperature."""
        if self.capacity_curve_btu:
            return interpolate_points(self.capacity_curve_btu, outdoor_temp_f)
        return self.nominal_btu

    def delivered_cost_per_mmbtu(
        self,
        outdoor_temp_f: float,
        tariffs: Any,
        load_btu: Optional[float] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        part_load: bool = False,
    ) -> float:
        cop = self.cop_at(outdoor_temp_f, part_load=part_load)
        if cop <= 0:
            return float("inf")
        elec_cost = tariffs.marginal_electric_cost(
            telemetry.get("solar_export_watts", 0.0) if telemetry else 0.0
        )
        return elec_cost / (cop * 0.0034121416)


@dataclass
class ModulatingBoilerProducer(Producer):
    """
    Modulating-condensing (Mod-Con) gas hydronic boiler (e.g. Burnham Alta 150).
    Features turndown limits, short-cycling penalty modeling, and condensing dew-point physics.
    """
    producer_type: ProducerType = field(default=ProducerType.MODULATING_BOILER, init=False)
    fuel_type: FuelType = field(default=FuelType.NATURAL_GAS, init=False)

    rated_afue: float = 0.95
    min_fire_btu: float = 20000.0
    turndown_ratio: float = 7.5
    cycling_degradation_factor: float = 0.14  # ASHRAE/DOE Cd cycling loss coefficient

    def efficiency_at(
        self,
        load_btu: Optional[float] = None,
        return_water_temp_f: Optional[float] = None,
    ) -> float:
        """
        Determines the in-situ thermal efficiency accounting for:
        1. Measured return water temperature dew-point condensation curve (if thermistor available).
        2. Burner short-cycling degradation when load < min_fire_btu.
        """
        base_eff = self.rated_afue

        # 1. Physical condensing dew-point curve lookup if telemetry exists
        if return_water_temp_f is not None:
            if return_water_temp_f > 135.0:
                base_eff = 0.865  # Strictly non-condensing dry heat exchanger
            elif return_water_temp_f > 120.0:
                base_eff = 0.890  # Weak condensation
            elif return_water_temp_f > 105.0:
                base_eff = 0.935  # Active condensation
            else:
                base_eff = 0.965  # Deep sub-100°F condensation (cold recovery pull-up)

        # 2. Burner modulation & short-cycling penalty
        if load_btu is not None and load_btu < self.min_fire_btu and self.min_fire_btu > 0:
            part_load_ratio = max(0.1, min(1.0, load_btu / self.min_fire_btu))
            # Standard ASHRAE cycling derate: eta = eta_steady * (1 - Cd * (1 - PLR))
            degradation = 1.0 - (self.cycling_degradation_factor * (1.0 - part_load_ratio))
            return max(0.70, base_eff * degradation)

        return base_eff

    def delivered_cost_per_mmbtu(
        self,
        outdoor_temp_f: float,
        tariffs: Any,
        load_btu: Optional[float] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        part_load: bool = False,
    ) -> float:
        return_temp = telemetry.get("boiler_return_temp") if telemetry else None
        eff = self.efficiency_at(load_btu=load_btu, return_water_temp_f=return_temp)
        return (tariffs.gas_cost_per_therm / (eff * 0.100))


@dataclass
class SteamBoilerProducer(Producer):
    """
    Atmospheric steam boiler common in historic New England homes.
    Fixed non-condensing efficiency with an enforced minimum run time lock to prevent short-cycling.
    """
    producer_type: ProducerType = field(default=ProducerType.STEAM_BOILER, init=False)
    fuel_type: FuelType = field(default=FuelType.NATURAL_GAS, init=False)

    rated_afue: float = 0.81
    min_run_time_minutes: int = 25  # Steam must run long enough to clear mains and vents

    def delivered_cost_per_mmbtu(
        self,
        outdoor_temp_f: float,
        tariffs: Any,
        load_btu: Optional[float] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        part_load: bool = False,
    ) -> float:
        return tariffs.gas_cost_per_therm / (self.rated_afue * 0.100)


class ProducerProfileFactory:
    """Loads pre-seeded equipment models from the profiles/ library."""

    @staticmethod
    def load_from_json(file_path: str, producer_id: str, name: Optional[str] = None) -> Producer:
        if not os.path.exists(file_path):
            pkg_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            candidate = os.path.join(pkg_dir, "profiles", "producers", file_path)
            if os.path.exists(candidate):
                file_path = candidate
            elif os.path.exists(os.path.join(pkg_dir, file_path)):
                file_path = os.path.join(pkg_dir, file_path)

        with open(file_path, "r", encoding="utf-8") as f:
            data = json.load(f)


        p_type = data.get("producer_type", "inverter_heat_pump")
        nominal = float(data.get("nominal_heating_btu", data.get("nominal_btu", 30000)))
        disp_name = name or data.get("model", producer_id)

        if p_type == "inverter_heat_pump":
            perf = data.get("heating_performance", {})
            full_cop = {float(k): float(v) for k, v in perf.get("full_load_cop", {}).items()}
            part_cop = {float(k): float(v) for k, v in perf.get("part_load_cop", {}).items()} if "part_load_cop" in perf else None
            cap_curve = {float(k): float(v) for k, v in perf.get("max_capacity_btu", {}).items()}

            return InverterHeatPumpProducer(
                id=producer_id,
                name=disp_name,
                nominal_btu=nominal,
                cop_curve_full_load=full_cop,
                cop_curve_part_load=part_cop,
                capacity_curve_btu=cap_curve,
                refrigerant=data.get("refrigerant", "R410A"),
                max_ports=int(data.get("max_ports", 3)),
            )

        if p_type == "modulating_boiler":
            return ModulatingBoilerProducer(
                id=producer_id,
                name=disp_name,
                nominal_btu=nominal,
                rated_afue=float(data.get("rated_afue", 0.95)),
                min_fire_btu=float(data.get("min_fire_btu", 20000)),
                turndown_ratio=float(data.get("turndown_ratio", 7.5)),
            )

        if p_type == "steam_boiler":
            return SteamBoilerProducer(
                id=producer_id,
                name=disp_name,
                nominal_btu=nominal,
                rated_afue=float(data.get("rated_afue", 0.81)),
                min_run_time_minutes=int(data.get("min_run_time_minutes", 25)),
            )

        raise ValueError(f"Unknown producer_type: {p_type}")
