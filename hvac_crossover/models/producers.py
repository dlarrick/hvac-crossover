"""Equipment producer models (Heat pumps, Mod-Con boilers, Steam boilers)."""

import json
import os
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .delivery import HYDRONIC_EMITTER_CHARACTERISTICS
from .enums import FuelType, ProducerType

NATURAL_GAS_CONDENSING_CURVE: dict[float, float] = {
    80.0: 0.965,
    100.0: 0.965,
    105.0: 0.935,
    120.0: 0.890,
    135.0: 0.865,
    180.0: 0.865,
}

PROPANE_CONDENSING_CURVE: dict[float, float] = {
    80.0: 0.940,
    95.0: 0.940,
    105.0: 0.915,
    120.0: 0.880,
    125.0: 0.860,
    180.0: 0.860,
}


def interpolate_points(points: dict[float, float], x: float) -> float:
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
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
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

    cop_curve_full_load: dict[float, float] = field(default_factory=dict)
    cop_curve_part_load: dict[float, float] | None = None
    capacity_derate_curve: dict[float, float] = field(default_factory=dict)

    refrigerant: str = "R410A"
    max_ports: int = 3
    supports_simultaneous_heat_cool: bool = False  # True for VRF Heat Recovery (e.g. City Multi with BC controller)
    min_operational_temp_f: float = -4.0  # Rated minimum outdoor operational envelope temperature (°F)

    def __init__(
        self,
        id: str,
        name: str,
        nominal_btu: float = 24000.0,
        cop_curve_full_load: dict[float, float] | None = None,
        cop_curve_part_load: dict[float, float] | None = None,
        capacity_derate_curve: dict[float, float] | None = None,
        capacity_curve_btu: dict[float, float] | None = None,
        refrigerant: str = "R410A",
        max_ports: int = 3,
        supports_simultaneous_heat_cool: bool = False,
        min_operational_temp_f: float = -4.0,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            producer_type=ProducerType.INVERTER_HEAT_PUMP,
            nominal_btu=nominal_btu,
            fuel_type=FuelType.ELECTRICITY,
        )
        self.cop_curve_full_load = cop_curve_full_load or {}
        self.cop_curve_part_load = cop_curve_part_load
        if capacity_derate_curve is not None:
            self.capacity_derate_curve = capacity_derate_curve
        elif capacity_curve_btu:
            ref = capacity_curve_btu.get(47.0, max(capacity_curve_btu.values(), default=nominal_btu or 24000.0))
            self.capacity_derate_curve = {temp: btu / ref for temp, btu in capacity_curve_btu.items()}
        else:
            self.capacity_derate_curve = {}
        self.refrigerant = refrigerant
        self.max_ports = max_ports
        self.supports_simultaneous_heat_cool = supports_simultaneous_heat_cool
        self.min_operational_temp_f = min_operational_temp_f

    @property
    def capacity_curve_btu(self) -> dict[float, float]:
        """Compute absolute BTU capacity curve from nominal_btu and derate curve."""
        return {temp: self.nominal_btu * ratio for temp, ratio in self.capacity_derate_curve.items()}

    def is_within_envelope(self, outdoor_temp_f: float, safety_band_f: float = 0.0) -> bool:
        """Returns True if outdoor temp is at or above minimum operational envelope plus safety margin."""
        return outdoor_temp_f >= (self.min_operational_temp_f + safety_band_f)

    def effective_min_temp_f(self, safety_band_f: float = 0.0) -> float:
        """Returns the effective low ambient cutoff threshold including the safety margin."""
        return self.min_operational_temp_f + safety_band_f

    def cop_at(self, outdoor_temp_f: float, part_load: bool = False) -> float:
        """Interpolates COP at given outdoor ambient temperature."""
        if part_load and self.cop_curve_part_load:
            return interpolate_points(self.cop_curve_part_load, outdoor_temp_f)
        return interpolate_points(self.cop_curve_full_load, outdoor_temp_f)

    def max_capacity_at(self, outdoor_temp_f: float) -> float:
        """Interpolates maximum heating capacity in BTU/h at outdoor ambient temperature."""
        if outdoor_temp_f < self.min_operational_temp_f:
            return 0.0
        if self.capacity_derate_curve:
            return self.nominal_btu * interpolate_points(self.capacity_derate_curve, outdoor_temp_f)
        return self.nominal_btu

    def delivered_cost_per_mmbtu(
        self,
        outdoor_temp_f: float,
        tariffs: Any,
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
        part_load: bool = False,
    ) -> float:
        cop = self.cop_at(outdoor_temp_f, part_load=part_load)
        if cop <= 0:
            return float("inf")
        elec_cost = tariffs.marginal_electric_cost(telemetry.get("solar_export_watts", 0.0) if telemetry else 0.0)
        return elec_cost / (cop * 0.0034121416)


