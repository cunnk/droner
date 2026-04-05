"""
Crop row orientation estimation from a priority grid.

Uses the 2D FFT magnitude spectrum to find the dominant periodic direction in
the field.  Crop rows produce repeating intensity bands; their perpendicular
in frequency space marks the dominant spectral peak.

Returns
-------
orientation_deg : float
    Estimated strip alignment angle in [0, 175] degrees (rounded to 5°).
confidence : float
    0–1.  < 0.25 → no clear periodic structure; don't trust the estimate.
"""
from __future__ import annotations

import numpy as np


def estimate_orientation_deg(
    grid: np.ndarray,
    n_bins: int = 36,
) -> tuple[float, float]:
    """Estimate dominant crop row orientation and return (angle_deg, confidence).

    Parameters
    ----------
    grid : 2-D float array, values in [0, 1]
    n_bins : histogram resolution (default 36 → 5° bins)
    """
    grid = np.asarray(grid, dtype=float)
    nrows, ncols = grid.shape

    # ── 0. Downsample if image is large (orientation doesn't need full res) ───
    MAX_DIM = 512
    if max(nrows, ncols) > MAX_DIM:
        step_r = max(1, nrows // MAX_DIM)
        step_c = max(1, ncols // MAX_DIM)
        grid = grid[::step_r, ::step_c]
        nrows, ncols = grid.shape

    # ── 1. Subtract mean & apply Hann window to reduce spectral leakage ───────
    windowed = (grid - grid.mean())
    wr = np.hanning(nrows)[:, None]
    wc = np.hanning(ncols)[None, :]
    windowed *= wr * wc

    # ── 2. Zero-pad to a power of 2 (capped at 1024 to bound memory use) ──────
    pad = min(1024, max(128, int(2 ** np.ceil(np.log2(max(nrows, ncols) * 4)))))
    padded = np.zeros((pad, pad))
    r0, c0 = (pad - nrows) // 2, (pad - ncols) // 2
    padded[r0:r0 + nrows, c0:c0 + ncols] = windowed

    # ── 3. Magnitude spectrum (centred) ───────────────────────────────────────
    F = np.abs(np.fft.fftshift(np.fft.fft2(padded)))

    # ── 4. Zero out DC patch ──────────────────────────────────────────────────
    cr, cc = pad // 2, pad // 2
    dc_r = max(4, pad // 24)
    dc_c = max(4, pad // 24)
    F[cr - dc_r:cr + dc_r + 1, cc - dc_c:cc + dc_c + 1] = 0

    # ── 5. Compute angle from centre for every pixel ──────────────────────────
    rows_idx, cols_idx = np.mgrid[0:pad, 0:pad]
    dy = (rows_idx - cr).astype(float)
    dx = (cols_idx - cc).astype(float)
    angles = np.degrees(np.arctan2(dy, dx)) % 180   # collapse to [0, 180)

    # ── 6. Magnitude-weighted histogram of angles ─────────────────────────────
    weights = F.ravel()
    valid = weights > 0
    hist, edges = np.histogram(
        angles.ravel()[valid],
        bins=n_bins,
        range=(0.0, 180.0),
        weights=weights[valid],
    )

    # Smooth with a small circular kernel to reduce single-bin spikes
    k = 3
    kernel = np.ones(k) / k
    hist_smooth = np.convolve(
        np.concatenate([hist[-k:], hist, hist[:k]]),
        kernel,
        mode='same',
    )[k:-k]

    # ── 7. Peak = dominant frequency direction ────────────────────────────────
    peak_bin = int(np.argmax(hist_smooth))
    bin_width = 180.0 / n_bins
    freq_angle = peak_bin * bin_width + bin_width / 2   # bin centre

    # Strip orientation is perpendicular to the dominant spectral direction
    orientation = (freq_angle + 90.0) % 180.0

    # Round to nearest 5° to match the UI slider step
    orientation = round(orientation / 5) * 5
    if orientation >= 180:
        orientation = 175

    # ── 8. Confidence: peak-to-mean ratio (normalised to [0, 1]) ─────────────
    nonzero = hist_smooth[hist_smooth > 0]
    if nonzero.size == 0:
        confidence = 0.0
    else:
        mean_val = nonzero.mean()
        peak_val = hist_smooth[peak_bin]
        # ratio > 3 → high confidence; cap at 1
        confidence = float(min(1.0, (peak_val / mean_val - 1.0) / 4.0))

    return float(orientation), confidence
