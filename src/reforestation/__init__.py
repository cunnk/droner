"""
Reforestation drone mission planning module.

Adapts the core drone fleet optimization system for seed-planting missions,
with a focus on mangrove reforestation (Distant Imagery / Abu Dhabi style).

Primary entry points
--------------------
    from src.reforestation.config import MissionConfig, mangrove_preset, compute_sim_params
    from src.reforestation.soil_detector import detect_soil_mask, apply_tidal_mask
"""
