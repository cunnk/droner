# Drone Fleet Failure-Aware Optimization

## Project Overview

This project explores **failure-aware optimization for autonomous drone fleets**, with an emphasis on real-world operational constraints rather than purely theoretical optimization.

**Primary research question:**
> When something fails — solver timeout, infeasible model, drone breakdown — how badly does the system degrade, and how can we design to bound that damage?

**Secondary questions:**
1. When does optimization outperform heuristics? When does it fail?
2. How much performance is lost under degradation?
3. How should the system adapt under time pressure?
4. What replanning cadence minimizes coverage loss?
5. How sensitive are plans to bad state estimates?

**Target audience:** Agriculture drone automation domain. Applies Operations Research + AI/ML background applied to real autonomy problems.

**Framing:** This is a **systems design project** with optimization as one component — not an optimization project with systems wrapped around it. The value is in understanding failure modes and graceful degradation.

---

## High-Level Architecture

```
Field Representation (image/grid)
        ↓
Strip Generator
        ↓
Fleet Optimizer (MILP)
        ↓
Routing Layer (heuristic)
        ↓
Simulation Engine
        ↓
Failure Injection
        ↓
Replanning Loop
        ↓
Visualization
```

---

## Directory Structure

```
drones/
├── data/               # Raw and processed field/imagery data
├── notebooks/          # Exploratory analysis and prototyping
├── src/
│   ├── field/          # Field representation and strip generation
│   ├── optimizer/      # MILP model and replanning logic
│   ├── routing/        # Heuristic routing layer
│   ├── simulation/     # Simulation engine and failure injection
│   └── viz/            # Visualization (decoupled from sim)
├── tests/
├── results/            # State histories, metrics, saved runs
├── CLAUDE.md
└── requirements.txt
```

---

## Example Input Data

### Field Grid
In practice, field data may come from aerial imagery. For development, we approximate with a grid:

```python
field_grid = [
    [0.8, 0.7, 0.6, 0.4],
    [0.9, 0.85, 0.7, 0.5],
    [0.95, 0.9, 0.75, 0.6],
    [0.7, 0.65, 0.6, 0.4]
]
```

Values represent vegetation density (0–1). Higher values may indicate higher spray need or priority.

### Derived Strips

```python
strips = [
    {"id": 0, "time": 12.5, "priority": 0.9},
    {"id": 1, "time": 10.2, "priority": 0.8},
    {"id": 2, "time": 14.1, "priority": 0.95},
    {"id": 3, "time": 9.8,  "priority": 0.7}
]
```

### Drone Inputs

```python
drones = [
    {"id": 0, "battery": 100, "spray_capacity": 100},
    {"id": 1, "battery": 100, "spray_capacity": 100},
    {"id": 2, "battery": 100, "spray_capacity": 100}
]
```

---

## Optimization Model

### Decision Variables
```
x[d, k] = 1 if drone d is assigned strip k
T_d     = workload of drone d
T_max   = maximum workload across drones
```

### Objective
```
minimize T_max
```

### Constraints
1. Each strip assigned exactly once
2. Drone workload = sum of assigned strip times
3. `T_d ≤ T_max`

**Solver:** PuLP (default) or OR-Tools. Replanning triggered by failure events; heuristics used when time budget is exceeded.

---

## Simulation Engine

### Drone State
```python
drone = {
    "id": 0,
    "position": (x, y),
    "route": [strip_ids],
    "current_target": 2,
    "battery": 72,
    "state": "spraying"  # moving | spraying | returning | charging
}
```

### Animation Loop
```python
for t in range(T):
    update_drone_positions()
    update_grid_coverage()
    update_battery_levels()

    if failure_event:
        trigger_failure()

    if replanning_needed:
        run_optimizer()

    record_state()
```

### Output Format
```python
state_history = [
    {"timestep": 0, "grid": [...], "drones": [...]},
    {"timestep": 1, "grid": [...], "drones": [...]},
    ...
]
```

