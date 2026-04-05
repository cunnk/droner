"""
Simulation engine.

Drones start at their assigned dock and move one grid cell per timestep.
There is no teleportation — all transitions between strips or between
field and dock are physically animated.

States
------
idle       : no current task; will pick up next strip from queue.
moving     : flying to a strip entry point (no spraying).
spraying   : actively covering a strip, cell by cell.
frozen     : comms loss — stationary, resumes when countdown expires.
returning  : flying back to dock (battery depleted, recharge mode on).
charging   : sitting at dock, recharging (ticked separately from active).
failed     : permanent failure — drone is out for the rest of the mission.

Transit and strip entry
-----------------------
When a drone picks up a strip, it compares the distance from its current
position to both ends of the strip and enters from whichever end is closer.
It then traverses the strip in that direction. This prevents unnecessary
cross-field hops when the assignment happens to list strips in an order that
would otherwise require backtracking.

Dock behaviour
--------------
Pass dock_positions as a list of (row, col) grid coordinates.
Drones are assigned round-robin. All drones start at their dock.

When a battery-depleted drone has recharge enabled it enters "returning"
state and flies physically back to its dock, then recharges for
recharge_time_steps, then flies to the nearest entry of its next strip.

Battery
-------
Drains at battery_drain_per_cell % per cell traversed in any state
(spraying or transit). A drone already heading back to dock (returning)
does not trigger another failure on hitting 0 again.

Replanning
----------
Permanent failures trigger a MILP replan of unfinished strips across
remaining active drones. If the solver returns infeasible or times out
with no solution, a greedy least-workload fallback is used so strips
are never silently dropped.
"""

from __future__ import annotations
import copy
from typing import Any, Dict, List, Optional, Tuple

from src.field.generator import Strip
from src.optimizer.milp import AssignmentResult, DroneSpec, assign_strips


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DroneState = Dict[str, Any]
GridState  = List[List[int]]

CELL_UNTOUCHED  = 0
CELL_INPROGRESS = 1
CELL_COMPLETE   = 2
CELL_FAILED     = 3


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _blank_grid(nrows: int, ncols: int) -> GridState:
    return [[CELL_UNTOUCHED] * ncols for _ in range(nrows)]


def _manhattan(a: Tuple[int, int], b: Tuple[int, int]) -> int:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _step_toward(pos: Tuple[int, int], target: Tuple[int, int]) -> Tuple[int, int]:
    """One-cell step from pos toward target — row-first, then column."""
    r, c   = pos
    tr, tc = target
    if r < tr: return (r + 1, c)
    if r > tr: return (r - 1, c)
    if c < tc: return (r, c + 1)
    if c > tc: return (r, c - 1)
    return pos  # already there


def _choose_entry(
    position: Tuple[int, int],
    strip: Strip,
) -> Tuple[Tuple[int, int], List[Tuple[int, int]]]:
    """
    Choose which end of the strip to enter from.

    Returns (entry_cell, ordered_cells) where ordered_cells starts at
    entry_cell. If the far end is closer, the cell list is reversed so
    the drone traverses from far-to-near without backtracking.
    """
    if not strip.cells:
        return position, []
    start, end = strip.cells[0], strip.cells[-1]
    if _manhattan(position, end) < _manhattan(position, start):
        return end, list(reversed(strip.cells))
    return start, list(strip.cells)


def _assign_docks(
    drones: List[DroneSpec],
    dock_positions: List[Tuple[int, int]],
) -> Dict[int, Tuple[int, int]]:
    """Round-robin dock assignment."""
    return {d.id: dock_positions[i % len(dock_positions)] for i, d in enumerate(drones)}


# ---------------------------------------------------------------------------
# Simulation
# ---------------------------------------------------------------------------

