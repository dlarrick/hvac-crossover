"""Economics and thermodynamic crossover calculation engine."""

from typing import List, Tuple, Optional, Dict, Any
from ..models.tariffs import TariffSchedule
from ..models.producers import Producer, InverterHeatPumpProducer, ModulatingBoilerProducer
from ..models.spaces import Space
from ..models.delivery import DeliveryUnit, MiniSplitHead, HydronicEmitter
from ..models.topology import SystemTopology


class EconomicsEngine:
    """
    Evaluates marginal energy costs, thermodynamic efficiency, and break-even crossover points.
    """

    def compute_crossover_cop(
        self,
        tariffs: TariffSchedule,
        boiler: Producer,
        load_btu: Optional[float] = None,
        telemetry: Optional[Dict[str, Any]] = None,
    ) -> float:
        """
        Derives critical break-even COP where Cost_HP ($/MMBtu) == Cost_Boiler ($/MMBtu).
        COP* = (100,000 / 3,412.14) * eta_boiler * (P_elec / P_gas)
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

        return (100000.0 / 3412.1416) * boiler_eff * (elec_cost / tariffs.gas_cost_per_therm)

    def compute_crossover_temperature(
        self,
        tariffs: TariffSchedule,
        odu: InverterHeatPumpProducer,
        boiler: Producer,
        load_btu: Optional[float] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        part_load: bool = False,
        temp_bounds: Tuple[float, float] = (-15.0, 65.0),
    ) -> Optional[float]:
        """
        Finds the outdoor ambient temperature where the ODU's COP equals the break-even COP*.
        Uses bisection search over temp_bounds.
        """
        target_cop = self.compute_crossover_cop(tariffs, boiler, load_btu=load_btu, telemetry=telemetry)
        low, high = temp_bounds

        if odu.cop_at(low, part_load=part_load) >= target_cop:
            return low  # Heat pump is cheaper across the entire range
        if odu.cop_at(high, part_load=part_load) <= target_cop:
            return high # Boiler is cheaper across the entire range

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
        load_btu: Optional[float] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        part_load: bool = False,
    ) -> Tuple[float, float]:
        """
        Returns (delivered_cost_per_mmbtu, effective_cop_or_efficiency) for a delivery unit.
        """
        producer = topology.get_producer(delivery_unit.producer_id)

        if isinstance(producer, InverterHeatPumpProducer):
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
            cost_mmbtu = tariffs.gas_cost_per_therm / (eff * 0.100)
            return cost_mmbtu, eff

        else:
            eff = getattr(producer, "rated_afue", 0.81)
            cost_mmbtu = tariffs.gas_cost_per_therm / (eff * 0.100)
            return cost_mmbtu, eff

    def rank_space_emitters_by_cost(
        self,
        space: Space,
        outdoor_temp_f: float,
        tariffs: TariffSchedule,
        topology: SystemTopology,
        load_btu: Optional[float] = None,
        telemetry: Optional[Dict[str, Any]] = None,
        part_load: bool = False,
    ) -> List[Tuple[DeliveryUnit, float, float]]:
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
            )
            ranked.append((d, cost, eff))

        ranked.sort(key=lambda x: x[1])
        return ranked
