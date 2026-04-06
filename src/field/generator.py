"""
Field representation and strip generation.

A "strip" is a contiguous sequence of cells a drone covers in one pass.
Strips are generated in a boustrophedon (lawnmower) pattern and can be
oriented at any angle to match real crop row directions.

Orientation
-----------
Agricultural fields are rarely all east-west. Rows are aligned to:
  - The field's longest edge (minimises turns, maximises efficiency)
  - Slope direction (erosion control -- rows run along contour lines)
  - Previous season's tillage direction

Pass orientation_deg to generate_strips() to match the field:
  0deg   east-west strips   (default, horizontal sweeps)
  90deg  north-south strips (vertical sweeps)
  45deg  diagonal strips    (45deg from east)
  any  arbitrary angle

Algorithm (arbitrary angle)
---------------------------
For an orientation theta degrees from horizontal:

  strip_vec  = (cos theta,  sin theta)   -- direction along each strip
  across_vec = (-sin theta, cos theta)   -- direction from one strip to the next

For each grid cell (r, c):
  along_proj  = r*cos theta + c*sin theta    -- position along strip
  across_proj = -r*sin theta + c*cos theta   -- which strip the cell belongs to

Cells are binned by quantised across_proj into strips, then ordered by
along_proj within each strip. Boustrophedon alternates the direction
every other strip so adjacent strips connect end-to-end.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np


@dataclass
class Strip:
    id: int
    cells: List[Tuple[int, int]]         # all (row, col) in traversal order -- drone flies all of these
    spray_cells: List[Tuple[int, int]]   # subset where sprayer is active (above threshold, inside mask)
    spray_segments: List[Tuple[int, int]]  # contiguous spray runs as (start_idx, end_idx) into cells
    priority: float                      # mean spray priority over spray_cells only
    time: float                          # transit time (all cells) + spray overhead (spray cells)
    orientation_deg: float = 0.0         # strip direction in degrees from east

    def __repr__(self) -> str:
        return (f"Strip(id={self.id}, cells={len(self.cells)}, "
                f"spray={len(self.spray_cells)}, segments={len(self.spray_segments)}, "
                f"priority={self.priority:.2f}, time={self.time:.1f}s, "
                f"orientation={self.orientation_deg:.0f}deg)")


def generate_strips(
    field_grid,
    seconds_per_cell: float = 2.0,
    orientation_deg: float = 0.0,
    strip_width: int = 1,
    spray_threshold: float = 0.0,
    field_mask: Optional[np.ndarray] = None,
) -> List[Strip]:
    """
    Convert a 2D priority grid into a list of Strip objects.

    Args:
        field_grid:       2D array-like of floats in [0, 1] (spray priority).
        seconds_per_cell: Simulated seconds per cell at baseline priority.
        orientation_deg:  Crop row direction in degrees clockwise from east.
                          0   = east-west strips  (default)
                          90  = north-south strips
                          Any = arbitrary angle
        strip_width:      Number of grid rows (in the rotated frame) per strip.
                          1 = one cell wide (default).
        spray_threshold:  Cells with priority strictly below this value are
                          skipped (drone transits over them, sprayer stays off).
                          0.0 = spray every cell (default, backward-compatible).
        field_mask:       Optional boolean 2D array, same shape as field_grid.
                          False/0 cells are outside the field boundary -- drone
                          still flies over them (path continuity) but never
                          sprays them regardless of priority.

    Returns:
        List of Strip objects with at least one spray cell. Strips where every
        cell is below threshold or outside the mask are dropped entirely.
    """
    grid = np.asarray(field_grid, dtype=float)
    if field_mask is not None:
        field_mask = np.asarray(field_mask, dtype=bool)

    kwargs = dict(
        seconds_per_cell=seconds_per_cell,
        strip_width=strip_width,
        orientation_deg=orientation_deg,
        spray_threshold=spray_threshold,
        field_mask=field_mask,
    )

    # Axis-aligned fast paths (no floating point projection needed)
    if orientation_deg % 180 == 0:
        return _strips_horizontal(grid, **kwargs)
    if orientation_deg % 180 == 90:
        return _strips_vertical(grid, **kwargs)

    return _strips_angled(grid, **kwargs)


# ---------------------------------------------------------------------------
# Axis-aligned implementations (fast, exact)
# ---------------------------------------------------------------------------

def _strips_horizontal(
    grid: np.ndarray,
    seconds_per_cell: float,
    strip_width: int,
    orientation_deg: float,
    spray_threshold: float,
    field_mask: Optional[np.ndarray],
) -> List[Strip]:
    """East-west strips, boustrophedon row order."""
    nrows, ncols = grid.shape
    strips: List[Strip] = []
    strip_id = 0

    for row_start in range(0, nrows, strip_width):
        row_end = min(row_start + strip_width, nrows)
        forward = strip_id % 2 == 0
        cols = range(ncols) if forward else range(ncols - 1, -1, -1)
        cells: List[Tuple[int, int]] = []
        for col in cols:
            for row in range(row_start, row_end):
                cells.append((row, col))
        s = _make_strip(strip_id, cells, grid, seconds_per_cell,
                        orientation_deg, spray_threshold, field_mask)
        if s is not None:
            strips.append(s)
            strip_id += 1

    return strips


def _strips_vertical(
    grid: np.ndarray,
    seconds_per_cell: float,
    strip_width: int,
    orientation_deg: float,
    spray_threshold: float,
    field_mask: Optional[np.ndarray],
) -> List[Strip]:
    """North-south strips, boustrophedon column order."""
    nrows, ncols = grid.shape
    strips: List[Strip] = []
    strip_id = 0

    for col_start in range(0, ncols, strip_width):
        col_end = min(col_start + strip_width, ncols)
        forward = strip_id % 2 == 0
        rows = range(nrows) if forward else range(nrows - 1, -1, -1)
        cells: List[Tuple[int, int]] = []
        for row in rows:
            for col in range(col_start, col_end):
                cells.append((row, col))
        s = _make_strip(strip_id, cells, grid, seconds_per_cell,
                        orientation_deg, spray_threshold, field_mask)
        if s is not None:
            strips.append(s)
            strip_id += 1

    return strips


# ---------------------------------------------------------------------------
# Arbitrary angle implementation
# ---------------------------------------------------------------------------

def _strips_angled(
    grid: np.ndarray,
    seconds_per_cell: float,
    orientation_deg: float,
    strip_width: int,
    spray_threshold: float,
    field_mask: Optional[np.ndarray],
) -> List[Strip]:
    """
    Generate strips at an arbitrary angle using coordinate projection.

    Each cell is projected onto two orthogonal axes:
      - along_proj  -> position along the strip (determines traversal order)
      - across_proj -> which strip the cell belongs to

    Cells are binned by quantised across_proj, ordered by along_proj,
    and alternated (boustrophedon) every other strip.
    """
    nrows, ncols = grid.shape
    theta = math.radians(orientation_deg)
    cos_t, sin_t = math.cos(theta), math.sin(theta)

    # Project every cell
    cell_projections: List[Tuple[float, float, int, int]] = []
    for r in range(nrows):
        for c in range(ncols):
            along  =  r * cos_t + c * sin_t
            across = -r * sin_t + c * cos_t
            cell_projections.append((along, across, r, c))

    # Bin cells into strips by quantised across_proj
    across_vals = [x[1] for x in cell_projections]
    across_min  = min(across_vals)
    # Bin width = strip_width cells (approximate, since we're in rotated space)
    bin_size = strip_width

    bins: dict = defaultdict(list)
    for along, across, r, c in cell_projections:
        bin_idx = int(math.floor((across - across_min) / bin_size))
        bins[bin_idx].append((along, r, c))

    # Sort bins by their across position, then cells within each bin by along
    sorted_bin_keys = sorted(bins.keys())
    strips: List[Strip] = []

    strip_id = 0
    for strip_idx, bin_key in enumerate(sorted_bin_keys):
        cells_with_proj = sorted(bins[bin_key], key=lambda x: x[0])
        if strip_idx % 2 != 0:
            cells_with_proj = list(reversed(cells_with_proj))
        cells = [(r, c) for _, r, c in cells_with_proj]
        if cells:
            s = _make_strip(strip_id, cells, grid, seconds_per_cell,
                            orientation_deg, spray_threshold, field_mask)
            if s is not None:
                strips.append(s)
                strip_id += 1

    return strips


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _make_strip(
    strip_id: int,
    cells: List[Tuple[int, int]],
    grid: np.ndarray,
    seconds_per_cell: float,
    orientation_deg: float,
    spray_threshold: float,
    field_mask: Optional[np.ndarray],
) -> Optional["Strip"]:
    """
    Build a Strip from a cell list and the priority grid.

    Returns None if no cells pass the spray threshold / field mask -- the
    caller should drop the strip entirely rather than assigning empty work.

    Time model:
        transit_time = all cells x seconds_per_cell   (drone always flies full path)
        spray_time   = spray cells x seconds_per_cell x mean_priority
    This ensures the optimizer accounts for real expected duration: a strip
    with 60% non-crop cells is genuinely faster than a fully cropped one.
    """
    spray_cells = [
        (r, c) for r, c in cells
        if (field_mask is None or field_mask[r, c])
        and grid[r, c] >= spray_threshold
    ]
    if not spray_cells:
        return None

    spray_set = set(spray_cells)

    # Contiguous spray segments: (start_idx, end_idx) into cells
    spray_segments: List[Tuple[int, int]] = []
    in_seg = False
    seg_start = 0
    for i, cell in enumerate(cells):
        if cell in spray_set:
            if not in_seg:
                seg_start = i
                in_seg = True
        else:
            if in_seg:
                spray_segments.append((seg_start, i))
                in_seg = False
    if in_seg:
        spray_segments.append((seg_start, len(cells)))

    priority = float(np.mean([grid[r, c] for r, c in spray_cells]))
    transit_time = len(cells) * seconds_per_cell
    spray_time   = len(spray_cells) * seconds_per_cell * priority
    time = round(transit_time + spray_time, 2)

    return Strip(
        id=strip_id,
        cells=cells,
        spray_cells=spray_cells,
        spray_segments=spray_segments,
        priority=priority,
        time=time,
        orientation_deg=orientation_deg,
    )


# ---------------------------------------------------------------------------
# Field generators
# ---------------------------------------------------------------------------

def grid_from_array(array) -> np.ndarray:
    """Normalise an arbitrary 2D array to [0, 1]."""
    arr  = np.asarray(array, dtype=float)
    lo, hi = arr.min(), arr.max()
    if hi == lo:
        return np.ones_like(arr)
    return (arr - lo) / (hi - lo)


def synthetic_field(
    nrows: int = 8,
    ncols: int = 8,
    seed: int = 42,
    n_patches: int = 6,
) -> np.ndarray:
    """
    Generate a reproducible synthetic priority grid for development.

    Creates spatially coherent crop patches (Gaussian blobs) rather than
    pure random noise, so the field looks like distinct crop zones with
    varying treatment priority -- closer to a real field than a uniform grid.

    Args:
        nrows, ncols: Grid dimensions.
        seed:         RNG seed for reproducibility.
        n_patches:    Number of distinct crop zones to simulate.
    """
    rng  = np.random.default_rng(seed)
    grid = np.zeros((nrows, ncols))

    # Place Gaussian blobs of varying intensity to simulate crop patches
    for _ in range(n_patches):
        cr    = rng.uniform(0, nrows)
        cc    = rng.uniform(0, ncols)
        sigma = rng.uniform(nrows * 0.15, nrows * 0.45)
        val   = rng.uniform(0.3, 1.0)
        for r in range(nrows):
            for c in range(ncols):
                dist = math.sqrt((r - cr) ** 2 + (c - cc) ** 2)
                grid[r, c] += val * math.exp(-(dist ** 2) / (2 * sigma ** 2))

    return grid_from_array(grid)
