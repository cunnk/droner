"""
Three-tier planner — the core Phase 2 contribution.

The system chooses between three modes based on context:

    FULL       All drones, all strips, full solver budget.
               Best result, may be slow.

    DEGRADED   Filtered drones (battery threshold), limited strip horizon,
               short solver budget. Good-enough result, reliably fast.

    HEURISTIC  Greedy round-robin. Always feasible, always fast.
               Last resort.

The planner selects mode automatically from context, or the caller can force one.

Why this matters
----------------
Real replanning happens under time pressure. A system that can only choose
between "full MILP" and "crash" is brittle. The degraded mode is the buffer
that keeps the system functional when the optimizer is too slow or the state
is too uncertain.
"""

from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional

from src.field.generator import Strip
from src.optimizer.milp import AssignmentResult, DroneSpec, assign_strips


# ---------------------------------------------------------------------------
# Mode enum
# ---------------------------------------------------------------------------

class PlannerMode(str, Enum):
    FULL      = "full"
    DEGRADED  = "degraded"
    HEURISTIC = "heuristic"


# ---------------------------------------------------------------------------
# Context used to select mode automatically
# ---------------------------------------------------------------------------

@dataclass
class PlannerContext:
    """Runtime context the planner uses to choose its mode."""
    time_budget_seconds: float = 10.0   # wall-clock budget for this replan
    min_battery_pct: float     = 20.0   # exclude drones below this threshold
    horizon_strips: int        = 999    # max strips per drone in degraded mode
    uncertainty: float         = 0.0    # 0=confident, 1=very uncertain


def select_mode(
    n_strips: int,
    n_drones: int,
    context: PlannerContext,
) -> PlannerMode:
    """
    Heuristic mode selection.

    Rules (in priority order):
    1. No active drones → heuristic (will be empty anyway)
    2. No strips left   → heuristic
    3. High uncertainty OR very tight budget → degraded
    4. Problem large enough that MILP might be slow → degraded
    5. Otherwise → full

    These thresholds are intentionally conservative — the goal is that
    FULL mode is only used when we're confident it will return quickly.
    """
    if n_drones == 0 or n_strips == 0:
        return PlannerMode.HEURISTIC

    # High uncertainty → don't trust a long solve
    if context.uncertainty > 0.6:
        return PlannerMode.DEGRADED

    # Very short budget → skip full solve
    if context.time_budget_seconds < 2.0:
        return PlannerMode.HEURISTIC
    if context.time_budget_seconds < 10.0:
        return PlannerMode.DEGRADED

    # Large problem → probably slow
    if n_strips * n_drones > 200:
        return PlannerMode.DEGRADED

    return PlannerMode.FULL


# ---------------------------------------------------------------------------
# Main planner entry point
# ---------------------------------------------------------------------------

def plan(
    strips: List[Strip],
    drones: List[DroneSpec],
    drone_batteries: Optional[Dict[int, float]] = None,
    objective_mode: str = "makespan",
    priority_weight: float = 1.0,
    makespan_penalty: float = 0.5,
    battery_capacity_seconds: Optional[float] = None,
    mode: Optional[PlannerMode] = None,
    context: Optional[PlannerContext] = None,
) -> AssignmentResult:
    """
    Run the appropriate planner for the given context.

    Args:
        strips:                   Strips to assign.
        drones:                   All drone specs (may include low-battery drones).
        drone_batteries:          Current battery % per drone id. Used to filter
                                  drones in DEGRADED mode. If None, uses DroneSpec.battery.
        objective_mode:           "makespan" or "weighted".
        priority_weight:          Weighted mode reward coefficient.
        makespan_penalty:         Weighted mode makespan penalty.
        battery_capacity_seconds: Hard per-drone workload cap (seconds).
                                  Connects optimizer to simulation's battery model.
        mode:                     Force a specific mode. If None, auto-select.
        context:                  Context for auto-selection. Defaults to PlannerContext().

    Returns:
        AssignmentResult with mode recorded in objective_mode field.
    """
    if context is None:
        context = PlannerContext()

    batteries = {d.id: (drone_batteries or {}).get(d.id, d.battery) for d in drones}

    if mode is None:
        mode = select_mode(len(strips), len(drones), context)

    if mode == PlannerMode.FULL:
        return _full(
            strips, drones, objective_mode,
            priority_weight, makespan_penalty,
            battery_capacity_seconds,
            time_limit=context.time_budget_seconds,
        )

    if mode == PlannerMode.DEGRADED:
        return _degraded(
            strips, drones, batteries, objective_mode,
            priority_weight, makespan_penalty,
            battery_capacity_seconds,
            context,
        )

    return _heuristic(strips, drones)


