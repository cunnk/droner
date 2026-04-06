"""
Drone Fleet Coordinator -- MCP Server

Exposes the fleet optimizer and simulation as Claude-callable tools.

Architecture
------------
Ground station runs this server. Claude (via Claude Code or the Anthropic
API) connects and can:
  1. Load a field image and generate spray strips
  2. Plan a mission (MILP -> degraded -> heuristic automatically)
  3. Run the simulation
  4. Monitor a weather feed for incoming storms
  5. Replan with a priority-weighted deadline when a storm is approaching

The key design insight: only *strategic* decisions are delegated to Claude.
Per-timestep physics, pathfinding, and battery math stay in Python.

Usage
-----
Start the server (stdio transport, for use with Claude Code):
    python -m src.mcp.server

Add to Claude Code:
    In .claude/settings.json -> "mcpServers":
    {
      "drone-fleet": {
        "command": "python",
        "args": ["-m", "src.mcp.server"],
        "cwd": "/path/to/drones"
      }
    }

Fallback behavior
-----------------
If this coordinator becomes unreachable (link drop, timeout), each drone's
onboard system falls back to the three-tier planner's heuristic mode --
already implemented in src/optimizer/planner.py. This is the correct
architecture for real ag autonomy: LLM at the coordinator, local heuristic
as the emergency fallback.
"""
from mcp.server.fastmcp import FastMCP
from src.mcp import tools

mcp = FastMCP(
    "drone-fleet-coordinator",
    instructions=(
        "You are a drone fleet mission coordinator for an agricultural spraying operation. "
        "Use these tools to load field data, plan missions, run simulations, and respond to "
        "weather alerts. When get_weather_alert returns an active alert, you must call "
        "replan_with_deadline to maximize coverage of high-priority strips before the storm "
        "arrives -- then run the simulation again with the new plan."
    ),
)


@mcp.tool()
def load_synthetic_field(
    nrows: int = 32,
    ncols: int = 32,
    n_patches: int = 6,
    seed: int = 42,
    seconds_per_cell: float = 2.0,
) -> dict:
    """Generate a synthetic field with random priority patches and create spray strips.

    Useful for demos and testing. Returns a run_id for subsequent calls.
    """
    return tools.load_synthetic_field(nrows, ncols, n_patches, seed, seconds_per_cell)


@mcp.tool()
def load_field(
    image_path: str,
    target_size: int = 32,
    channel: str = "green",
    orientation_deg: float = 0.0,
    seconds_per_cell: float = 2.0,
) -> dict:
    """Load a field image and generate spray strips.

    Returns a run_id for all subsequent calls. The run_id keeps all session
    state server-side so you don't need to pass large data between tool calls.

    Args:
        image_path: Path to the field image (JPG, PNG, GeoTIFF).
        target_size: Downsample target (e.g. 32 -> 32x32 grid).
        channel: Spectral channel to use as priority proxy ('green', 'pseudo_ndvi', 'grayscale').
        orientation_deg: Strip alignment angle in degrees (0=horizontal, 90=vertical).
        seconds_per_cell: Simulated spray time per grid cell.
    """
    return tools.load_field(image_path, target_size, channel, orientation_deg, seconds_per_cell)


@mcp.tool()
def create_plan(
    run_id: str,
    n_drones: int = 3,
    battery: float = 100.0,
    time_limit_s: float = 10.0,
    objective_mode: str = "makespan",
) -> dict:
    """Assign strips to drones using the three-tier planner.

    The planner automatically selects Full MILP -> Degraded MILP -> Heuristic
    based on available time and problem size.

    Args:
        run_id: Session ID from load_field.
        n_drones: Number of drones in the fleet.
        battery: Starting battery level (0-100).
        time_limit_s: Solver time budget in seconds.
        objective_mode: 'makespan' (balanced workload) or 'weighted' (priority-first).
    """
    return tools.create_plan(run_id, n_drones, battery, time_limit_s, objective_mode)


@mcp.tool()
def run_simulation(
    run_id: str,
    battery_drain_per_cell: float = 0.0,
    recharge_time_steps: int = 10,
) -> dict:
    """Execute the mission simulation with the current plan.

    Stores the full state history server-side. Call get_state or get_metrics
    afterward to inspect results.

    Args:
        run_id: Session ID from load_field.
        battery_drain_per_cell: Battery % consumed per grid cell sprayed.
        recharge_time_steps: Timesteps needed to recharge at dock.
    """
    return tools.run_simulation(
        run_id,
        battery_drain_per_cell=battery_drain_per_cell,
        recharge_time_steps=recharge_time_steps,
    )


