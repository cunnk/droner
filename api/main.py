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
from src.simulation.metrics import compute_metrics, compute_reforestation_metrics
from src.reforestation.config import MissionConfig, compute_sim_params
from src.reforestation.soil_detector import detect_soil_mask

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
    soil_mask: Optional[List[List[bool]]] = None
    nrows: int
    ncols: int
    n_drones: int = 3
    dock_positions: List[List[int]] = [[0, 0]]
    orientation_deg: float = 0.0
    strip_mode: str = "lawnmower"   # "lawnmower" | "shore_first"
    strip_width: int = 2
    # Mangrove / seed params
    battery_life_minutes: float = 35.0
    recharge_time_minutes: float = 60.0
    seed_capacity: int = 6000
    seed_spacing_m: float = 1.5
    seed_jitter_sigma: float = 0.3
    wind_speed_ms: float = 0.0
    failure_prob: float = 0.0
    survival_rate: float = 0.4
    tidal_threshold: float = 0.6
    seconds_per_cell: float = 2.0
    seed: int = 42


class MCRequest(BaseModel):
    grid: List[List[float]]
    soil_mask: Optional[List[List[bool]]] = None
    nrows: int
    ncols: int
    n_drones: int = 3
    dock_positions: List[List[int]] = [[0, 0]]
    orientation_deg: float = 0.0
    strip_mode: str = "lawnmower"   # "lawnmower" | "shore_first"
    strip_width: int = 2
    n_runs: int = 50
    # Mangrove params (varied per run or held fixed)
    battery_life_minutes: float = 35.0
    recharge_time_minutes: float = 60.0
    seed_capacity: int = 6000
    seed_spacing_m: float = 1.5
    seed_jitter_sigma: float = 0.3
    wind_speed_ms: float = 0.0
    failure_prob: float = 0.0
    survival_rate: float = 0.4
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


def _sort_strips_by_shore(strips, mask_np):
    """
    Re-order strips so those nearest the water/canopy boundary come first.
    This mimics natural mangrove colonisation: pioneer species establish
    closest to the tidal zone and expand inland.

    If no mask is available, strips are returned in their original order.
    """
    if mask_np is None or not strips:
        return strips

    try:
        from scipy.ndimage import distance_transform_edt
        # Distance from each plantable cell to nearest excluded cell (water/canopy)
        # distance_transform_edt measures distance to nearest *False* pixel when
        # applied to the inverted mask.
        dist = distance_transform_edt(mask_np)   # 0 at water edge, grows inland
        # For each strip compute mean distance of its spray cells from shore
        strip_distances = []
        for s in strips:
            if s.spray_cells:
                d = float(np.mean([dist[r, c] for r, c in s.spray_cells]))
            else:
                d = float(np.mean([dist[r, c] for r, c in s.cells])) if s.cells else 0.0
            strip_distances.append(d)
        # Sort ascending: strips closest to shore first
        ordered = sorted(zip(strip_distances, strips), key=lambda x: x[0])
        return [s for _, s in ordered]
    except ImportError:
        return strips