# ---------------------------------------------------------------------------
# Mode implementations
# ---------------------------------------------------------------------------

def _full(
    strips, drones, objective_mode,
    priority_weight, makespan_penalty,
    battery_capacity_seconds, time_limit,
) -> AssignmentResult:
    return assign_strips(
        strips=strips,
        drones=drones,
        objective_mode=objective_mode,
        priority_weight=priority_weight,
        makespan_penalty=makespan_penalty,
        battery_capacity_seconds=battery_capacity_seconds,
        time_limit_seconds=time_limit,
    )


def _degraded(
    strips: List[Strip],
    drones: List[DroneSpec],
    batteries: Dict[int, float],
    objective_mode: str,
    priority_weight: float,
    makespan_penalty: float,
    battery_capacity_seconds: Optional[float],
    context: PlannerContext,
) -> AssignmentResult:
    """
    Degraded MILP:
    1. Exclude drones below battery threshold.
    2. Limit strips to a rolling horizon (highest priority first).
    3. Use a shorter solver budget.
    4. Fall back to heuristic if no drones survive the filter.
    """
    # Filter drones
    active = [d for d in drones if batteries.get(d.id, d.battery) >= context.min_battery_pct]
    if not active:
        return _heuristic(strips, drones)  # can't filter everyone out entirely

    # Limit horizon: take highest-priority strips up to horizon_strips
    horizon = context.horizon_strips
    if len(strips) > horizon * len(active):
        sorted_strips = sorted(strips, key=lambda s: s.priority, reverse=True)
        strips_to_plan = sorted_strips[:horizon * len(active)]
    else:
        strips_to_plan = strips

    # Shorter time budget for degraded solve
    degraded_limit = min(context.time_budget_seconds * 0.4, 8.0)

    result = assign_strips(
        strips=strips_to_plan,
        drones=active,
        objective_mode=objective_mode,
        priority_weight=priority_weight,
        makespan_penalty=makespan_penalty,
        battery_capacity_seconds=battery_capacity_seconds,
        time_limit_seconds=degraded_limit,
    )

    # If solver timed out or failed, fall back
    if result.status not in ("Optimal", "Feasible"):
        return _heuristic(strips_to_plan, active)

    # Ensure all drone ids are in the assignment (include filtered-out drones as empty)
    for d in drones:
        if d.id not in result.assignment:
            result.assignment[d.id] = []

    return result


def _heuristic(strips: List[Strip], drones: List[DroneSpec]) -> AssignmentResult:
    """
    Greedy round-robin: sort strips by priority descending, assign in turn.
    Always feasible. O(n) time.
    """
    import time
    t0 = time.perf_counter()

    sorted_strips = sorted(strips, key=lambda s: s.priority, reverse=True)
    assignment: Dict[int, List[int]] = {d.id: [] for d in drones}
    workloads: Dict[int, float] = {d.id: 0.0 for d in drones}

    for s in sorted_strips:
        # Assign to least-loaded drone
        d_id = min(workloads, key=workloads.__getitem__)
        assignment[d_id].append(s.id)
        workloads[d_id] += s.time

    makespan = max(workloads.values()) if workloads else 0.0

    return AssignmentResult(
        assignment=assignment,
        makespan=round(makespan, 2),
        objective_value=round(makespan, 2),
        objective_mode="heuristic",
        solve_time=round(time.perf_counter() - t0, 4),
        status="Heuristic",
    )
