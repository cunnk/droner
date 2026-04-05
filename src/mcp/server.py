"""
Drone Fleet Coordinator — MCP Server

Exposes the fleet optimizer and simulation as Claude-callable tools.

Architecture
------------
Ground station runs this server. Claude (via Claude Code or the Anthropic
API) connects and can:
  1. Load a field image and generate spray strips
  2. Plan a mission (MILP → degraded → heuristic automatically)
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
    In .claude/settings.json → "mcpServers":
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
onboard system falls back to the three-tier planner's heuristic mode —
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
        "arrives — then run the simulation again with the new plan."
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
        target_size: Downsample target (e.g. 32 → 32x32 grid).
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

    The planner automatically selects Full MILP → Degraded MILP → Heuristic
    based on available time and problem size.

    Args:
        run_id: Session ID from load_field.
        n_drones: Number of drones in the fleet.
        battery: Starting battery level (0–100).
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
      coverage_pct        — % of field cells sprayed
      priority_coverage   — weighted coverage (high-priority strips count more)
      makespan            — total mission duration in timesteps
      replan_count        — number of replanning events triggered
      time_to_recovery    — timesteps between first failure and full replanning

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
    description: str = "Storm approaching — mission window closing",
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


if __name__ == "__main__":
    mcp.run()
