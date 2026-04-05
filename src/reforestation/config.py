"""
Mission configuration for reforestation drone operations.

MissionConfig
-------------
Single dataclass that centralises all user-facing parameters.  Call
``compute_sim_params(config, ncols)`` to derive the low-level kwargs that the
simulation engine and metrics module expect.

Usage
-----
    from src.reforestation.config import mangrove_preset, compute_sim_params

    cfg  = mangrove_preset()               # Distant Imagery / Abu Dhabi defaults
    cfg.seed_spacing_m = 1.0              # tighter planting density
    cfg.wind_speed_ms  = 5.0              # 5 m/s headwind penalty

    params = compute_sim_params(cfg, ncols=64)
    # params is a flat dict ready to unpack into simulate() and DroneSpec(...)

Seed dispersal rate
-------------------
The user specifies a desired *average spacing* between seeds (in metres).
We convert this to a per-cell seed count:

    seeds_per_cell = cell_area_m² / seed_spacing_m²

So:
  spacing = 0.5 m  →  cell 1 m² → 4 seeds / cell
  spacing = 1.5 m  →  cell 1 m² → 0.44 seeds / cell  (≈ 1 seed every 2–3 cells)
  spacing = 3.0 m  →  cell 1 m² → 0.11 seeds / cell  (≈ 1 seed every 9 m²)

The formula naturally handles fractional seeds — the simulation accumulates a
running seed counter and drops a seed whenever it crosses an integer threshold,
giving the correct *average* density without requiring sub-cell precision.

Battery model
-------------
Battery life is given as total flight minutes (30–45 min typical).  We convert
to the per-cell drain percentage used by the simulation engine:

    battery_drain_per_cell = 100% / (battery_life_minutes × 60 s/min / seconds_per_cell)

Wind adds a multiplicative penalty:  multiplier = 1 + 0.015 × wind_speed_ms
(rough approximation; 10 m/s headwind ≈ +15 % drain).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# MissionConfig
# ---------------------------------------------------------------------------

@dataclass
class MissionConfig:
    """All user-facing parameters for a reforestation drone mission.

    Fields are intentionally kept in real-world units (metres, minutes,
    seconds) so operators can reason about them directly.  The helper
    ``compute_sim_params()`` handles all unit conversions for the engine.
    """

    # ------------------------------------------------------------------
    # Seed parameters
    # ------------------------------------------------------------------
    seed_capacity: int = 6000
    """Seeds carried per drone voyage before returning to dock to refill."""

    seed_spacing_m: float = 1.5
    """Desired average distance between planted seeds on the ground (metres).
    Valid range: 0.5 m (dense) – 3.0 m (sparse).
    Formula: seeds/m² = 1 / seed_spacing_m²."""

    seed_jitter_sigma: float = 0.3
    """Standard deviation of the Gaussian positional jitter applied to each
    recorded seed drop, expressed in grid cells.  Gives the seed cloud a
    natural, scattered appearance rather than a perfect grid pattern.
    Set to 0.0 to disable jitter (seeds land exactly at cell centres)."""

    # ------------------------------------------------------------------
    # Field scale
    # ------------------------------------------------------------------
    field_width_m: float = 100.0
    """Approximate real-world width of the field / image in metres.
    Used to derive metres_per_cell = field_width_m / ncols.
    Tip: estimate from flight altitude and known camera FOV, or use a
    reference object visible in the image."""

    # ------------------------------------------------------------------
    # Battery & recharge
    # ------------------------------------------------------------------
    battery_life_minutes: float = 35.0
    """Total usable flight time on a full charge (minutes).  30–45 min is
    typical for agricultural / reforestation drones in calm conditions.
    Wind increases effective drain (see wind_speed_ms)."""

    recharge_time_seconds: float = 3600.0
    """Time to fully recharge at dock (seconds).  Default 3600 s = 1 hour."""

    # ------------------------------------------------------------------
    # Environmental
    # ------------------------------------------------------------------
    wind_speed_ms: float = 0.0
    """Ambient wind speed (m/s).  Applied as a battery drain multiplier:
    multiplier = 1.0 + 0.015 × wind_speed_ms
    0 m/s = calm, 5 m/s ≈ +7.5 %, 10 m/s ≈ +15 % extra drain."""

    tidal_threshold: float = 0.6
    """Cells with tidal / moisture value above this threshold are considered
    submerged and excluded from planting.  Scale is 0–1 (0 = dry, 1 = fully
    inundated).  Used by apply_tidal_mask() in soil_detector.py."""

    # ------------------------------------------------------------------
    # Simulation timing
    # ------------------------------------------------------------------
    seconds_per_cell: float = 2.0
    """Simulated time (seconds) per grid cell traversed.  Used to convert
    battery life and recharge time into timestep counts."""

    # ------------------------------------------------------------------
    # Fleet
    # ------------------------------------------------------------------
    n_drones: int = 3
    """Number of drones in the fleet."""

    dock_positions: List = field(default_factory=lambda: [(0, 0)])
    """Grid coordinates of charging / refill docks.  Multiple docks are
    supported; drones are assigned round-robin."""

    # ------------------------------------------------------------------
    # Mission identity
    # ------------------------------------------------------------------
    name: str = "Mangrove Reforestation"
    """Human-readable mission label used in animation titles and reports."""

    # ------------------------------------------------------------------
    # Survival model (informational — not used in core simulation)
    # ------------------------------------------------------------------
    survival_rate: float = 0.40
    """Fraction of planted seeds expected to survive to seedling stage.
    Mangrove: ~40 %.  Used in reforestation metrics summary."""

    target_density_per_m2: float = 1.0
    """Desired surviving seedlings per m² after all reseeding passes."""


# ---------------------------------------------------------------------------
# Named presets
# ---------------------------------------------------------------------------

def mangrove_preset() -> MissionConfig:
    """Distant Imagery / Abu Dhabi coastal mangrove parameters.

    Reflects publicly available information on their Abu Dhabi operations:
    - 6 000 seeds per drone voyage
    - 1.5 m average spacing (mid-range density, mimics natural dispersal)
    - 35-minute battery life (calm coastal conditions)
    - 1-hour recharge
    """
    return MissionConfig(
        seed_capacity       = 6000,
        seed_spacing_m      = 1.5,
        seed_jitter_sigma   = 0.35,
        battery_life_minutes= 35.0,
        recharge_time_seconds=3600.0,
        survival_rate       = 0.40,
        name                = "Mangrove Reforestation (Abu Dhabi)",
    )


# ---------------------------------------------------------------------------
# Parameter derivation
# ---------------------------------------------------------------------------

def compute_sim_params(config: MissionConfig, ncols: int) -> Dict[str, Any]:
    """Derive all simulation-layer parameters from a MissionConfig.

    Parameters
    ----------
    config:
        A populated MissionConfig instance.
    ncols:
        Number of grid columns in the field (i.e. the grid width).

    Returns
    -------
    dict with keys:
        meters_per_cell         float   — real-world size of one grid cell (m)
        cell_area_m2            float   — area of one grid cell (m²)
        seeds_per_cell          float   — average seeds dropped per spray cell
        battery_drain_per_cell  float   — % battery lost per cell traversed
        recharge_time_steps     int     — timesteps at dock to fully recharge
        wind_multiplier         float   — battery drain multiplier from wind
        seed_capacity           int     — seeds per drone voyage (pass-through)
        seed_jitter_sigma       float   — Gaussian jitter std dev (pass-through)

    The dict can be unpacked directly into simulate() and used to build
    DroneSpec objects::

        params = compute_sim_params(cfg, ncols=64)

        drones = [DroneSpec(id=i, seed_capacity=params["seed_capacity"])
                  for i in range(cfg.n_drones)]

        history = simulate(
            strips, drones, result, nrows, ncols,
            battery_drain_per_cell = params["battery_drain_per_cell"],
            recharge_time_steps    = params["recharge_time_steps"],
            seeds_per_cell         = params["seeds_per_cell"],
            seed_jitter_sigma      = params["seed_jitter_sigma"],
            dock_positions         = config.dock_positions,
        )
    """
    # --- Scale ---
    meters_per_cell = config.field_width_m / max(ncols, 1)
    cell_area_m2    = meters_per_cell ** 2

    # --- Seed density ---
    # seeds/m² = 1 / spacing²  →  seeds/cell = cell_area × seeds/m²
    seeds_per_cell = cell_area_m2 / max(config.seed_spacing_m ** 2, 1e-6)

    # --- Wind penalty ---
    wind_multiplier = 1.0 + 0.015 * max(config.wind_speed_ms, 0.0)

    # --- Battery drain ---
    # 100 % battery lasts (battery_life_minutes × 60 / seconds_per_cell) cells
    cells_per_charge = (config.battery_life_minutes * 60.0
                        / max(config.seconds_per_cell, 0.01))
    battery_drain_per_cell = (100.0 / cells_per_charge) * wind_multiplier

    # --- Recharge ---
    recharge_time_steps = max(
        1,
        int(config.recharge_time_seconds / max(config.seconds_per_cell, 0.01))
    )

    return {
        "meters_per_cell"        : meters_per_cell,
        "cell_area_m2"           : cell_area_m2,
        "seeds_per_cell"         : seeds_per_cell,
        "battery_drain_per_cell" : battery_drain_per_cell,
        "recharge_time_steps"    : recharge_time_steps,
        "wind_multiplier"        : wind_multiplier,
        "seed_capacity"          : config.seed_capacity,
        "seed_jitter_sigma"      : config.seed_jitter_sigma,
    }


# ---------------------------------------------------------------------------
# Pretty-print helper
# ---------------------------------------------------------------------------

def print_mission_summary(config: MissionConfig, ncols: int) -> None:
    """Print a human-readable parameter table to stdout."""
    p = compute_sim_params(config, ncols)
    print(f"\n{'-'*52}")
    print(f"  Mission: {config.name}")
    print(f"{'-'*52}")
    print(f"  Fleet")
    print(f"    Drones              : {config.n_drones}")
    print(f"    Seed capacity       : {config.seed_capacity:,} seeds/voyage")
    print(f"    Seed spacing        : {config.seed_spacing_m} m  "
          f"({1/config.seed_spacing_m**2:.3f} seeds/m²)")
    print(f"  Scale")
    print(f"    Field width         : {config.field_width_m} m")
    print(f"    Grid columns        : {ncols}")
    print(f"    Metres per cell     : {p['meters_per_cell']:.2f} m")
    print(f"    Cell area           : {p['cell_area_m2']:.2f} m²")
    print(f"    Seeds per cell      : {p['seeds_per_cell']:.2f}")
    print(f"  Battery")
    print(f"    Battery life        : {config.battery_life_minutes} min")
    print(f"    Wind speed          : {config.wind_speed_ms} m/s  "
          f"(×{p['wind_multiplier']:.3f} drain)")
    print(f"    Drain per cell      : {p['battery_drain_per_cell']:.4f} %")
    print(f"    Recharge time       : {config.recharge_time_seconds/3600:.1f} hr  "
          f"({p['recharge_time_steps']} steps)")
    print(f"  Survival model")
    print(f"    Survival rate       : {config.survival_rate*100:.0f} %")
    print(f"    Target density      : {config.target_density_per_m2} survivors/m²")
    print(f"{'-'*52}\n")
