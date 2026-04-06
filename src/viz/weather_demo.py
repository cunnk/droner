"""
Side-by-side GIF: Run A (Baseline) vs Run B (Field-Adaptive MCP).

Timeline
--------
  0 -> notice_frame        : both panels identical, mission in progress
  notice_frame            : storm ALERT fires on Run B -- countdown + mandatory zones revealed
  storm_frame (last sim)  : storm ARRIVES -- both panels flash, mission ends
  hold frames             : freeze on final state for 1 s

Terminology
-----------
  strip.priority  = spray urgency derived from vegetation density (0-1)
  mandatory zones = compliance / disease-priority strips that MUST be done
  Labelled differently in displays to avoid confusion.
"""
from __future__ import annotations

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.animation import FuncAnimation
from matplotlib.gridspec import GridSpec
from typing import Any, Dict, List, Optional


DRONE_COLOURS = [
    "#00e5ff",  # cyan
    "#ff6d00",  # deep orange
    "#76ff03",  # lime
    "#ea80fc",  # purple
    "#ffea00",  # yellow
]

_OVERLAY: Dict[int, tuple] = {
    0: (0.00, 0.00, 0.00, 0.00),
    1: (1.00, 0.95, 0.20, 0.55),
    2: (0.65, 0.65, 0.65, 0.45),
    3: (0.92, 0.30, 0.30, 0.65),
}
_MANDATORY_DONE_RGBA = (1.00, 0.70, 0.00, 0.65)   # gold when mandatory cell sprayed


def _coverage_pct(grid):
    total = sum(len(r) for r in grid)
    done  = sum(cell == 2 for row in grid for cell in row)
    return 100.0 * done / total if total else 0.0


def _urgency_coverage(grid, urgency_grid):
    total = urgency_grid.sum()
    if total == 0:
        return 0.0
    g = np.array(grid)
    return float(((g == 2).astype(float) * urgency_grid).sum() / total)


def _mandatory_coverage(grid, mandatory_cells):
    if not mandatory_cells:
        return 0.0
    done = sum(1 for (r, c) in mandatory_cells if grid[r][c] == 2)
    return 100.0 * done / len(mandatory_cells)


def _grid_to_rgba(grid_arr, mandatory_mask=None):
    h, w  = grid_arr.shape
    rgba  = np.zeros((h, w, 4), dtype=np.float32)
    for val, colour in _OVERLAY.items():
        rgba[grid_arr == val] = colour
    if mandatory_mask is not None:
        rgba[(grid_arr == 2) & mandatory_mask] = _MANDATORY_DONE_RGBA
    return rgba


