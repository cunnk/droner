"""End-to-end reforestation pipeline on the real Abu Dhabi mangrove image."""
import sys, matplotlib
sys.path.insert(0, '.')
matplotlib.use('Agg')

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
from matplotlib.patches import Patch

from src.reforestation.config        import MissionConfig, compute_sim_params, print_mission_summary
from src.reforestation.soil_detector import detect_soil_mask, load_field_image
from src.field.generator             import generate_strips
from src.field.contour               import generate_contour_strips
from src.optimizer.milp              import DroneSpec, assign_strips
from src.simulation.engine           import simulate
from src.simulation.metrics          import (
    compute_reforestation_metrics, print_reforestation_summary, plot_seed_drops
)
from src.viz.renderer                import animate

IMG_PATH = 'aerial_mangrove_images/screenshot1.jpg'

# -- Field dimensions ------------------------------------------------------
FIELD_W_M = 500   # metres wide
FIELD_H_M = 300   # metres tall
NCOLS = 64
NROWS = int(round(NCOLS * FIELD_H_M / FIELD_W_M))   # 38
print(f'Grid: {NCOLS}x{NROWS}  ({FIELD_W_M/NCOLS:.1f}m x {FIELD_H_M/NROWS:.1f}m per cell)')

# -- Soil detection --------------------------------------------------------
# Thresholds calibrated from pixel sampling:
#   Water (teal):  NDVI=0.22, Blue=0.56  -> excluded by NDVI threshold
#   Mudflat (tan): NDVI=-0.00, Blue=0.44, Brightness=0.57  -> plantable
#   Canopy (dark): NDVI=-0.02, Blue=0.28, Brightness=0.39  -> excluded by brightness
soil_mask, priority_grid, meta = detect_soil_mask(
    IMG_PATH,
    nrows=NROWS, ncols=NCOLS,
    soil_ndvi_threshold       = 0.12,   # catches teal water (NDVI~0.22)
    water_blue_threshold      = 0.50,   # backup water exclusion
    min_brightness_threshold  = 0.42,   # removes dark canopy (brightness~0.39)
    min_patch_cells           = 2,
)

print('Detection results:')
for k, v in meta.items():
    print(f'  {k}: {v}')

# -- Diagnostic plot -------------------------------------------------------
img_rgb = load_field_image(IMG_PATH, nrows=NROWS, ncols=NCOLS)
R = img_rgb[:,:,0].astype(np.float32) / 255.0
G = img_rgb[:,:,1].astype(np.float32) / 255.0
B = img_rgb[:,:,2].astype(np.float32) / 255.0
ndvi_arr   = (G - R) / (G + R + 1e-6)
brightness = 0.299*R + 0.587*G + 0.114*B

fig, axes = plt.subplots(1, 4, figsize=(20, 4))

axes[0].imshow(img_rgb, aspect='auto')
axes[0].set_title('Aerial Image (RGB)')
axes[0].axis('off')

im1 = axes[1].imshow(ndvi_arr, cmap='RdYlGn', vmin=-0.3, vmax=0.5, aspect='auto')
axes[1].set_title('Pseudo-NDVI\n(threshold=0.12)')
axes[1].axis('off')
plt.colorbar(im1, ax=axes[1], fraction=0.046)

im2 = axes[2].imshow(brightness, cmap='YlOrBr', vmin=0, vmax=1, aspect='auto')
axes[2].set_title('Brightness\n(min threshold=0.42)')
axes[2].axis('off')
plt.colorbar(im2, ax=axes[2], fraction=0.046)

cmap_m = mcolors.ListedColormap(['#a8d5e2', '#c8a97a'])
axes[3].imshow(soil_mask.astype(int), cmap=cmap_m, vmin=0, vmax=1, aspect='auto')
axes[3].set_title(
    f'Plantable Soil Mask\n'
    f'soil={meta["soil_pct"]:.1%}  water={meta["water_pct"]:.1%}  '
    f'dark-canopy={meta["dark_canopy_pct"]:.1%}'
)
axes[3].axis('off')
axes[3].legend(handles=[
    Patch(facecolor='#c8a97a', label=f'Plantable ({meta["plantable_cells"]} cells)'),
    Patch(facecolor='#a8d5e2', label='Skip (water/canopy)'),
], loc='lower right', fontsize=7)

plt.suptitle(f'Soil Detection -- Abu Dhabi Mangrove ({NCOLS}x{NROWS} grid)', fontsize=12)
plt.tight_layout()
plt.savefig('results/phase5_soil_detection.png', dpi=150, bbox_inches='tight')
print('Saved: results/phase5_soil_detection.png')
plt.close()

# -- Mission config --------------------------------------------------------
cfg = MissionConfig(
    seed_capacity         = 6000,
    seed_spacing_m        = 1.5,
    seed_jitter_sigma     = 0.4,
    field_width_m         = FIELD_W_M,
    battery_life_minutes  = 35.0,
    recharge_time_seconds = 3600.0,
    n_drones              = 3,
    dock_positions        = [(0, 0)],
    name                  = 'Mangrove Reforestation -- Abu Dhabi',
)
params = compute_sim_params(cfg, ncols=NCOLS)
print_mission_summary(cfg, ncols=NCOLS)

