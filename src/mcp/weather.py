"""Mock weather feed for the drone fleet MCP server.

In a real system this would pull from a weather API or on-site sensor.
Here it's a simple in-process state that can be armed programmatically
(from a notebook or test) or via the arm_weather_alert MCP tool.

The key design point: this is an *external signal* that the simulation
knows nothing about. Claude (via MCP) is the only component that can
observe it and decide how to respond.
"""

_state: dict = {
    "alert": False,
    "minutes_remaining": None,
    "description": None,
}


def arm_alert(
    minutes_remaining: float,
    description: str = "Storm approaching -- mission window closing",
) -> None:
    """Arm the weather alert (simulate an incoming storm)."""
    _state["alert"] = True
    _state["minutes_remaining"] = minutes_remaining
    _state["description"] = description


def disarm_alert() -> None:
    """Clear the weather alert."""
    _state.update({"alert": False, "minutes_remaining": None, "description": None})


def get_alert() -> dict:
    """Return current alert state. Safe to call repeatedly."""
    return dict(_state)
