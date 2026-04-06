"""
Real cropland data ingestion pipeline.

Two sources are supported:

1. Aerial imagery (JPG / JPEG / JFIF)
   ----------------------------------
   load_image_grid()       -- single image -> priority grid
   load_image_directory()  -- batch-load all images in a folder

   Channel options:
     'green'        -- raw green channel / 255  (best default for RGB aerials)
     'pseudo_ndvi'  -- (G - R) / (G + R + eps), normalised to [0, 1]
                      (approximates NDVI from RGB; separates green veg from soil)
     'grayscale'    -- perceptual luminance
     'red' / 'blue' -- raw single channels

   target_size controls the output grid resolution (e.g. 64 -> 64x64).
   For non-square images the longer axis is cropped to the shorter one
   (centre crop) before resizing, preserving square cells.

   invert=True flips the priority surface -- useful when bright = stressed crop
   rather than dense/healthy crop.

2. Winnipeg Cropland Dataset (tabular, not needed for image workflow)
   ------------------------------------------------------------------
   load_cropland_grid()    -- load WinnipegDataset.txt -> priority grid

   Crop -> priority mapping
   -----------------------
   Priority represents spray urgency / treatment value for that crop type.
   Defaults are agronomy-informed estimates; pass custom_priority_map to override.

       Canola   : 0.95  (high-value oil crop, sensitive to weed competition)
       Corn     : 0.90  (high-value, dense canopy needs full coverage)
       Soybeans : 0.85  (nitrogen-fixer, pest-sensitive)
       Wheat    : 0.80  (staple grain, broad acreage)
       Peas     : 0.75  (legume, shorter season)
       Oats     : 0.65  (lower value, hardier)
       Broadleaf: 0.55  (mixed/weed-prone areas, lowest priority)

   Grid reconstruction
   -------------------
   The dataset has no explicit XY pixel coordinates. Pixels are in raster order
   (row 0 left-to-right, then row 1, etc.). To reconstruct the 2D grid you must
   know the number of columns in the original raster.

       load_cropland_grid(..., ncols=571)        # specify directly
       load_cropland_grid(..., tile_size=64)     # take a square tile, infer ncols

   If ncols is None and the data is ~325,834 rows, the code defaults to
   sqrt(n_pixels) rounded to the nearest integer as a best guess.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Aerial image ingestion
# ---------------------------------------------------------------------------

_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.jfif', '.png', '.tif', '.tiff'}


def load_image_grid(
    path: str,
    target_size: int = 64,
    channel: str = 'green',
    invert: bool = False,
) -> Tuple[List[List[float]], Dict]:
    """
    Load an aerial cropland image and return a 2D priority grid.

    The image is centre-cropped to a square (to preserve aspect ratio of
    individual cells), then downsampled to (target_size x target_size).
    The chosen channel is extracted and normalised to [0, 1].

    Args:
        path        : Path to the image file (JPG / JPEG / JFIF / PNG / TIFF).
        target_size : Output grid dimension in cells (both rows and cols).
                      64 -> 64x64 grid, 128 -> 128x128, etc.
        channel     : Pixel->priority mapping strategy.
                        'green'       -- green channel / 255
                        'pseudo_ndvi' -- (G-R)/(G+R+eps), normalised to [0,1]
                        'grayscale'   -- perceptual luminance (0.299R+0.587G+0.114B)
                        'red'         -- red channel / 255
                        'blue'        -- blue channel / 255
        invert      : If True, flip the priority surface (1 - p).
                      Use when bright pixels = low-priority (e.g. bare soil,
                      dry zones) rather than dense/healthy vegetation.

    Returns:
        (grid, meta) where:
            grid : List[List[float]] -- 2D priority grid, values in [0, 1].
            meta : dict with keys:
                     nrows, ncols    -- output grid dimensions (both = target_size)
                     source_size     -- (width, height) of the original image
                     crop_box        -- (left, top, right, bottom) square crop applied
                     channel         -- channel strategy used
                     invert          -- whether the surface was inverted
                     mean_priority   -- mean of the output grid
                     path            -- source file path
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("Pillow is required: pip install pillow")

    filepath = Path(path)
    if not filepath.exists():
        raise FileNotFoundError(f"Image not found: {filepath}")

    img = Image.open(filepath).convert('RGB')
    orig_w, orig_h = img.size

    # --- Centre-crop to square ---
    side = min(orig_w, orig_h)
    left   = (orig_w - side) // 2
    top    = (orig_h - side) // 2
    right  = left + side
    bottom = top + side
    img = img.crop((left, top, right, bottom))

    # --- Resize to target_size x target_size ---
    img = img.resize((target_size, target_size), Image.LANCZOS)

    # --- Extract chosen channel ---
    arr = np.array(img, dtype=np.float32)   # shape (H, W, 3)
    R, G, B = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]

    if channel == 'green':
        raw = G / 255.0
    elif channel == 'pseudo_ndvi':
        raw = (G - R) / (G + R + 1e-6)
        # raw is in [-1, 1]; normalise to [0, 1]
        raw = (raw + 1.0) / 2.0
    elif channel == 'grayscale':
        raw = (0.299 * R + 0.587 * G + 0.114 * B) / 255.0
    elif channel == 'red':
        raw = R / 255.0
    elif channel == 'blue':
        raw = B / 255.0
    else:
        raise ValueError(f"Unknown channel '{channel}'. "
                         "Choose: 'green', 'pseudo_ndvi', 'grayscale', 'red', 'blue'")

    # --- Normalise to [0, 1] (stretch contrast) ---
    lo, hi = raw.min(), raw.max()
    if hi > lo:
        raw = (raw - lo) / (hi - lo)

    if invert:
        raw = 1.0 - raw

    grid = raw.tolist()

    meta = {
        'nrows'         : target_size,
        'ncols'         : target_size,
        'source_size'   : (orig_w, orig_h),
        'crop_box'      : (left, top, right, bottom),
        'channel'       : channel,
        'invert'        : invert,
        'mean_priority' : float(raw.mean()),
        'path'          : str(filepath),
        'name'          : filepath.stem,
    }
    return grid, meta


