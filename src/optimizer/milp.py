"""
MILP-based strip assignment for drone fleets.

Objective modes
---------------
makespan   : minimize the maximum drone workload (classic makespan minimisation).
weighted   : maximize priority-weighted coverage minus a penalty on makespan.
             Useful when some strips matter more than others and full coverage
             is not guaranteed (e.g. battery constraints force triage).

Toggle via objective_mode argument. Both share the same constraint structure.
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import pulp

from src.field.generator import Strip


@dataclass
class DroneSpec:
    id: int
    battery: float = 100.0        # percentage (0-100)
    spray_capacity: float = 100.0 # percentage (0-100) -- spraying mode
    seed_capacity: int = 6000     # seeds per voyage   -- reforestation mode


@dataclass
class AssignmentResult:
    assignment: Dict[int, List[int]]   # drone_id -> [strip_ids]
    makespan: float                    # seconds
    objective_value: float
    objective_mode: str
    solve_time: float                  # wall-clock seconds
    status: str                        # "Optimal" | "Feasible" | "Infeasible" | ...


def assign_strips(
    strips: List[Strip],
    drones: List[DroneSpec],
    objective_mode: str = "makespan",
    priority_weight: float = 1.0,
    makespan_penalty: float = 0.5,
    time_limit_seconds: float = 30.0,
    battery_capacity_seconds: Optional[float] = None,
) -> AssignmentResult:
    """
    Assign strips to drones via MILP.

    Args:
        strips:                   Strips to assign.
        drones:                   Available drones.
        objective_mode:           "makespan" or "weighted".
        priority_weight:          (weighted mode) reward per unit of priority*time covered.
        makespan_penalty:         (weighted mode) cost per second of makespan.
        time_limit_seconds:       Solver wall-clock budget. If exceeded, best feasible
                                  solution is returned (graceful degradation).
        battery_capacity_seconds: Max workload per drone. None = unlimited.

    Returns:
        AssignmentResult with assignment dict and metadata.
    """
    if objective_mode not in ("makespan", "weighted"):
        raise ValueError(f"objective_mode must be 'makespan' or 'weighted', got {objective_mode!r}")

    prob = pulp.LpProblem("DroneStripAssignment", pulp.LpMinimize)

    D = [d.id for d in drones]
    K = [s.id for s in strips]
    strip_map = {s.id: s for s in strips}

    # --- Decision variables ---
    # x[d, k] = 1 if drone d covers strip k
    x = {
        (d, k): pulp.LpVariable(f"x_{d}_{k}", cat="Binary")
        for d in D for k in K
    }
    # T_d = workload of drone d (seconds)
    T = {d: pulp.LpVariable(f"T_{d}", lowBound=0) for d in D}
    # T_max = maximum workload
    T_max = pulp.LpVariable("T_max", lowBound=0)

    # --- Constraints ---
    # 1. Each strip assigned to exactly one drone
    for k in K:
        prob += pulp.lpSum(x[d, k] for d in D) == 1, f"cover_{k}"

    # 2. Workload definition
    for d in D:
        prob += T[d] == pulp.lpSum(strip_map[k].time * x[d, k] for k in K), f"workload_{d}"

    # 3. T_max dominates all workloads
    for d in D:
        prob += T[d] <= T_max, f"makespan_{d}"

    # 4. Optional battery constraint
    if battery_capacity_seconds is not None:
        for d in D:
            prob += T[d] <= battery_capacity_seconds, f"battery_{d}"

    # --- Objective ---
    if objective_mode == "makespan":
        prob += T_max

    else:  # weighted
        # Maximize priority coverage, penalise makespan.
        # PuLP minimises, so negate the reward term.
        priority_reward = pulp.lpSum(
            priority_weight * strip_map[k].priority * strip_map[k].time * x[d, k]
            for d in D for k in K
        )
        prob += makespan_penalty * T_max - priority_reward

    # --- Solve ---
    solver = pulp.PULP_CBC_CMD(
        timeLimit=time_limit_seconds,
        msg=0,
    )
    t0 = time.perf_counter()
    prob.solve(solver)
    solve_time = time.perf_counter() - t0

    status = pulp.LpStatus[prob.status]

    # --- Extract solution ---
    assignment: Dict[int, List[int]] = {d: [] for d in D}
    if prob.status in (pulp.constants.LpStatusOptimal, pulp.constants.LpStatusNotSolved):
        for d in D:
            for k in K:
                if pulp.value(x[d, k]) is not None and pulp.value(x[d, k]) > 0.5:
                    assignment[d].append(k)

    makespan = pulp.value(T_max) or 0.0
    obj = pulp.value(prob.objective) or 0.0

    return AssignmentResult(
        assignment=assignment,
        makespan=round(makespan, 2),
        objective_value=round(obj, 4),
        objective_mode=objective_mode,
        solve_time=round(solve_time, 3),
        status=status,
    )
