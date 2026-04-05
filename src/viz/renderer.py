"""
Visualisation layer — fully decoupled from simulation and optimisation.

Input:  state_history produced by simulate()
Output: matplotlib animation (displayed or saved as GIF)

Two rendering modes
-------------------
Standard mode (default)
    Solid-colour grid cells on a white background.
    Best for small grids and synthetic data.

Overlay mode  (pass background_image=<numpy array>)
    Aerial photograph as background.
    Coverage status rendered as a semi-transparent RGBA layer on top:
        Untouched  : fully transparent — photograph shows through unobstructed
        In-progress: yellow tint  (alpha 0.55)
        Complete   : gray tint    (alpha 0.45)
        Failed     : red tint     (alpha 0.65)
    Drone markers and info panel drawn on top of both layers.

Grid lines
----------
Drawn by default for grids ≤ 64 cells per side.
Automatically suppressed for larger grids (too dense to be useful).
Override with show_grid_lines=True/False.
"""

from __future__ import annotations
from typing import Dict, List, Any, Optional, Tuple

import numpy as np
import matplotlib
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.animation import FuncAnimation, PillowWriter
from matplotlib.colors import ListedColormap

# Cell value → colour (used in standard mode)
CELL_CMAP = ListedColormap([
    "#c8e6c9",   # 0 untouched   → light green
    "#fff176",   # 1 in-progress → yellow
    "#bdbdbd",   # 2 complete    → gray
    "#ef9a9a",   # 3 failed      → light red
])

# Cell value → RGBA used in overlay mode
# Untouched = fully transparent so the aerial photo shows through
_OVERLAY_COLOURS: Dict[int, Tuple[float, float, float, float]] = {
    0: (0.00, 0.00, 0.00, 0.00),   # untouched   — transparent
    1: (1.00, 0.95, 0.20, 0.55),   # in-progress — yellow
    2: (0.65, 0.65, 0.65, 0.45),   # complete    — gray
    3: (0.92, 0.30, 0.30, 0.65),   # failed      — red
}

DRONE_COLOURS = [
    "#1565c0",  # blue
    "#e65100",  # orange
    "#558b2f",  # dark green
    "#6a1b9a",  # purple
    "#00838f",  # teal
]