def simulate(
    strips: List[Strip],
    drones: List[DroneSpec],
    result: AssignmentResult,
    nrows: int,
    ncols: int,
    failure_events: Optional[List[Dict[str, Any]]] = None,
    battery_drain_per_cell: float = 0.0,
    replan_objective: str = "makespan",
    replan_priority_weight: float = 1.0,
    replan_makespan_penalty: float = 0.5,
    replan_time_limit: float = 10.0,
    recharge_time_steps: int = 0,
    dock_positions: Optional[List[Tuple[int, int]]] = None,
    max_timesteps: Optional[int] = None,
    initial_state: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """
    Run the simulation and return a state_history list.

    Each entry:
        {
            "timestep" : int,
            "grid"     : GridState,
            "drones"   : [DroneState, ...],
            "event"    : str | None
        }

    Args:
        strips                : Strips to cover.
        drones                : Drone fleet specs.
        result                : Assignment plan from optimizer or planner.
        nrows, ncols          : Grid dimensions.
        failure_events        : Scripted failures:
                                  [{"timestep": t, "drone_id": d, "type": "battery"|"mechanical"|"comms"}]
        battery_drain_per_cell: % battery lost per cell traversed (spraying + transit).
                                0 = unlimited battery.
        replan_*              : Forwarded to the MILP replanner on failure.
        recharge_time_steps   : Steps to recharge at dock. 0 = permanent failure.
                                Only applies to battery failures; mechanical is always permanent.
        dock_positions        : (row, col) dock locations. Drones assigned round-robin.
                                Default: [(0, 0)].
        initial_state         : If provided, resume from a previous chunk's final
                                state_history frame (dict with "timestep", "grid",
                                "drones").  The new result.assignment replaces queues;
                                drone positions, battery, and in-progress strips are
                                restored from the frame.  stale held_strips are cleared
                                so drones pick from the new assignment after recharging.
    """
    dock_positions  = dock_positions or [(0, 0)]
    failure_events  = failure_events or []
    failure_index: Dict[int, List[Dict]] = {}
    for ev in failure_events:
        failure_index.setdefault(ev["timestep"], []).append(ev)

    strip_map   = {s.id: s for s in strips}
    drone_map   = {d.id: d for d in drones}

    # Queues always come from the (new) result.assignment
    queues: Dict[int, List[int]] = {
        d_id: list(strip_ids)
        for d_id, strip_ids in result.assignment.items()
    }

    if initial_state is not None:
        # ---- Resume from a saved chunk boundary ----
        grid = copy.deepcopy(initial_state["grid"])
        drone_states: Dict[int, DroneState] = {}
        for d_raw in initial_state["drones"]:
            ds = copy.deepcopy(d_raw)
            # Stale held_strips belong to the old plan — clear them so the
            # drone picks up from the new queue after it finishes recharging.
            ds["held_strips"] = []
            # Restore position as a tuple (JSON round-trips lists)
            ds["position"] = tuple(ds["position"])
            ds["dock"]     = tuple(ds["dock"])
            if ds.get("transit_target") is not None:
                ds["transit_target"] = tuple(ds["transit_target"])
            drone_states[ds["id"]] = ds
        active_drones: set = {
            d_id for d_id, ds in drone_states.items()
            if ds["state"] not in ("failed", "charging")
        }
        charging_drones: set = {
            d_id for d_id, ds in drone_states.items()
            if ds["state"] == "charging"
        }
        t = initial_state["timestep"] + 1
    else:
        # ---- Fresh start ----
        drone_docks = _assign_docks(drones, dock_positions)
        drone_states = {}
        for d in drones:
            dock = drone_docks[d.id]
            drone_states[d.id] = {
                "id"                : d.id,
                "position"          : dock,
                "dock"              : dock,
                "state"             : "idle",
                "current_strip"     : None,
                "current_strip_cells": [],
                "current_cell_index": 0,
                "transit_target"    : None,
                "transit_action"    : None,
                "battery"           : d.battery,
                "spray_capacity"    : d.spray_capacity,
                "frozen_remaining"  : 0,
                "recharge_countdown": 0,
                "held_strips"       : [],
            }
        grid            = _blank_grid(nrows, ncols)
        active_drones   = set(d.id for d in drones)
        charging_drones = set()
        t               = 0

    state_history: List[Dict[str, Any]] = []
    max_steps = max_timesteps if max_timesteps is not None else t + nrows * ncols * 20

    while t < max_steps:
        event_log: List[str] = []

        # --------------------------------------------------------------------
        # 1. Inject scripted failures / fleet-wide events
        # --------------------------------------------------------------------
        for ev in failure_index.get(t, []):
            ftype = ev.get("type", "battery")

            # --- Fleet-wide weather event (no drone_id) ---
            if ftype == "weather_replan":
                # Global pool reassignment: collect ALL queued strips from every
                # active drone, then greedily reassign using a position-aware
                # priority score.  Per-drone reordering can't fix a makespan
                # assignment that put a drone's best strips on the far side of
                # the field; global pooling can.
                #
                # Greedy loop: repeatedly pick the (drone, strip) pair with the
                # highest score = priority / (1 + λ × dist), assign that strip
                # to that drone, then advance the drone's effective position to
                # the strip exit so the next pick accounts for travel.
                lam = ev.get("lambda_dist", 1.0 / (nrows + ncols))
                mandatory_ids: set = ev.get("mandatory_strip_ids", set())

                # Collect pool and clear queues
                pool: List[int] = []
                for d_id in list(active_drones):
                    pool.extend(queues.get(d_id, []))
                    queues[d_id] = []
                remaining = list(pool)

                # Effective positions start at each drone's current position
                eff_pos = {d_id: drone_states[d_id]["position"]
                           for d_id in active_drones}
                new_queues: Dict[int, List[int]] = {d_id: [] for d_id in active_drones}

                while remaining:
                    best_score = -1.0
                    best_drone = None
                    best_strip = None
                    for d_id in active_drones:
                        pos = eff_pos[d_id]
                        for sid in remaining:
                            s = strip_map[sid]
                            dist = min(
                                _manhattan(pos, s.cells[0]),
                                _manhattan(pos, s.cells[-1]),
                            ) if s.cells else 0
                            base = s.priority * 1000.0 if sid in mandatory_ids else s.priority
                            score = base / (1.0 + lam * dist)
                            if score > best_score:
                                best_score = score
                                best_drone = d_id
                                best_strip  = sid
                    if best_drone is None:
                        break
                    new_queues[best_drone].append(best_strip)
                    remaining.remove(best_strip)
                    # Advance effective position to the exit end of the chosen strip
                    s = strip_map[best_strip]
                    if s.cells:
                        p = eff_pos[best_drone]
                        eff_pos[best_drone] = (
                            s.cells[-1] if _manhattan(p, s.cells[0]) <= _manhattan(p, s.cells[-1])
                            else s.cells[0]
                        )

                for d_id in active_drones:
                    queues[d_id] = new_queues[d_id]

                event_log.append(
                    f"Weather alert: global pool reassignment ({len(pool)} strips, "
                    f"{len(active_drones)} drones, priority/distance greedy)"
                )
                continue

            # --- Per-drone failure events ---
            d_id  = ev["drone_id"]
            if d_id not in active_drones:
                continue

            if ftype in ("battery", "mechanical"):
                if ftype == "battery" and recharge_time_steps > 0:
                    _initiate_return(d_id, drone_states[d_id], queues, event_log,
                                     "injected battery failure")
                else:
                    _fail_drone(d_id, drone_states, queues, active_drones,
                                strip_map, drone_map, grid, event_log,
                                replan_objective, replan_priority_weight,
                                replan_makespan_penalty, replan_time_limit, ftype)

            elif ftype == "comms":
                freeze = ev.get("duration", 3)
                ds = drone_states[d_id]
                ds["frozen_remaining"] = freeze
                ds["state"]            = "frozen"
                event_log.append(f"Drone {d_id} comms loss ({freeze} steps)")

        # --------------------------------------------------------------------
        # 2. Tick recharging drones
        # --------------------------------------------------------------------
        for d_id in list(charging_drones):
            ds = drone_states[d_id]
            ds["recharge_countdown"] -= 1
            if ds["recharge_countdown"] > 0:
                continue

            # Recharge complete — restore battery and return to field
            ds["battery"] = 100.0
            charging_drones.discard(d_id)
            active_drones.add(d_id)

            if ds["held_strips"]:
                next_sid = ds["held_strips"].pop(0)
                # Merge any leftover held strips back into the queue
                queues[d_id] = ds["held_strips"] + queues.get(d_id, [])
                ds["held_strips"] = []

                strip = strip_map[next_sid]
                entry, ordered_cells = _choose_entry(ds["position"], strip)
                ds["current_strip"]        = next_sid
                ds["current_strip_cells"]  = ordered_cells
                ds["current_cell_index"]   = 0
                ds["transit_target"]       = entry
                ds["transit_action"]       = "spray"
                ds["state"]                = "moving"
                event_log.append(f"Drone {d_id} recharged — flying to strip {next_sid}")
            else:
                ds["state"] = "idle"
                event_log.append(f"Drone {d_id} recharged — idle")

        # --------------------------------------------------------------------
        # 3. Advance each active drone one step
        # --------------------------------------------------------------------
        all_done = True

        for d_id in list(active_drones):
            ds    = drone_states[d_id]
            state = ds["state"]

            # ---- Frozen ----
            if state == "frozen":
                ds["frozen_remaining"] -= 1
                if ds["frozen_remaining"] <= 0:
                    ds["state"] = "spraying"
                all_done = False
                continue

            # ---- Moving to strip entry or returning to dock ----
            if state in ("moving", "returning"):
                new_pos = _step_toward(ds["position"], ds["transit_target"])
                ds["position"] = new_pos

                # Drain battery during transit (but not for a drone already
                # heading home — battery is already 0)
                if battery_drain_per_cell > 0 and state != "returning":
                    ds["battery"] = max(0.0, ds["battery"] - battery_drain_per_cell)
                    if ds["battery"] <= 0:
                        if recharge_time_steps > 0:
                            _initiate_return(d_id, ds, queues, event_log,
                                             "battery out during transit")
                        else:
                            _fail_drone(d_id, drone_states, queues, active_drones,
                                        strip_map, drone_map, grid, event_log,
                                        replan_objective, replan_priority_weight,
                                        replan_makespan_penalty, replan_time_limit,
                                        "battery")
                        all_done = False
                        continue

                if new_pos == ds["transit_target"]:
                    # Arrived
                    if ds["transit_action"] == "charge":
                        ds["state"]              = "charging"
                        ds["recharge_countdown"] = recharge_time_steps
                        active_drones.discard(d_id)
                        charging_drones.add(d_id)
                        event_log.append(f"Drone {d_id} at dock — charging")
                    elif ds["transit_action"] == "spray":
                        ds["state"] = "spraying"
                    ds["transit_target"] = None
                    ds["transit_action"] = None

                all_done = False
                continue

            # ---- Idle — pick up next strip ----
            if state == "idle":
                if not queues[d_id]:
                    continue  # nothing to do

                next_sid = queues[d_id].pop(0)
                strip    = strip_map[next_sid]
                entry, ordered_cells = _choose_entry(ds["position"], strip)

                ds["current_strip"]        = next_sid
                ds["current_strip_cells"]  = ordered_cells
                ds["current_cell_index"]   = 0

                if ds["position"] == entry:
                    ds["state"] = "spraying"
                else:
                    ds["transit_target"] = entry
                    ds["transit_action"] = "spray"
                    ds["state"]          = "moving"

                all_done = False
                continue

            # ---- Spraying ----
            if state == "spraying":
                cells = ds["current_strip_cells"]
                ci    = ds["current_cell_index"]

                if ci < len(cells):
                    r, c = cells[ci]
                    # Only activate the sprayer on cells that are in the strip's
                    # spray zone; sub-threshold / out-of-mask cells are traversed
                    # but left untouched in the grid.
                    spray_set = set(strip_map[ds["current_strip"]].spray_cells)
                    if (r, c) in spray_set:
                        grid[r][c] = CELL_INPROGRESS
                    ds["position"]           = (r, c)
                    ds["current_cell_index"] = ci + 1

                    # Drain battery
                    if battery_drain_per_cell > 0:
                        ds["battery"] = max(0.0, ds["battery"] - battery_drain_per_cell)
                        if ds["battery"] <= 0:
                            if recharge_time_steps > 0:
                                # Hold this strip (restart from top on return)
                                held = [ds["current_strip"]] + list(queues.get(d_id, []))
                                ds["held_strips"]          = held
                                queues[d_id]               = []
                                ds["current_strip"]        = None
                                ds["current_strip_cells"]  = []
                                ds["current_cell_index"]   = 0
                                _initiate_return(d_id, ds, queues, event_log,
                                                 "battery depleted mid-spray")
                            else:
                                # Mark remaining spray cells failed (skip non-spray cells)
                                remaining_spray = spray_set & {(r2, c2) for r2, c2 in cells[ci + 1:]}
                                for r2, c2 in remaining_spray:
                                    if grid[r2][c2] != CELL_COMPLETE:
                                        grid[r2][c2] = CELL_FAILED
                                _fail_drone(d_id, drone_states, queues, active_drones,
                                            strip_map, drone_map, grid, event_log,
                                            replan_objective, replan_priority_weight,
                                            replan_makespan_penalty, replan_time_limit,
                                            "battery")
                            all_done = False
                            continue

                    all_done = False

                else:
                    # Strip finished — mark spray cells complete, leave non-spray cells untouched
                    spray_set = set(strip_map[ds["current_strip"]].spray_cells)
                    for r, c in cells:
                        if (r, c) in spray_set and grid[r][c] != CELL_FAILED:
                            grid[r][c] = CELL_COMPLETE
                    ds["current_strip"]        = None
                    ds["current_strip_cells"]  = []
                    ds["current_cell_index"]   = 0
                    ds["state"]                = "idle"
                    if queues[d_id]:
                        all_done = False

        # --------------------------------------------------------------------
        # 4. Record state
        # --------------------------------------------------------------------
        state_history.append({
            "timestep": t,
            "grid"    : copy.deepcopy(grid),
            "drones"  : copy.deepcopy(list(drone_states.values())),
            "event"   : "; ".join(event_log) if event_log else None,
        })

        t += 1

        # Terminate when all active drones are idle with empty queues
        # and no drones are recharging
        active_idle  = all(drone_states[d]["state"] == "idle" for d in active_drones)
        queues_empty = not any(queues.get(d) for d in active_drones)
        if active_idle and queues_empty and not charging_drones:
            break

    return state_history


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _initiate_return(
    d_id: int,
    ds: DroneState,
    queues: Dict[int, List[int]],
    event_log: List[str],
    reason: str,
) -> None:
    """
    Send a drone back to its dock for recharging.

    Strips are held on the drone (not redistributed). After recharging
    the drone will fly to the nearest entry of its first held strip.
    Note: held_strips may already be populated (set by caller for mid-spray
    departures); this function does not overwrite a non-empty held_strips.
    """
    ds["battery"] = 0.0

    # Collect remaining work if caller hasn't already set held_strips
    if not ds["held_strips"]:
        held = []
        if ds["current_strip"] is not None:
            held.append(ds["current_strip"])
        held.extend(queues.get(d_id, []))
        ds["held_strips"]         = held
        queues[d_id]              = []
        ds["current_strip"]       = None
        ds["current_strip_cells"] = []
        ds["current_cell_index"]  = 0

    ds["transit_target"] = ds["dock"]
    ds["transit_action"] = "charge"
    ds["state"]          = "returning"
    event_log.append(f"Drone {d_id} returning to dock ({reason})")


def _fail_drone(
    d_id: int,
    drone_states: Dict[int, DroneState],
    queues: Dict[int, List[int]],
    active_drones: set,
    strip_map: Dict[int, Strip],
    drone_map: Dict[int, DroneSpec],
    grid: GridState,
    event_log: List[str],
    objective_mode: str,
    priority_weight: float,
    makespan_penalty: float,
    time_limit: float,
    ftype: str,
) -> None:
    """Permanently remove a drone and replan its remaining strips."""
    ds = drone_states[d_id]
    ds["state"] = "failed"
    active_drones.discard(d_id)

    # Mark unvisited spray cells of current strip as failed
    # (non-spray cells are never marked — they stay untouched)
    cells = ds.get("current_strip_cells", [])
    ci    = ds.get("current_cell_index", 0)
    if ds.get("current_strip") is not None:
        spray_set = set(strip_map[ds["current_strip"]].spray_cells)
    else:
        spray_set = set()
    for r, c in cells[ci:]:
        if (r, c) in spray_set and grid[r][c] != CELL_COMPLETE:
            grid[r][c] = CELL_FAILED

    event_log.append(f"Drone {d_id} failed ({ftype})")

    # Collect all remaining work (queue + current strip + held strips)
    remaining_ids: List[int] = []
    if ds["current_strip"] is not None:
        remaining_ids.append(ds["current_strip"])
    remaining_ids.extend(queues.get(d_id, []))
    remaining_ids.extend(ds.get("held_strips", []))
    queues[d_id]      = []
    ds["held_strips"] = []

    remaining = [strip_map[sid] for sid in remaining_ids]
    if remaining and active_drones:
        _replan(remaining, list(active_drones), drone_map, strip_map,
                queues, event_log, objective_mode, priority_weight,
                makespan_penalty, time_limit)


def _replan(
    remaining: List[Strip],
    active_drone_ids: List[int],
    drone_map: Dict[int, DroneSpec],
    strip_map: Dict[int, Strip],
    queues: Dict[int, List[int]],
    event_log: List[str],
    objective_mode: str,
    priority_weight: float,
    makespan_penalty: float,
    time_limit: float,
) -> None:
    """Re-optimise remaining strips. Falls back to greedy if MILP fails."""
    active_drones = [drone_map[d_id] for d_id in active_drone_ids]
    try:
        new_result = assign_strips(
            strips=remaining,
            drones=active_drones,
            objective_mode=objective_mode,
            priority_weight=priority_weight,
            makespan_penalty=makespan_penalty,
            time_limit_seconds=time_limit,
        )
        has_assignment = any(ids for ids in new_result.assignment.values())
        if not has_assignment:
            raise ValueError(f"MILP returned {new_result.status} with no assignment")
        for d_id, strip_ids in new_result.assignment.items():
            queues[d_id].extend(strip_ids)
        event_log.append(
            f"Replanned {len(remaining)} strips ({new_result.status}, "
            f"{new_result.solve_time:.2f}s)"
        )
    except Exception as exc:
        # Greedy fallback: assign strips by priority to least-loaded drone
        workloads = {
            d_id: sum(strip_map[s].time for s in queues.get(d_id, []))
            for d_id in active_drone_ids
        }
        for s in sorted(remaining, key=lambda s: s.priority, reverse=True):
            d_id = min(workloads, key=workloads.__getitem__)
            queues[d_id].append(s.id)
            workloads[d_id] += s.time
        event_log.append(f"Replan failed ({exc}); greedy fallback used")