def build_plan(grid, nrows, ncols, n_drones, dock_positions, orientation_deg, seconds_per_cell,
               strip_mode="lawnmower", strip_width=2, soil_mask=None, seed_capacity=6000):
    """grid → strips → assignment. Returns (strips, drones, result, dock_tuples)."""
    grid_np = np.array(grid)
    mask_np = np.array(soil_mask, dtype=bool) if soil_mask is not None else None

    # Generate lawnmower strips for all modes
    strips = generate_strips(
        grid_np,
        seconds_per_cell=seconds_per_cell,
        orientation_deg=orientation_deg,
        field_mask=mask_np,
    )

    # Shore-first: reorder strips by distance from water/canopy edge
    if strip_mode == "shore_first":
        strips = _sort_strips_by_shore(strips, mask_np)

    drones = [DroneSpec(id=i, battery=100.0, seed_capacity=seed_capacity)
              for i in range(n_drones)]
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
    target_size: int = Query(64),
    orientation_deg: float = Query(0.0),
    seconds_per_cell: float = Query(2.0),
    soil_ndvi_threshold: float = Query(0.15),
    water_blue_threshold: float = Query(0.45),
    min_brightness_threshold: float = Query(0.42),
):
    content = await file.read()
    suffix = Path(file.filename).suffix or ".jpg"

    def _run():
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            from PIL import Image as PILImage
            # Soil mask + priority grid via mangrove detector
            soil_mask_np, priority_grid, meta = detect_soil_mask(
                tmp_path,
                target_size=target_size,
                soil_ndvi_threshold=soil_ndvi_threshold,
                water_blue_threshold=water_blue_threshold,
                min_brightness_threshold=min_brightness_threshold,
            )
            grid_np = priority_grid.astype(np.float32)
            # Generate strips using the soil mask as field mask
            strips = generate_strips(
                grid_np,
                seconds_per_cell=seconds_per_cell,
                orientation_deg=orientation_deg,
                field_mask=soil_mask_np,
            )
            # Background image: resize original, encode as base64 PNG
            pil_img = PILImage.open(tmp_path).convert('RGB')
            nrows, ncols = grid_np.shape
            pil_small = pil_img.resize((ncols, nrows), PILImage.LANCZOS)
            buf = io.BytesIO()
            pil_small.save(buf, format='PNG')
            img_b64 = base64.b64encode(buf.getvalue()).decode()
            image_data_url = f'data:image/png;base64,{img_b64}'
            # Orientation estimate on full-resolution grayscale
            gray_np = np.array(pil_img.convert('L')) / 255.0
            est_angle, est_conf = estimate_orientation_deg(gray_np)
            plantable = int(soil_mask_np.sum())
            return (grid_np, soil_mask_np, strips, image_data_url,
                    float(est_angle), float(est_conf), plantable)
        finally:
            os.unlink(tmp_path)

    grid_np, soil_mask_np, strips, image_data_url, est_angle, est_conf, plantable = \
        await asyncio.to_thread(_run)
    return {
        "grid": grid_np.tolist(),
        "soil_mask": soil_mask_np.tolist(),
        "nrows": int(grid_np.shape[0]),
        "ncols": int(grid_np.shape[1]),
        "n_strips": len(strips),
        "plantable_cells": plantable,
        "total_spray_time": float(sum(s.time for s in strips)),
        "image_data_url": image_data_url,
        "estimated_orientation_deg": est_angle,
        "orientation_confidence": est_conf,
    }