Visualization consumes this structure. Simulation and visualization are **decoupled**.

---

## Visualization

### Design Principle: Pac-Man Style Grid

| Element        | Meaning                  |
|----------------|--------------------------|
| Green cells    | Untreated                |
| Yellow cells   | In progress              |
| Gray cells     | Completed                |
| Red cells      | Failed / skipped         |
| Drone label    | Current position + ID    |

### Grid Representation
```python
# 0 = untouched, 1 = in progress, 2 = completed
grid = [
    [0, 0, 0, 0],
    [0, 1, 1, 0],
    [0, 2, 2, 0],
    [0, 0, 0, 0]
]
```

### Metrics Overlay
- % coverage complete
- Elapsed time
- Current makespan estimate
- Replanning events (logged + highlighted)

### Implementation Order
1. `matplotlib` animation (initial)
2. Streamlit or canvas-based UI (future)

---

## Planner Modes (Three-Tier, System Chooses)

The system must support three planner modes — not just "optimize or crash":

| Mode | When | How |
|---|---|---|
| **Full optimization** | Sufficient time, stable state | MILP, full horizon, all constraints |
| **Degraded optimization** | Time pressure, uncertainty high | Shorter horizon, relaxed constraints, skip unreliable drones |
| **Heuristic fallback** | Timeout, infeasible, emergency | Greedy nearest-strip, always feasible, always fast |

Currently implemented: full + heuristic. Degraded mode is a Phase 2 target.

---

## Key Metrics (Operator-Style, Not Academic)

Report these — not optimality gaps or solver objective values:

- **% mission completed** — primary outcome
- **Worst-case coverage loss** — how bad can it get?
- **Time to recovery after failure** — how fast does replanning restore progress?
- **Energy waste post-failure** — cost of the disruption
- **Variance across runs** — is the system predictable?

---

## Design Principles

- **Decouple everything:** `simulate(plan) → state_history`, `visualize(state_history)`
- **Interpretability first:** visualization must clearly answer what each drone is doing, what work remains, and what changed after failure
- **Speed over optimality:** must replan frequently; sub-optimal feasible beats slow optimal
- **Explicit fallbacks:** degradation must be predictable, not mysterious
- **Build core logic before UI complexity**

---

## What NOT to Do

- Do not build a full frontend early
- Do not tightly couple simulation and optimization
- Do not overcomplicate visuals before core logic works
- Do not over-engineer the MILP before the simulation loop is stable
- Do not use time-indexed formulations (state explosion)
- Do not embed routing inside the fleet optimizer (combine VRP + assignment = NP-hard, slow)
- Do not add detailed physics or wind to the optimizer (handle in simulation + replanning)
- Do not decentralize before the centralized version works
- Do not model transit speed separately from spray speed — simplification noted, not worth the complexity
- Do not use the Winnipeg cropland dataset (162MB radar-optical fusion, wrong tool for the job)

---

## Interview Framing

**What TO claim:**
- "I designed an autonomy system that survives optimization failure"
- "Here's how we bounded performance loss under real constraints"
- "Here's what operators actually care about — not optimality gaps"

**What NOT to claim:**
- "I built an optimal planner"
- "I solved agricultural drone routing"
- "My system outperforms all heuristics"

---

## Simulation Design Decisions (settled)

- **Drones start at dock(s):** all drones must begin at a dock grid coordinate, not mid-field
- **Return-to-dock on low battery:** drone flies back to nearest dock, recharges, then resumes — does not just pause in place. Replanner picks up remaining strips from dock position after recharge.
- **Infeasible MILP → heuristic fallback:** infeasible models do not crash; they silently fall back to greedy. Takes longer but always produces a plan.
- **Transit speed = spray speed:** simplification noted, not worth the added complexity for this project
- **Strip orientation:** strips must align with crop row direction (not always east-west). `orientation_deg` parameter needed in `generate_strips()`.
- **Visualization decoupled:** `simulate() → state_history`, `visualize(state_history)` — never mix