def load_image_directory(
    dir_path: str,
    target_size: int = 64,
    channel: str = 'green',
    invert: bool = False,
    extensions: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Tuple[List[List[float]], Dict]]:
    """
    Batch-load all aerial images in a directory.

    Args:
        dir_path   : Directory containing image files.
        target_size: Output grid size (cells per side) -- passed to load_image_grid.
        channel    : Channel strategy -- passed to load_image_grid.
        invert     : Invert priority surface -- passed to load_image_grid.
        extensions : File extensions to include (default: jpg/jpeg/jfif/png/tif/tiff).

    Returns:
        OrderedDict mapping filename stem -> (grid, meta), sorted alphabetically.
    """
    exts = set(extensions) if extensions else _IMAGE_EXTENSIONS
    folder = Path(dir_path)
    if not folder.is_dir():
        raise NotADirectoryError(f"Not a directory: {folder}")

    files = sorted(
        p for p in folder.iterdir()
        if p.suffix.lower() in exts
    )
    if not files:
        raise FileNotFoundError(f"No image files found in {folder}")

    results: Dict[str, Tuple[List[List[float]], Dict]] = {}
    for f in files:
        try:
            grid, meta = load_image_grid(
                str(f), target_size=target_size,
                channel=channel, invert=invert,
            )
            results[f.stem] = (grid, meta)
            print(f"  {f.name:<40}  {target_size}x{target_size}  "
                  f"mean_priority={meta['mean_priority']:.3f}")
        except Exception as exc:
            print(f"  SKIP {f.name}: {exc}")

    print(f"\nLoaded {len(results)} / {len(files)} images from {folder}")
    return results


def load_image_as_array(
    path: str,
    target_size: int = 64,
) -> np.ndarray:
    """
    Load an aerial image as a uint8 RGB numpy array (H x W x 3),
    applying the same centre-crop and resize as load_image_grid().

    Use this to get the background image for the overlay renderer:

        grid, meta = load_image_grid(path, target_size=128)
        bg_arr     = load_image_as_array(path, target_size=128)
        animate(hist, 128, 128, background_image=bg_arr)

    The crop box and dimensions are guaranteed to match the grid
    produced by load_image_grid() with the same path and target_size.

    Returns:
        np.ndarray of shape (target_size, target_size, 3), dtype uint8.
    """
    try:
        from PIL import Image
    except ImportError:
        raise ImportError("Pillow is required: pip install pillow")

    filepath = Path(path)
    if not filepath.exists():
        raise FileNotFoundError(f"Image not found: {filepath}")

    img = Image.open(filepath).convert('RGB')
    w, h = img.size
    side   = min(w, h)
    left   = (w - side) // 2
    top    = (h - side) // 2
    img    = img.crop((left, top, left + side, top + side))
    img    = img.resize((target_size, target_size), Image.LANCZOS)
    return np.array(img, dtype=np.uint8)


