"""Core enumerations for the hybrid HVAC coordination system."""

from enum import Enum


class HvacMode(str, Enum):
    """Requested operational mode for a space or delivery unit."""

    OFF = "off"
    HEAT = "heat"
    COOL = "cool"
    DRY = "dry"
    FAN_ONLY = "fan_only"
    AUTO = "auto"


class HvacAction(str, Enum):
    """Current instantaneous action being performed."""

    OFF = "off"
    IDLE = "idle"
    HEATING = "heating"
    COOLING = "cooling"
    DEFROSTING = "defrosting"
    PASSIVE_VENTILATION = "passive_ventilation"


class ProducerType(str, Enum):
    """Type of thermal energy generator."""

    INVERTER_HEAT_PUMP = "inverter_heat_pump"
    MODULATING_BOILER = "modulating_boiler"
    ATMOSPHERIC_BOILER = "atmospheric_boiler"
    STEAM_BOILER = "steam_boiler"
    FURNACE = "furnace"


class EmitterType(str, Enum):
    """Terminal heat/cool delivery hardware."""

    DUCTLESS_HEAD = "ductless_head"
    DUCTED_AIR_HANDLER = "ducted_air_handler"
    FIN_TUBE_BASEBOARD = "fin_tube_baseboard"
    CAST_IRON_RADIATOR = "cast_iron_radiator"
    IN_FLOOR_RADIANT = "in_floor_radiant"
    TRV = "smart_radiator_valve"


class FuelType(str, Enum):
    """Fuel or energy source."""

    ELECTRICITY = "electricity"
    NATURAL_GAS = "natural_gas"
    OIL = "oil"
    PROPANE = "propane"


class NetMeteringMode(str, Enum):
    """Solar net metering compensation tariff."""

    ONE_TO_ONE = "1_to_1"
    NET_BILLING = "net_billing"


class ThermalProfile(str, Enum):
    """Envelope thermal loss and mass archetype."""

    HISTORIC_VICTORIAN = "historic_victorian"
    AVERAGE_INSULATED = "average_insulated"
    MODERN_TIGHT = "modern_tight"


class SharedBoilerRole(str, Enum):
    """Role in shared hydronic zone arbitration."""

    INDEPENDENT = "independent"
    MASTER = "master"
    SLAVE = "slave"


class SystemPhase(str, Enum):
    """Macro operational phase of the system."""

    SETBACK_RECOVERY = "setback_recovery"
    PLATEAU_MAINTENANCE = "plateau_maintenance"
    PASSIVE_VENTILATION = "passive_ventilation"
    NIGHT_SLEEP = "night_sleep"
    AWAY_COAST = "away_coast"
