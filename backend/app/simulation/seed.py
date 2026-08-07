"""Seed data for the default simulated cluster.

Used once, at first startup, to create the durable Cluster/Rack rows the
simulation then attaches live telemetry to. Rack names mirror the frontend's
existing digital twin so both sides of the product describe the same
cluster.
"""

DEFAULT_CLUSTER_NAME = "Lukstack Alpha"
DEFAULT_CLUSTER_LOCATION = "Primary Datacenter"

RACK_SEEDS: list[dict[str, object]] = [
    {"name": "Rack A1", "baseline_gpu": 55.0, "baseline_jobs": 14},
    {"name": "Rack B2", "baseline_gpu": 48.0, "baseline_jobs": 11},
    {"name": "Rack C1", "baseline_gpu": 62.0, "baseline_jobs": 17},
    {"name": "Rack D4", "baseline_gpu": 44.0, "baseline_jobs": 9},
]