@mcp.tool()
def get_state(run_id: str) -> dict:
    """Get current coverage and drone states from the last simulation.

    Returns remaining strips sorted by priority (highest first) so you can
    quickly assess what work is most urgent.

    Args:
        run_id: Session ID from load_field.
    """
    return tools.get_state(run_id)


@mcp.tool()
def get_metrics(run_id: str) -> dict:
    """Compute operator metrics for the completed simulation.

    Key metrics:
      coverage_pct        -- % of field cells sprayed
      priority_coverage   -- weighted coverage (high-priority strips count more)
      makespan            -- total mission duration in timesteps
      replan_count        -- number of replanning events triggered
      time_to_recovery    -- timesteps between first failure and full replanning

    Args:
        run_id: Session ID from load_field.
    """
    return tools.get_metrics(run_id)


@mcp.tool()
def get_weather_alert() -> dict:
    """Check for active weather alerts.

    Returns alert status, minutes remaining before storm arrival, and a
    description. If alert is True, you should immediately call
    replan_with_deadline using minutes_remaining * 60 as the time budget.
    """
    return tools.get_weather_alert()


@mcp.tool()
def arm_weather_alert(
    minutes_remaining: float,
    description: str = "Storm approaching -- mission window closing",
) -> dict:
    """Arm a simulated weather alert (for testing and demos).

    In production this would be triggered by an external weather API.
    After arming, get_weather_alert will return an active alert.

    Args:
        minutes_remaining: How many minutes until the storm arrives.
        description: Human-readable alert description.
    """
    return tools.arm_weather_alert(minutes_remaining, description)


@mcp.tool()
def replan_with_deadline(
    run_id: str,
    time_budget_seconds: float,
) -> dict:
    """Replan the mission with a hard time cap, optimizing for priority coverage.

    Switches to the weighted objective so the MILP assigns the highest-priority
    strips first, then fills remaining capacity. Strips that don't fit within
    the deadline are deferred (not crashed).

    Use this when get_weather_alert returns an active alert. The deadline
    should be minutes_remaining * 60.

    After calling this, run run_simulation again to execute the new plan.

    Args:
        run_id: Session ID from load_field.
        time_budget_seconds: Hard cap on per-drone workload (= storm deadline).
    """
    return tools.replan_with_deadline(run_id, time_budget_seconds)


# ---------------------------------------------------------------------------
# Reforestation tools
# ---------------------------------------------------------------------------

@mcp.tool()
def load_reforestation_field(
    image_path: str,
    field_width_m: float = 500.0,
    field_height_m: float = 300.0,
    target_ncols: int = 64,
    soil_ndvi_threshold: float = 0.12,
    water_blue_threshold: float = 0.50,
    min_brightness_threshold: float = 0.42,
    wind_speed_ms: float = 0.0,
    battery_life_minutes: float = 35.0,
    seed_spacing_m: float = 1.5,
    seed_capacity: int = 6000,
    n_drones: int = 3,
    planting_mode: str = "contour",
    contour_strip_width: int = 2,
    seconds_per_cell: float = 2.0,
) -> dict:
    """Load an aerial mangrove image and prepare a reforestation mission.

    Detects plantable mudflat soil from the aerial photograph, generates
    flight strips in the chosen mode, and stores all session state needed
    for tidal/wind updates mid-mission. Returns a run_id for all subsequent
    tool calls.

    Args:
        image_path: Path to aerial photograph (JPG, PNG).
        field_width_m: Real-world field width in metres.
        field_height_m: Real-world field height in metres.
        target_ncols: Grid resolution -- columns (rows derived from aspect ratio).
        soil_ndvi_threshold: NDVI >= this -> existing vegetation, not plantable.
        water_blue_threshold: Blue channel >= this -> water channel, not plantable.
        min_brightness_threshold: Brightness < this -> existing canopy, not plantable.
            Critical for separating dark mangrove canopy from mudflat (both have
            near-zero NDVI).
        wind_speed_ms: Initial wind speed for battery and dispersal modelling.
        battery_life_minutes: Full-charge flight time per drone.
        seed_spacing_m: Target metres between planted seeds.
        seed_capacity: Seeds per drone per charge (Distant Imagery: 6,000).
        n_drones: Fleet size.
        planting_mode: 'contour' -- sinuous tidal-channel-following strips;
            'boustrophedon' -- standard lawnmower rows.
        contour_strip_width: Band width in cells per strip (contour mode).
            2 = recommended balance of sinuosity and MILP speed.
        seconds_per_cell: Simulation timesteps per grid cell.
    """
    return tools.load_reforestation_field(
        image_path, field_width_m, field_height_m, target_ncols,
        soil_ndvi_threshold, water_blue_threshold, min_brightness_threshold,
        wind_speed_ms, battery_life_minutes, seed_spacing_m, seed_capacity,
        n_drones, planting_mode, contour_strip_width, seconds_per_cell,
    )