---

## Dataset Criteria (Phase 3)

Need 4–5 small aerial field images. What makes a good input:

**Must have:**
- Spatial variation (heterogeneous priority surface — not uniform)
- Visible crop row structure (so `orientation_deg` has real justification)
- Resolvable to ~64×64 after downsampling (256–2048px source range is fine)

**Nice to have:**
- Multiple distinct crop zones (varied priority, interesting assignment problem)
- Non-axis-aligned rows (validates orientation feature)
- Irregular field boundaries

**Best channel to use:** NDVI (if NIR available) or green channel from RGB. Both normalize to 0–1 and proxy vegetation density → spray priority directly.

**Rejected datasets:**
- Winnipeg cropland (pcbreviglieri/cropland-mapping): 162MB radar-optical fusion, wrong tool
- RGB/NIR Aerial Crop (masiaslahi): 27.95GB, too large
- Semantic Seg. of Aerial Imagery (humansintheloop): 31MB but urban Dubai, not cropland

**Best sources to check:**
- NAIP on AWS (free, no account, 4-band GeoTIFF, 1m resolution, US agricultural fields)
- Copernicus Browser (free account, NDVI tiles downloadable as PNG)
- Drone Mapper sample data (dronemapper.com/sample_data — has agricultural field sample)
- OpenAerialMap (map.openaerialmap.org — CC-BY 4.0, filter for agricultural)

---

## Future Enhancements

- Interactive controls (pause, rewind)
- Web-based UI (Streamlit or canvas)
- Multi-dock visualization
- Wind / weather uncertainty overlays
- MCP-based decision visualization

---

## Tech Stack

| Layer         | Library           |
|---------------|-------------------|
| Optimization  | PuLP / OR-Tools   |
| Simulation    | Python (custom)   |
| Visualization | matplotlib → Streamlit |
| Data          | pandas, numpy     |
| Notebooks     | Jupyter           |

---

## Implementation Phases

**Phase 1 — Core Loop** ✓ Done
- [x] Grid/strip generator from field_grid (boustrophedon)
- [x] MILP assignment model — makespan + weighted objective (toggleable)
- [x] Simulation loop with failure injection (battery, mechanical, comms)
- [x] Battery drain model (natural failures)
- [x] Replanning — MILP with greedy fallback
- [x] Metrics module (coverage %, priority coverage, makespan, replan count)
- [x] matplotlib animation
- [x] Jupyter walkthrough notebook

**Phase 2 — Degraded Planner + Operator Metrics** ✓ Done
- [x] Three-tier planner: Full MILP → Degraded MILP → Heuristic (`src/optimizer/planner.py`)
- [x] Auto mode selection from `PlannerContext` (time budget, uncertainty, battery threshold)
- [x] Battery-constrained MILP (`battery_capacity_seconds` hard cap; returns Infeasible correctly)
- [x] Time-to-recovery and coverage-at-failure metrics
- [x] Monte Carlo worst-case analysis (`monte_carlo_analysis()` in metrics.py)
- [x] Failure rate vs. coverage sensitivity sweep
- [x] Phase 2 Jupyter notebook

**Phase 3 — Real Data**
- [ ] Source 4–5 small aerial field images (see Dataset Criteria below)
- [ ] Build ingestion pipeline: image → priority grid (green channel or NDVI)
- [ ] Add `orientation_deg` parameter to `generate_strips()` — boustrophedon strips must align with visible crop row direction, not assumed east-west
- [ ] Map pixel intensity / NDVI to spray priority weights
- [ ] End-to-end run on real field geometry
- [ ] Benchmarking: MILP vs. greedy vs. degraded mode

**Phase 4 — Polish**
- [ ] MCP-style tool orchestration layer (expose optimizer/sim as callable tools)
- [ ] Streamlit dashboard
- [ ] GitHub README with GIF demo
- [ ] Write-up / blog post
