"""
Contour-following strip generator for mangrove reforestation.

Replaces the boustrophedon (lawnmower) approach with strips that hug
tidal channel edges — mirroring how mangroves naturally colonise mudflats
from the water margin inward.

Algorithm
---------
1. Compute the Euclidean distance transform of the soil mask.
   Each plantable cell's value = distance (in cells) to the nearest
   non-plantable cell (water channel or existing canopy).

2. Bucket plantable cells into distance bands of width `strip_width`.
   Band 0 = cells at the tidal margin (distance < strip_width).
   Band k = cells strip_width*k ≤ dist < strip_width*(k+1).

3. Within each band, order cells by their angle relative to the band's
   centroid.  This traces the perimeter of the ring, producing a sinuous
   looping path rather than a straight line.

4. Alternate traversal direction every other band (boustrophedon cadence)
   so drones don't dead-head back to the start between strips.

Why this beats the alternatives
--------------------------------
- Image-edge detection on raw RGB: re-runs image processing, sensitive to
  noise, must re-calibrate thresholds.  We already *have* the soil mask.
- Parametric curves (sine/spiral): artificial, don't follow real topology.
- Distance transform: O(n), deterministic, self-aligning to the actual
  tidal channel geometry encoded in the soil mask.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

import numpy as np
from scipy.ndimage import distance_transform_edt

from src.field.generator import Strip, _make_strip


def generate_contour_strips(
    soil_mask: np.ndarray,
    priority_grid: np.ndarray,
    strip_width: int = 2,
    seconds_per_cell: float = 2.0,
    min_cells: int = 3,
    max_cells_per_strip: Optional[int] = None,
) -> List[Strip]:
    """Generate sinuous contour strips that hug tidal channel boundaries.

    Parameters
    ----------
    soil_mask:
        Boolean array (nrows × ncols).  True = plantable mudflat.
        Typically the output of ``detect_soil_mask()`` from
        ``src.reforestation.soil_detector``.
    priority_grid:
        Float array (nrows × ncols) in [0, 1].  Used to compute strip
        priority and spray time.  Pass the ``soil_priority_grid`` returned
        by ``detect_soil_mask()``.
    strip_width:
        Number of distance-bands per strip.  Smaller = more sinuous curves
        and more strips (slower MILP); larger = smoother coverage and fewer
        strips.  ``strip_width=2`` is recommended.
    seconds_per_cell:
        Simulation timesteps per grid cell, forwarded to ``_make_strip()``.
    min_cells:
        Strips with fewer plantable cells than this threshold are discarded
        (avoids tiny isolated fragments clogging the optimizer).
    max_cells_per_strip:
        If set, any band with more cells than this limit is split into
        consecutive sub-strips of at most this size.  Use this when
        running with seed capacity enabled: set it to
        ``int(seed_capacity / seeds_per_cell) - margin`` so each strip
        fits within one hopper load.  The simulation engine resets a
        drone's position within a strip when it returns to dock, so
        strips longer than one hopper load will cause the drone to replay
        the start of the strip on every refill rather than advancing.
        ``None`` (default) = no splitting.

    Returns
    -------
    List[Strip]
        Same format as ``generate_strips()``, compatible with the existing
        MILP optimizer, simulation engine, and visualiser.

    Notes
    -----
    strip_width=1  — maximum sinuosity; many small strips, slower MILP
    strip_width=2  — recommended: smooth curves, ~30–50 strips for 64×38
    strip_width=3  — wider swaths, less curve detail, faster MILP
    """
    soil = soil_mask.astype(bool)

    # Distance from the nearest non-plantable cell (water / canopy boundary)
    dist = distance_transform_edt(soil)
    max_d = int(dist.max())

    strips: List[Strip] = []
    strip_id = 0

    for band_start in range(0, max_d + strip_width, strip_width):
        band_end = band_start + strip_width
        in_band = (dist >= band_start) & (dist < band_end) & soil
        coords = np.argwhere(in_band)   # shape (N, 2) — [row, col]

        if len(coords) < min_cells:
            continue

        # Order cells to stay geographically local — boustrophedon within the band.
        # Angle-sorting around the centroid was tried but produces large jumps
        # (mean 6 cells, max 25) because the centroid often lands in open water
        # for a coastal mudflat, making the angle assignment meaningless.
        # Row-boustrophedon keeps consecutive cells within the same or adjacent
        # rows, producing strips that run roughly parallel to the shoreline.
        row_groups: dict = {}
        for r, c in [(int(r), int(c)) for r, c in coords]:
            row_groups.setdefault(r, []).append(c)

        cells: List[Tuple[int, int]] = []
        for row_idx, row in enumerate(sorted(row_groups)):
            cols = sorted(row_groups[row])
            if row_idx % 2 == 1:
                cols = cols[::-1]   # alternate direction each row
            for c in cols:
                cells.append((row, c))

        # Split large bands into sub-strips so each fits within one hopper load.
        # Without this, the engine's strip-restart-on-return behaviour causes the
        # drone to loop over the first hopper-load of cells indefinitely.
        if max_cells_per_strip and len(cells) > max_cells_per_strip:
            chunks = [
                cells[i : i + max_cells_per_strip]
                for i in range(0, len(cells), max_cells_per_strip)
            ]
        else:
            chunks = [cells]

        for chunk in chunks:
            strip = _make_strip(
                strip_id,
                chunk,
                priority_grid,
                seconds_per_cell,
                orientation_deg=0.0,   # contour strips have no fixed orientation
                spray_threshold=0.0,
                field_mask=soil,
            )
            if strip is not None:
                strips.append(strip)
                strip_id += 1

    return strips
