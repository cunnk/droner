"""
Soil detection for reforestation drone missions.

detect_soil_mask()
------------------
Takes an aerial RGB image and returns a boolean mask of *plantable* cells --
exposed mudflat / bare soil where seeds can take root.  Non-plantable areas:

  * Open water / tidal channels : high blue channel, low green
  * Existing canopy              : high pseudo-NDVI (green - red / green + red)
  * Submerged mudflat            : classified as water (see tidal_threshold)

The returned mask plugs directly into generate_strips() via the existing
``field_mask`` parameter, so the optimizer and simulation need no changes.

apply_tidal_mask()
------------------
Overlays a dynamic tidal / moisture grid onto the base soil mask.  Use this
for replanning events when a section of the field floods mid-mission or after
a rain report from a sensor / operator.

Detection approach
------------------
Mangrove mudflat imagery typically shows three spectrally distinct zones:

  Zone            | NDVI          | Blue channel  | Brightness
  ----------------+---------------+---------------+------------
  Open water      | HIGH (teal)   | HIGH          | moderate
  Exposed soil    | near-zero     | moderate      | HIGH (pale)
  Existing canopy | near-zero     | low           | LOW (dark)

Three signal cuts -- any combination can be used depending on the image:
  1. pseudo_ndvi < soil_ndvi_threshold       ->  eliminates water (greenish-teal)
  2. blue_norm   < water_blue_threshold      ->  eliminates open water
  3. brightness  > min_brightness_threshold  ->  eliminates dark canopy

In many coastal mangrove scenes, existing canopy has brownish-dark coloration
(near-zero NDVI, similar to bare soil) and **brightness** is the primary signal
that separates it from pale exposed mudflat.  Set min_brightness_threshold
(e.g. 0.40-0.50) to activate this cut; default 0.0 = disabled.

Tuning guidance
---------------
* If the mask over-includes water: lower water_blue_threshold (e.g. 0.40)
  OR rely on NDVI cut (water is often greenish -> NDVI > 0.15)
* If the mask over-includes dark canopy: raise min_brightness_threshold (0.40-0.50)
* If the mask over-excludes healthy bare soil: raise soil_ndvi_threshold (0.20-0.25)
* For upland / non-mangrove scenes: set water_blue_threshold=1.0 and
  min_brightness_threshold=0.0, rely on NDVI alone

Non-square fields
-----------------
Use load_field_image(path, nrows, ncols) to load an image at the correct
aspect ratio without center-cropping, then pass the result to detect_soil_mask_from_array().
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

# Lazy import -- ingest.py is in the same project; this avoids a hard
# circular dependency if callers import both modules.
from src.field.ingest import load_image_as_array


# ---------------------------------------------------------------------------
# Primary detector
# ---------------------------------------------------------------------------

def load_field_image(
    image_path: str,
    nrows: int,
    ncols: int,
) -> np.ndarray:
    """Load an aerial image and resize to (nrows, ncols) without centre-cropping.

    Unlike load_image_as_array() which forces a square centre-crop, this function
    stretches the image to fill the target grid exactly.  Use this for non-square
    fields (e.g. 500 m wide x 300 m tall -> ncols=64, nrows=38).

    Parameters
    ----------
    image_path : path to JPG / PNG / TIFF
    nrows      : output grid height  (matches field height in metres)
    ncols      : output grid width   (matches field width in metres)

    Returns
    -------
    img_array : np.ndarray, shape (nrows, ncols, 3), uint8 [0, 255]
    """
    from PIL import Image as _Image
    img = _Image.open(image_path).convert("RGB")
    img_resized = img.resize((ncols, nrows), _Image.LANCZOS)  # (W, H) for PIL
    return np.array(img_resized, dtype=np.uint8)


def detect_soil_mask(
    image_path: str,
    target_size: int = 64,
    soil_ndvi_threshold: float = 0.15,
    water_blue_threshold: float = 0.45,
    min_brightness_threshold: float = 0.0,
    min_patch_cells: int = 2,
    nrows: Optional[int] = None,
    ncols: Optional[int] = None,
) -> Tuple[np.ndarray, np.ndarray, Dict[str, Any]]:
    """Classify aerial RGB image pixels into plantable / non-plantable.

    Parameters
    ----------
    image_path:
        Path to the aerial photograph (JPG, PNG, TIFF, etc.).
    target_size:
        Square output resolution when nrows/ncols are not specified.
        The image is centre-cropped then downsampled to target_size x target_size.
    soil_ndvi_threshold:
        Cells with pseudo-NDVI *above* this value are classified as existing
        canopy / water and excluded.  Default 0.15.  Teal water often has
        NDVI > 0.15 due to strong green channel, so this also helps exclude water.
    water_blue_threshold:
        Cells with normalised blue channel *above* this value are excluded as
        open water.  Default 0.45.
    min_brightness_threshold:
        Cells with perceptual luminance *below* this value are excluded as
        dark canopy (brownish-dark mangrove).  Default 0.0 = disabled.
        Set to 0.40-0.50 for coastal mangrove imagery where existing canopy
        has near-zero NDVI but is visually much darker than bare mudflat.
        Luminance = 0.299*R + 0.587*G + 0.114*B.
    min_patch_cells:
        Minimum connected-region size (cells) to retain as plantable.
        Tiny isolated soil pixels (noise, artefacts) are removed.  Set to 1
        to disable.
    nrows, ncols:
        If both are provided, the image is resized to this exact shape without
        centre-cropping (preserves non-square aspect ratio).  target_size is
        ignored when these are set.  Use for rectangular fields.

    Returns
    -------
    soil_mask : np.ndarray[bool], shape (H, W)
        True where seeds can be planted (exposed soil / mudflat).

    soil_priority_grid : np.ndarray[float], shape (H, W)
        Priority surface for strip generation.  Within the soil mask, values
        are the *inverted* pseudo-NDVI (barer soil = higher priority = denser
        planting).  Non-soil cells are 0.0.  Range [0, 1].

    meta : dict
        Detection statistics and thresholds used.
    """
    # --- Load image ---
    if nrows is not None and ncols is not None:
        img_rgb = load_field_image(image_path, nrows=nrows, ncols=ncols)
    else:
        img_rgb = load_image_as_array(image_path, target_size=target_size)
    H, W = img_rgb.shape[:2]

    R = img_rgb[:, :, 0].astype(np.float32) / 255.0
    G = img_rgb[:, :, 1].astype(np.float32) / 255.0
    B = img_rgb[:, :, 2].astype(np.float32) / 255.0

    # --- Spectral indices ---
    pseudo_ndvi = (G - R) / (G + R + 1e-6)
    ndvi_norm   = (pseudo_ndvi + 1.0) / 2.0   # [0, 1], 0 = bare soil

    # Perceptual luminance (ITU-R BT.601)
    brightness = 0.299 * R + 0.587 * G + 0.114 * B

    # --- Classification ---
    canopy_mask = pseudo_ndvi >= soil_ndvi_threshold   # greenish water + healthy canopy
    water_mask  = B >= water_blue_threshold             # open water (high blue)
    dark_mask   = (min_brightness_threshold > 0) & (brightness < min_brightness_threshold)

    # Plantable = not water, not canopy/water by NDVI, not dark canopy
    raw_soil_mask = ~canopy_mask & ~water_mask & ~dark_mask

    # --- Small-patch removal ---
    soil_mask = _remove_small_patches(raw_soil_mask, min_size=min_patch_cells)

    # --- Priority surface (brightness-based for mangrove imagery) ---
    # Brighter bare soil = more exposed = higher planting priority.
    # Inverted NDVI works for lush-canopy scenes; brightness inversion is
    # better for brownish-dark mangrove canopy scenes.
    if min_brightness_threshold > 0:
        # Use brightness as priority signal
        soil_priority_grid = np.where(soil_mask, brightness, 0.0).astype(np.float64)
        # Normalise to [0.05, 1.0]
        if soil_mask.any():
            pmin = soil_priority_grid[soil_mask].min()
            pmax = soil_priority_grid[soil_mask].max()
            rng  = pmax - pmin if pmax > pmin else 1.0
            soil_priority_grid = np.where(
                soil_mask,
                0.05 + 0.95 * (soil_priority_grid - pmin) / rng,
                0.0,
            )
    else:
        # Standard NDVI inversion
        inverted_ndvi = np.clip(1.0 - ndvi_norm, 0.0, 1.0)
        soil_priority_grid = np.where(soil_mask, inverted_ndvi, 0.0).astype(np.float64)
        soil_priority_grid = np.where(
            soil_mask & (soil_priority_grid < 0.05), 0.05, soil_priority_grid
        )

    # --- Meta ---
    total_cells    = H * W
    n_soil         = int(soil_mask.sum())
    n_canopy       = int(canopy_mask.sum())
    n_water        = int(water_mask.sum())
    n_dark         = int(dark_mask.sum()) if min_brightness_threshold > 0 else 0
    n_unclassified = max(0, total_cells - n_soil - n_canopy - n_water)

    meta = {
        "soil_pct"                 : round(n_soil / total_cells, 3),
        "water_pct"                : round(n_water / total_cells, 3),
        "canopy_pct"               : round(n_canopy / total_cells, 3),
        "dark_canopy_pct"          : round(n_dark / total_cells, 3),
        "unclassified_pct"         : round(n_unclassified / total_cells, 3),
        "plantable_cells"          : n_soil,
        "total_cells"              : total_cells,
        "grid_shape"               : (H, W),
        "soil_ndvi_threshold"      : soil_ndvi_threshold,
        "water_blue_threshold"     : water_blue_threshold,
        "min_brightness_threshold" : min_brightness_threshold,
    }

    return soil_mask, soil_priority_grid, meta


# ---------------------------------------------------------------------------
# Tidal / dynamic mask overlay
# ---------------------------------------------------------------------------

def apply_tidal_mask(
    base_soil_mask: np.ndarray,
    tidal_grid: np.ndarray,
    tidal_threshold: float = 0.6,
) -> np.ndarray:
    """Overlay a dynamic tidal / moisture grid on the base soil mask.

    Use this for replanning events: when an operator reports (or a sensor
    detects) that part of the field has been inundated, pass the updated
    tidal_grid here to get a revised plantable mask.  The result can be fed
    back into generate_strips() to produce a fresh set of strips for the
    replanner.

    Parameters
    ----------
    base_soil_mask:
        Boolean array (H x W) from detect_soil_mask() or a previous call.
    tidal_grid:
        Float array (H x W), same shape as base_soil_mask.
        Values: 0.0 = dry, 1.0 = fully inundated.
        Can be a synthetic array or derived from sensor / remote-sensing data.
    tidal_threshold:
        Cells with tidal_grid value *at or above* this threshold are treated
        as currently submerged and excluded from planting.

    Returns
    -------
    updated_mask : np.ndarray[bool]
        Cells that are both in the base soil mask AND currently above water.
    """
    tidal_grid = np.asarray(tidal_grid, dtype=np.float32)
    if tidal_grid.shape != base_soil_mask.shape:
        raise ValueError(
            f"tidal_grid shape {tidal_grid.shape} does not match "
            f"base_soil_mask shape {base_soil_mask.shape}."
        )
    dry = tidal_grid < tidal_threshold
    return base_soil_mask & dry


def make_synthetic_tidal_grid(
    nrows: int,
    ncols: int,
    flooded_rows: Optional[Tuple[int, int]] = None,
    flood_value: float = 0.8,
    background: float = 0.1,
    seed: Optional[int] = None,
) -> np.ndarray:
    """Create a synthetic tidal grid for testing / demo purposes.

    Parameters
    ----------
    nrows, ncols:
        Grid dimensions.
    flooded_rows:
        (start_row, end_row) slice of rows to mark as flooded.
        Default: bottom quarter of the grid.
    flood_value:
        Tidal value assigned to flooded cells (should exceed tidal_threshold).
    background:
        Tidal value for non-flooded cells.
    seed:
        Random seed for reproducibility (adds slight noise to the background).

    Returns
    -------
    tidal_grid : np.ndarray[float32], shape (nrows, ncols)
    """
    rng = np.random.default_rng(seed)
    grid = np.full((nrows, ncols), background, dtype=np.float32)
    grid += rng.uniform(-0.05, 0.05, size=(nrows, ncols)).astype(np.float32)

    if flooded_rows is None:
        flooded_rows = (int(nrows * 0.75), nrows)
    r0, r1 = flooded_rows
    grid[r0:r1, :] = flood_value + rng.uniform(-0.05, 0.05, size=(r1 - r0, ncols))

    return np.clip(grid, 0.0, 1.0)


# ---------------------------------------------------------------------------
# Visualisation helpers
# ---------------------------------------------------------------------------

def plot_soil_detection(
    image_path: str,
    target_size: int = 64,
    soil_ndvi_threshold: float = 0.15,
    water_blue_threshold: float = 0.45,
    min_brightness_threshold: float = 0.0,
    nrows: Optional[int] = None,
    ncols: Optional[int] = None,
    figsize: Tuple[float, float] = (18, 4),
) -> Any:
    """Four-panel diagnostic plot: RGB | NDVI | Brightness | soil mask.

    Returns the matplotlib Figure so callers can save or further customise it.
    """
    import matplotlib.pyplot as plt
    import matplotlib.colors as mcolors
    from matplotlib.patches import Patch

    soil_mask, priority, meta = detect_soil_mask(
        image_path,
        target_size=target_size,
        soil_ndvi_threshold=soil_ndvi_threshold,
        water_blue_threshold=water_blue_threshold,
        min_brightness_threshold=min_brightness_threshold,
        nrows=nrows,
        ncols=ncols,
    )

    if nrows is not None and ncols is not None:
        img_rgb = load_field_image(image_path, nrows=nrows, ncols=ncols)
    else:
        img_rgb = load_image_as_array(image_path, target_size=target_size)

    R = img_rgb[:, :, 0].astype(np.float32) / 255.0
    G = img_rgb[:, :, 1].astype(np.float32) / 255.0
    B = img_rgb[:, :, 2].astype(np.float32) / 255.0
    pseudo_ndvi = (G - R) / (G + R + 1e-6)
    brightness  = 0.299 * R + 0.587 * G + 0.114 * B

    n_panels = 4
    fig, axes = plt.subplots(1, n_panels, figsize=figsize)

    # Panel 1 -- RGB
    axes[0].imshow(img_rgb, aspect="auto")
    axes[0].set_title("Aerial Image (RGB)")
    axes[0].axis("off")

    # Panel 2 -- pseudo-NDVI heatmap
    im1 = axes[1].imshow(pseudo_ndvi, cmap="RdYlGn", vmin=-0.3, vmax=0.5, aspect="auto")
    axes[1].set_title(f"Pseudo-NDVI\n(NDVI threshold = {soil_ndvi_threshold})")
    axes[1].axis("off")
    plt.colorbar(im1, ax=axes[1], fraction=0.046, pad=0.04)

    # Panel 3 -- Brightness heatmap
    im2 = axes[2].imshow(brightness, cmap="YlOrBr", vmin=0, vmax=1, aspect="auto")
    axes[2].set_title(
        f"Perceptual Brightness\n"
        f"(min threshold = {min_brightness_threshold:.2f})"
        + (" [active]" if min_brightness_threshold > 0 else " [disabled]")
    )
    axes[2].axis("off")
    plt.colorbar(im2, ax=axes[2], fraction=0.046, pad=0.04)

    # Panel 4 -- soil mask
    cmap_mask = mcolors.ListedColormap(["#a8d5e2", "#c8a97a"])
    axes[3].imshow(soil_mask.astype(np.uint8), cmap=cmap_mask, vmin=0, vmax=1, aspect="auto")
    detail = (
        f"soil={meta['soil_pct']:.1%}  water={meta['water_pct']:.1%}\n"
        f"NDVI-canopy={meta['canopy_pct']:.1%}  "
        f"dark-canopy={meta['dark_canopy_pct']:.1%}"
    )
    axes[3].set_title(f"Plantable Soil Mask\n{detail}")
    axes[3].axis("off")

    legend_elements = [
        Patch(facecolor="#c8a97a", label=f"Plantable ({meta['plantable_cells']} cells)"),
        Patch(facecolor="#a8d5e2", label="Skip (water / canopy)"),
    ]
    axes[3].legend(handles=legend_elements, loc="lower right", fontsize=8, framealpha=0.85)

    grid_str = f"{meta['grid_shape'][0]}x{meta['grid_shape'][1]}"
    fig.suptitle(
        f"Soil Detection -- Reforestation Mission  ({grid_str} grid)",
        fontsize=12, y=1.01,
    )
    fig.tight_layout()
    return fig, soil_mask, priority, meta


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _remove_small_patches(mask: np.ndarray, min_size: int) -> np.ndarray:
    """Remove connected True-regions smaller than min_size cells.

    Uses a simple flood-fill (BFS) rather than scipy to avoid adding a
    mandatory dependency.  Fast enough for grids <= 256 x 256.
    """
    if min_size <= 1:
        return mask.copy()

    visited  = np.zeros_like(mask, dtype=bool)
    cleaned  = np.zeros_like(mask, dtype=bool)
    nrows, ncols = mask.shape

    for start_r in range(nrows):
        for start_c in range(ncols):
            if mask[start_r, start_c] and not visited[start_r, start_c]:
                # BFS
                region: list[Tuple[int, int]] = []
                queue  = [(start_r, start_c)]
                visited[start_r, start_c] = True
                while queue:
                    r, c = queue.pop()
                    region.append((r, c))
                    for dr, dc in [(-1, 0), (1, 0), (0, -1), (0, 1)]:
                        nr, nc = r + dr, c + dc
                        if (0 <= nr < nrows and 0 <= nc < ncols
                                and mask[nr, nc] and not visited[nr, nc]):
                            visited[nr, nc] = True
                            queue.append((nr, nc))

                if len(region) >= min_size:
                    for r, c in region:
                        cleaned[r, c] = True

    return cleaned
