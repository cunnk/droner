"""In-memory session store for MCP tool state.

Each call to load_field() creates a new session (run_id).
Subsequent tool calls (create_plan, run_simulation, etc.) read and update
the same session so Claude can compose a multi-step mission without
re-passing large data structures between tool calls.
"""
import uuid
from typing import Any, Dict, List


_store: Dict[str, Dict[str, Any]] = {}


def new_run(data: Dict[str, Any]) -> str:
    run_id = str(uuid.uuid4())[:8]
    _store[run_id] = dict(data)
    return run_id


def get_run(run_id: str) -> Dict[str, Any]:
    if run_id not in _store:
        raise KeyError(
            f"No session with run_id='{run_id}'. "
            "Call load_field first to create a session."
        )
    return _store[run_id]


def update_run(run_id: str, **kwargs) -> None:
    if run_id not in _store:
        raise KeyError(f"No session with run_id='{run_id}'.")
    _store[run_id].update(kwargs)


def list_runs() -> List[str]:
    return list(_store.keys())


def clear() -> None:
    """Clear all sessions (useful for testing)."""
    _store.clear()
