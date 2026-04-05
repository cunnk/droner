"""
Metrics computed from a completed state_history.

All functions are pure: they read state_history and return plain dicts or
lists, with no side effects. Plot helpers are thin wrappers around matplotlib.

Scalar metrics
--------------
coverage_pct            : % of cells marked complete at end of run.
priority_coverage       : weighted coverage — sum over completed strips of
                          (strip.priority * cells_in_strip) / total_possible.
makespan                : total number of simulation steps taken.
replan_count            : how many replanning events occurred.
failed_drone_count      : drones that ended in a failed state.
efficiency              : coverage_pct / makespan (cells completed per step, normalised).
time_to_recovery        : steps from first failure to when coverage resumes growing.
                          None if no failure occurred.
coverage_at_failure     : coverage % at the moment of first failure.
                          None if no failure occurred.

Series metrics
--------------
cells_per_step          : time-series of cumulative completed cells.
battery_series          : per-drone battery % over time.

Monte Carlo
-----------
monte_carlo_analysis()  : run N simulations with random failures, return
                          distribution of coverage_pct and time_to_recovery.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

import numpy as np

from src.field.generator import Strip


# ---------------------------------------------------------------------------
# Summary metrics
# ---------------------------------------------------------------------------

def compute_metrics(
    state_history: List[Dict[str, Any]],
    strips: List[Strip],
    nrows: int,
    ncols: int,
) -> Dict[str, Any]:
    """
    Compute a summary metrics dict from a completed simulation run.

    Args:
        state_history: Output of simulate().
        strips:        Strip list used in the simulation.
        nrows, ncols:  Grid dimensions.

    Returns:
        Dict with scalar and series metrics.
    """
    if not state_history:
        return {}

    total_grid_cells = nrows * ncols
    final_grid = state_history[-1]["grid"]

    # Spray-cell denominator: only cells above spray threshold can ever be
    # marked complete.  Use spray_cells if available, fall back to cells.
    spray_cell_set: set = set()
    for s in strips:
        sc = getattr(s, "spray_cells", None) or s.cells
        spray_cell_set.update(tuple(xy) for xy in sc)
    total_spray_cells = len(spray_cell_set) if spray_cell_set else total_grid_cells

    # Coverage
    flat = [c for row in final_grid for c in row]
    complete_cells = sum(1 for c in flat if c == 2)
    failed_cells   = sum(1 for c in flat if c == 3)

    # Primary coverage: fraction of sprayable cells that got sprayed.
    # grid_coverage_pct kept for backward compat (always lower when threshold > 0).
    spray_coverage_pct = 100.0 * complete_cells / total_spray_cells
    grid_coverage_pct  = 100.0 * complete_cells / total_grid_cells
    # Canonical alias so existing callers of coverage_pct get the correct value.
    coverage_pct = spray_coverage_pct

    # Priority-weighted coverage against spray cells only.
    priority_score = 0.0
    max_priority_score = 0.0
    for s in strips:
        sc = list(getattr(s, "spray_cells", None) or s.cells)
        max_priority_score += s.priority * len(sc)
        done = sum(1 for r, c in sc if final_grid[r][c] == 2)
        priority_score += s.priority * done
    priority_coverage = priority_score / max_priority_score if max_priority_score > 0 else 0.0

    # Makespan and replanning
    makespan = state_history[-1]["timestep"] + 1
    replan_events = [
        s for s in state_history
        if s.get("event") and "Replanned" in s["event"]
    ]
    replan_count = len(replan_events)

    # Failed drones
    final_drones = state_history[-1]["drones"]
    failed_drone_count = sum(1 for d in final_drones if d["state"] == "failed")

    # Efficiency: spray cells completed per step.
    efficiency = (complete_cells / total_spray_cells) / makespan if makespan > 0 else 0.0

    # Time series
    cells_per_step = [
        sum(1 for row in s["grid"] for c in row if c == 2)
        for s in state_history
    ]

    battery_series: Dict[int, List[float]] = {}
    for state in state_history:
        for ds in state["drones"]:
            battery_series.setdefault(ds["id"], []).append(ds["battery"])

    # Time-to-recovery and coverage-at-failure (use spray denominator)
    failure_steps = [
        s["timestep"] for s in state_history
        if s.get("event") and any(
            kw in s["event"] for kw in ("failed", "depleted")
        )
    ]
    time_to_recovery = None
    coverage_at_failure = None

    if failure_steps:
        t_fail = failure_steps[0]
        coverage_at_failure = round(
            100.0 * cells_per_step[t_fail] / total_spray_cells, 2
        )
        # Recovery = first step after failure where coverage is strictly higher
        # than it was at the failure timestep (drones moving again)
        for t in range(t_fail + 1, len(cells_per_step)):
            if cells_per_step[t] > cells_per_step[t_fail]:
                time_to_recovery = t - t_fail
                break

    return {
        # Scalars
        "coverage_pct":         round(coverage_pct, 2),          # spray cells (correct)
        "spray_coverage_pct":   round(spray_coverage_pct, 2),    # explicit alias
        "grid_coverage_pct":    round(grid_coverage_pct, 2),     # vs full grid (for reference)
        "priority_coverage":    round(priority_coverage, 4),
        "makespan":             makespan,
        "replan_count":         replan_count,
        "failed_drone_count":   failed_drone_count,
        "complete_cells":       complete_cells,
        "failed_cells":         failed_cells,
        "total_cells":          total_grid_cells,                 # kept for compat
        "total_spray_cells":    total_spray_cells,
        "efficiency":           round(efficiency, 6),
        "time_to_recovery":     time_to_recovery,
        "coverage_at_failure":  coverage_at_failure,
        # Series
        "cells_per_step":       cells_per_step,
        "battery_series":       battery_series,
        "replan_steps":         [s["timestep"] for s in replan_events],
        "failure_steps":        failure_steps,
    }


def compare_runs(
    runs: Dict[str, Dict[str, Any]],
    keys: Optional[List[str]] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Compare scalar metrics across multiple named runs.

    Args:
        runs: {run_label: metrics_dict}
        keys: Which scalar metrics to include. Defaults to standard set.

    Returns:
        {metric_key: {run_label: value}}
    """
    if keys is None:
        keys = [
            "coverage_pct", "priority_coverage", "makespan",
            "replan_count", "failed_drone_count", "efficiency",
        ]
    result: Dict[str, Dict[str, Any]] = {k: {} for k in keys}
    for label, m in runs.items():
        for k in keys:
            result[k][label] = m.get(k)
    return result