@dataclass
class ModulatingBoilerProducer(Producer):
    """
    Modulating-condensing (Mod-Con) hydronic boiler (e.g. Burnham Alta 150).
    Supports Natural Gas and Propane (LP) with physics-based condensing dew-point curves,
    primary-secondary decoupler bypass mixing, turndown limits, and short-cycling penalty modeling.
    """

    producer_type: ProducerType = field(default=ProducerType.MODULATING_BOILER, init=False)
    fuel_type: FuelType = FuelType.NATURAL_GAS

    rated_afue: float = 0.95
    turndown_ratio: float = 7.5
    _min_fire_btu: float | None = field(default=None, repr=False)
    cycling_degradation_factor: float = 0.14  # ASHRAE/DOE Cd cycling loss coefficient
    primary_flow_gpm: float = 12.0  # Decoupler primary loop circulator flow rate
    has_outdoor_reset: bool = False  # True if equipped with outdoor reset supply sensor

    def __init__(
        self,
        id: str,
        name: str,
        nominal_btu: float = 100000.0,
        fuel_type: FuelType = FuelType.NATURAL_GAS,
        rated_afue: float = 0.95,
        turndown_ratio: float = 7.5,
        min_fire_btu: float | None = None,
        cycling_degradation_factor: float = 0.14,
        primary_flow_gpm: float = 12.0,
        has_outdoor_reset: bool = False,
    ) -> None:
        super().__init__(
            id=id,
            name=name,
            producer_type=ProducerType.MODULATING_BOILER,
            nominal_btu=nominal_btu,
            fuel_type=fuel_type,
        )
        self.rated_afue = rated_afue
        self.turndown_ratio = turndown_ratio
        self._min_fire_btu = min_fire_btu
        self.cycling_degradation_factor = cycling_degradation_factor
        self.primary_flow_gpm = primary_flow_gpm
        self.has_outdoor_reset = has_outdoor_reset

    @property
    def min_fire_btu(self) -> float:
        """Minimum firing rate in BTU/h, computed via turndown_ratio or explicitly overridden."""
        if self._min_fire_btu is not None:
            return self._min_fire_btu
        if self.turndown_ratio > 0 and self.nominal_btu > 0:
            return self.nominal_btu / self.turndown_ratio
        return 0.0

    @min_fire_btu.setter
    def min_fire_btu(self, value: float | None) -> None:
        self._min_fire_btu = value

    def estimate_return_temperature(
        self,
        active_emitters: Sequence[Any] | None = None,
        outdoor_temp_f: float | None = None,
    ) -> float:
        """
        Calculates physically grounded return water temperature accounting for:
        1. Emitter design supply temperature requirements and outdoor reset curves.
        2. Hydraulic decoupler bypass mixing (flow ratio alpha = sum(Q_zones) / Q_primary).
        3. Emitter temperature drop (Delta T).
        """
        if not active_emitters:
            # Baseline when idle or no emitters specified: single baseline fin-tube baseboard
            # 160 - (3.5 / 12.0) * 20 = 154.167°F
            return 154.2

        # Group emitters by zone_id to determine zone-level flow and supply requirement
        zones: dict[str, list[Any]] = {}
        for em in active_emitters:
            zid = getattr(em, "zone_id", "z1")
            zones.setdefault(zid, []).append(em)

        supply_temps: list[float] = []
        zone_flows: list[float] = []
        zone_delta_ts: list[float] = []

        for zid, ems in zones.items():
            # Zone flow rate is governed by maximum or configured emitter in zone
            z_flow = max((getattr(e, "design_gpm", 3.5) for e in ems), default=3.5)
            zone_flows.append(z_flow)

            # Average design delta T across emitters in this zone
            z_dt = sum(getattr(e, "design_delta_t_f", 20.0) for e in ems) / len(ems)
            zone_delta_ts.append(z_dt)

            # Target supply water temperature
            for e in ems:
                if self.has_outdoor_reset and outdoor_temp_f is not None:
                    # Outdoor reset interpolation between 0°F and 65°F outdoor
                    t_od = max(0.0, min(65.0, outdoor_temp_f))
                    odr_range = getattr(e, "odr_temp_range", None)
                    if odr_range is None:
                        etype = getattr(e, "emitter_type", None)
                        specs = HYDRONIC_EMITTER_CHARACTERISTICS.get(etype)
                        odr_range = specs.get("odr_temp_range") if specs else (180.0, 120.0)
                    t_0, t_65 = odr_range
                    t_sup = t_65 + (t_0 - t_65) * ((65.0 - t_od) / 65.0)
                else:
                    t_sup = getattr(e, "design_water_temp_f", 160.0)
                supply_temps.append(t_sup)

        # 1. Governing Supply Temp: maximum required by any active emitter
        t_supply_req = max(supply_temps) if supply_temps else 160.0

        # 2. Decoupler Flow Ratio (alpha = sum(Q_zones) / Q_primary)
        total_zone_flow = sum(zone_flows)
        q_prim = max(1.0, self.primary_flow_gpm)
        alpha = min(1.0, total_zone_flow / q_prim)

        # 3. Average emitter Delta T across active zones
        avg_delta_t = sum(zone_delta_ts) / len(zone_delta_ts) if zone_delta_ts else 20.0

        # 4. Blended Return Water Temperature: T_return = T_supply - alpha * Delta_T
        return t_supply_req - (alpha * avg_delta_t)

    def condensing_state(self, return_water_temp_f: float) -> str:
        """Categorizes condensing operation into 'condensing', 'marginal', or 'dry'."""
        if self.fuel_type == FuelType.PROPANE:
            if return_water_temp_f <= 100.0:
                return "condensing"
            if return_water_temp_f <= 120.0:
                return "marginal"
            return "dry"

        if return_water_temp_f <= 110.0:
            return "condensing"
        if return_water_temp_f <= 130.0:
            return "marginal"
        return "dry"

    def efficiency_at(
        self,
        load_btu: float | None = None,
        return_water_temp_f: float | None = None,
    ) -> float:
        """
        Determines the in-situ thermal efficiency accounting for:
        1. Continuous return water temperature dew-point condensation curve (fuel-specific).
           - Natural Gas flue gas dew point is ~130-135°F.
           - Propane flue gas dew point is ~120-122°F (~8-10°F colder return required to condense).
        2. Burner short-cycling degradation when load < min_fire_btu.
        """
        base_eff = self.rated_afue

        # 1. Continuous physical condensing dew-point curve lookup if return temp exists
        if return_water_temp_f is not None:
            curve = PROPANE_CONDENSING_CURVE if self.fuel_type == FuelType.PROPANE else NATURAL_GAS_CONDENSING_CURVE
            base_eff = interpolate_points(curve, return_water_temp_f)

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
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
        part_load: bool = False,
    ) -> float:
        return_temp = telemetry.get("boiler_return_temp") if telemetry else None
        if return_temp is None and telemetry and "active_emitters" in telemetry:
            return_temp = self.estimate_return_temperature(
                telemetry["active_emitters"],
                outdoor_temp_f=outdoor_temp_f,
            )

        eff = self.efficiency_at(load_btu=load_btu, return_water_temp_f=return_temp)

        if self.fuel_type == FuelType.PROPANE:
            fuel_cost_per_mmbtu = tariffs.propane_cost_per_gal / 0.0915 if tariffs.propane_cost_per_gal > 0 else 0.0
            return fuel_cost_per_mmbtu / eff if eff > 0 else 0.0

        if self.fuel_type == FuelType.OIL:
            fuel_cost_per_mmbtu = tariffs.oil_cost_per_gal / 0.1385 if tariffs.oil_cost_per_gal > 0 else 0.0
            return fuel_cost_per_mmbtu / eff if eff > 0 else 0.0

        return tariffs.gas_cost_per_therm / (eff * 0.100) if eff > 0 else 0.0


