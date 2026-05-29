"""
Regenerate results/mangrove_validation_comparison.png -before/after soil detection.

Two aerial images of the same UAE mangrove restoration site, ~21 months apart.
Each image is run through the soil detector with the same thresholds used in
run_mangrove.py, producing a 4-panel figure (RGB + mask for each date).
"""
import sys
sys.path.insert(0, '.')
import matplotlib
matplotlib.use('Agg')

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import matplotlib.gridspec as gridspec
from matplotlib.patches import Patch

from src.reforestation.soil_detector import detect_soil_mask, load_field_image

NROWS = 64
NCOLS = 64

# Thresholds calibrated for the before/after validation images
THRESHOLDS = dict(
    nrows=NROWS,
    ncols=NCOLS,
    soil_ndvi_threshold=0.10,
    water_blue_threshold=0.55,
    min_brightness_threshold=0.55,
    min_patch_cells=2,
)

FEB2024_PATH = 'aerial_mangrove_images/before.jpg'
NOV2025_PATH = 'aerial_mangrove_images/after.jpg'

CMAP_MASK = mcolors.ListedColormap(['#a8d5e2', '#c8a97a'])

def process(path):
    soil_mask, _, meta = detect_soil_mask(path, **THRESHOLDS)
    img_rgb = load_field_image(path, nrows=NROWS, ncols=NCOLS)
    return img_rgb, soil_mask, meta

print('Processing Feb 2024 image...')
img_before, mask_before, meta_before = process(FEB2024_PATH)
print(f'  Plantable cells: {meta_before["plantable_cells"]} ({meta_before["soil_pct"]:.1%})')

print('Processing Nov 2025 image...')
img_after,  mask_after,  meta_after  = process(NOV2025_PATH)
print(f'  Plantable cells: {meta_after["plantable_cells"]} ({meta_after["soil_pct"]:.1%})')

transitioned = meta_before['plantable_cells'] - meta_after['plantable_cells']
transition_pct = transitioned / meta_before['plantable_cells'] if meta_before['plantable_cells'] else 0

print(f'\nTransitioned cells (plantable -> growth): {transitioned} '
      f'({transition_pct:.1%} of 2024 mudflat)')

# ---------------------------------------------------------------------------
# Figure: 2 rows x 2 cols (RGB | mask) for each date
# ---------------------------------------------------------------------------
fig = plt.figure(figsize=(14, 8))
gs  = gridspec.GridSpec(2, 2, figure=fig,
                        hspace=0.10, wspace=0.05,
                        left=0.02, right=0.98,
                        top=0.88, bottom=0.10)

axes = [[fig.add_subplot(gs[r, c]) for c in range(2)] for r in range(2)]

legend_elements = [
    Patch(facecolor='#c8a97a', label='Plantable mudflat'),
    Patch(facecolor='#a8d5e2', label='Excluded (water / growth)'),
]

for row_idx, (img_rgb, soil_mask, meta, label) in enumerate([
    (img_before, mask_before, meta_before, 'Feb 2024'),
    (img_after,  mask_after,  meta_after,  'Nov 2025'),
]):
    # Left: aerial RGB
    axes[row_idx][0].imshow(img_rgb, aspect='auto')
    axes[row_idx][0].set_title(f'{label} — Aerial Image', fontsize=11, pad=4)
    axes[row_idx][0].axis('off')

    # Right: soil mask
    axes[row_idx][1].imshow(
        soil_mask.astype(np.uint8), cmap=CMAP_MASK, vmin=0, vmax=1, aspect='auto'
    )
    axes[row_idx][1].set_title(
        f'{label} — Plantable Mask  '
        f'({meta["plantable_cells"]} cells, {meta["soil_pct"]:.1%} of frame)',
        fontsize=11, pad=4,
    )
    axes[row_idx][1].axis('off')
    if row_idx == 1:
        axes[row_idx][1].legend(
            handles=legend_elements, loc='lower right', fontsize=8, framealpha=0.9
        )

fig.suptitle(
    'UAE Mangrove Restoration Site — Soil Detection Comparison\n'
    'Aerial imagery: Distant Imagery Solutions  |  Identical detection thresholds applied to both dates',
    fontsize=12, y=0.97,
)

# Footer annotation
fig.text(
    0.5, 0.03,
    f'Plantable mudflat: {meta_before["plantable_cells"]} cells (Feb 2024)  ->  '
    f'{meta_after["plantable_cells"]} cells (Nov 2025)'
    f'   |   {transitioned} cells ({transition_pct:.0%}) consistent with new growth',
    ha='center', va='bottom', fontsize=9.5, color='#333333',
)

out_path = 'results/mangrove_validation_comparison.png'
plt.savefig(out_path, dpi=150, bbox_inches='tight')
print(f'\nSaved: {out_path}')
plt.close()
