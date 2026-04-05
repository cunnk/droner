import sys
import os
import json
import asyncio
import tempfile
import numpy as np
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).parent.parent))

import io
import base64

from src.field.generator import generate_strips, synthetic_field
from src.field.ingest import load_image_grid
from src.field.orientation import estimate_orientation_deg
from src.optimizer.milp import assign_strips, DroneSpec
from src.optimizer.planner import plan, PlannerMode, PlannerContext
from src.simulation.engine import simulate
from src.simulation.metrics import compute_metrics

from api.mcp_routes import router as mcp_router

app = FastAPI(title="Drone Fleet Optimizer API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Pydantic models ────────────────────────────────────────────────────────────

class SyntheticFieldRequest(BaseModel):
    nrows: int = 32
    ncols: int = 32
    seed: int = 42
    n_patches: int = 6
    seconds_per_cell: float = 2.0
    orientation_deg: float = 0.0


class SimulateRequest(BaseModel):
    grid: List[List[float]]
    nrows: int
    ncols: int
    n_drones: int = 3
    dock_positions: List[List[int]] = [[0, 0]]
    orientation_deg: float = 0.0
    battery_drain: float = 0.0
    failure_prob: float = 0.0
    seconds_per_cell: float = 2.0
    recharge_time: int = 10
    seed: int = 42


class MCRequest(BaseModel):
    grid: List[List[float]]
    nrows: int
    ncols: int
    n_drones: int = 3
    dock_positions: List[List[int]] = [[0, 0]]
    orientation_deg: float = 0.0
    n_runs: int = 50
    failure_prob: float = 0.3
    battery_drain_max: float = 3.0
    seconds_per_cell: float = 2.0
    seed: int = 42


# ── Helpers ────────────────────────────────────────────────────────────────────

def to_python(obj):
    """Recursively convert numpy types to Python natives for JSON serialization."""
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


def serialize_state_history(history):
    """Convert state_history tuples to lists for JSON."""
    result = []
    for step in history:
        s = {
            "timestep": step["timestep"],
            "grid": step["grid"],
            "event": step.get("event"),
        }
        drones = []
        for d in step.get("drones", []):
            dd = dict(d)
            if "position" in dd and isinstance(dd["position"], tuple):
                dd["position"] = list(dd["position"])
            if "dock" in dd and isinstance(dd["dock"], tuple):
                dd["dock"] = list(dd["dock"])
            # route may contain numpy ints
            if "route" in dd:
                dd["route"] = [int(x) for x in dd["route"]]
            drones.append(dd)
        s["drones"] = drones
        result.append(s)
    return result


def build_plan(grid, nrows, ncols, n_drones, dock_positions, orientation_deg, seconds_per_cell):
    """grid → strips → assignment. Returns (strips, drones, result, dock_tuples)."""
    grid_np = np.array(grid)
    strips = generate_strips(
        grid_np,
        seconds_per_cell=seconds_per_cell,
        orientation_deg=orientation_deg,
    )
    drones = [DroneSpec(id=i, battery=100.0) for i in range(n_drones)]
    result = plan(strips, drones)
    dock_tuples = [tuple(d) for d in dock_positions]
    return strips, drones, result, dock_tuples


def generate_failures(n_drones, makespan_estimate, failure_prob, rng):
    """Generate random failure events from a probability, matching engine expectations."""
    events = []
    for drone_id in range(n_drones):
        if rng.random() < failure_prob:
            # Fail in first 60% of mission (consistent with monte_carlo_analysis internals)
            max_t = max(6, int(makespan_estimate * 0.6))
            t = int(rng.integers(5, max_t))
            ftype = rng.choice(["battery", "mechanical", "comms"])
            event = {"timestep": t, "drone_id": drone_id, "type": ftype}
            if ftype == "comms":
                event["duration"] = int(rng.integers(3, 8))
            events.append(event)
    return events


# ── Routes ─────────────────────────────────────────────────────────────────────

app.include_router(mcp_router)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.post("/api/field/synthetic")
async def get_synthetic_field(req: SyntheticFieldRequest):
    def _run():
        grid = synthetic_field(req.nrows, req.ncols, seed=req.seed, n_patches=req.n_patches)
        strips = generate_strips(
            grid,
            seconds_per_cell=req.seconds_per_cell,
            orientation_deg=req.orientation_deg,
        )
        return grid, strips

    grid, strips = await asyncio.to_thread(_run)
    return {
        "grid": grid.tolist(),
        "nrows": req.nrows,
        "ncols": req.ncols,
        "n_strips": len(strips),
        "total_spray_time": float(sum(s.time for s in strips)),
    }


@app.post("/api/field/upload")
async def upload_field(
    file: UploadFile = File(...),
    target_size: int = Query(32),
    channel: str = Query("green"),
    orientation_deg: float = Query(0.0),
    seconds_per_cell: float = Query(2.0),
):
    content = await file.read()
    suffix = Path(file.filename).suffix or ".jpg"

    def _run():
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            from PIL import Image as PILImage
            # Priority grid
            grid, metadata = load_image_grid(tmp_path, target_size=target_size, channel=channel)
            grid_np = np.array(grid)
            strips = generate_strips(
                grid_np,
                seconds_per_cell=seconds_per_cell,
                orientation_deg=orientation_deg,
            )
            # Background image: resize original to grid size, encode as base64 PNG
            pil_img = PILImage.open(tmp_path).convert('RGB')
            pil_small = pil_img.resize((target_size, target_size), PILImage.LANCZOS)
            buf = io.BytesIO()
            pil_small.save(buf, format='PNG')
            img_b64 = base64.b64encode(buf.getvalue()).decode()
            image_data_url = f'data:image/png;base64,{img_b64}'
            # Orientation estimate on full-resolution grayscale
            gray_np = np.array(pil_img.convert('L')) / 255.0
            est_angle, est_conf = estimate_orientation_deg(gray_np)
            return (grid_np, strips, {k: str(v) for k, v in metadata.items()},
                    image_data_url, float(est_angle), float(est_conf))
        finally:
            os.unlink(tmp_path)

    grid_np, strips, metadata, image_data_url, est_angle, est_conf = await asyncio.to_thread(_run)
    return {
        "grid": grid_np.tolist(),
        "nrows": int(grid_np.shape[0]),
        "ncols": int(grid_np.shape[1]),
        "n_strips": len(strips),
        "total_spray_time": float(sum(s.time for s in strips)),
        "metadata": metadata,
        "image_data_url": image_data_url,
        "estimated_orientation_deg": est_angle,
        "orientation_confidence": est_conf,
    }


@app.post("/api/simulate")
async def run_simulation(req: SimulateRequest):
    def _run():
        rng = np.random.default_rng(req.seed)
        strips, drones, assignment, dock_tuples = build_plan(
            req.grid, req.nrows, req.ncols, req.n_drones,
            req.dock_positions, req.orientation_deg, req.seconds_per_cell,
        )

        # Estimate makespan from plan for failure timing
        makespan_est = assignment.makespan if assignment.makespan > 0 else (
            sum(s.time for s in strips) / max(req.n_drones, 1)
        )

        failure_events = []
        if req.failure_prob > 0:
            failure_events = generate_failures(
                req.n_drones, makespan_est, req.failure_prob, rng
            )

        history = simulate(
            strips, drones, assignment,
            req.nrows, req.ncols,
            failure_events=failure_events or None,
            battery_drain_per_cell=req.battery_drain,
            recharge_time_steps=req.recharge_time,
            dock_positions=dock_tuples,
        )
        metrics = compute_metrics(history, strips, req.nrows, req.ncols)

        plan_info = {
            "status": assignment.status,
            "objective_mode": assignment.objective_mode,
            "makespan": float(assignment.makespan),
            "solve_time": float(assignment.solve_time),
            "n_strips": len(strips),
            "assignment": {str(k): v for k, v in assignment.assignment.items()},
            "failure_events": failure_events,
        }

        # Separate scalar metrics from series data
        scalar_metrics = {}
        series_metrics = {}
        for k, v in metrics.items():
            if isinstance(v, (list, np.ndarray)):
                series_metrics[k] = to_python(v)
            elif isinstance(v, dict):
                pass  # skip battery_series (too large)
            else:
                scalar_metrics[k] = to_python(v)

        return {
            "state_history": serialize_state_history(history),
            "metrics": scalar_metrics,
            "series": series_metrics,
            "plan": plan_info,
            "nrows": req.nrows,
            "ncols": req.ncols,
        }

    return await asyncio.to_thread(_run)


@app.post("/api/monte-carlo/stream")
async def mc_stream(req: MCRequest):
    """SSE endpoint — streams progress events then a final result event."""

    async def generate():
        rng = np.random.default_rng(req.seed)

        # Build plan once; reuse for all MC runs
        def setup():
            return build_plan(
                req.grid, req.nrows, req.ncols, req.n_drones,
                req.dock_positions, req.orientation_deg, req.seconds_per_cell,
            )

        strips, drones, base_result, dock_tuples = await asyncio.to_thread(setup)
        makespan_est = base_result.makespan if base_result.makespan > 0 else (
            sum(s.time for s in strips) / max(req.n_drones, 1)
        )

        all_coverage: List[float] = []
        all_priority: List[float] = []
        all_recovery: List[float] = []

        seeds = rng.integers(0, 100_000, size=req.n_runs).tolist()

        for i, run_seed in enumerate(seeds):
            def run_one(s=run_seed):
                r = np.random.default_rng(s)
                drain = float(r.uniform(0.0, req.battery_drain_max))
                events = generate_failures(req.n_drones, makespan_est, req.failure_prob, r)
                hist = simulate(
                    strips, drones, base_result,
                    req.nrows, req.ncols,
                    failure_events=events or None,
                    battery_drain_per_cell=drain,
                    dock_positions=dock_tuples,
                )
                return compute_metrics(hist, strips, req.nrows, req.ncols)

            m = await asyncio.to_thread(run_one)
            all_coverage.append(float(m.get("coverage_pct", 0)))
            all_priority.append(float(m.get("priority_coverage", 0)))
            rec = m.get("time_to_recovery")
            if rec is not None:
                all_recovery.append(float(rec))

            yield f"data: {json.dumps({'type': 'progress', 'done': i + 1, 'total': req.n_runs})}\n\n"

        cov = np.array(all_coverage)
        pri = np.array(all_priority)
        rec_arr = np.array(all_recovery) if all_recovery else np.array([0.0])

        result = {
            "type": "result",
            "n_runs": req.n_runs,
            "coverage_mean": float(np.mean(cov)),
            "coverage_p5": float(np.percentile(cov, 5)),
            "coverage_p50": float(np.median(cov)),
            "coverage_p95": float(np.percentile(cov, 95)),
            "coverage_std": float(np.std(cov)),
            "priority_mean": float(np.mean(pri)),
            "priority_p5": float(np.percentile(pri, 5)),
            "recovery_mean": float(np.mean(rec_arr)),
            "recovery_p95": float(np.percentile(rec_arr, 95)),
            "all_coverage": cov.tolist(),
            "all_recovery": rec_arr.tolist() if len(all_recovery) > 0 else [],
        }
        yield f"data: {json.dumps(result)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