@dataclass
class AtmosphericBoilerProducer(Producer):
    """
    Standard atmospheric cast-iron non-condensing hot water boiler
    (e.g. Burnham Series 2, Weil-McLain CGi / WTGO).
    High-temperature non-condensing (180/160°F) operation with fixed rated AFUE.
    """

    producer_type: ProducerType = field(default=ProducerType.ATMOSPHERIC_BOILER, init=False)
    fuel_type: FuelType = FuelType.NATURAL_GAS

    rated_afue: float = 0.83

    def delivered_cost_per_mmbtu(
        self,
        outdoor_temp_f: float,
        tariffs: Any,
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
        part_load: bool = False,
    ) -> float:
        if self.fuel_type == FuelType.PROPANE:
            fuel_cost = tariffs.propane_cost_per_gal / 0.0915 if tariffs.propane_cost_per_gal > 0 else 0.0
            return fuel_cost / self.rated_afue if self.rated_afue > 0 else 0.0

        if self.fuel_type == FuelType.OIL:
            fuel_cost = tariffs.oil_cost_per_gal / 0.1385 if tariffs.oil_cost_per_gal > 0 else 0.0
            return fuel_cost / self.rated_afue if self.rated_afue > 0 else 0.0

        return tariffs.gas_cost_per_therm / (self.rated_afue * 0.100) if self.rated_afue > 0 else 0.0


