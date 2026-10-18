"""Economics and thermodynamic crossover calculation engine."""

from typing import Any

from ..models.delivery import DeliveryUnit, MiniSplitHead
from ..models.enums import FuelType
from ..models.producers import InverterHeatPumpProducer, ModulatingBoilerProducer, Producer
from ..models.spaces import Space
from ..models.tariffs import TariffSchedule
from ..models.topology import SystemTopology


class EconomicsEngine:
    """
    Evaluates marginal energy costs, thermodynamic efficiency, and break-even crossover points.
    """

    def compute_crossover_cop(
        self,
        tariffs: TariffSchedule,
        boiler: Producer,
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
    ) -> float:
        """
        Derives critical break-even COP where Cost_HP ($/MMBtu) == Cost_Boiler ($/MMBtu).
        COP* = (100,000 / 3,412.14) * eta_boiler * (P_elec / P_gas)
        For propane: COP* = (91,500 / 3,412.14) * eta_boiler * (P_elec / P_propane_gal)
        """
        elec_cost = tariffs.marginal_electric_cost(
            telemetry.get("solar_export_watts", 0.0) if telemetry else 0.0,
            rate_override=telemetry.get("elec_rate_override") if telemetry else None,
        )
        if isinstance(boiler, ModulatingBoilerProducer):
            return_temp = telemetry.get("boiler_return_temp") if telemetry else None
            boiler_eff = boiler.efficiency_at(load_btu=load_btu, return_water_temp_f=return_temp)
        else:
            boiler_eff = getattr(boiler, "rated_afue", 0.81)

        fuel_type = getattr(boiler, "fuel_type", FuelType.NATURAL_GAS)
        if fuel_type == FuelType.PROPANE and tariffs.propane_cost_per_gal > 0:
            # 1 gal propane = 91,500 BTU
            return (91500.0 / 3412.1416) * boiler_eff * (elec_cost / tariffs.propane_cost_per_gal)

        # Natural gas (1 therm = 100,000 BTU)
        return (100000.0 / 3412.1416) * boiler_eff * (elec_cost / tariffs.gas_cost_per_therm)

    def compute_crossover_temperature(
        self,
        tariffs: TariffSchedule,
        odu: InverterHeatPumpProducer,
        boiler: Producer,
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
        part_load: bool = False,
        temp_bounds: tuple[float, float] = (-15.0, 65.0),
    ) -> float | None:
        """
        Finds the outdoor ambient temperature where the ODU's COP equals the break-even COP*.
        Uses bisection search over temp_bounds.
        """
        target_cop = self.compute_crossover_cop(tariffs, boiler, load_btu=load_btu, telemetry=telemetry)
        low, high = temp_bounds

        if odu.cop_at(low, part_load=part_load) >= target_cop:
            return low  # Heat pump is cheaper across the entire range
        if odu.cop_at(high, part_load=part_load) <= target_cop:
            return high  # Boiler is cheaper across the entire range

        for _ in range(50):
            mid = (low + high) / 2.0
            cop_mid = odu.cop_at(mid, part_load=part_load)
            if abs(cop_mid - target_cop) < 0.005:
                return round(mid, 1)
            if cop_mid < target_cop:
                low = mid
            else:
                high = mid

        return round((low + high) / 2.0, 1)

    def evaluate_delivery_unit_cost(
        self,
        delivery_unit: DeliveryUnit,
        outdoor_temp_f: float,
        tariffs: TariffSchedule,
        topology: SystemTopology,
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
        part_load: bool = False,
        safety_band_f: float = 5.0,
    ) -> tuple[float, float]:
        """
        Returns (delivered_cost_per_mmbtu, effective_cop_or_efficiency) for a delivery unit.
        """
        producer = topology.get_producer(delivery_unit.producer_id)

        if isinstance(producer, InverterHeatPumpProducer):
            if not producer.is_within_envelope(outdoor_temp_f, safety_band_f=safety_band_f):
                return float("inf"), 0.0

            base_cop = producer.cop_at(outdoor_temp_f, part_load=part_load)
            if isinstance(delivery_unit, MiniSplitHead):
                eff_cop = delivery_unit.effective_cop(base_cop, outdoor_temp_f)
            else:
                eff_cop = base_cop

            elec_rate = tariffs.marginal_electric_cost(
                telemetry.get("solar_export_watts", 0.0) if telemetry else 0.0,
                rate_override=telemetry.get("elec_rate_override") if telemetry else None,
            )
            cost_mmbtu = elec_rate / (eff_cop * 0.0034121416) if eff_cop > 0 else float("inf")
            return cost_mmbtu, eff_cop

        elif isinstance(producer, ModulatingBoilerProducer):
            return_temp = telemetry.get("boiler_return_temp") if telemetry else None
            eff = producer.efficiency_at(load_btu=load_btu, return_water_temp_f=return_temp)
            cost_mmbtu = producer.delivered_cost_per_mmbtu(
                outdoor_temp_f=outdoor_temp_f,
                tariffs=tariffs,
                load_btu=load_btu,
                telemetry=telemetry,
                part_load=part_load,
            )
            return cost_mmbtu, eff

        else:
            eff = getattr(producer, "rated_afue", 0.81)
            if hasattr(producer, "delivered_cost_per_mmbtu"):
                cost_mmbtu = producer.delivered_cost_per_mmbtu(
                    outdoor_temp_f=outdoor_temp_f,
                    tariffs=tariffs,
                    load_btu=load_btu,
                    telemetry=telemetry,
                    part_load=part_load,
                )
            else:
                cost_mmbtu = tariffs.gas_cost_per_therm / (eff * 0.100)
            return cost_mmbtu, eff

    def rank_space_emitters_by_cost(
        self,
        space: Space,
        outdoor_temp_f: float,
        tariffs: TariffSchedule,
        topology: SystemTopology,
        load_btu: float | None = None,
        telemetry: dict[str, Any] | None = None,
        part_load: bool = False,
        safety_band_f: float = 5.0,
    ) -> list[tuple[DeliveryUnit, float, float]]:
        """
        Ranks all delivery units in a space from lowest cost to highest cost per delivered MMBtu.
        Returns list of (delivery_unit, cost_per_mmbtu, efficiency_or_cop).
        """
        ranked = []
        for d in space.emitters:
            cost, eff = self.evaluate_delivery_unit_cost(
                delivery_unit=d,
                outdoor_temp_f=outdoor_temp_f,
                tariffs=tariffs,
                topology=topology,
                load_btu=load_btu,
                telemetry=telemetry,
                part_load=part_load,
                safety_band_f=safety_band_f,
            )
            ranked.append((d, cost, eff))

        ranked.sort(key=lambda x: x[1])
        return ranked
