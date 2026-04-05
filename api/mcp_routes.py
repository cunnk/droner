"""
FastAPI router — MCP-enabled field adaptation.

Runs Claude against a subset of drone fleet tools (defined in src/mcp/tools.py).
Tool calls are executed directly (no MCP transport) and streamed back to the UI
as SSE events so the operator can watch every decision in real time.

SSE event shapes
----------------
{"type": "text",        "text": "..."}               — Claude's narrative
{"type": "tool_call",   "name": "...", "input": {...}}
{"type": "tool_result", "name": "...", "result": {...}}
{"type": "done",  "state_history": [...], "baseline_metrics": {...},
                  "adapted_metrics": {...}, "run_id": "..."}
{"type": "error", "message": "..."}
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.mcp import tools as mcp_tools
from src.mcp import state_store, weather as weather_module
from src.mcp.field_events import generate_event_feed, events_to_dicts
from src.field.generator import generate_strips, synthetic_field
from src.optimizer.milp import DroneSpec
from src.optimizer.planner import plan, PlannerContext
from src.simulation.metrics import compute_metrics

router = APIRouter(prefix="/api/mcp")


# ── Anthropic tool definitions ─────────────────────────────────────────────────

TOOLS = [
    {
        "name": "get_state",
        "description": (
            "Get current mission coverage and drone states. "
            "Call this first to understand the situation before making decisions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "description": "Session ID."}
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_weather_alert",
        "description": (
            "Check for active weather alerts. "
            "If alert is true, minutes_remaining tells you how long the mission window is. "
            "You must replan immediately using time_budget_seconds = minutes_remaining * 60."
        ),
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "arm_mandatory_zones",
        "description": (
            "Designate the highest-priority strips as mandatory — they must be completed "
            "before storm arrival. Call this before replanning when a weather alert is active."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "n_strips": {
                    "type": "integer",
                    "description": "Number of top-priority strips to designate as mandatory.",
                    "default": 6,
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_mandatory_strips",
        "description": "Check completion status of mandatory compliance strips.",
        "input_schema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "replan_with_deadline",
        "description": (
            "Replan the mission with a hard time cap per drone, switching to "
            "priority-weighted objective so the most important strips are sprayed first. "
            "Use time_budget_seconds = minutes_remaining * 60 when a storm is approaching."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "time_budget_seconds": {
                    "type": "number",
                    "description": "Hard per-drone deadline in seconds (= minutes_remaining * 60).",
                },
            },
            "required": ["run_id", "time_budget_seconds"],
        },
    },
    {
        "name": "run_simulation",
        "description": "Execute the current plan. Always call this after replanning.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "battery_drain_per_cell": {
                    "type": "number",
                    "default": 0.0,
                    "description": "Battery % consumed per cell. 0 = unlimited.",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_metrics",
        "description": (
            "Get operator metrics: coverage_pct, priority_coverage, makespan, replan_count. "
            "Use deadline_timestep to measure coverage AT storm arrival, not at mission end."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "deadline_timestep": {
                    "type": "integer",
                    "description": "If set, metrics are measured at this timestep (storm arrival).",
                },
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_field_events",
        "description": (
            "Check for field reports (pest sightings, sensor alerts, worker reports). "
            "Call this periodically when field_events are enabled."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "since_timestep": {"type": "integer", "default": 0},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "find_strips_at",
        "description": "Find strips near a field event coordinate. Use to identify affected strips.",
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "row": {"type": "integer"},
                "col": {"type": "integer"},
                "radius": {"type": "integer", "default": 3},
            },
            "required": ["run_id", "row", "col"],
        },
    },
    {
        "name": "update_planner_params",
        "description": (
            "Update optimization parameters before the next replan. "
            "Raise priority_weight (0–1) when field events flag critical zones."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string"},
                "priority_weight": {
                    "type": "number",
                    "description": "0=pure makespan balance, 1=pure priority-first.",
                },
                "must_complete_strip_ids": {
                    "type": "array",
                    "items": {"type": "integer"},
                },
            },
            "required": ["run_id"],
        },
    },
]


# ── Tool dispatcher ────────────────────────────────────────────────────────────

def dispatch_tool(name: str, inputs: dict) -> Any:
    """Execute a tool by name, return its result."""
    fn_map = {
        "get_state":             mcp_tools.get_state,
        "get_weather_alert":     mcp_tools.get_weather_alert,
        "arm_mandatory_zones":   mcp_tools.arm_mandatory_zones,
        "get_mandatory_strips":  mcp_tools.get_mandatory_strips,
        "replan_with_deadline":  mcp_tools.replan_with_deadline,
        "run_simulation":        mcp_tools.run_simulation,
        "get_metrics":           mcp_tools.get_metrics,
        "get_field_events":      mcp_tools.get_field_events,
        "find_strips_at":        mcp_tools.find_strips_at,
        "update_planner_params": mcp_tools.update_planner_params,
    }
    if name not in fn_map:
        return {"error": f"Unknown tool: {name}"}
    try:
        return fn_map[name](**inputs)
    except Exception as e:
        return {"error": str(e)}


# ── Serialisation helpers ──────────────────────────────────────────────────────

def to_python(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    if isinstance(obj, np.integer):
        return int(obj)
    if isinstance(obj, np.floating):
        return float(obj)
    if isinstance(obj, dict):
        return {k: to_python(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_python(v) for v in obj]
    return obj


def serialize_history(history):
    result = []
    for step in history:
        s = {"timestep": step["timestep"], "grid": step["grid"], "event": step.get("event")}
        drones = []
        for d in step.get("drones", []):
            dd = dict(d)
            for key in ("position", "dock"):
                if key in dd and isinstance(dd[key], tuple):
                    dd[key] = list(dd[key])
            if "route" in dd:
                dd["route"] = [int(x) for x in dd["route"]]
            drones.append(dd)
        s["drones"] = drones
        result.append(s)
    return result


def sse(data: dict) -> str:
    return f"data: {json.dumps(data)}\n\n"


# ── Request models ─────────────────────────────────────────────────────────────

class AdaptRequest(BaseModel):
    # Field / fleet config (mirrors SimulateRequest)
    grid: List[List[float]]
    nrows: int
    ncols: int
    n_drones: int = 3
    dock_positions: List[List[int]] = [[0, 0]]
    orientation_deg: float = 0.0
    seconds_per_cell: float = 2.0
    battery_drain: float = 0.0

    # MCP-specific
    weather_enabled: bool = True
    weather_minutes: float = 8.0
    field_events_enabled: bool = False
    n_field_events: int = 3

    api_key: str  # Anthropic API key (from localStorage)


# ── Main adaptation endpoint ───────────────────────────────────────────────────

@router.post("/adapt/stream")
async def adapt_stream(req: AdaptRequest):
    """
    Create a mission session, optionally arm weather/field events, then run
    Claude in an agentic loop.  Streams SSE events as the loop executes.
    """
    async def generate():
        try:
            import anthropic as anthropic_sdk
        except ImportError:
            yield sse({"type": "error", "message": "anthropic package not installed. Run: pip install anthropic"})
            return

        # ── 1. Build session ───────────────────────────────────────────────
        def _setup():
            grid_np = np.array(req.grid)
            strips = generate_strips(
                grid_np,
                seconds_per_cell=req.seconds_per_cell,
                orientation_deg=req.orientation_deg,
            )
            drones = [DroneSpec(id=i, battery=100.0) for i in range(req.n_drones)]
            context = PlannerContext(time_budget_seconds=10.0)
            result = plan(strips, drones, context=context)
            dock_tuples = [tuple(d) for d in req.dock_positions]

            run_id = state_store.new_run({
                "grid": req.grid,
                "strips": strips,
                "nrows": req.nrows,
                "ncols": req.ncols,
                "drones": drones,
                "result": result,
                "state_history": None,
                "mandatory_strip_ids": [],
                "must_complete_strip_ids": [],
                "exclude_strip_ids": [],
                "planner_priority_weight": 0.5,
                "field_events": [],
                "dock_positions": dock_tuples,
            })
            return run_id, result.makespan

        run_id, makespan_est = await asyncio.to_thread(_setup)

        # ── 1b. Compute storm timestep & run REAL baseline simulation ──────
        # Convert weather minutes → simulation timesteps (same units as simulate())
        storm_ts = (
            int(req.weather_minutes * 60 / max(req.seconds_per_cell, 0.1))
            if req.weather_enabled else None
        )

        def _run_baseline():
            """Run original plan (no MCP adaptation) and measure coverage at deadline."""
            orig = state_store.get_run(run_id)
            bl_id = state_store.new_run({
                **orig,
                "state_history": None,
                "mandatory_strip_ids": [],
                "must_complete_strip_ids": [],
                "field_events": [],
            })
            mcp_tools.run_simulation(bl_id, battery_drain_per_cell=req.battery_drain)
            bl_hist = state_store.get_run(bl_id).get("state_history") or []
            strips = orig["strips"]
            nrows, ncols = orig["nrows"], orig["ncols"]

            t = min(storm_ts, len(bl_hist) - 1) if (storm_ts and bl_hist) else None
            if t is not None:
                m = compute_metrics(bl_hist[:t + 1], strips, nrows, ncols)
                bl_m = {k: float(v) for k, v in m.items()
                        if isinstance(v, (int, float, np.floating, np.integer))}
            else:
                # No deadline — measure at end of mission
                m = compute_metrics(bl_hist, strips, nrows, ncols)
                bl_m = {k: float(v) for k, v in m.items()
                        if isinstance(v, (int, float, np.floating, np.integer))}

            # Measure top-N priority strip coverage at deadline (the real story)
            sorted_strips = sorted(strips, key=lambda s: s.priority, reverse=True)
            top_n = min(6, len(sorted_strips))
            top_ids = {s.id for s in sorted_strips[:top_n]}
            top_cells = [(r, c) for s in sorted_strips[:top_n] for r, c in s.cells]
            ref_hist = bl_hist[:t + 1] if t is not None else bl_hist
            if ref_hist and top_cells:
                grid_at_ref = ref_hist[-1]["grid"]
                done = sum(1 for r, c in top_cells if grid_at_ref[r][c] == 2)
                bl_m["top_priority_coverage"] = done / len(top_cells)
            else:
                bl_m["top_priority_coverage"] = 0.0

            return bl_m, top_ids

        baseline_at_deadline, top_strip_ids = await asyncio.to_thread(_run_baseline)

        # ── 2. Arm weather alert ───────────────────────────────────────────
        weather_module.disarm_alert()
        if req.weather_enabled:
            weather_module.arm_alert(req.weather_minutes, f"Storm approaching — {req.weather_minutes:.0f} min window")
            yield sse({"type": "system", "text": f"⛈ Weather alert armed: {req.weather_minutes:.0f} min until storm arrival"})

        # ── 3. Arm field events ────────────────────────────────────────────
        if req.field_events_enabled:
            def _gen_events():
                evts = generate_event_feed(
                    req.nrows, req.ncols,
                    makespan_timesteps=int(makespan_est),
                    n_events=req.n_field_events,
                )
                return events_to_dicts(evts)

            events = await asyncio.to_thread(_gen_events)
            state_store.update_run(run_id, field_events=events)
            yield sse({"type": "system", "text": f"📡 {len(events)} field events armed (pest sightings, sensor alerts)"})

        # ── 4. Build initial user message ──────────────────────────────────
        field_events_note = (
            "\nField events are armed — poll get_field_events periodically."
            if req.field_events_enabled else ""
        )
        weather_note = (
            f"\nA weather alert may be active — check get_weather_alert immediately."
            if req.weather_enabled else ""
        )

        initial_message = (
            f"Mission started. run_id = '{run_id}'. "
            f"Fleet: {req.n_drones} drones on a {req.nrows}×{req.ncols} field."
            f"{weather_note}{field_events_note}"
            "\n\nAssess the situation and make any necessary adjustments to maximise "
            "priority coverage. Report your decisions and the outcome."
        )

        system_prompt = (
            "You are a drone fleet mission coordinator for an agricultural spraying operation. "
            "Use the available tools to monitor mission state, respond to weather alerts, and "
            "adapt the plan to maximise coverage of high-priority strips.\n\n"
            "Decision protocol:\n"
            "1. Call get_state to see current coverage and drone states.\n"
            "2. Call get_weather_alert — if an alert is active, act immediately.\n"
            "3. When a storm is approaching: call arm_mandatory_zones, then "
            "replan_with_deadline(time_budget_seconds = minutes_remaining × 60).\n"
            "4. Always call run_simulation after replanning.\n"
            "5. Report the final outcome with get_metrics — include coverage_pct and "
            "priority_coverage at the deadline.\n\n"
            "Be concise. Explain decisions as a field operations manager would understand them."
        )

        # ── 5. Agentic loop ────────────────────────────────────────────────
        client = anthropic_sdk.Anthropic(api_key=req.api_key)
        messages = [{"role": "user", "content": initial_message}]
        max_turns = 12

        for _turn in range(max_turns):
            def _call_claude(msgs=messages):
                return client.messages.create(
                    model="claude-opus-4-6",
                    max_tokens=1024,
                    system=system_prompt,
                    tools=TOOLS,
                    messages=msgs,
                )

            response = await asyncio.to_thread(_call_claude)

            # Determine if this is the final turn before emitting content
            tool_uses = [b for b in response.content if b.type == "tool_use"]
            is_final = response.stop_reason == "end_turn" or not tool_uses

            # Emit assistant content — final text block becomes "summary"
            for block in response.content:
                if block.type == "text" and block.text.strip():
                    event_type = "summary" if is_final else "text"
                    yield sse({"type": event_type, "text": block.text})
                elif block.type == "tool_use":
                    yield sse({"type": "tool_call", "name": block.name, "input": block.input})

            if is_final:
                break

            # Execute tools
            messages.append({"role": "assistant", "content": response.content})
            tool_results_content = []

            for block in tool_uses:
                result = await asyncio.to_thread(dispatch_tool, block.name, block.input)
                safe_result = to_python(result)
                yield sse({"type": "tool_result", "name": block.name, "result": safe_result})
                tool_results_content.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(safe_result),
                })

            messages.append({"role": "user", "content": tool_results_content})

        # ── 6. Extract final state, compute adapted metrics, inject storm events ──
        def _finalize():
            session = state_store.get_run(run_id)
            history = session.get("state_history") or []
            strips = session["strips"]
            nrows, ncols = session["nrows"], session["ncols"]

            # Mandatory strip IDs (set by arm_mandatory_zones) — fallback to top-N
            mandatory_ids = set(session.get("mandatory_strip_ids") or []) or top_strip_ids

            # Mandatory cell coordinates for grid overlay
            mandatory_cells = [
                [r, c] for s in strips if s.id in mandatory_ids for r, c in s.cells
            ]

            # Field event cells for grid overlay — include timestep so UI
            # can reveal bugs progressively as the mission timeline advances
            field_events = session.get("field_events") or []
            event_cells = [
                [e["row"], e["col"], e.get("event_type", ""), e.get("severity", 0.5), e.get("timestep", 0)]
                for e in field_events
            ]

            # Adapted metrics at storm deadline (same cutoff as baseline)
            t = min(storm_ts, len(history) - 1) if (storm_ts and history) else None
            if t is not None:
                m = compute_metrics(history[:t + 1], strips, nrows, ncols)
                adapted_at_t = {k: float(v) for k, v in m.items()
                                if isinstance(v, (int, float, np.floating, np.integer))}
                # High-priority strip coverage at deadline
                mand_cells = [(r, c) for s in strips if s.id in mandatory_ids for r, c in s.cells]
                if mand_cells:
                    grid_at_t = history[t]["grid"]
                    done = sum(1 for r, c in mand_cells if grid_at_t[r][c] == 2)
                    adapted_at_t["top_priority_coverage"] = done / len(mand_cells)
                else:
                    adapted_at_t["top_priority_coverage"] = 0.0
            else:
                m = compute_metrics(history, strips, nrows, ncols)
                adapted_at_t = {k: float(v) for k, v in m.items()
                                if isinstance(v, (int, float, np.floating, np.integer))}
                adapted_at_t["top_priority_coverage"] = 0.0

            # Inject storm warning and arrival events into history for the event log
            if storm_ts and history:
                warn_ts = max(0, storm_ts - 10)
                injected_arrival = False
                for step in history:
                    ts = step["timestep"]
                    if ts == warn_ts and not step.get("event"):
                        step["event"] = f"⚠ Storm approaching — {req.weather_minutes:.0f} min window"
                    elif ts >= storm_ts and not injected_arrival and not step.get("event"):
                        step["event"] = "⛈ Storm arrived — mission window closed"
                        injected_arrival = True

            return history, adapted_at_t, mandatory_cells, event_cells

        history, adapted_at_t, mandatory_cells, event_cells = await asyncio.to_thread(_finalize)

        yield sse({
            "type": "done",
            "run_id": run_id,
            "state_history": serialize_history(history),
            "nrows": req.nrows,
            "ncols": req.ncols,
            "baseline_at_deadline": to_python(baseline_at_deadline),
            "adapted_at_deadline": to_python(adapted_at_t),
            "mandatory_cells": mandatory_cells,
            "event_cells": event_cells,
            "storm_arrival_timestep": storm_ts,
            "weather_minutes": req.weather_minutes if req.weather_enabled else None,
        })

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Weather status endpoint (for polling) ─────────────────────────────────────

@router.get("/weather/status")
async def weather_status():
    return weather_module.get_alert()
