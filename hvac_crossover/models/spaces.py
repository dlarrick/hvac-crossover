"""Space (Room), Comfort Range, and dynamic Space Condition models."""

from dataclasses import dataclass, field

from .delivery import DeliveryUnit
from .enums import SharedBoilerRole

# Whole-house gross UA per sq ft (BTU/(hr-°F-sqft)) anchored to Manual J / ASHRAE / PHIUS benchmarks:
# - UA: Overall heat-loss coefficient (envelope conduction A/R + air infiltration) in BTU/(hr-°F),
#   normalized here by conditioned floor area. https://en.wikipedia.org/wiki/Thermal_transmittance
# - ACH50: Air Changes per Hour at 50 Pa blower-door test pressure (airtightness; lower is tighter).
#   https://en.wikipedia.org/wiki/Blower_door
# - ERV: Energy Recovery Ventilator (recovers heat/moisture from exhaust air).
#   https://en.wikipedia.org/wiki/Energy_recovery_ventilation
# - Manual J (ACCA residential load calc): https://www.acca.org/standards/technical-manuals/manual-j
# - ASHRAE Fundamentals: https://www.ashrae.org/technical-resources/ashrae-handbook
# - PHIUS (Passive House Institute US): https://www.phius.org/passive-building/what-passive-building
ENVELOPE_UA_ANCHORS: tuple[tuple[float, float], ...] = (
    (1.0, 0.70),  # Uninsulated & very drafty (pre-1920s, ~18 ACH50)
    (3.0, 0.42),  # Older, minimal wall insulation (~12 ACH50)
    (5.0, 0.26),  # Average existing home (1970s-90s, ~7 ACH50)
    (7.0, 0.16),  # Modern code-built / well-insulated & air-sealed (~3 ACH50)
    (9.0, 0.09),  # High-performance / net-zero ready (~1.5 ACH50)
    (10.0, 0.06),  # Airtight Passive House (PHIUS/PHI, 0.6 ACH50 + ERV)
)

# Conservative baseline internal heat gains (appliances, lighting, occupants)
DEFAULT_INTERNAL_GAIN_BTU_PER_HR_SQFT: float = 1.0


@dataclass(frozen=True)
class ComfortRange:
    """
    Dual-boundary comfort range supporting open-ended floats and deadbands.
    - heat_to=None: Uncontrolled on the low end (no heating).
    - cool_to=None: Uncontrolled on the high end (no cooling).
    - Both None: Completely uncontrolled (e.g. passive thermal mass banking).
    """

    heat_to: float | None = None
    cool_to: float | None = None

    def demands_heat(self, current_temp: float) -> bool:
        """True if temperature has fallen below the heating comfort boundary."""
        return self.heat_to is not None and current_temp < self.heat_to

    def demands_cool(self, current_temp: float) -> bool:
        """True if temperature has risen above the cooling comfort boundary."""
        return self.cool_to is not None and current_temp > self.cool_to

    def is_in_deadband(self, current_temp: float) -> bool:
        """True if current temperature sits comfortably inside the deadband (zero energy needed)."""
        return not self.demands_heat(current_temp) and not self.demands_cool(current_temp)

    def is_satisfied_heating(self, current_temp: float, buffer: float = 0.5) -> bool:
        """True if current temperature has cleared the heating maintenance buffer."""
        if self.heat_to is None:
            return True
        return current_temp >= self.heat_to + buffer

    def is_satisfied_cooling(self, current_temp: float, buffer: float = 0.5) -> bool:
        """True if current temperature has cleared the cooling maintenance buffer."""
        if self.cool_to is None:
            return True
        return current_temp <= self.cool_to - buffer

    def heat_deficit(self, current_temp: float) -> float:
        """Returns degrees below heating setpoint, or 0.0."""
        if self.heat_to is not None:
            return max(0.0, self.heat_to - current_temp)
        return 0.0

    def cool_deficit(self, current_temp: float) -> float:
        """Returns degrees above cooling setpoint, or 0.0."""
        if self.cool_to is not None:
            return max(0.0, current_temp - self.cool_to)
        return 0.0


@dataclass
class Space:
    """
    A distinct living space / room with associated delivery hardware and thermal geometry.
    """

    id: str
    name: str
    emitters: list[DeliveryUnit] = field(default_factory=list)

    # Geometry & Thermal Mass
    floor_area_sqft: float = 300.0
    ceiling_height_ft: float = 9.0
    envelope_score: float = 5.0  # 1 (uninsulated) to 10 (Passive House)
    thermal_mass: int = 3  # 1 (very low) to 5 (very high)

    # Arbitration & Priority
    priority: int = 5  # Higher values win ODU mode conflicts
    shared_boiler_role: SharedBoilerRole = SharedBoilerRole.INDEPENDENT

    @property
    def volume_cuft(self) -> float:
        return self.floor_area_sqft * self.ceiling_height_ft

    @property
    def ua_factor(self) -> float:
        """
        Approximate overall gross heat loss coefficient (BTU/hr per °F delta)
        derived from the 1-10 envelope_score using a non-linear piecewise curve
        anchored to Manual J / ASHRAE / PHIUS whole-house benchmarks.
        """
        clamped = max(1.0, min(10.0, float(self.envelope_score)))
        per_sqft = ENVELOPE_UA_ANCHORS[-1][1]
        for (s0, u0), (s1, u1) in zip(ENVELOPE_UA_ANCHORS, ENVELOPE_UA_ANCHORS[1:]):
            if clamped <= s1:
                t = (clamped - s0) / (s1 - s0)
                per_sqft = u0 + t * (u1 - u0)
                break
        return self.floor_area_sqft * per_sqft

    @property
    def thermal_capacitance_btu_per_f(self) -> float:
        """
        Combined thermal storage mass (enclosed air + walls/framing/flooring)
        derived from the 1-5 thermal_mass rating. Used to calculate required BTUs
        for setback recovery pull-up.
        """
        # Air mass: ~0.018 BTU/cuft-°F
        air_mass = self.volume_cuft * 0.018
        mass_multipliers = {
            1: 6.0,
            2: 10.0,
            3: 14.0,
            4: 18.0,
            5: 24.0,
        }
        clamped = max(1, min(5, int(self.thermal_mass)))
        return air_mass * mass_multipliers[clamped]

    def estimated_heat_loss_btu_per_hr(
        self,
        indoor_temp_f: float,
        outdoor_temp_f: float,
        include_internal_gains: bool = True,
    ) -> float:
        """
        Calculates instantaneous net mechanical heating load (BTU/hr).

        When include_internal_gains is True (default), subtracts a conservative
        baseline internal heat-gain floor (1.0 BTU/(hr-sqft)) for appliances,
        lighting, and occupants, shifting the effective balance point below
        indoor temperature.
        """
        delta_t = max(0.0, indoor_temp_f - outdoor_temp_f)
        gross_loss = self.ua_factor * delta_t
        if include_internal_gains:
            internal_gains = self.floor_area_sqft * DEFAULT_INTERNAL_GAIN_BTU_PER_HR_SQFT
            return max(0.0, gross_loss - internal_gains)
        return gross_loss


@dataclass(frozen=True)
class SpaceCondition:
    """
    Dynamic snapshot of conditions and demand within a space at evaluation time.
    """

    current_temp: float
    comfort: ComfortRange
    window_open: bool = False
    occupied: bool = True
