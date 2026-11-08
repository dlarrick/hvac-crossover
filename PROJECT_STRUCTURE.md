# HVAC Crossover - Project Structure & Architecture

`hvac-crossover` is a standalone, pure-Python, zero-dependency library that optimizes multi-source residential HVAC setups (such as dual-fuel configurations pairing inverter heat pumps with condensing hydronic boilers). It handles the thermodynamics, marginal economic cost calculations, hardware constraint arbitration, and operational phase dispatching.

---

## 1. Directory Structure

```text
hvac-crossover/
├── hvac_crossover/                 # Main Python package
│   ├── __init__.py                 # Exports public API and version (__version__)
│   ├── config.py                   # CoordinatorConfig dataclass
│   ├── coordinator.py              # DualFuelCoordinator: orchestrates full evaluation pipeline
│   ├── engines/                    # Analytical and arbitration engines
│   │   ├── __init__.py
│   │   ├── arbitrator.py           # Multi-split ODU mode locks & shared boiler zone arbitration
│   │   ├── economics.py            # Delivered $/MMBtu, dynamic crossover temperature, COP*
│   │   └── recovery.py             # Setback recovery vs. plateau maintenance detection
│   ├── models/                     # Core domain models
│   │   ├── __init__.py
│   │   ├── delivery.py             # DeliveryUnit, MiniSplitHead, HydronicEmitter, FurnaceRegister
│   │   ├── directives.py           # SpaceDirective, ProducerDirective, DispatchPlan, Diagnostics
│   │   ├── enums.py                # HvacMode, HvacAction, SharedBoilerRole, ProducerType, etc.
│   │   ├── producers.py            # Producer base & subclasses (Inverter HP, Mod-Con Boiler, Atmospheric Boiler, Steam Boiler, Furnace), ProducerProfileFactory
│   │   ├── spaces.py               # Space, SpaceCondition, ComfortRange
│   │   ├── tariffs.py              # TariffSchedule (electric, gas, propane, oil rate math)
│   │   └── topology.py             # SystemTopology (graph of producers, emitters, spaces)
│   └── profiles/                   # Manufacturer & generic equipment performance profiles (JSON)
│       └── producers/
│           ├── bradford_white/     # Brute FT condensing boilers (bmfthw.json)
│           ├── burnham/            # Alta condensing boilers (alta.json)
│           ├── generic/            # Cold-climate HP, standard HP, mod-con boiler, atmospheric boiler, steam boiler, furnace
│           ├── haier/              # FlexFit multi-split ODUs (xuxxms2vhb.json)
│           └── mitsubishi/         # Hyper-Heat ODUs (mxz_xcxxnahz2.json, mxz_xdxxnlhz.json)
├── tests/                          # Complete unit test suite
│   ├── test_arbitrator.py          # ODU mode locking, shared-zone fallback arbitration
│   ├── test_comfort_range.py       # Deadband, heating/cooling deficits, float logic
│   ├── test_coordinator.py         # End-to-end evaluation pipeline dispatch tests
│   ├── test_economics.py           # Marginal costs, efficiency curves, cycling deratings
│   ├── test_generic_profiles.py    # Baseline profile validation and interpolation
│   └── test_recovery.py            # Setback recovery detection and thermal energy math
├── pyproject.toml                  # Packaging and build specification (PEP 517/621)
└── README.md                       # High-level overview and mathematical primer
```

---

## 2. Core Concepts & Subsystems

### A. DualFuelCoordinator (`coordinator.py`)
The central orchestrator. When `evaluate()` or `evaluate_conditions()` is called, it executes the pipeline:
1. **Phase Determination:** `RecoveryEngine` checks thermal deficits across spaces to select `SETBACK_RECOVERY` or `PLATEAU_MAINTENANCE`.
2. **Economic Ranking:** `EconomicsEngine` derives dynamic crossover temperature $T_{\text{cross}}$ and ranks emitters per space by delivered $/MMBtu.
3. **Candidate Emitter Selection:** Spaces pick their primary candidate emitter based on phase, demand, and economics.
4. **Shared Hydronic Zone Arbitration:** `ArbitratorEngine` checks multi-space boiler zone couplings. If the primary space is satisfied or not calling, secondary spaces fall back to their next best viable emitter (e.g. mini-split) within safety limits.
5. **ODU Mode Lock Arbitration:** `ArbitratorEngine` resolves opposing heat/cool demands across indoor heads sharing a single-refrigerant multi-split condenser.
6. **Directive Synthesis:** Generates immutable `SpaceDirective` and `ProducerDirective` objects inside a `DispatchPlan`.

### B. Hardware Arbitration (`engines/arbitrator.py`)
Enforces physical and equipment constraints:
* **Shared Hydronic Zones:** Uses `SharedBoilerRole.PRIMARY` and `SharedBoilerRole.SECONDARY` to arbitrate spaces sharing a physical boiler zone valve where the thermostat is located in the primary space.
* **ODU Single-Refrigerant Mode Locks:** Guarantees indoor heads on the same outdoor unit cannot call for contradictory modes (Heat vs. Cool/Dry).

### C. Economics Engine (`engines/economics.py`)
Computes marginal delivered heating cost per million BTU:
$$\text{Cost}_{\text{elec}} = \frac{P_{\text{elec}}}{\text{COP}(T_{\text{ambient}}) \times 0.003412}$$
$$\text{Cost}_{\text{gas}} = \frac{P_{\text{gas}}}{\text{AFUE} \times 0.100}$$
Accounts for modulation part-load ratios, boiler return water dew point efficiency shifts, and short-cycling standby losses.

---

## 3. Developer Quick Reference

### Running Unit Tests
Always run with the project's Python environment:
```bash
# Using standard unittest discovery (pure Python, zero dependencies)
python3 -m unittest discover -s tests
```

### Versioning
* Version is defined in `pyproject.toml` (`project.version`) and `hvac_crossover/__init__.py` (`__version__`).
* Always update the matching dependency version pin under `"requirements"` in `/home/doug/Projects/hvac-fusion/custom_components/hvac_fusion/manifest.json`.
* When updating `hvac-crossover`, bump the version and reinstall in editable mode into the consumer environment:
  ```bash
  pip install -e /home/doug/Projects/hvac-crossover
  ```
