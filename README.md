# hvac-crossover

**Intelligent multi-source HVAC efficiency optimizer and thermodynamic crossover coordinator.**

[![PyPI version](https://img.shields.io/pypi/v/hvac-crossover.svg)](https://pypi.org/project/hvac-crossover/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

`hvac-crossover` is a pure Python, zero-external-dependency library designed to optimize multi-source climate control (such as hybrid inverter heat pumps paired with condensing hydronic boilers). It solves the physical, economic, and constraint-arbitration challenges of running disparate heating and cooling systems together.

## Key Features

- **Thermodynamic Break-Even ($COP^*$):** Dynamic marginal delivered cost ranking ($/MMBtu) computed from live electric ($P_{\text{elec}}$) and gas ($P_{\text{gas}}$) utility tariffs against manufacturer multi-point performance curves.
- **Setback Recovery vs. Plateau Maintenance:** Automatically engages high-capacity boilers for large recovery pulls ($\Delta T \ge 3.0^\circ\text{F}$) while handing off steady-state maintenance ($\Delta T \le 1.0^\circ\text{F}$) to modulating heat pumps in their peak efficiency range.
- **Refrigerant Mode Arbitrator:** Prevents multi-split outdoor units (ODUs) from faulting due to opposing heat/cool calls across shared-refrigerant indoor heads, safely diverting conflicting calls to alternate heat sources.
- **Hydronic Shared-Zone Arbitration:** Resolves $N:M$ bedroom baseboard couplings (master/slave arbitration) and supports 1:1 smart radiator valves (TRVs).
- **Boiler Short-Cycling Derating:** Accounts for burner turndown limits and cycling penalties when zone calls drop below minimum boiler firing rates.

## Installation

```bash
pip install hvac-crossover
```

## Quick Start

```python
from hvac_crossover import (
    ProducerProfileFactory, Space, MiniSplitHead, HydronicEmitter,
    SystemTopology, TariffSchedule, DualFuelCoordinator, ComfortRange
)

# 1. Define Equipment Topology
odu = ProducerProfileFactory.load_from_json(
    "profiles/producers/mitsubishi/mxz_3c30nahz2.json",
    producer_id="odu1",
    name="Mitsubishi 30k ODU"
)
boiler = ProducerProfileFactory.load_from_json(
    "profiles/producers/burnham/alta_150.json",
    producer_id="alta150",
    name="Burnham Alta 150"
)

loft = Space(
    id="loft",
    name="3rd Floor Loft",
    emitters=[
        MiniSplitHead(id="loft_head", producer_id="odu1", nominal_btu=12000.0),
        HydronicEmitter(id="loft_baseboard", producer_id="alta150", zone_id="z3"),
    ],
)

topology = SystemTopology(producers=[odu, boiler], spaces=[loft])
coordinator = DualFuelCoordinator(topology)

# 2. Evaluate Live Conditions
plan = coordinator.evaluate_conditions(
    outdoor_temp=36.0,
    tariffs=TariffSchedule(gas_cost_per_therm=1.88, electric_cost_per_kwh=0.25),
    spaces={
        "loft": {"current": 66.5, "heat_to": 68.0, "cool_to": 74.0}
    }
)

directive = plan.space_directives["loft"]
print(f"Action: {directive.action.value}")
print(f"Active Emitter: {directive.active_delivery_unit_id}")
print(f"Economic Crossover Temp: {plan.diagnostics.economic_crossover_temp}°F")
```

## Development & Testing

```bash
git clone https://github.com/dlarrick/hvac-crossover.git
cd hvac-crossover
python3 -m unittest discover -s tests
```

## License

MIT License. See [LICENSE](LICENSE) for details.
