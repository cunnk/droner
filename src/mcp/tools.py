"""
MCP tool implementations — plain Python functions.

Importable directly from notebooks or tests without starting the MCP server.
The MCP server (server.py) wraps these same functions as Claude-callable tools.

Separation of concerns:
  tools.py  — logic (what each tool does)
  server.py — protocol (how Claude calls them)
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.field.generator import generate_strips, synthetic_field
from src.field.ingest import load_image_grid
from src.optimizer.milp import DroneSpec
from src.optimizer.planner import plan, PlannerContext
from src.simulation.engine import simulate
from src.simulation.metrics import compute_metrics
from src.mcp import state_store, weather
from src.mcp.field_events import events_to_dicts

_CELL_COMPLETE = 2   # mirrors engine.CELL_COMPLETE without a circular import


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _strip_summary(strips) -> dict:
    if not strips:
        return {"count": 0}
    return {
        "count": len(strips),
        "total_time_s": round(sum(s.time for s in strips), 1),
        "avg_priority": round(sum(s.priority for s in strips) / len(strips), 3),
        "max_priority": round(max(s.priority for s in strips), 3),
        "min_priority": round(min(s.priority for s in strips), 3),
    }


def _result_summary(result) -> dict:
    return {
        "status": result.status,
        "makespan_s": round(result.makespan, 1),
        "objective_value": round(result.objective_value, 3),
        "objective_mode": result.objective_mode,
        "solve_time_s": round(result.solve_time, 3),
        "assignment": {str(k): v for k, v in result.assignment.items()},
    }


def _completed_strip_ids(state_history: list, strips: list) -> set:
    """Return IDs of strips whose cells are all CELL_COMPLETE in the final state."""
    if not state_history:
        return set()
    final_grid = state_history[-1]["grid"]
    complete = set()
    for strip in strips:
        if all(final_grid[r][c] == 2 for (r, c) in strip.cells):
            complete.add(strip.id)
    return complete


def _coverage_from_grid(grid) -> dict:
    total = sum(len(row) for row in grid)
    complete = sum(cell == 2 for row in grid for cell in row)
    in_prog = sum(cell == 1 for row in grid for cell in row)
    return {
        "coverage_pct": round(100.0 * complete / total, 1) if total else 0.0,
        "in_progress_cells": in_prog,
        "total_cells": total,
        "complete_cells": complete,
    }


# ---------------------------------------------------------------------------
# Tool functions
# ---------------------------------------------------------------------------

def load_field(
    image_path: str,
    target_size: int = 32,
    channel: str = "green",
    orientation_deg: float = 0.0,
    seconds_per_cell: float = 2.0,
) -> dict:
    """Load a field image, build priority grid, generate spray strips.

    Returns a run_id that must be passed to all subsequent tool calls.
    """
    grid, meta = load_image_grid(image_path, target_size=target_size, channel=channel)
    strips = generate_strips(
        grid,
        seconds_per_cell=seconds_per_cell,
        orientation_deg=orientation_deg,
    )
    nrows = len(grid)
    ncols = len(grid[0]) if grid else 0
    run_id = state_store.new_run(
        {
            "image_path": image_path,
            "grid": grid,
            "strips": strips,
            "nrows": nrows,
            "ncols": ncols,
            "meta": meta,
            "drones": None,
            "result": None,
            "state_history": None,
            "mandatory_strip_ids": [],
            "must_complete_strip_ids": [],
            "exclude_strip_ids": [],
            "planner_priority_weight": 0.5,
            "field_events": [],
        }
    )
    return {
        "run_id": run_id,
        "grid_shape": [nrows, ncols],
        "strips": _strip_summary(strips),
        "channel": channel,
        "orientation_deg": orientation_deg,
    }


def load_synthetic_field(
    nrows: int = 32,
    ncols: int = 32,
    n_patches: int = 6,
    seed: int = 42,
    seconds_per_cell: float = 2.0,
) -> dict:
    """Generate a synthetic field with random priority patches and create spray strips.

    Useful for demos and testing when real imagery isn't needed. The synthetic
    field tends to have higher priority variance than real images, making the
    priority-aware benchmark more dramatic.

    Returns a run_id for subsequent calls.
    """
    grid_array = synthetic_field(nrows=nrows, ncols=ncols, seed=seed, n_patches=n_patches)
    grid = grid_array.tolist()
    strips = generate_strips(grid, seconds_per_cell=seconds_per_cell)
    run_id = state_store.new_run(
        {
            "image_path": f"synthetic(seed={seed})",
            "grid": grid,
            "strips": strips,
            "nrows": nrows,
            "ncols": ncols,
            "meta": {"source": "synthetic", "seed": seed, "n_patches": n_patches},
            "drones": None,
            "result": None,
            "state_history": None,
            "mandatory_strip_ids": [],
            "must_complete_strip_ids": [],
            "exclude_strip_ids": [],
            "planner_priority_weight": 0.5,
            "field_events": [],
        }
    )
    return {
        "run_id": run_id,
        "grid_shape": [nrows, ncols],
        "strips": _strip_summary(strips),
        "source": "synthetic",
    }


def create_plan(
    run_id: str,
    n_drones: int = 3,
    battery: float = 100.0,
    time_limit_s: float = 10.0,
    objective_mode: str = "makespan",
) -> dict:
    """Assign strips to drones using the three-tier planner (MILP → degraded → heuristic).

    objective_mode: "makespan" (balanced) or "weighted" (priority-first).
    """
    session = state_store.get_run(run_id)
    drones = [DroneSpec(id=i, battery=battery, spray_capacity=100.0) for i in range(n_drones)]
    context = PlannerContext(time_budget_seconds=time_limit_s)
    result = plan(
        session["strips"],
        drones,
        context=context,
        objective_mode=objective_mode,
    )
    state_store.update_run(run_id, drones=drones, result=result)
    return {"run_id": run_id, "plan": _result_summary(result)}


def run_simulation(
    run_id: str,
    failure_events: Optional[List[dict]] = None,
    battery_drain_per_cell: float = 0.0,
    recharge_time_steps: int = 10,
    dock_position: Optional[List[int]] = None,
    max_timesteps: Optional[int] = None,
) -> dict:
    """Execute the simulation with the current plan. Stores state history in session.

    max_timesteps: hard stop — simulation ends at this timestep regardless of
    mission completion. Use this to enforce a storm deadline ("pencils down").
    """
    session = state_store.get_run(run_id)
    if session["result"] is None:
        raise ValueError("No plan found. Call create_plan before run_simulation.")
    dock_positions = [tuple(dock_position)] if dock_position else [(0, 0)]
    state_history = simulate(
        session["strips"],
        session["drones"],
        session["result"],
        session["nrows"],
        session["ncols"],
        failure_events=failure_events or [],
        battery_drain_per_cell=battery_drain_per_cell,
        recharge_time_steps=recharge_time_steps,
        dock_positions=dock_positions,
        max_timesteps=max_timesteps,
    )
    state_store.update_run(run_id, state_history=state_history)
    final = state_history[-1]["grid"] if state_history else []
    cov = _coverage_from_grid(final)
    return {
        "run_id": run_id,
        "timesteps": len(state_history),
        **cov,
    }


def run_weather_aware_simulation(
    run_id: str,
    notice_timestep: int,
    storm_timestep: int,
    dock_position: Optional[List[int]] = None,
) -> dict:
    """Run the simulation with a mid-mission weather-triggered priority replan.

    Timeline:
      t=0               : mission starts (identical to baseline up to notice_timestep)
      t=notice_timestep : weather alert fires — each drone's remaining queue is
                          reordered by priority so the most critical strips are
                          sprayed first in the time that remains.
      t=storm_timestep  : storm arrives, simulation stops ("pencils down").

    Use get_metrics() after this to compare priority_coverage against the baseline run.
    """
    session = state_store.get_run(run_id)
    if session["result"] is None:
        raise ValueError("No plan found. Call create_plan first.")

    mandatory_ids = session.get("mandatory_strip_ids", [])
    weather_event = [{
        "timestep": notice_timestep,
        "type": "weather_replan",
        "mandatory_strip_ids": set(mandatory_ids),
    }]
    dock_positions = [tuple(dock_position)] if dock_position else [(0, 0)]

    state_history = simulate(
        session["strips"],
        session["drones"],
        session["result"],
        session["nrows"],
        session["ncols"],
        failure_events=weather_event,
        dock_positions=dock_positions,
        max_timesteps=storm_timestep,
    )
    state_store.update_run(run_id, state_history=state_history)
    final = state_history[-1]["grid"] if state_history else []
    cov = _coverage_from_grid(final)
    return {
        "run_id": run_id,
        "timesteps": len(state_history),
        "notice_fired_at": notice_timestep,
        "storm_arrived_at": storm_timestep,
        **cov,
        "note": (
            f"Priority replan fired at t={notice_timestep}. "
            f"Simulation stopped at storm arrival t={storm_timestep}."
        ),
    }


def arm_mandatory_zones(
    run_id: str,
    n_strips: int = 6,
    strip_ids: Optional[List[int]] = None,
) -> dict:
    """Designate compliance / disease-priority strips that must be sprayed before the storm.

    If strip_ids is provided those are used directly; otherwise the top-n_strips
    by priority are selected.  Call this before run_simulation so the engine and
    metrics layer both know which strips are non-negotiable.
    """
    session = state_store.get_run(run_id)
    if strip_ids is not None:
        mandatory_ids = list(strip_ids)
    else:
        sorted_strips = sorted(session["strips"], key=lambda s: s.priority, reverse=True)
        mandatory_ids = [s.id for s in sorted_strips[:n_strips]]
    state_store.update_run(run_id, mandatory_strip_ids=mandatory_ids)
    mandatory_strips = [s for s in session["strips"] if s.id in set(mandatory_ids)]
    return {
        "run_id": run_id,
        "mandatory_count": len(mandatory_ids),
        "mandatory_strip_ids": mandatory_ids,
        "avg_priority": round(
            sum(s.priority for s in mandatory_strips) / len(mandatory_strips), 3
        ) if mandatory_strips else 0.0,
        "note": (
            f"{len(mandatory_ids)} compliance zones designated. "
            "These strips must be completed before storm arrival."
        ),
    }


def get_mandatory_strips(run_id: str) -> dict:
    """Return the mandatory compliance strips and their current completion status.

    Call this after a weather alert to understand what must be done before the storm.
    """
    session = state_store.get_run(run_id)
    mandatory_ids = set(session.get("mandatory_strip_ids", []))
    if not mandatory_ids:
        return {
            "run_id": run_id,
            "mandatory_count": 0,
            "note": "No mandatory zones armed. Call arm_mandatory_zones first.",
        }

    strip_map = {s.id: s for s in session["strips"]}
    history = session.get("state_history")

    completed_ids: set = set()
    if history:
        final_grid = history[-1]["grid"]
        for sid in mandatory_ids:
            s = strip_map.get(sid)
            if s and all(final_grid[r][c] == 2 for (r, c) in s.spray_cells):
                completed_ids.add(sid)

    remaining = [sid for sid in mandatory_ids if sid not in completed_ids]
    strips_info = [
        {"id": sid, "priority": round(strip_map[sid].priority, 3)}
        for sid in mandatory_ids
        if sid in strip_map
    ]
    strips_info.sort(key=lambda x: x["priority"], reverse=True)

    return {
        "run_id": run_id,
        "mandatory_count": len(mandatory_ids),
        "complete": len(completed_ids),
        "remaining": len(remaining),
        "remaining_strip_ids": remaining,
        "strips": strips_info,
        "note": (
            f"{len(remaining)} of {len(mandatory_ids)} mandatory zones still need spraying. "
            "Front-load these in the replan to ensure compliance before storm arrival."
        ) if remaining else "All mandatory zones already complete.",
    }


def arm_event_feed(run_id: str, events: List[dict]) -> dict:
    """Store a pre-generated field event timeline in the session.

    Events should be dicts with keys: timestep, event_type, row, col,
    description, severity.  Call this before run_simulation_until so
    Claude can poll get_field_events() between chunks.
    """
    state_store.update_run(run_id, field_events=events)
    return {
        "run_id": run_id,
        "events_armed": len(events),
        "first_event_at": events[0]["timestep"] if events else None,
        "last_event_at":  events[-1]["timestep"] if events else None,
    }


def get_field_events(
    run_id: str,
    since_timestep: int = 0,
    until_timestep: Optional[int] = None,
) -> dict:
    """Return field events that fired in [since_timestep, until_timestep].

    Call this after each simulation chunk to see what intelligence arrived.
    Then use find_strips_at() to map locations to strip IDs, and
    update_planner_params() + reoptimize() to respond.
    """
    session  = state_store.get_run(run_id)
    all_evts = session.get("field_events", [])
    filtered = [
        e for e in all_evts
        if e["timestep"] >= since_timestep
        and (until_timestep is None or e["timestep"] <= until_timestep)
    ]
    hint = (
        f"{len(filtered)} events in t=[{since_timestep}, "
        f"{until_timestep if until_timestep is not None else '∞'}]. "
        "For each event: call find_strips_at(row, col) to identify affected "
        "strips, then update_planner_params() and reoptimize()."
    ) if filtered else "No new field events in this window."
    return {
        "run_id":       run_id,
        "event_count":  len(filtered),
        "events":       filtered,
        "note":         hint,
    }


def find_strips_at(
    run_id: str,
    row: int,
    col: int,
    radius: int = 3,
) -> dict:
    """Find strips whose cells fall within Manhattan radius of (row, col).

    Use this to translate a field-event coordinate into actionable strip IDs
    before calling update_planner_params(must_complete_strip_ids=[...]).
    """
    session = state_store.get_run(run_id)
    nearby  = []
    for s in session["strips"]:
        min_dist = min(
            (abs(r - row) + abs(c - col) for (r, c) in s.cells),
            default=float("inf"),
        )
        if min_dist <= radius:
            nearby.append({
                "id":           s.id,
                "priority":     round(s.priority, 3),
                "min_distance": int(min_dist),
            })
    nearby.sort(key=lambda x: (x["min_distance"], -x["priority"]))
    return {
        "run_id":       run_id,
        "coordinate":   [row, col],
        "radius":       radius,
        "strips_found": len(nearby),
        "strips":       nearby[:10],
    }


def update_planner_params(
    run_id: str,
    priority_weight: Optional[float] = None,
    must_complete_strip_ids: Optional[List[int]] = None,
    exclude_strip_ids: Optional[List[int]] = None,
) -> dict:
    """Update optimization parameters that will be used by the next reoptimize() call.

    priority_weight          : 0 = pure makespan balance, 1 = pure priority coverage.
                               Raise this when field events flag critical zones.
    must_complete_strip_ids  : strips that must appear at the front of each drone's
                               queue in the next plan regardless of makespan balance.
    exclude_strip_ids        : strips to drop entirely (no-spray zones, already handled).

    Changes accumulate — calling twice merges lists rather than replacing them.
    Call reoptimize() after this to apply the new parameters.
    """
    session = state_store.get_run(run_id)
    updates: Dict[str, Any] = {}

    if priority_weight is not None:
        updates["planner_priority_weight"] = float(max(0.0, min(1.0, priority_weight)))

    if must_complete_strip_ids is not None:
        existing = set(session.get("must_complete_strip_ids", []))
        existing.update(must_complete_strip_ids)
        updates["must_complete_strip_ids"] = list(existing)

    if exclude_strip_ids is not None:
        existing = set(session.get("exclude_strip_ids", []))
        existing.update(exclude_strip_ids)
        updates["exclude_strip_ids"] = list(existing)

    state_store.update_run(run_id, **updates)
    session = state_store.get_run(run_id)  # re-read after update
    return {
        "run_id":              run_id,
        "priority_weight":     session.get("planner_priority_weight"),
        "must_complete_count": len(session.get("must_complete_strip_ids", [])),
        "exclude_count":       len(session.get("exclude_strip_ids", [])),
        "note": "Parameters updated. Call reoptimize() to apply to the mission plan.",
    }


def reoptimize(run_id: str) -> dict:
    """Re-run the three-tier optimizer on remaining strips using current session params.

    Identifies strips not yet complete in the grid, excludes any in exclude_strip_ids,
    then runs MILP (falling back to degraded / heuristic if needed).

    must_complete_strip_ids are front-loaded in every drone's queue after solving,
    guaranteeing they are sprayed before lower-priority work.

    Call run_simulation_until() after this to continue the mission with the new plan.
    """
    session = state_store.get_run(run_id)
    history = session.get("state_history")
    if not history:
        raise ValueError("No simulation data. Call run_simulation_until first.")

    grid      = history[-1]["grid"]
    strip_map = {s.id: s for s in session["strips"]}

    # Strips whose spray_cells are all CELL_COMPLETE — skip these
    completed_ids = {
        s.id for s in session["strips"]
        if all(grid[r][c] == _CELL_COMPLETE for (r, c) in s.spray_cells)
    }

    # Strips a drone is actively mid-spray — let it finish, don't reassign
    active_strip_ids = {
        d["current_strip"]
        for d in history[-1]["drones"]
        if d.get("current_strip") is not None
    }

    exclude_ids = set(session.get("exclude_strip_ids", []))

    remaining = [
        s for s in session["strips"]
        if s.id not in completed_ids
        and s.id not in active_strip_ids
        and s.id not in exclude_ids
    ]

    if not remaining:
        return {
            "run_id": run_id,
            "status": "nothing_to_reoptimize",
            "remaining": 0,
            "note": "All strips are complete, active, or excluded.",
        }

    priority_weight = float(session.get("planner_priority_weight", 0.5))
    objective_mode  = "weighted" if priority_weight > 0.3 else "makespan"
    context         = PlannerContext(time_budget_seconds=30.0)

    result = plan(
        remaining,
        session["drones"],
        context=context,
        objective_mode=objective_mode,
        priority_weight=priority_weight,
    )

    # Post-process: move must_complete + mandatory strips to front of each queue
    front_ids = (
        set(session.get("must_complete_strip_ids", []))
        | set(session.get("mandatory_strip_ids", []))
    )
    if front_ids:
        for d_id, strip_ids in result.assignment.items():
            front = [sid for sid in strip_ids if sid in front_ids]
            back  = [sid for sid in strip_ids if sid not in front_ids]
            result.assignment[d_id] = front + back

    state_store.update_run(run_id, result=result)

    assigned   = sum(len(v) for v in result.assignment.values())
    front_loaded = len(front_ids & {s.id for s in remaining})
    return {
        "run_id":            run_id,
        "planner_status":    result.status,
        "objective_mode":    objective_mode,
        "priority_weight":   priority_weight,
        "strips_remaining":  len(remaining),
        "strips_assigned":   assigned,
        "front_loaded":      front_loaded,
        "note": (
            f"{result.status} plan for {len(remaining)} remaining strips. "
            f"{front_loaded} must-complete/mandatory strips front-loaded. "
            "Call run_simulation_until() to continue."
        ),
    }


def run_simulation_until(
    run_id: str,
    until_timestep: int,
    dock_position: Optional[List[int]] = None,
) -> dict:
    """Run (or continue) the simulation until until_timestep.

    If the session already has state_history, resumes from the last recorded
    frame with the current plan (result.assignment).  Otherwise starts fresh.
    New frames are appended to the existing history so the full trajectory is
    preserved for visualisation and metrics.

    This is the engine of the chunked loop:
      run_simulation_until(run_id, NOTICE_T)   → first chunk
      ...Claude replans via reoptimize()...
      run_simulation_until(run_id, STORM_T)    → second chunk (resumes)
    """
    session = state_store.get_run(run_id)
    if session["result"] is None:
        raise ValueError("No plan. Call create_plan or reoptimize first.")

    dock_positions = [tuple(dock_position)] if dock_position else [(0, 0)]
    history        = session.get("state_history") or []
    initial_state  = history[-1] if history else None

    new_history = simulate(
        session["strips"],
        session["drones"],
        session["result"],
        session["nrows"],
        session["ncols"],
        dock_positions = dock_positions,
        max_timesteps  = until_timestep,
        initial_state  = initial_state,
    )

    combined = list(history) + new_history
    state_store.update_run(run_id, state_history=combined)

    final_grid = new_history[-1]["grid"] if new_history else (history[-1]["grid"] if history else [])
    cov = _coverage_from_grid(final_grid)
    return {
        "run_id":                run_id,
        "ran_until_timestep":    until_timestep,
        "frames_this_chunk":     len(new_history),
        "total_history_length":  len(combined),
        **cov,
    }


def get_state(run_id: str) -> dict:
    """Return coverage snapshot and drone states from the last simulation."""
    session = state_store.get_run(run_id)
    history = session.get("state_history")
    if not history:
        return {"run_id": run_id, "status": "no simulation run yet — call run_simulation first"}

    last = history[-1]
    cov = _coverage_from_grid(last["grid"])
    completed_ids = _completed_strip_ids(history, session["strips"])
    remaining = [
        {"id": s.id, "priority": round(s.priority, 3), "time_s": round(s.time, 1)}
        for s in session["strips"]
        if s.id not in completed_ids
    ]
    # Sort remaining by priority descending so Claude sees most important first
    remaining.sort(key=lambda x: x["priority"], reverse=True)

    return {
        "run_id": run_id,
        "timestep": last["timestep"],
        **cov,
        "drone_states": [
            {
                "id": d["id"],
                "state": d["state"],
                "battery_pct": round(d["battery"], 1),
                "position": list(d["position"]),
                "current_strip": d.get("current_strip"),
            }
            for d in last["drones"]
        ],
        "remaining_strips": remaining,
        "remaining_strip_count": len(remaining),
        "completed_strip_count": len(completed_ids),
    }


def get_metrics(run_id: str, deadline_timestep: Optional[int] = None) -> dict:
    """Compute operator metrics: coverage %, priority coverage, makespan, recovery time.

    deadline_timestep: if set, metrics are computed from the simulation state at that
    timestep rather than at mission completion. Use this to measure coverage *at storm
    arrival* rather than after all strips finish.
    """
    session = state_store.get_run(run_id)
    history = session.get("state_history")
    if not history:
        raise ValueError("No simulation data. Call run_simulation first.")
    if deadline_timestep is not None:
        history = history[: min(deadline_timestep + 1, len(history))]
    metrics = compute_metrics(
        history,
        session["strips"],
        session["nrows"],
        session["ncols"],
    )
    result = {k: round(v, 3) if isinstance(v, float) else v
              for k, v in metrics.items()
              if not isinstance(v, (list, dict))}

    # Mandatory coverage — check if each mandatory strip's spray_cells are all complete
    mandatory_ids = set(session.get("mandatory_strip_ids", []))
    if mandatory_ids:
        final_grid = history[-1]["grid"]
        strip_map = {s.id: s for s in session["strips"]}
        n_complete = sum(
            1 for sid in mandatory_ids
            if sid in strip_map
            and all(final_grid[r][c] == 2 for (r, c) in strip_map[sid].spray_cells)
        )
        result["mandatory_complete"] = n_complete
        result["mandatory_total"] = len(mandatory_ids)
        result["mandatory_coverage_pct"] = round(
            100.0 * n_complete / len(mandatory_ids), 1
        ) if mandatory_ids else 0.0

    return result


def get_weather_alert() -> dict:
    """Check for active weather alerts. Returns alert status and minutes remaining."""
    return weather.get_alert()


def arm_weather_alert(
    minutes_remaining: float,
    description: str = "Storm approaching — mission window closing",
) -> dict:
    """Arm a simulated weather alert. Claude should respond by calling replan_with_deadline."""
    weather.arm_alert(minutes_remaining, description)
    return {
        "armed": True,
        "minutes_remaining": minutes_remaining,
        "description": description,
        "time_budget_seconds": minutes_remaining * 60,
    }


def trim_plan_to_deadline(run_id: str, time_budget_seconds: float) -> dict:
    """Trim the existing plan's assignment to fit within a deadline, preserving current strip order.

    Unlike replan_with_deadline, this does NOT re-optimize or re-sort by priority —
    it just cuts each drone's strip list once the budget runs out. Use this to apply
    the same deadline to a makespan-optimized plan for a fair benchmark comparison.
    """
    session = state_store.get_run(run_id)
    if session["result"] is None:
        raise ValueError("No plan found. Call create_plan first.")
    strip_by_id = {s.id: s for s in session["strips"]}
    result = session["result"]
    for drone_id, strip_ids in result.assignment.items():
        kept, budget = [], time_budget_seconds
        for sid in strip_ids:  # preserve planner's original order
            if strip_by_id[sid].time <= budget:
                kept.append(sid)
                budget -= strip_by_id[sid].time
        result.assignment[drone_id] = kept
    state_store.update_run(run_id, result=result, state_history=None)
    assigned_count = sum(len(v) for v in result.assignment.values())
    return {
        "run_id": run_id,
        "deadline_seconds": time_budget_seconds,
        "strips_assigned": assigned_count,
        "strips_total": len(session["strips"]),
        "strips_deferred": len(session["strips"]) - assigned_count,
    }


def reorder_by_priority(run_id: str) -> dict:
    """Reorder each drone's assigned strips highest-priority first.

    Does NOT change which strips are assigned or how many — only their order.
    Both Run A and Run B attempt the same strips; the difference is sequence.
    Measure coverage at the deadline timestep (not mission end) to see the lift.

    Call this when a weather alert fires: by spraying high-value zones first,
    the fleet maximises priority coverage before the storm window closes.
    """
    session = state_store.get_run(run_id)
    if session["result"] is None:
        raise ValueError("No plan found. Call create_plan first.")
    strip_by_id = {s.id: s for s in session["strips"]}
    result = session["result"]
    for drone_id, strip_ids in result.assignment.items():
        result.assignment[drone_id] = sorted(
            strip_ids, key=lambda sid: strip_by_id[sid].priority, reverse=True
        )
    state_store.update_run(run_id, result=result, state_history=None)
    total = sum(len(v) for v in result.assignment.values())
    priorities = [strip_by_id[sid].priority
                  for ids in result.assignment.values() for sid in ids]
    return {
        "run_id": run_id,
        "strips_reordered": total,
        "top3_priorities": sorted(priorities, reverse=True)[:3],
        "note": (
            "Assignment unchanged — strips reordered priority-first per drone. "
            "Run run_simulation, then compare get_metrics(deadline_timestep=N) vs baseline."
        ),
    }


def prioritize_within_deadline(
    run_id: str,
    time_budget_seconds: float,
) -> dict:
    """Trim the existing plan to the deadline then reorder each drone's strips
    by priority (highest first).

    Critically, this does NOT change *which* strips are assigned — only their
    order. This makes the benchmark fair: Run A and Run B cover the same strips
    within the same deadline, but Run B tackles the most important ones first.

    Call this when a weather alert fires to ensure priority coverage is maximised
    without changing the overall workload distribution.
    """
    session = state_store.get_run(run_id)
    if session["result"] is None:
        raise ValueError("No plan found. Call create_plan first.")
    strip_by_id = {s.id: s for s in session["strips"]}
    result = session["result"]
    for drone_id, strip_ids in result.assignment.items():
        # Step 1: trim to deadline (same strips that Run A would keep)
        kept, budget = [], time_budget_seconds
        for sid in strip_ids:
            if strip_by_id[sid].time <= budget:
                kept.append(sid)
                budget -= strip_by_id[sid].time
        # Step 2: reorder by priority descending
        result.assignment[drone_id] = sorted(
            kept, key=lambda sid: strip_by_id[sid].priority, reverse=True
        )
    state_store.update_run(run_id, result=result, state_history=None)
    assigned_count = sum(len(v) for v in result.assignment.values())
    return {
        "run_id": run_id,
        "deadline_seconds": time_budget_seconds,
        "strips_assigned": assigned_count,
        "strips_total": len(session["strips"]),
        "strips_deferred": len(session["strips"]) - assigned_count,
        "note": (
            "Same strips as baseline plan, reordered priority-first within deadline. "
            "Call run_simulation to execute."
        ),
    }


def replan_with_deadline(
    run_id: str,
    time_budget_seconds: float,
    n_drones: int = 3,
    priority_weight: float = 2.0,
) -> dict:
    """Replan with a hard time cap per drone, optimizing for priority-weighted coverage.

    Uses the weighted objective so high-priority strips are assigned first.
    Call this when a weather alert is active to ensure the most important
    strips are sprayed before the storm arrives.

    After calling this, run run_simulation again to execute the new plan.
    """
    session = state_store.get_run(run_id)
    # Auto-create drones if create_plan hasn't been called yet
    if session["drones"] is None:
        drones = [DroneSpec(id=i, battery=100.0, spray_capacity=100.0) for i in range(n_drones)]
        state_store.update_run(run_id, drones=drones)
        session = state_store.get_run(run_id)
    context = PlannerContext(time_budget_seconds=5.0)  # MILP solver budget
    result = plan(
        session["strips"],
        session["drones"],
        context=context,
        objective_mode="weighted",
        priority_weight=priority_weight,
        battery_capacity_seconds=time_budget_seconds,
    )

    # Post-process: enforce the deadline hard cap regardless of planner mode.
    # The heuristic fallback ignores battery_capacity_seconds, and MILP may
    # round up. Trim each drone's strip list greedily by priority so only work
    # that genuinely fits within the deadline is assigned.
    strip_by_id = {s.id: s for s in session["strips"]}
    for drone_id, strip_ids in result.assignment.items():
        # Preserve priority order (weighted objective already sorts high→low)
        ordered = sorted(strip_ids, key=lambda sid: strip_by_id[sid].priority, reverse=True)
        kept, budget = [], time_budget_seconds
        for sid in ordered:
            if strip_by_id[sid].time <= budget:
                kept.append(sid)
                budget -= strip_by_id[sid].time
        result.assignment[drone_id] = kept

    state_store.update_run(run_id, result=result, state_history=None)
    assigned_count = sum(len(v) for v in result.assignment.values())
    total_strips = len(session["strips"])
    return {
        "run_id": run_id,
        "status": result.status,
        "deadline_seconds": time_budget_seconds,
        "makespan_s": round(result.makespan, 1),
        "strips_assigned": assigned_count,
        "strips_total": total_strips,
        "strips_deferred": total_strips - assigned_count,
        "note": (
            f"Priority-weighted plan created within {time_budget_seconds}s deadline. "
            "Call run_simulation to execute the deadline-aware mission."
        ),
    }