# ---------------------------------------------------------------------------
# Plot helpers
# ---------------------------------------------------------------------------

def plot_coverage_over_time(
    runs: Dict[str, List[int]],
    total_cells: int,
    title: str = "Coverage over time",
    ax=None,
):
    """
    Line plot of % coverage vs timestep for one or more runs.

    Args:
        runs:        {label: cells_per_step list}
        total_cells: Used to convert cell counts to percentages.
        ax:          Optional existing matplotlib Axes.
    """
    import matplotlib.pyplot as plt

    if ax is None:
        _, ax = plt.subplots(figsize=(7, 4))

    for label, series in runs.items():
        pct = [100.0 * v / total_cells for v in series]
        ax.plot(pct, label=label)

    ax.set_xlabel("Timestep")
    ax.set_ylabel("Coverage (%)")
    ax.set_title(title)
    ax.legend()
    ax.grid(alpha=0.3)
    return ax


def plot_battery_over_time(
    battery_series: Dict[int, List[float]],
    replan_steps: Optional[List[int]] = None,
    title: str = "Battery over time",
    ax=None,
):
    """
    Line plot of battery % for each drone, with optional replan markers.
    """
    import matplotlib.pyplot as plt

    COLOURS = ["#1565c0", "#e65100", "#558b2f", "#6a1b9a", "#00838f"]

    if ax is None:
        _, ax = plt.subplots(figsize=(7, 4))

    for i, (d_id, series) in enumerate(battery_series.items()):
        ax.plot(series, label=f"Drone {d_id}", color=COLOURS[i % len(COLOURS)])

    if replan_steps:
        for step in replan_steps:
            ax.axvline(step, color="red", linestyle="--", alpha=0.5, linewidth=1)
        ax.axvline(replan_steps[0], color="red", linestyle="--",
                   alpha=0.5, linewidth=1, label="Replan event")

    ax.set_xlabel("Timestep")
    ax.set_ylabel("Battery (%)")
    ax.set_title(title)
    ax.legend()
    ax.grid(alpha=0.3)
    return ax