# ---------------------------------------------------------------------------
# Crop type priority weights
# ---------------------------------------------------------------------------

DEFAULT_PRIORITY_MAP: Dict[int, float] = {
    1: 0.90,   # Corn
    2: 0.75,   # Peas
    3: 0.95,   # Canola
    4: 0.85,   # Soybeans
    5: 0.65,   # Oats
    6: 0.80,   # Wheat
    7: 0.55,   # Broadleaf
}

CROP_NAMES: Dict[int, str] = {
    1: "Corn",
    2: "Peas",
    3: "Canola",
    4: "Soybeans",
    5: "Oats",
    6: "Wheat",
    7: "Broadleaf",
}


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def load_cropland_grid(
    path: str,
    ncols: Optional[int] = None,
    tile_size: Optional[int] = None,
    tile_origin: Tuple[int, int] = (0, 0),
    downsample: int = 1,
    label_col: Optional[int] = None,
    custom_priority_map: Optional[Dict[int, float]] = None,
    max_rows_to_read: Optional[int] = None,
) -> Tuple[List[List[float]], Dict]:
    """
    Load the Winnipeg cropland dataset and return a 2D priority grid.

    Args:
        path               : Path to WinnipegDataset.txt.
        ncols              : Number of columns in the original raster. If None,
                             estimated as sqrt(total pixels).
        tile_size          : If set, extract a (tile_size x tile_size) tile from
                             the reconstructed grid, starting at tile_origin.
                             Overrides ncols-based full-grid output.
        tile_origin        : (row, col) top-left corner of the tile in the
                             reconstructed grid. Default (0, 0).
        downsample         : Take every Nth pixel in each dimension after tiling.
                             1 = no downsampling.
        label_col          : Column index of the crop class label. If None,
                             auto-detected (tries last column, then first).
        custom_priority_map: Override default crop -> priority mapping.
        max_rows_to_read   : Cap on rows read from file (useful for quick tests).

    Returns:
        (grid, meta) where:
            grid : List[List[float]] -- 2D priority grid, values in [0, 1].
            meta : dict with keys:
                     nrows, ncols         -- final grid dimensions
                     crop_counts          -- {crop_name: pixel_count}
                     priority_map         -- crop_id -> priority used
                     tile_origin          -- (row, col) used
                     source_pixels        -- total pixels in source before tile/downsample
    """
    priority_map = {**DEFAULT_PRIORITY_MAP, **(custom_priority_map or {})}
    filepath     = Path(path)

    if not filepath.exists():
        raise FileNotFoundError(
            f"Dataset not found at {filepath}.\n"
            "Download from https://www.kaggle.com/datasets/pcbreviglieri/cropland-mapping\n"
            "and extract WinnipegDataset.txt into drones/data/"
        )

    # ------------------------------------------------------------------
    # 1. Load raw data
    # ------------------------------------------------------------------
    print(f"Loading {filepath.name} ...")
    df = pd.read_csv(
        filepath,
        sep=r"\s+",
        header=None,
        nrows=max_rows_to_read,
        engine="c",
        dtype=np.float32,
    )
    n_pixels, n_cols_total = df.shape
    print(f"  {n_pixels:,} pixels x {n_cols_total} columns loaded")

    # ------------------------------------------------------------------
    # 2. Identify label column
    # ------------------------------------------------------------------
    if label_col is None:
        label_col = _detect_label_col(df)
        print(f"  Label column auto-detected: column {label_col}")

    labels = df.iloc[:, label_col].astype(int).values

    # ------------------------------------------------------------------
    # 3. Infer raster dimensions
    # ------------------------------------------------------------------
    if ncols is None:
        ncols = int(round(math.sqrt(n_pixels)))
        print(f"  ncols not specified -- using sqrt({n_pixels}) ~= {ncols}")
    nrows_full = math.ceil(n_pixels / ncols)

    # Pad labels to fill the last row if needed
    pad = nrows_full * ncols - n_pixels
    if pad > 0:
        labels = np.concatenate([labels, np.zeros(pad, dtype=int)])

    label_grid = labels.reshape(nrows_full, ncols)
    print(f"  Reconstructed raster: {nrows_full} x {ncols}")

    # ------------------------------------------------------------------
    # 4. Tile extraction
    # ------------------------------------------------------------------
    r0, c0 = tile_origin
    if tile_size is not None:
        r1 = min(r0 + tile_size, nrows_full)
        c1 = min(c0 + tile_size, ncols)
        label_grid = label_grid[r0:r1, c0:c1]
        print(f"  Tile [{r0}:{r1}, {c0}:{c1}] -> {label_grid.shape}")
    else:
        label_grid = label_grid[r0:, c0:]

    # ------------------------------------------------------------------
    # 5. Downsample
    # ------------------------------------------------------------------
    if downsample > 1:
        label_grid = label_grid[::downsample, ::downsample]
        print(f"  Downsampled x{downsample} -> {label_grid.shape}")

    # ------------------------------------------------------------------
    # 6. Map crop labels -> priority values
    # ------------------------------------------------------------------
    priority_grid_arr = np.zeros(label_grid.shape, dtype=float)
    for crop_id, priority in priority_map.items():
        priority_grid_arr[label_grid == crop_id] = priority

    # Unknown labels (0 or unlabelled) get a low default priority
    priority_grid_arr[label_grid == 0] = 0.3

    # ------------------------------------------------------------------
    # 7. Build metadata
    # ------------------------------------------------------------------
    final_nrows, final_ncols = priority_grid_arr.shape
    crop_counts = {}
    for crop_id, name in CROP_NAMES.items():
        count = int(np.sum(label_grid == crop_id))
        if count > 0:
            crop_counts[name] = count

    grid = priority_grid_arr.tolist()

    meta = {
        "nrows"         : final_nrows,
        "ncols"         : final_ncols,
        "crop_counts"   : crop_counts,
        "priority_map"  : {CROP_NAMES[k]: v for k, v in priority_map.items()},
        "tile_origin"   : tile_origin,
        "source_pixels" : n_pixels,
        "downsample"    : downsample,
    }

    print(f"  Output grid: {final_nrows} x {final_ncols} ({final_nrows * final_ncols:,} cells)")
    print(f"  Crops present: {list(crop_counts.keys())}")
    return grid, meta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _detect_label_col(df: pd.DataFrame) -> int:
    """
    Heuristic: the label column contains small positive integers (1-7).
    Try last column first (most common convention), then first column.
    """
    for col_idx in [df.shape[1] - 1, 0]:
        col = df.iloc[:, col_idx]
        unique_vals = col.dropna().unique()
        if (
            len(unique_vals) <= 10
            and float(col.min()) >= 0
            and float(col.max()) <= 20
            and (col == col.astype(int)).all()
        ):
            return col_idx
    # Fallback: last column
    return df.shape[1] - 1


def describe_grid(grid: List[List[float]], meta: Dict) -> None:
    """Print a human-readable summary of a loaded priority grid."""
    arr   = np.array(grid)
    print(f"\nGrid summary")
    print(f"  Shape         : {arr.shape[0]} rows x {arr.shape[1]} cols")
    print(f"  Priority range: {arr.min():.2f} - {arr.max():.2f}")
    print(f"  Mean priority : {arr.mean():.3f}")
    print(f"\nCrop distribution:")
    total = sum(meta["crop_counts"].values())
    for crop, count in sorted(meta["crop_counts"].items(),
                              key=lambda x: x[1], reverse=True):
        pct = 100.0 * count / total if total else 0
        bar = "#" * int(pct / 2)
        print(f"  {crop:<12} {count:>6,}  ({pct:4.1f}%)  {bar}")
    print(f"\nPriority weights used:")
    for crop, w in meta["priority_map"].items():
        print(f"  {crop:<12} -> {w}")
