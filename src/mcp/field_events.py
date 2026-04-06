"""
Simulated field event feed.

In a real deployment these would come from ground sensors, field worker
apps, or adjacent drone telemetry.  Here we generate a plausible timeline
that fires during the mission so Claude has something concrete to respond to.

Event types
-----------
pest_sighting   : field worker or scout reports pest pressure at a location
sensor_alert    : onboard / ground sensor detects NDVI anomaly or soil spike
worker_report   : field supervisor flags discolouration or disease symptoms
"""
from __future__ import annotations

import random
from dataclasses import asdict, dataclass
from typing import List


@dataclass
class FieldEvent:
    timestep: int
    event_type: str        # "pest_sighting" | "sensor_alert" | "worker_report"
    row: int
    col: int
    description: str
    severity: float = 0.5  # 0-1; higher = more urgent


def generate_event_feed(
    nrows: int,
    ncols: int,
    makespan_timesteps: int,
    n_events: int = 4,
    start_pct: float = 0.05,
    end_pct: float = 0.55,
    seed: int = 99,
) -> List[FieldEvent]:
    """
    Generate a plausible sequence of field events spread across the mission.

    Events are biased toward the high-priority corner of the field (top-right
    for the elevation image) so they are likely to correspond to strips that
    the baseline plan reaches late -- making the MCP response meaningful.

    Parameters
    ----------
    nrows, ncols          : grid dimensions
    makespan_timesteps    : estimated mission length in timesteps
    n_events              : number of events to generate
    start_pct, end_pct   : events fire within this fraction of the mission
    seed                  : reproducibility
    """
    rng = random.Random(seed)

    templates = [
        ("pest_sighting",  "Field worker reports aphid cluster"),
        ("sensor_alert",   "Onboard sensor detects NDVI anomaly"),
        ("worker_report",  "Ground crew flags fungal lesions"),
        ("sensor_alert",   "Soil-moisture probe spike detected"),
        ("pest_sighting",  "Adjacent drone relays elevated pest pressure"),
        ("worker_report",  "Field supervisor reports leaf discolouration"),
    ]

    start_t = int(makespan_timesteps * start_pct)
    end_t   = int(makespan_timesteps * end_pct)
    n       = min(n_events, end_t - start_t)
    timesteps = sorted(rng.sample(range(start_t, end_t), n))

    events: List[FieldEvent] = []
    for i, t in enumerate(timesteps):
        etype, base_desc = templates[i % len(templates)]
        # Bias toward top-right quadrant (high-elevation / high-priority cells)
        row = rng.randint(0, nrows // 2)
        col = rng.randint(ncols // 2, ncols - 1)
        severity = round(rng.uniform(0.6, 1.0), 2)
        events.append(FieldEvent(
            timestep    = t,
            event_type  = etype,
            row         = row,
            col         = col,
            description = f"{base_desc} near ({row}, {col}). Severity: {severity:.2f}",
            severity    = severity,
        ))

    return events


def events_to_dicts(events: List[FieldEvent]) -> List[dict]:
    """Serialise events for storage in the session / JSON transport."""
    return [asdict(e) for e in events]