@app.post("/api/simulate")
async def run_simulation(req: SimulateRequest):
    def _run():
        rng = np.random.default_rng(req.seed)

        # Build MissionConfig from request
        cfg = MissionConfig(
            seed_capacity=req.seed_capacity,
            seed_spacing_m=req.seed_spacing_m,
            seed_jitter_sigma=req.seed_jitter_sigma,
            battery_life_minutes=req.battery_life_minutes,
            recharge_time_seconds=req.recharge_time_minutes * 60.0,
            wind_speed_ms=req.wind_speed_ms,
            tidal_threshold=req.tidal_threshold,
            seconds_per_cell=req.seconds_per_cell,
            survival_rate=req.survival_rate,
        )
        sim_params = compute_sim_params(cfg, ncols=req.ncols)

        strips, drones, assignment, dock_tuples = build_plan(
            req.grid, req.nrows, req.ncols, req.n_drones,
            req.dock_positions, req.orientation_deg, req.seconds_per_cell,
            strip_mode=req.strip_mode, strip_width=req.strip_width,
            soil_mask=req.soil_mask, seed_capacity=req.seed_capacity,
        )

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
            battery_drain_per_cell=sim_params["battery_drain_per_cell"],
            recharge_time_steps=sim_params["recharge_time_steps"],
            seeds_per_cell=sim_params["seeds_per_cell"],
            seed_jitter_sigma=sim_params["seed_jitter_sigma"],
            dock_positions=dock_tuples,
        )

        # Compute standard + reforestation metrics
        metrics = compute_metrics(history, strips, req.nrows, req.ncols)
        refo_metrics = compute_reforestation_metrics(
            history, strips, req.nrows, req.ncols,
            survival_rate=req.survival_rate,
            meters_per_cell=sim_params["meters_per_cell"],
        )
        # Merge (reforestation keys take precedence for overlapping names)
        merged = {**metrics, **refo_metrics}

        plan_info = {
            "status": assignment.status,
            "objective_mode": assignment.objective_mode,
            "makespan": float(assignment.makespan),
            "solve_time": float(assignment.solve_time),
            "n_strips": len(strips),
            "assignment": {str(k): v for k, v in assignment.assignment.items()},
            "failure_events": failure_events,
        }

        scalar_metrics = {}
        series_metrics = {}
        for k, v in merged.items():
            if isinstance(v, (list, np.ndarray)):
                series_metrics[k] = to_python(v)
            elif isinstance(v, dict):
                pass
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
                strip_mode=req.strip_mode, strip_width=req.strip_width,
                soil_mask=req.soil_mask, seed_capacity=req.seed_capacity,
            )

        strips, drones, base_result, dock_tuples = await asyncio.to_thread(setup)
        makespan_est = base_result.makespan if base_result.makespan > 0 else (
            sum(s.time for s in strips) / max(req.n_drones, 1)
        )

        all_coverage: List[float] = []
        all_priority: List[float] = []
        all_recovery: List[float] = []

        seeds = rng.integers(0, 100_000, size=req.n_runs).tolist()

        # Build MissionConfig once for shared sim params
        cfg = MissionConfig(
            seed_capacity=req.seed_capacity,
            seed_spacing_m=req.seed_spacing_m,
            seed_jitter_sigma=req.seed_jitter_sigma,
            battery_life_minutes=req.battery_life_minutes,
            recharge_time_seconds=req.recharge_time_minutes * 60.0,
            wind_speed_ms=req.wind_speed_ms,
            seconds_per_cell=req.seconds_per_cell,
            survival_rate=req.survival_rate,
        )
        sim_params = compute_sim_params(cfg, ncols=req.ncols)

        all_seeds: List[float] = []
        all_survivors: List[float] = []

        for i, run_seed in enumerate(seeds):
            def run_one(s=run_seed):
                r = np.random.default_rng(s)
                events = generate_failures(req.n_drones, makespan_est, req.failure_prob, r)
                hist = simulate(
                    strips, drones, base_result,
                    req.nrows, req.ncols,
                    failure_events=events or None,
                    battery_drain_per_cell=sim_params["battery_drain_per_cell"],
                    recharge_time_steps=sim_params["recharge_time_steps"],
                    seeds_per_cell=sim_params["seeds_per_cell"],
                    seed_jitter_sigma=sim_params["seed_jitter_sigma"],
                    dock_positions=dock_tuples,
                )
                base_m = compute_metrics(hist, strips, req.nrows, req.ncols)
                refo_m = compute_reforestation_metrics(
                    hist, strips, req.nrows, req.ncols,
                    survival_rate=req.survival_rate,
                    meters_per_cell=sim_params["meters_per_cell"],
                )
                return {**base_m, **refo_m}

            m = await asyncio.to_thread(run_one)
            all_coverage.append(float(m.get("coverage_pct", 0)))
            all_priority.append(float(m.get("priority_coverage", 0)))
            rec = m.get("time_to_recovery")
            if rec is not None:
                all_recovery.append(float(rec))
            seeds_planted = m.get("total_seeds_planted")
            if seeds_planted is not None:
                all_seeds.append(float(seeds_planted))
            survivors = m.get("expected_survivors")
            if survivors is not None:
                all_survivors.append(float(survivors))

            yield f"data: {json.dumps({'type': 'progress', 'done': i + 1, 'total': req.n_runs})}\n\n"

        cov = np.array(all_coverage)
        pri = np.array(all_priority)
        rec_arr = np.array(all_recovery) if all_recovery else np.array([0.0])
        seeds_arr = np.array(all_seeds) if all_seeds else None
        surv_arr = np.array(all_survivors) if all_survivors else None

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
        if seeds_arr is not None:
            result["seeds_median"] = float(np.median(seeds_arr))
            result["seeds_p5"] = float(np.percentile(seeds_arr, 5))
        if surv_arr is not None:
            result["survivors_median"] = float(np.median(surv_arr))
        yield f"data: {json.dumps(result)}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
