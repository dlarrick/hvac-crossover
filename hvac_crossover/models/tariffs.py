from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from .enums import NetMeteringMode



@dataclass(frozen=True)
class TariffSchedule:
    """
    Utility rate schedule for electricity and fossil fuels.
    """
    elec_cost_per_kwh: float
    gas_cost_per_therm: float
    solar_export_credit: float = 0.05
    net_metering_mode: NetMeteringMode = NetMeteringMode.ONE_TO_ONE
    oil_cost_per_gal: float = 0.0
    propane_cost_per_gal: float = 0.0

    # Optional Time-of-Use (TOU) tier specifications
    tou_peak_rate: Optional[float] = None
    tou_peak_start_hour: Optional[int] = None  # e.g., 16 (4:00 PM)
    tou_peak_end_hour: Optional[int] = None    # e.g., 21 (9:00 PM)

    def marginal_electric_cost(
        self,
        instantaneous_solar_surplus_watts: float = 0.0,
        current_hour: Optional[int] = None,
        rate_override: Optional[float] = None,
    ) -> float:
        """
        Calculates the true marginal opportunity cost for the next consumed kWh.
        - If a dynamic rate_override (e.g. from a live utility sensor) is provided, it takes precedence.
        - Under net billing (avoided cost), if panels are actively exporting surplus power,
          the opportunity cost drops to the forgone export credit ($0.05/kWh).
        - If TOU hours match, the peak rate applies.
        """
        if rate_override is not None:
            base_rate = rate_override
        elif (
            self.tou_peak_rate is not None
            and self.tou_peak_start_hour is not None
            and self.tou_peak_end_hour is not None
            and current_hour is not None
            and self.tou_peak_start_hour <= current_hour < self.tou_peak_end_hour
        ):
            base_rate = self.tou_peak_rate
        else:
            base_rate = self.elec_cost_per_kwh

        if self.net_metering_mode == NetMeteringMode.NET_BILLING and instantaneous_solar_surplus_watts > 500.0:
            return self.solar_export_credit

        return base_rate

    @property
    def gas_cost_per_mmbtu_input(self) -> float:
        """Cost per 1,000,000 BTU of raw fuel input (before burner efficiency)."""
        return self.gas_cost_per_therm / 0.100

    @property
    def electric_cost_per_mmbtu_input(self) -> float:
        """Cost per 1,000,000 BTU of raw electric resistance heat (COP = 1.0)."""
        return self.elec_cost_per_kwh / 0.0034121416