# -- Strips ----------------------------------------------------------------
# Contour mode: distance-transform rings that hug tidal channel boundaries.
# max_cells_per_strip keeps each strip within one hopper load so the drone
# advances through the strip without replaying the beginning after each refill.
hopper_cells = int(cfg.seed_capacity / params['seeds_per_cell'])   # ~221 cells
strips = generate_contour_strips(
    soil_mask,
    priority_grid,
    strip_width          = 1,          # finest rings -- maximum sinuosity
    seconds_per_cell     = cfg.seconds_per_cell,
    max_cells_per_strip  = hopper_cells - 10,  # small safety margin
)
total_spray = sum(len(s.spray_cells) for s in strips)
transit_only = sum(len(s.cells) - len(s.spray_cells) for s in strips)
print(f'Contour strips: {len(strips)}   Plantable cells: {total_spray}   Transit-only: {transit_only}')

# -- Assignment ------------------------------------------------------------
drones = [DroneSpec(id=i, seed_capacity=cfg.seed_capacity) for i in range(cfg.n_drones)]
result = assign_strips(strips, drones, objective_mode='makespan')
print(f'MILP: {result.status}  makespan={result.makespan:.0f}s  solve={result.solve_time:.2f}s')

# -- Simulation ------------------------------------------------------------
print('Simulating...')
history = simulate(
    strips, drones, result, NROWS, NCOLS,
    battery_drain_per_cell = params['battery_drain_per_cell'],
    recharge_time_steps    = params['recharge_time_steps'],
    dock_positions         = cfg.dock_positions,
    seeds_per_cell         = params['seeds_per_cell'],
    seed_jitter_sigma      = params['seed_jitter_sigma'],
)
print(f'Simulation: {len(history)} timesteps')

hopper_events = [s for s in history if s.get('event') and 'seed hopper' in s['event']]
print(f'Hopper-empty refill stops: {len(hopper_events)}')

# -- Metrics ---------------------------------------------------------------
m = compute_reforestation_metrics(
    history, strips, NROWS, NCOLS,
    meters_per_cell = params['meters_per_cell'],
    survival_rate   = cfg.survival_rate,
)
print_reforestation_summary(m, cfg)

# -- Animation with aerial overlay -----------------------------------------
print('Rendering GIF...')
FRAME_SKIP = max(1, len(history) // 150)
gif_hist   = history[::FRAME_SKIP]

n_plantable = sum(len(s.spray_cells) for s in strips)

anim = animate(
    state_history     = gif_hist,
    nrows             = NROWS,
    ncols             = NCOLS,
    interval_ms       = 150,
    dock_positions    = cfg.dock_positions,
    background_image  = img_rgb,
    save_path         = 'results/phase5_contour_mangrove.gif',
    show              = False,
    show_seed_drops   = True,
    mode_label        = cfg.name,
    n_plantable_cells = n_plantable,
)
print(f'GIF: results/phase5_contour_mangrove.gif ({len(gif_hist)} frames)')

# -- Seed drop visualisation -----------------------------------------------
fig2, axes2 = plt.subplots(1, 2, figsize=(14, 5))

# Left: seed cloud on aerial photo
axes2[0].imshow(img_rgb, aspect='auto', alpha=0.75, origin='upper',
                extent=(-0.5, NCOLS-0.5, NROWS-0.5, -0.5))
all_xs, all_ys = [], []
for s in history:
    for drop in s.get('seed_drops', []):
        ar, ac = drop['actual']
        all_xs.append(ac)
        all_ys.append(ar)
if all_xs:
    axes2[0].scatter(all_xs, all_ys, s=1.5, alpha=0.07, color='#4a148c', linewidths=0)
axes2[0].set_xlim(-0.5, NCOLS-0.5)
axes2[0].set_ylim(NROWS-0.5, -0.5)
axes2[0].set_title(f'Seed cloud on aerial image ({len(all_xs):,} drops)')
axes2[0].axis('off')

# Right: density heatmap
density = np.zeros((NROWS, NCOLS))
for s in history:
    for drop in s.get('seed_drops', []):
        r, c = drop['actual']
        density[r, c] += 1
im3 = axes2[1].imshow(density, cmap='YlOrRd', origin='upper', aspect='auto')
axes2[1].set_title(f'Seed density heatmap (max={density.max():.0f} seeds/cell)')
axes2[1].axis('off')
plt.colorbar(im3, ax=axes2[1], fraction=0.046, label='Seeds/cell')

total_drops = len(all_xs)
plt.suptitle(
    f'Non-Linear Seed Dispersal -- Abu Dhabi Mangrove\n'
    f'{total_drops:,} total drops  |  '
    f'{m.get("total_seeds_planted",0):,} seeds planted  |  '
    f'{m.get("total_area_covered_m2",0):,.0f} m2 covered',
    fontsize=11,
)
plt.tight_layout()
plt.savefig('results/phase5_seed_drops.png', dpi=150, bbox_inches='tight')
print('Saved: results/phase5_seed_drops.png')
plt.close()

print('\nAll outputs written to results/')