@dataclass
class SteamBoilerProducer(Producer):
    """
    Atmospheric steam boiler common in historic New England homes.
    Fixed non-condensing efficiency with an enforced minimum run time lock to prevent short-cycling.
    """

    producer_type: ProducerType = field(default=ProducerType.STEAM_BOILER, init=False)
    fuel_type: FuelType = FuelType.NATURAL_GAS

    rated_afue: float = 0.81
    min_run_time_minutes: int = 25  # Steam must run long enough to clear mains and vents

    def delivered_cost_per_mmbtu(
        self,
        outdoor_temp_f: float,
        tariffs: Any,
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
        part_load: bool = False,
    ) -> float:
        if self.fuel_type == FuelType.PROPANE:
            fuel_cost = tariffs.propane_cost_per_gal / 0.0915 if tariffs.propane_cost_per_gal > 0 else 0.0
            return fuel_cost / self.rated_afue if self.rated_afue > 0 else 0.0

        if self.fuel_type == FuelType.OIL:
            fuel_cost = tariffs.oil_cost_per_gal / 0.1385 if tariffs.oil_cost_per_gal > 0 else 0.0
            return fuel_cost / self.rated_afue if self.rated_afue > 0 else 0.0

        return tariffs.gas_cost_per_therm / (self.rated_afue * 0.100) if self.rated_afue > 0 else 0.0