def monte_carlo_analysis(
    strips,
    drones,
    result,
    nrows: int,
    ncols: int,
    n_runs: int = 100,
    failure_prob_per_drone: float = 0.3,
    battery_drain_range: tuple = (0.0, 0.0),
    replan_objective: str = "makespan",
    seed: int = 0,
) -> Dict[str, Any]:
    """
    Run N simulations with randomly injected failures. Returns the distribution
    of key operator metrics — the primary tool for worst-case analysis.

    Args:
        strips:                   Strips used in simulation.
        drones:                   Drone fleet.
        result:                   Initial assignment plan.
        nrows, ncols:             Grid dimensions.
        n_runs:                   Number of Monte Carlo samples.
        failure_prob_per_drone:   Probability that each drone fails in a run.
        battery_drain_range:      (min, max) drain rate sampled uniformly per run.
                                  (0, 0) = no drain, failures only from injected events.
        replan_objective:         Objective mode for replanning.
        seed:                     RNG seed for reproducibility.

    Returns:
        Dict with:
            coverage_pct_mean / _std / _p5 / _p95
            time_to_recovery_mean / _p95  (None-safe)
            priority_coverage_mean / _p5
            n_runs
            all_coverage_pcts      (list, for histogram)
            all_recovery_times     (list, None filtered out)
    """
    from src.simulation.engine import simulate

    rng = np.random.default_rng(seed)
    coverage_pcts: List[float] = []
    makespans: List[int] = []
    recovery_times: List[int] = []
    priority_coverages: List[float] = []
    timed_out_runs: int = 0
    fleet_failed_runs: int = 0
    max_steps_cap = nrows * ncols * 20

    failure_types = ["battery", "mechanical"]

    for _ in range(n_runs):
        # Random failure injection
        failure_events = []
        for d in drones:
            if rng.random() < failure_prob_per_drone:
                # Pick a random timestep within the first 60% of expected makespan
                t_max = max(1, int(result.makespan * 0.6))
                t_fail = int(rng.integers(1, t_max + 1))
                ftype = rng.choice(failure_types)
                failure_events.append({
                    "timestep": t_fail,
                    "drone_id": d.id,
                    "type": ftype,
                })

        # Random battery drain
        drain = float(rng.uniform(*battery_drain_range)) if battery_drain_range[1] > 0 else 0.0

        hist = simulate(
            strips=strips,
            drones=drones,
            result=result,
            nrows=nrows,
            ncols=ncols,
            failure_events=failure_events,
            battery_drain_per_cell=drain,
            replan_objective=replan_objective,
        )
        m = compute_metrics(hist, strips, nrows, ncols)
        coverage_pcts.append(m["coverage_pct"])
        makespans.append(m["makespan"])
        priority_coverages.append(m["priority_coverage"])
        if m["time_to_recovery"] is not None:
            recovery_times.append(m["time_to_recovery"])

        # Classify how the run ended
        if len(hist) >= max_steps_cap:
            timed_out_runs += 1
        elif m["failed_drone_count"] == len(drones):
            fleet_failed_runs += 1

    cov = np.array(coverage_pcts)
    mks = np.array(makespans)
    pri = np.array(priority_coverages)
    rec = np.array(recovery_times) if recovery_times else np.array([np.nan])
    completed_runs = int((cov >= 99.9).sum())  # runs that hit ~100% spray coverage

    return {
        "n_runs":                   n_runs,
        # Coverage distribution (now correctly vs spray cells, not grid)
        "coverage_pct_mean":        round(float(cov.mean()), 2),
        "coverage_pct_std":         round(float(cov.std()), 2),
        "coverage_pct_p5":          round(float(np.percentile(cov, 5)), 2),
        "coverage_pct_p95":         round(float(np.percentile(cov, 95)), 2),
        # Makespan distribution — primary operator metric
        "makespan_mean":            round(float(mks.mean()), 1),
        "makespan_std":             round(float(mks.std()), 1),
        "makespan_p5":              int(np.percentile(mks, 5)),
        "makespan_p95":             int(np.percentile(mks, 95)),
        # Priority coverage
        "priority_coverage_mean":   round(float(pri.mean()), 4),
        "priority_coverage_p5":     round(float(np.percentile(pri, 5)), 4),
        # Recovery
        "time_to_recovery_mean":    round(float(np.nanmean(rec)), 2) if recovery_times else None,
        "time_to_recovery_p95":     round(float(np.nanpercentile(rec, 95)), 2) if recovery_times else None,
        # Run outcome classification
        "completion_rate":          round(100.0 * completed_runs / n_runs, 1),
        "timed_out_runs":           timed_out_runs,
        "fleet_failed_runs":        fleet_failed_runs,
        # Raw lists for custom plots
        "all_coverage_pcts":        coverage_pcts,
        "all_makespans":            makespans,
        "all_recovery_times":       recovery_times,
    }