def create_weather_demo_gif(
    session_a: Dict[str, Any],
    session_b: Dict[str, Any],
    metrics_a: Dict[str, Any],
    metrics_b: Dict[str, Any],
    notice_timestep: int = 0,
    storm_timestep: int  = 0,
    save_path: str       = "results/mcp_demo.gif",
    fps: int             = 7,
    frame_step: int      = 3,
    background_image: Optional[np.ndarray] = None,
    field_events: Optional[List[Dict]]     = None,
    # Legacy pct params kept for backwards compat but ignored when timesteps > 0
    notice_frame_pct: float = 0.40,
    storm_frame_pct: float  = 0.70,
) -> str:
    hist_a       = session_a["state_history"]
    hist_b       = session_b["state_history"]
    urgency_grid = np.array(session_a["grid"])

    frames_a = hist_a[::frame_step]
    frames_b = hist_b[::frame_step]
    n_sim    = max(len(frames_a), len(frames_b))

    # Simulation is already capped at storm_timestep -- last frame IS the storm arrival
    storm_frame  = n_sim - 1
    notice_frame = (notice_timestep // frame_step) if notice_timestep > 0 \
                   else int(n_sim * notice_frame_pct)
    notice_frame = min(notice_frame, storm_frame - 1)

    # Countdown from notice -> storm
    storm_seconds = max(storm_timestep - notice_timestep, 1) * 2.0   # 2 s/cell
    storm_minutes = storm_seconds / 60.0

    # Hold on final frame for 1 s after storm
    n_hold        = max(fps, 4)
    n_frames_total = n_sim + n_hold

    # Mandatory geometry
    mandatory_ids   = set(session_a.get("mandatory_strip_ids", []))
    strip_map       = {s.id: s for s in session_a["strips"]}
    mandatory_cells: set = set()
    for sid in mandatory_ids:
        if sid in strip_map:
            mandatory_cells.update(strip_map[sid].spray_cells)

    nrows, ncols = urgency_grid.shape
    mandatory_mask = np.zeros((nrows, ncols), dtype=bool)
    for (r, c) in mandatory_cells:
        mandatory_mask[r, c] = True

    mand_rows = [r for (r, _) in mandatory_cells]
    mand_cols = [c for (_, c) in mandatory_cells]

    # Field events -> frame windows (~2 s visible)
    event_display: List[Dict] = []
    for ev in (field_events or []):
        sf = ev["timestep"] // frame_step
        event_display.append({
            "start": sf, "end": sf + max(6, fps * 2),
            "row": ev["row"], "col": ev["col"],
            "label": ev["event_type"].replace("_", " ").title(),
        })

    # Drone metadata
    drone_ids  = [ds["id"] for ds in frames_a[0]["drones"]]
    d_colours  = {d_id: DRONE_COLOURS[i % len(DRONE_COLOURS)]
                  for i, d_id in enumerate(drone_ids)}
    dock_set: set = set()
    for ds in frames_a[0]["drones"]:
        dock_set.add(tuple(ds.get("dock", (0, 0))))

    marker_r   = max(0.30, min(0.55, 12.0 / max(nrows, ncols)))
    font_drone = max(6,  min(10,  420 // max(nrows, ncols)))
    dock_ms    = max(9,  min(16,  500 // max(nrows, ncols)))

    # -- Figure ---------------------------------------------------------------
    has_log = bool(field_events)
    fig_h   = 8.2 if has_log else 6.5
    fig     = plt.figure(figsize=(13, fig_h))
    fig.patch.set_facecolor("#1a1a2e")

    if has_log:
        gs   = GridSpec(2, 2, figure=fig, height_ratios=[5.5, 1.0],
                        hspace=0.38, left=0.04, right=0.96, top=0.95, bottom=0.09)
        axes = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])]
        ax_log = fig.add_subplot(gs[1, :])
    else:
        gs   = GridSpec(1, 2, figure=fig,
                        left=0.04, right=0.96, top=0.95, bottom=0.10)
        axes  = [fig.add_subplot(gs[0, 0]), fig.add_subplot(gs[0, 1])]
        ax_log = None

    for ax in axes:
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="white")
        for sp in ax.spines.values():
            sp.set_edgecolor("#444")
            sp.set_linewidth(1.0)

    # Event log panel
    log_text = None
    if ax_log is not None:
        ax_log.set_facecolor("#0d1117")
        ax_log.set_xticks([])
        ax_log.set_yticks([])
        for sp in ax_log.spines.values():
            sp.set_edgecolor("#30363d")
            sp.set_linewidth(1.0)
        log_text = ax_log.text(
            0.01, 0.92,
            "[MCP] MCP DATA STREAM  (Run B field intelligence -- not visible to Run A)",
            transform=ax_log.transAxes,
            ha="left", va="top", color="#58a6ff",
            fontsize=8.0, fontfamily="monospace", zorder=5,
        )

    overlay_mode = background_image is not None
    extent = (-0.5, ncols - 0.5, nrows - 0.5, -0.5)

    if overlay_mode:
        bg = background_image.astype(np.float32)
        if bg.max() > 1.0:
            bg = bg / 255.0
        for ax in axes:
            ax.imshow(bg, origin="upper", extent=extent, aspect="equal", zorder=1)
        init = np.zeros((nrows, ncols, 4), dtype=np.float32)
        img_a = axes[0].imshow(init.copy(), origin="upper", extent=extent,
                               aspect="equal", zorder=2)
        img_b = axes[1].imshow(init.copy(), origin="upper", extent=extent,
                               aspect="equal", zorder=2)
    else:
        from matplotlib.colors import ListedColormap, BoundaryNorm
        _cmap = ListedColormap(["#4caf50", "#ffeb3b", "#9e9e9e", "#f44336"])
        _norm = BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], _cmap.N)
        init_g = np.zeros((nrows, ncols), dtype=int)
        img_a  = axes[0].imshow(init_g.copy(), cmap=_cmap, norm=_norm,
                                interpolation="nearest")
        img_b  = axes[1].imshow(init_g.copy(), cmap=_cmap, norm=_norm,
                                interpolation="nearest")

    # Mandatory zone outlines -- Run A: never shown  |  Run B: hidden until notice
    mand_scat_b = None
    if mandatory_cells:
        mand_scat_b = axes[1].scatter(
            mand_cols, mand_rows, marker="s", s=18, linewidths=0.8,
            facecolors="none", edgecolors="#ffd600", zorder=5, alpha=0.90,
        )
        mand_scat_b.set_visible(False)   # revealed at notice_frame only

    # Field-event markers
    ev_scat_a = axes[0].scatter([], [], marker="*", s=150, c="#ff1744", zorder=6)
    ev_scat_b = axes[1].scatter([], [], marker="*", s=150, c="#ff1744", zorder=6)
    ev_lbl_a  = axes[0].text(0, 0, "", color="#ff1744", fontsize=7.5,
                             fontweight="bold", ha="center", va="bottom", zorder=7)
    ev_lbl_b  = axes[1].text(0, 0, "", color="#ff1744", fontsize=7.5,
                             fontweight="bold", ha="center", va="bottom", zorder=7)

    # Docks -- gold diamond, visible on both panels
    for (dr, dc) in dock_set:
        for ax in axes:
            ax.plot(dc, dr, marker="D", markersize=dock_ms,
                    color="#ffd600", markeredgecolor="white",
                    markeredgewidth=1.5, zorder=7)
            ax.text(dc, dr, "D", ha="center", va="center",
                    fontsize=max(5, dock_ms - 4), color="#1a1a2e",
                    fontweight="bold", zorder=8)

    # Drone circles -- per panel, white edge for contrast
    circ_a: Dict[int, plt.Circle] = {}
    text_a: Dict[int, plt.Text]   = {}
    circ_b: Dict[int, plt.Circle] = {}
    text_b: Dict[int, plt.Text]   = {}
    for d_id in drone_ids:
        col = d_colours[d_id]
        for cd, td, ax in [(circ_a, text_a, axes[0]), (circ_b, text_b, axes[1])]:
            c = plt.Circle((0, 0), marker_r, color=col, zorder=9,
                           linewidth=1.2, edgecolor="white")
            ax.add_patch(c)
            t = ax.text(0, 0, str(d_id), ha="center", va="center",
                        fontsize=font_drone, color="white",
                        fontweight="bold", zorder=10)
            cd[d_id] = c
            td[d_id] = t

    # Titles
    title_a = axes[0].set_title(
        "Run A -- Baseline (makespan)\nNo field intelligence",
        color="white", fontsize=11, fontweight="bold", pad=8,
    )
    title_b_ax = axes[1].set_title(
        "Run B -- Field-Adaptive (MCP)\nLive data stream coordination",
        color="#90caf9", fontsize=11, fontweight="bold", pad=8,
    )

    # Coverage counters -- low enough to clear x-tick labels
    cov_txt_a = axes[0].text(0.5, -0.11, "Coverage: --",
                             transform=axes[0].transAxes,
                             ha="center", color="white", fontsize=9)
    cov_txt_b = axes[1].text(0.5, -0.11, "Coverage: --",
                             transform=axes[1].transAxes,
                             ha="center", color="#90caf9", fontsize=9)

    # Storm alert badge -- Run B right side, hidden until notice
    alert_badge = axes[1].text(
        0.98, 0.97,
        "[!]  STORM ALERT\n   --:-- remaining",
        transform=axes[1].transAxes,
        ha="right", va="top", color="white",
        fontsize=8.5, fontweight="bold", zorder=10,
        bbox=dict(boxstyle="round,pad=0.4", facecolor="#b71c1c",
                  edgecolor="#ff5252", linewidth=1.5),
    )
    alert_badge.set_visible(False)

    # MCP badge -- inside Run B panel at bottom, hidden until notice
    mcp_badge = axes[1].text(
        0.5, 0.03,
        "[*] MCP ENGAGED -- COMPLIANCE-FIRST REPLAN",
        transform=axes[1].transAxes,
        ha="center", va="bottom", color="#ffeb3b",
        fontsize=8.5, fontweight="bold", zorder=11,
        bbox=dict(boxstyle="round,pad=0.3", facecolor="#1565c0",
                  edgecolor="#ffeb3b", linewidth=1.5, alpha=0.92),
    )
    mcp_badge.set_visible(False)

    # Storm-arrived overlay -- both panels, hidden until storm + hold
    def _make_storm_overlay(ax):
        t = ax.text(0.5, 0.5, "STORM\nARRIVED",
                    transform=ax.transAxes,
                    ha="center", va="center",
                    color="white", fontsize=22, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.5",
                              facecolor="#b71c1c", edgecolor="#ff5252",
                              linewidth=2, alpha=0.85),
                    zorder=20)
        t.set_visible(False)
        return t

    storm_ov_a = _make_storm_overlay(axes[0])
    storm_ov_b = _make_storm_overlay(axes[1])

    suptitle = fig.suptitle("Drone Fleet -- Mission in Progress",
                            color="white", fontsize=13,
                            fontweight="bold", y=0.99)

    # Legend
    legend_patches = [
        mpatches.Patch(color="#4caf50", label="Untreated"),
        mpatches.Patch(color="#9e9e9e", label="Sprayed"),
    ]
    if mandatory_cells:
        legend_patches += [
            mpatches.Patch(facecolor="none", edgecolor="#ffd600",
                           linewidth=1.5, label="Mandatory zone (pending)"),
            mpatches.Patch(color="#ffb300", label="Mandatory zone (complete)"),
        ]
    if field_events:
        legend_patches.append(mpatches.Patch(color="#ff1744", label="Field event"))
    fig.legend(handles=legend_patches, loc="lower center",
               ncol=min(5, len(legend_patches)),
               bbox_to_anchor=(0.5, 0.0),
               facecolor="#1a1a2e", labelcolor="white",
               edgecolor="#444", fontsize=8.5)

    # Layout handled by GridSpec -- no tight_layout needed

    # -- Helpers --------------------------------------------------------------

    def _update_drones(cd, td, frames, fi):
        fi   = min(fi, len(frames) - 1)
        dmap = {ds["id"]: ds for ds in frames[fi]["drones"]}
        for d_id, circle in cd.items():
            ds = dmap.get(d_id)
            if ds is None:
                continue
            r, c = ds["position"]
            circle.center = (c, r)
            td[d_id].set_position((c, r))
            st = ds.get("state", "moving")
            if st in ("returning", "charging"):
                circle.set_facecolor("#f9a825")
            elif st == "failed":
                circle.set_facecolor("#b71c1c")
            else:
                circle.set_facecolor(d_colours[d_id])
            circle.set_alpha(0.5 if st == "idle" else 1.0)

    # -- Animation update -----------------------------------------------------

    def update(frame):
        # Clamp to last real sim frame during hold
        fa = min(frame, len(frames_a) - 1)
        fb = min(frame, len(frames_b) - 1)

        ga = np.array(frames_a[fa]["grid"])
        gb = np.array(frames_b[fb]["grid"])

        if overlay_mode:
            img_a.set_data(_grid_to_rgba(ga))                    # Run A: no mandatory colouring
            img_b.set_data(_grid_to_rgba(gb, mandatory_mask))    # Run B: gold when mandatory done
        else:
            img_a.set_data(ga)
            img_b.set_data(gb)

        _update_drones(circ_a, text_a, frames_a, fa)
        _update_drones(circ_b, text_b, frames_b, fb)

        cov_a  = _coverage_pct(frames_a[fa]["grid"])
        cov_b  = _coverage_pct(frames_b[fb]["grid"])
        ucov_a = _urgency_coverage(frames_a[fa]["grid"], urgency_grid)
        ucov_b = _urgency_coverage(frames_b[fb]["grid"], urgency_grid)
        mcov_b = _mandatory_coverage(frames_b[fb]["grid"], mandatory_cells)

        cov_txt_a.set_text(
            f"Coverage {cov_a:.1f}%  |  Urgency {ucov_a:.1%}"
        )
        cov_txt_b.set_text(
            f"Coverage {cov_b:.1f}%  |  Urgency {ucov_b:.1%}"
            + (f"  |  Mandatory {mcov_b:.0f}%" if mandatory_cells and frame >= notice_frame else "")
        )

        # Field events
        active = [e for e in event_display if e["start"] <= frame < e["end"]]
        if active:
            ec = [e["col"] for e in active]
            er = [e["row"] for e in active]
            ev_scat_a.set_offsets(np.column_stack([ec, er]))
            ev_scat_b.set_offsets(np.column_stack([ec, er]))
            first = active[0]
            ev_lbl_a.set_position((first["col"], first["row"] - 1.2))
            ev_lbl_b.set_position((first["col"], first["row"] - 1.2))
            ev_lbl_a.set_text(first["label"])
            ev_lbl_b.set_text(first["label"])
        else:
            ev_scat_a.set_offsets(np.empty((0, 2)))
            ev_scat_b.set_offsets(np.empty((0, 2)))
            ev_lbl_a.set_text("")
            ev_lbl_b.set_text("")

        # -- Event log ----------------------------------------------------
        if log_text is not None:
            cur_sim_t = frame * frame_step
            fired = [ev for ev in (field_events or []) if ev["timestep"] <= cur_sim_t]
            fired_sorted = sorted(fired, key=lambda e: e["timestep"])
            recent = fired_sorted[-4:]          # last 4 events
            header = "[MCP] MCP DATA STREAM  (Run B field intelligence -- not visible to Run A)"
            if not recent:
                body = "     awaiting field reports..."
            else:
                rows = []
                for i, ev in enumerate(recent):
                    marker = ">" if i == len(recent) - 1 else " "
                    desc   = ev.get("description", "").strip() or \
                             ev["event_type"].replace("_", " ").title()
                    rows.append(f"  {marker}  t={ev['timestep']:>4}   {desc}")
                body = "\n".join(rows)
            log_text.set_text(f"{header}\n{body}")

        # -- Timeline states -----------------------------------------------
        if frame >= storm_frame:
            # Storm arrived -- freeze + show overlay on both panels
            suptitle.set_text("STORM ARRIVED -- Mission concluded")
            storm_ov_a.set_visible(True)
            storm_ov_b.set_visible(True)
            for ax in axes:
                for sp in ax.spines.values():
                    sp.set_edgecolor("#ff5252")
                    sp.set_linewidth(2.5)

        elif frame >= notice_frame:
            elapsed   = frame - notice_frame
            remaining = max(0.0, storm_minutes * (1 - elapsed / max(storm_frame - notice_frame, 1)))
            mins      = int(remaining)
            secs      = int((remaining - mins) * 60)

            alert_badge.set_text(f"[!]  STORM ALERT\n   {mins}:{secs:02d} remaining")
            alert_badge.set_visible(True)
            mcp_badge.set_visible(True)
            if mand_scat_b is not None:
                mand_scat_b.set_visible(True)

            title_b_ax.set_text(
                "Run B -- Field-Adaptive (MCP)\n[*] Compliance-first replan active"
            )
            suptitle.set_text("Drone Fleet -- Storm Alert Active  [Run B replanning]")

            # Flash Run B border at alert onset
            flash_on = elapsed < 8 and elapsed % 2 == 0
            for sp in axes[1].spines.values():
                sp.set_edgecolor("#ff5252" if flash_on else "#ff8a65")
                sp.set_linewidth(2.5)

        else:
            suptitle.set_text("Drone Fleet -- Mission in Progress")

        extras = (log_text,) if log_text is not None else ()
        return (img_a, img_b, cov_txt_a, cov_txt_b,
                alert_badge, mcp_badge, suptitle,
                storm_ov_a, storm_ov_b,
                ev_scat_a, ev_scat_b, ev_lbl_a, ev_lbl_b) + extras

    anim = FuncAnimation(fig, update, frames=n_frames_total,
                         interval=1000 // fps, blit=False)
    anim.save(save_path, writer="pillow", fps=fps,
              savefig_kwargs={"facecolor": "#1a1a2e"})
    plt.close(fig)
    print(f"Saved: {save_path}")

    ucov_a = metrics_a.get("priority_coverage", 0)
    ucov_b = metrics_b.get("priority_coverage", 0)
    mcov_a = metrics_a.get("mandatory_coverage_pct", float("nan"))
    mcov_b = metrics_b.get("mandatory_coverage_pct", float("nan"))
    print(f"Urgency-weighted coverage -- A: {ucov_a:.1%}  B: {ucov_b:.1%}  "
          f"(lift {ucov_b - ucov_a:+.1%})")
    if not np.isnan(mcov_a):
        print(f"Mandatory zone coverage   -- A: {mcov_a:.1f}%  B: {mcov_b:.1f}%  "
              f"(lift {mcov_b - mcov_a:+.1f}pp)")
    return save_path