@dataclass
class FurnaceProducer(Producer):
    """
    Central ducted warm-air furnace paired with central heat pump A-coil or standalone.
    Delivers hot air via central ductwork at fixed rated AFUE.
    """

    producer_type: ProducerType = field(default=ProducerType.FURNACE, init=False)
    fuel_type: FuelType = FuelType.NATURAL_GAS

    rated_afue: float = 0.80

    def delivered_cost_per_mmbtu(
        self,
        outdoor_temp_f: float,
        tariffs: Any,
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
        part_load: bool = False,
    ) -> float:
        if self.fuel_type == FuelType.PROPANE:
            fuel_cost = tariffs.propane_cost_per_gal / 0.0915 if tariffs.propane_cost_per_gal > 0 else 0.0
            return fuel_cost / self.rated_afue if self.rated_afue > 0 else 0.0

        if self.fuel_type == FuelType.OIL:
            fuel_cost = tariffs.oil_cost_per_gal / 0.1385 if tariffs.oil_cost_per_gal > 0 else 0.0
            return fuel_cost / self.rated_afue if self.rated_afue > 0 else 0.0

        return tariffs.gas_cost_per_therm / (self.rated_afue * 0.100) if self.rated_afue > 0 else 0.0


class ProducerProfileFactory:
    """Loads pre-seeded equipment models from the profiles/ library."""

    @staticmethod
    def resolve_profile_path(profile_str: str) -> str:
        """Finds profile JSON in installed hvac_crossover library or user path."""
        if os.path.isabs(profile_str) and os.path.exists(profile_str):
            return profile_str

        pkg_dir = os.path.dirname(os.path.dirname(__file__))
        pkg_profiles_dir = os.path.join(pkg_dir, "profiles", "producers")
        candidate = os.path.join(pkg_profiles_dir, profile_str)
        if not candidate.endswith(".json"):
            candidate += ".json"
        if os.path.exists(candidate):
            return candidate

        candidate_rel = os.path.join(pkg_dir, profile_str)
        if not candidate_rel.endswith(".json"):
            candidate_rel += ".json"
        if os.path.exists(candidate_rel):
            return candidate_rel

        if os.path.exists(profile_str):
            return profile_str
        if os.path.exists(profile_str + ".json"):
            return profile_str + ".json"

        return profile_str

    @classmethod
    def load_from_json(
        cls,
        file_path: str,
        producer_id: str,
        name: str | None = None,
        fuel_type: FuelType | None = None,
        nominal_btu: float | None = None,
    ) -> Producer:
        resolved_path = cls.resolve_profile_path(file_path)
        with open(resolved_path, encoding="utf-8") as f:
            data = json.load(f)

        p_type = data.get("producer_type", "inverter_heat_pump")
        raw_nominal = nominal_btu
        if raw_nominal is None:
            raw_val = data.get("nominal_heating_btu", data.get("nominal_btu"))
            if raw_val is not None:
                raw_nominal = float(raw_val)

        disp_name = name or data.get("model", producer_id)

        if p_type == "inverter_heat_pump":
            perf = data.get("heating_performance", {})
            full_cop = {float(k): float(v) for k, v in perf.get("full_load_cop", {}).items()}
            part_cop = (
                {float(k): float(v) for k, v in perf.get("part_load_cop", {}).items()}
                if "part_load_cop" in perf
                else None
            )
            raw_derate = (
                perf.get("capacity_percentage")
                or perf.get("max_capacity_percentage")
                or perf.get("max_capacity_btu", {})
            )
            derate_curve: dict[float, float] = {}
            if raw_derate:
                sample_max = max(float(v) for v in raw_derate.values())
                if sample_max > 100.0:
                    ref_val = float(raw_derate.get("47", raw_derate.get(47.0, sample_max)))
                    derate_curve = {float(k): float(v) / ref_val for k, v in raw_derate.items()}
                elif sample_max > 1.5:
                    derate_curve = {float(k): float(v) / 100.0 for k, v in raw_derate.items()}
                else:
                    derate_curve = {float(k): float(v) for k, v in raw_derate.items()}

            raw_min_temp = data.get("min_operational_temp_f", data.get("min_operating_temp_f"))
            min_temp_f = float(raw_min_temp) if raw_min_temp is not None else -4.0

            nominal = raw_nominal if raw_nominal is not None else 24000.0

            return InverterHeatPumpProducer(
                id=producer_id,
                name=disp_name,
                nominal_btu=nominal,
                cop_curve_full_load=full_cop,
                cop_curve_part_load=part_cop,
                capacity_derate_curve=derate_curve,
                refrigerant=data.get("refrigerant", "R410A"),
                max_ports=int(data.get("max_ports", 3)),
                min_operational_temp_f=min_temp_f,
            )

        if p_type == "modulating_boiler":
            resolved_fuel = fuel_type
            if resolved_fuel is None:
                fuel_str = str(data.get("fuel_type", "natural_gas")).lower()
                if fuel_str in ("propane", "lp"):
                    resolved_fuel = FuelType.PROPANE
                elif fuel_str in ("oil", "fuel_oil", "oil_no2"):
                    resolved_fuel = FuelType.OIL
                else:
                    resolved_fuel = FuelType.NATURAL_GAS

            min_fire = float(data["min_fire_btu"]) if "min_fire_btu" in data else None
            turndown = float(data.get("turndown_ratio", 4.0))

            return ModulatingBoilerProducer(
                id=producer_id,
                name=disp_name,
                nominal_btu=raw_nominal if raw_nominal is not None else 100000.0,
                fuel_type=resolved_fuel,
                rated_afue=float(data.get("rated_afue", 0.95)),
                min_fire_btu=min_fire,
                turndown_ratio=turndown,
                primary_flow_gpm=float(data.get("primary_flow_gpm", 12.0)),
                has_outdoor_reset=bool(data.get("has_outdoor_reset", False)),
            )

        if p_type == "atmospheric_boiler":
            resolved_fuel = fuel_type
            if resolved_fuel is None:
                fuel_str = str(data.get("fuel_type", "natural_gas")).lower()
                if fuel_str in ("propane", "lp"):
                    resolved_fuel = FuelType.PROPANE
                elif fuel_str in ("oil", "fuel_oil", "oil_no2"):
                    resolved_fuel = FuelType.OIL
                else:
                    resolved_fuel = FuelType.NATURAL_GAS

            return AtmosphericBoilerProducer(
                id=producer_id,
                name=disp_name,
                nominal_btu=raw_nominal if raw_nominal is not None else 100000.0,
                fuel_type=resolved_fuel,
                rated_afue=float(data.get("rated_afue", 0.83)),
            )

        if p_type == "steam_boiler":
            resolved_fuel = fuel_type
            if resolved_fuel is None:
                fuel_str = str(data.get("fuel_type", "natural_gas")).lower()
                if fuel_str in ("propane", "lp"):
                    resolved_fuel = FuelType.PROPANE
                elif fuel_str in ("oil", "fuel_oil", "oil_no2"):
                    resolved_fuel = FuelType.OIL
                else:
                    resolved_fuel = FuelType.NATURAL_GAS

            return SteamBoilerProducer(
                id=producer_id,
                name=disp_name,
                nominal_btu=raw_nominal if raw_nominal is not None else 100000.0,
                fuel_type=resolved_fuel,
                rated_afue=float(data.get("rated_afue", 0.81)),
                min_run_time_minutes=int(data.get("min_run_time_minutes", 25)),
            )

        if p_type == "furnace":
            resolved_fuel = fuel_type
            if resolved_fuel is None:
                fuel_str = str(data.get("fuel_type", "natural_gas")).lower()
                if fuel_str in ("propane", "lp"):
                    resolved_fuel = FuelType.PROPANE
                elif fuel_str in ("oil", "fuel_oil", "oil_no2"):
                    resolved_fuel = FuelType.OIL
                else:
                    resolved_fuel = FuelType.NATURAL_GAS

            return FurnaceProducer(
                id=producer_id,
                name=disp_name,
                nominal_btu=raw_nominal if raw_nominal is not None else 80000.0,
                fuel_type=resolved_fuel,
                rated_afue=float(data.get("rated_afue", 0.80)),
            )

        raise ValueError(f"Unknown producer_type: {p_type}")