def plot_monte_carlo(
    mc_result: Dict[str, Any],
    title: str = "Monte Carlo: Coverage Distribution",
    ax=None,
):
    """Histogram of coverage % across Monte Carlo runs with percentile markers."""
    import matplotlib.pyplot as plt

    # Two-panel layout: makespan distribution (primary) + spray coverage (secondary).
    if ax is None:
        fig, axes = plt.subplots(1, 2, figsize=(13, 4))
    else:
        # Caller supplied a single Axes — fall back to single-panel makespan only.
        axes = [ax, None]

    n    = mc_result["n_runs"]
    comp = mc_result.get("completion_rate", "?")
    to   = mc_result.get("timed_out_runs", 0)
    ff   = mc_result.get("fleet_failed_runs", 0)

    # --- Left: makespan histogram ---
    ax0 = axes[0]
    mks_data = mc_result.get("all_makespans", [])
    if mks_data:
        ax0.hist(mks_data, bins=20, color="#1565c0", alpha=0.75, edgecolor="white")
        ax0.axvline(mc_result["makespan_p5"],   color="red",    linestyle="--",
                    label=f'P5  = {mc_result["makespan_p5"]} steps')
        ax0.axvline(mc_result["makespan_mean"], color="orange", linestyle="--",
                    label=f'Mean = {mc_result["makespan_mean"]:.0f} steps')
        ax0.axvline(mc_result["makespan_p95"],  color="green",  linestyle="--",
                    label=f'P95 = {mc_result["makespan_p95"]} steps')
    subtitle = (f'{n} runs  |  completed={comp}%  |  '
                f'timed-out={to}  fleet-failed={ff}')
    ax0.set_xlabel("Makespan (steps to finish)")
    ax0.set_ylabel("Runs")
    ax0.set_title(f'{title}\n{subtitle}', fontsize=10)
    ax0.legend(fontsize=9)
    ax0.grid(alpha=0.3)

    # --- Right: spray coverage distribution ---
    if axes[1] is not None:
        ax1 = axes[1]
        cov_data = mc_result["all_coverage_pcts"]
        ax1.hist(cov_data, bins=20, color="#558b2f", alpha=0.75, edgecolor="white")
        ax1.axvline(mc_result["coverage_pct_p5"],   color="red",    linestyle="--",
                    label=f'P5  = {mc_result["coverage_pct_p5"]}%')
        ax1.axvline(mc_result["coverage_pct_mean"], color="orange", linestyle="--",
                    label=f'Mean = {mc_result["coverage_pct_mean"]}%')
        ax1.axvline(mc_result["coverage_pct_p95"],  color="green",  linestyle="--",
                    label=f'P95 = {mc_result["coverage_pct_p95"]}%')
        ax1.set_xlabel("Spray coverage (% of targetable cells)")
        ax1.set_ylabel("Runs")
        ax1.set_title("Coverage distribution\n(100% = all crop cells sprayed)", fontsize=10)
        ax1.legend(fontsize=9)
        ax1.grid(alpha=0.3)

    return axes[0]


def plot_metrics_bar(
    comparison: Dict[str, Dict[str, Any]],
    title: str = "Run comparison",
    ax=None,
):
    """
    Grouped bar chart comparing scalar metrics across runs.

    Args:
        comparison: Output of compare_runs().
    """
    import matplotlib.pyplot as plt
    import numpy as np

    metrics = list(comparison.keys())
    run_labels = list(next(iter(comparison.values())).keys())
    n_metrics = len(metrics)
    n_runs = len(run_labels)
    x = np.arange(n_metrics)
    width = 0.8 / n_runs

    if ax is None:
        _, ax = plt.subplots(figsize=(max(8, n_metrics * 2), 4))

    for i, label in enumerate(run_labels):
        vals = [comparison[m].get(label, 0) or 0 for m in metrics]
        ax.bar(x + i * width - (n_runs - 1) * width / 2, vals,
               width, label=label)

    ax.set_xticks(x)
    ax.set_xticklabels(metrics, rotation=20, ha="right")
    ax.set_title(title)
    ax.legend()
    ax.grid(axis="y", alpha=0.3)
    return ax