@mcp.tool()
def report_tidal_change(
    run_id: str,
    region: dict,
    tidal_level: float = 0.8,
    tidal_threshold: float = 0.6,
) -> dict:
    """Report a tidal or moisture sensor reading that floods part of the field.

    Marks the specified region as flooded, re-applies the soil mask to exclude
    underwater cells, and regenerates flight strips. Call reoptimize() after
    this to reassign the revised strip set to the fleet.

    Simulates real-world inputs that could come from:
      - Moisture sensors embedded in the mudflat
      - Tidal gauge readings from a nearby station
      - Human observer report via radio
      - Remote sensing (SAR or optical) updated mid-mission

    Args:
        run_id: Session ID from load_reforestation_field.
        region: The flooded zone, one of:
            {"rows": [r_min, r_max], "cols": [c_min, c_max]}  -- bounding box
            {"type": "full_field"}                             -- entire field
        tidal_level: Water level in this region (0=dry, 1=submerged).
            Cells where tidal_level >= tidal_threshold are excluded from planting.
        tidal_threshold: Exclusion cutoff (0.6 = significant flooding).
    """
    return tools.report_tidal_change(run_id, region, tidal_level, tidal_threshold)


@mcp.tool()
def report_wind_change(
    run_id: str,
    wind_speed_ms: float,
    base_jitter_sigma: float = 0.3,
) -> dict:
    """Report a wind speed change and update battery drain and seed dispersal.

    Updates two session parameters that affect subsequent simulation chunks:
      battery_drain_per_cell -- increases with wind (more energy fighting headwind)
      seed_jitter_sigma      -- increases with wind (seeds scattered further on drop)

    No replanning required for moderate wind changes. The next
    run_simulation_until() chunk automatically uses the updated values.

    Seed physics note: Distant Imagery drones DROP seeds under gravity (no
    pneumatic cannon). Dispersal offset = forward drift during fall + wind drift.
    At 5 m/s drone speed, 5 m altitude: fall_time ~= 1 s, forward drift ~= 5 m.
    Wind adds proportionally to this.

    Args:
        run_id: Session ID from load_reforestation_field.
        wind_speed_ms: New wind speed in metres per second.
            >= 15 m/s triggers a mission-hold warning.
        base_jitter_sigma: Baseline seed dispersal sigma at zero wind (cells).
    """
    return tools.report_wind_change(run_id, wind_speed_ms, base_jitter_sigma)


@mcp.tool()
def enable_contour_planting(
    run_id: str,
    strip_width: int = 2,
) -> dict:
    """Switch to contour-following strip paths that mimic natural mangrove growth.

    Replaces the current strip set with distance-transform contour strips --
    each strip follows a ring of cells equidistant from the tidal channel edge.
    Outer rings (tidal margin) are planted first, matching natural mangrove
    colonisation direction.

    Ecological basis: experts avoid straight-line industrial rows because they
    create artificial erosion channels. Contour paths create hydrological flow
    clustering, biodiversity pockets, and nurse-effect groupings that a uniform
    grid cannot.

    Requires load_reforestation_field() to have been called (needs soil_mask).
    Call create_plan() then run_simulation_until() after this.

    Args:
        run_id: Session ID from load_reforestation_field.
        strip_width: Distance-band width in cells per strip.
            1 = maximum sinuosity, many small strips (slower MILP)
            2 = recommended -- smooth curves, fewer strips (default)
            3 = wider swaths, less curve detail, fastest MILP
    """
    return tools.enable_contour_planting(run_id, strip_width)


if __name__ == "__main__":
    mcp.run()