# State → (face_colour, alpha)
STATE_STYLE: Dict[str, tuple] = {
    "spraying" : (None,      1.0),
    "idle"     : (None,      0.5),
    "moving"   : (None,      0.7),
    "returning": ("#f9a825", 1.0),
    "charging" : ("#9e9e9e", 1.0),
    "frozen"   : ("#78909c", 1.0),
    "failed"   : ("#b71c1c", 1.0),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _coverage_pct(grid: List[List[int]],
                  n_plantable: Optional[int] = None) -> float:
    """Fraction of cells with state==2 (complete/seeded).

    Parameters
    ----------
    grid:
        2-D list of cell states.
    n_plantable:
        Denominator override — total number of plantable (soil mask) cells.
        When provided, coverage is expressed as a fraction of the viable area
        rather than the full grid.  Pass ``sum(len(s.spray_cells) for s in strips)``
        from the calling code.  When None (default), the full grid is used as
        denominator (backward-compatible behaviour for spray missions).
    """
    flat = [c for row in grid for c in row]
    done = sum(1 for c in flat if c == 2)
    denom = n_plantable if (n_plantable is not None and n_plantable > 0) else len(flat)
    return 100.0 * done / denom if denom else 0.0


def _replan_steps(state_history: List[Dict[str, Any]]) -> List[int]:
    return [s["timestep"] for s in state_history
            if s.get("event") and "Replanned" in s["event"]]


def _grid_to_rgba(grid_arr: np.ndarray) -> np.ndarray:
    """Convert integer cell grid to RGBA overlay array."""
    h, w   = grid_arr.shape
    rgba   = np.zeros((h, w, 4), dtype=np.float32)
    for state_val, colour in _OVERLAY_COLOURS.items():
        mask = grid_arr == state_val
        rgba[mask] = colour
    return rgba


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def animate(
    state_history: List[Dict[str, Any]],
    nrows: int,
    ncols: int,
    interval_ms: int = 200,
    save_path: Optional[str] = None,
    show: bool = True,
    dock_positions: Optional[List[Tuple[int, int]]] = None,
    background_image: Optional[np.ndarray] = None,
    show_grid_lines: Optional[bool] = None,
    frame_step: int = 1,
    max_frames: Optional[int] = None,
    # ---- Reforestation options (backward-compatible defaults) ----
    show_seed_drops: bool = False,
    mode_label: str = "Drone Fleet Simulation",
    n_plantable_cells: Optional[int] = None,
) -> FuncAnimation:
    """
    Render state_history as a matplotlib animation.

    Args:
        state_history   : Output of simulate().
        nrows, ncols    : Grid dimensions.
        interval_ms     : Milliseconds between frames.
        save_path       : If given (e.g. "results/run.gif"), save animation there.
        show            : Whether to call plt.show().
        dock_positions  : List of (row, col) dock locations. If None, inferred
                          from drone states.
        background_image: Optional H×W×3 uint8 or float32 numpy array of the
                          aerial photograph, already cropped and resized to
                          (nrows × ncols) pixels. When provided, the animation
                          renders in overlay mode: aerial photo as background,
                          semi-transparent coverage tints on top.
                          Use load_image_as_array() from src.field.ingest to
                          get the correctly aligned image.
        show_grid_lines : Draw white cell borders. Defaults to True for grids
                          ≤ 64 cells per side, False for larger grids.
        frame_step      : Render every Nth timestep (default 1 = every step).
                          Use e.g. 10 to cut a 2000-step sim to 200 frames.
        max_frames      : If set, auto-compute frame_step so the GIF has at most
                          this many frames. Overrides frame_step when specified.
        show_seed_drops : If True, render a static scatter of all seed drop
                          positions (from state_history["seed_drops"]) as a
                          translucent purple cloud on the grid.  Zero per-frame
                          cost — drawn once before the animation loop.
        mode_label      : Title prefix shown in every frame (e.g.
                          "Mangrove Reforestation Mission").
        n_plantable_cells: Total number of plantable (soil mask) cells.
                          When provided, the info panel and title show
                          "soil seeded %" using this as the denominator
                          instead of the full grid area.  Pass
                          ``sum(len(s.spray_cells) for s in strips)``.
                          Omit for spray missions (backward-compatible).

    Returns:
        FuncAnimation object (useful for Jupyter display).
    """
    if max_frames is not None and max_frames > 0:
        frame_step = max(1, len(state_history) // max_frames)
    overlay_mode = background_image is not None

    # Default grid line behaviour: on for small grids, off for large
    if show_grid_lines is None:
        show_grid_lines = (nrows <= 64 and ncols <= 64)

    # Drone marker radius scales with grid size
    marker_radius = max(0.25, min(0.45, 10.0 / max(nrows, ncols)))
    font_size_drone = max(5, min(9, 400 // max(nrows, ncols)))

    replan_steps = set(_replan_steps(state_history))
    drone_colours = {
        ds["id"]: DRONE_COLOURS[i % len(DRONE_COLOURS)]
        for i, ds in enumerate(state_history[0]["drones"])
    }

    # Infer dock positions from drone state if not provided
    if dock_positions is None:
        seen = set()
        dock_positions = []
        for ds in state_history[0]["drones"]:
            p = tuple(ds.get("dock", (0, 0)))
            if p not in seen:
                seen.add(p)
                dock_positions.append(p)

    # Figure layout
    fig = plt.figure(figsize=(12, 5))
    gs  = fig.add_gridspec(1, 2, width_ratios=[3, 1])
    ax_grid = fig.add_subplot(gs[0])
    ax_info = fig.add_subplot(gs[1])
    ax_info.axis("off")

    ax_grid.set_xlim(-0.5, ncols - 0.5)
    ax_grid.set_ylim(nrows - 0.5, -0.5)   # origin='upper'
    ax_grid.set_xticks([])
    ax_grid.set_yticks([])
    ax_grid.set_aspect("equal")

    if overlay_mode:
        # Layer 1: aerial photograph (static)
        bg = background_image.astype(np.float32)
        if bg.max() > 1.0:
            bg = bg / 255.0
        ax_grid.imshow(bg, origin="upper", extent=(-0.5, ncols - 0.5, nrows - 0.5, -0.5),
                       aspect="equal", zorder=1)
        # Layer 2: RGBA coverage overlay (updated each frame)
        init_rgba = np.zeros((nrows, ncols, 4), dtype=np.float32)
        im = ax_grid.imshow(init_rgba, origin="upper",
                            extent=(-0.5, ncols - 0.5, nrows - 0.5, -0.5),
                            aspect="equal", zorder=2)
    else:
        blank = np.zeros((nrows, ncols), dtype=int)
        im = ax_grid.imshow(blank, cmap=CELL_CMAP, vmin=0, vmax=3,
                            origin="upper", aspect="equal", zorder=1)

    # Grid lines (small grids only)
    if show_grid_lines:
        lw = max(0.3, 1.0 - ncols / 128)
        for x in range(ncols + 1):
            ax_grid.axvline(x - 0.5, color="white", linewidth=lw, zorder=3)
        for y in range(nrows + 1):
            ax_grid.axhline(y - 0.5, color="white", linewidth=lw, zorder=3)

    # Dock markers
    dock_marker_size = max(6, min(12, 400 // max(nrows, ncols)))
    for dr, dc in dock_positions:
        ax_grid.plot(dc, dr, marker="D", markersize=dock_marker_size,
                     color="#212121", markeredgecolor="white",
                     markeredgewidth=1.2, zorder=7)
        ax_grid.text(dc, dr, "D", ha="center", va="center",
                     fontsize=max(5, dock_marker_size - 3),
                     color="white", fontweight="bold", zorder=8)

    # Seed drop scatter (reforestation mode — static, drawn once)
    if show_seed_drops:
        all_xs, all_ys = [], []
        for state in state_history:
            for drop in state.get("seed_drops", []):
                ar, ac = drop["actual"]
                all_xs.append(ac)
                all_ys.append(ar)
        if all_xs:
            ax_grid.scatter(all_xs, all_ys, s=1.5, alpha=0.08,
                            color="#4a148c", linewidths=0, zorder=2.5,
                            label="Seed drops")

    # Drone markers
    drone_circles = {}
    drone_texts   = {}
    for ds in state_history[0]["drones"]:
        d_id   = ds["id"]
        circle = plt.Circle((0, 0), marker_radius,
                             color=drone_colours[d_id], zorder=5)
        ax_grid.add_patch(circle)
        txt = ax_grid.text(0, 0, str(d_id), ha="center", va="center",
                           fontsize=font_size_drone, color="white",
                           fontweight="bold", zorder=6)
        drone_circles[d_id] = circle
        drone_texts[d_id]   = txt

    # Legend
    if overlay_mode:
        legend_patches = [
            mpatches.Patch(facecolor=(1.0, 0.95, 0.2),  label="In progress"),
            mpatches.Patch(facecolor=(0.65, 0.65, 0.65), label="Complete"),
            mpatches.Patch(facecolor=(0.92, 0.3, 0.3),  label="Failed cell"),
            mpatches.Patch(facecolor="#f9a825",          label="Drone returning"),
            mpatches.Patch(facecolor="#9e9e9e",          label="Drone charging"),
        ]
    else:
        legend_patches = [
            mpatches.Patch(color="#c8e6c9", label="Untouched"),
            mpatches.Patch(color="#fff176", label="In progress"),
            mpatches.Patch(color="#bdbdbd", label="Complete"),
            mpatches.Patch(color="#ef9a9a", label="Failed cell"),
            mpatches.Patch(color="#f9a825", label="Drone returning"),
            mpatches.Patch(color="#9e9e9e", label="Drone charging"),
        ]
    ax_grid.legend(handles=legend_patches, loc="lower right",
                   fontsize=6.5, framealpha=0.85)

    # Pre-compute cumulative seeds planted up to each frame index
    # (empty list when no seed data — backward-compatible)
    _cumulative_seeds: List[int] = []
    _running = 0
    for _s in state_history:
        _running += len(_s.get("seed_drops", []))
        _cumulative_seeds.append(_running)
    _seed_mode = _running > 0   # True only when seed drops are present

    # Info panel
    info_text = ax_info.text(
        0.05, 0.95, "", transform=ax_info.transAxes,
        fontsize=9, va="top", ha="left", family="monospace",
        linespacing=1.6,
    )

    def _update(frame_idx: int):
        state  = state_history[frame_idx]
        grid   = np.array(state["grid"], dtype=int)
        drones = {ds["id"]: ds for ds in state["drones"]}
        t      = state["timestep"]

        if overlay_mode:
            im.set_data(_grid_to_rgba(grid))
        else:
            im.set_data(grid)

        for d_id, circle in drone_circles.items():
            ds   = drones[d_id]
            r, c = ds["position"]
            circle.center = (c, r)
            drone_texts[d_id].set_position((c, r))

            drone_state = ds["state"]
            style       = STATE_STYLE.get(drone_state, (None, 1.0))
            face_colour = style[0] if style[0] else drone_colours[d_id]
            circle.set_facecolor(face_colour)
            circle.set_alpha(style[1])

        # Coverage — use plantable denominator when available
        cov         = _coverage_pct(state["grid"], n_plantable_cells)
        replan_flag = " [REPLAN]" if t in replan_steps else ""
        event_str   = state.get("event") or "—"
        if len(event_str) > 38:
            event_str = event_str[:35] + "..."

        seeds_so_far = _cumulative_seeds[frame_idx] if _seed_mode else None

        # Build info panel text
        if _seed_mode:
            cov_label  = "Soil seeded"
            seeds_line = f"Seeds     : {seeds_so_far:,}\n"
        else:
            cov_label  = "Coverage"
            seeds_line = ""

        info_lines = (
            f"Step      : {t}{replan_flag}\n"
            f"{cov_label:<10}: {cov:.1f}%\n"
            f"{seeds_line}"
            f"\nDrones\n"
            + "\n".join(
                (
                    f"  D{ds['id']} {ds['state'][:9]:<9} "
                    f"bat={ds['battery']:.0f}%"
                    + (f" sd={ds['seed_load']:.0f}"
                       if ds.get("seed_load") is not None else "")
                )
                for ds in state["drones"]
            )
            + f"\n\nEvent:\n  {event_str}"
        )
        info_text.set_text(info_lines)

        # Title — show seeds + soil-seeded % in reforestation mode
        if _seed_mode:
            title = (
                f"{mode_label}  |  t={t}  |  "
                f"{cov:.1f}% soil seeded  |  "
                f"{seeds_so_far:,} seeds planted"
                + ("  [overlay]" if overlay_mode else "")
            )
        else:
            title = (
                f"{mode_label}  |  t={t}  |  {cov:.1f}% covered"
                + ("  [overlay]" if overlay_mode else "")
            )
        ax_grid.set_title(title, fontsize=10, fontweight="bold")

        return [im, info_text] + list(drone_circles.values()) + list(drone_texts.values())

    frame_indices = range(0, len(state_history), frame_step)
    anim = FuncAnimation(
        fig, _update,
        frames=frame_indices,
        interval=interval_ms,
        blit=False,
    )

    if save_path:
        writer = PillowWriter(fps=max(1, 1000 // interval_ms))
        anim.save(save_path, writer=writer)
        print(f"Saved animation to {save_path}")

    if show:
        plt.tight_layout()
        plt.show()

    return anim
