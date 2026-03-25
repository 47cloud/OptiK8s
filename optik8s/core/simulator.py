"""Workload simulator – generate synthetic pod metrics for testing.

Produces realistic-looking resource usage data based on common Kubernetes
application architecture patterns.  Scenarios can be triggered on demand
from the CLI or the web UI, with an optional random seed for reproducibility.

Usage examples
--------------
Generate a scenario and pipe to the analyze command::

    optik8s simulate generate --architecture backend-api --load idle > /tmp/sim.json
    optik8s metrics analyze --input /tmp/sim.json

Run a built-in scenario and show the analysis directly::

    optik8s simulate run --scenario ecommerce

List available architectures and named scenarios::

    optik8s simulate list
"""

from __future__ import annotations

import datetime
import random
from typing import Optional

# ---------------------------------------------------------------------------
# Architecture profiles
# ---------------------------------------------------------------------------

ARCHITECTURES: dict[str, dict] = {
    "frontend": {
        "name": "Frontend (nginx / Node.js SPA)",
        "description": (
            "Stateless web frontend serving static assets or a server-side rendered app. "
            "Typically over-requested because developers set generous CPU limits."
        ),
        "cpu_request_range": (50, 200),       # millicores
        "memory_request_range": (64, 256),     # MiB
        "cpu_usage_ratios": {                  # fraction of requested CPU actually used
            "idle":   (0.02, 0.08),
            "normal": (0.10, 0.25),
            "high":   (0.50, 0.80),
        },
        "memory_usage_ratios": {
            "idle":   (0.15, 0.30),
            "normal": (0.30, 0.55),
            "high":   (0.60, 0.85),
        },
    },
    "backend-api": {
        "name": "Backend REST API",
        "description": (
            "General-purpose microservice / REST API. CPU usage spikes under load. "
            "Memory footprint is more stable but often over-requested."
        ),
        "cpu_request_range": (100, 500),
        "memory_request_range": (128, 512),
        "cpu_usage_ratios": {
            "idle":   (0.03, 0.10),
            "normal": (0.20, 0.50),
            "high":   (0.60, 0.95),
        },
        "memory_usage_ratios": {
            "idle":   (0.20, 0.40),
            "normal": (0.35, 0.65),
            "high":   (0.65, 0.90),
        },
    },
    "database": {
        "name": "Relational Database (PostgreSQL / MySQL)",
        "description": (
            "Stateful database pod. Memory usage is high and stable (buffer pool). "
            "CPU usage is generally low unless under heavy query load."
        ),
        "cpu_request_range": (100, 400),
        "memory_request_range": (512, 2048),
        "cpu_usage_ratios": {
            "idle":   (0.01, 0.05),
            "normal": (0.10, 0.30),
            "high":   (0.40, 0.80),
        },
        "memory_usage_ratios": {
            "idle":   (0.40, 0.65),
            "normal": (0.55, 0.80),
            "high":   (0.75, 0.95),
        },
    },
    "cache": {
        "name": "In-memory Cache (Redis / Memcached)",
        "description": (
            "In-memory data store. Memory usage tracks data volume. "
            "CPU is almost always very low."
        ),
        "cpu_request_range": (50, 150),
        "memory_request_range": (128, 1024),
        "cpu_usage_ratios": {
            "idle":   (0.01, 0.05),
            "normal": (0.03, 0.12),
            "high":   (0.15, 0.40),
        },
        "memory_usage_ratios": {
            "idle":   (0.10, 0.25),
            "normal": (0.40, 0.70),
            "high":   (0.70, 0.95),
        },
    },
    "worker": {
        "name": "Background Worker / Queue Consumer",
        "description": (
            "Consumes tasks from a queue (Celery, BullMQ, etc.). "
            "CPU is steady at medium utilisation; memory is moderate."
        ),
        "cpu_request_range": (100, 400),
        "memory_request_range": (128, 512),
        "cpu_usage_ratios": {
            "idle":   (0.02, 0.08),
            "normal": (0.35, 0.65),
            "high":   (0.70, 0.95),
        },
        "memory_usage_ratios": {
            "idle":   (0.15, 0.30),
            "normal": (0.30, 0.60),
            "high":   (0.60, 0.85),
        },
    },
    "batch": {
        "name": "Batch / ETL Job",
        "description": (
            "Periodic batch processing or ETL pipeline. "
            "Bursty CPU and high memory when running; nearly idle otherwise."
        ),
        "cpu_request_range": (200, 1000),
        "memory_request_range": (256, 2048),
        "cpu_usage_ratios": {
            "idle":   (0.01, 0.04),
            "normal": (0.25, 0.60),
            # Capped at 1.00 because CPU requests are the scheduling guarantee;
            # actual burst above the request is not modelled here.
            "high":   (0.70, 1.00),
        },
        "memory_usage_ratios": {
            "idle":   (0.05, 0.15),
            "normal": (0.30, 0.65),
            "high":   (0.65, 0.95),
        },
    },
    "ml-inference": {
        "name": "ML Inference Service",
        "description": (
            "Serves machine-learning model predictions. "
            "Very high memory (model weights in RAM). "
            "CPU spikes per request batch; otherwise low."
        ),
        "cpu_request_range": (500, 2000),
        "memory_request_range": (1024, 8192),
        "cpu_usage_ratios": {
            "idle":   (0.01, 0.06),
            "normal": (0.15, 0.45),
            "high":   (0.55, 0.90),
        },
        "memory_usage_ratios": {
            "idle":   (0.50, 0.75),
            "normal": (0.60, 0.85),
            "high":   (0.80, 0.98),
        },
    },
    "microservice": {
        "name": "Generic Microservice",
        "description": (
            "Small, focused microservice in a service-mesh architecture. "
            "Moderate requests, often over-provisioned due to conservative default limits."
        ),
        "cpu_request_range": (50, 300),
        "memory_request_range": (64, 384),
        "cpu_usage_ratios": {
            "idle":   (0.02, 0.08),
            "normal": (0.15, 0.40),
            "high":   (0.50, 0.85),
        },
        "memory_usage_ratios": {
            "idle":   (0.10, 0.25),
            "normal": (0.25, 0.55),
            "high":   (0.60, 0.85),
        },
    },
}

LOAD_PROFILES: tuple[str, ...] = ("idle", "normal", "high", "mixed")

# ---------------------------------------------------------------------------
# Predefined named scenarios
# ---------------------------------------------------------------------------
# Each scenario entry contains a list of pod group descriptors:
#   architecture – key from ARCHITECTURES
#   load         – load profile for this group
#   count        – number of pods to generate for this group
#   prefix       – name prefix for the generated pod names

SCENARIOS: dict[str, dict] = {
    "ecommerce": {
        "description": (
            "E-commerce platform with a frontend, backend API, PostgreSQL, and Redis. "
            "Simulates a quiet off-peak period (mostly idle → overprovisioned)."
        ),
        "pods": [
            {"architecture": "frontend",    "load": "idle",   "count": 3, "prefix": "storefront"},
            {"architecture": "backend-api", "load": "idle",   "count": 2, "prefix": "product-api"},
            {"architecture": "database",    "load": "normal", "count": 1, "prefix": "postgres"},
            {"architecture": "cache",       "load": "normal", "count": 1, "prefix": "redis"},
        ],
    },
    "saas-platform": {
        "description": (
            "Multi-tenant SaaS application: API gateway, microservices, workers, and a database. "
            "Mix of idle and normal loads to demonstrate partial overprovisioning."
        ),
        "pods": [
            {"architecture": "microservice", "load": "idle",   "count": 4, "prefix": "gateway"},
            {"architecture": "backend-api",  "load": "normal", "count": 3, "prefix": "app-svc"},
            {"architecture": "worker",       "load": "idle",   "count": 2, "prefix": "worker"},
            {"architecture": "database",     "load": "normal", "count": 1, "prefix": "db"},
        ],
    },
    "data-pipeline": {
        "description": (
            "Data engineering pipeline: ingestion workers, ETL batch jobs, and ML inference. "
            "Heavy resource requests; idle between processing windows → heavily overprovisioned."
        ),
        "pods": [
            {"architecture": "worker",       "load": "idle", "count": 3, "prefix": "ingest"},
            {"architecture": "batch",        "load": "idle", "count": 2, "prefix": "etl"},
            {"architecture": "ml-inference", "load": "idle", "count": 1, "prefix": "model"},
        ],
    },
    "microservices": {
        "description": (
            "Cloud-native microservices mesh under normal operating load. "
            "Most services run well within requests – few flags expected."
        ),
        "pods": [
            {"architecture": "microservice", "load": "normal", "count": 5, "prefix": "svc"},
            {"architecture": "backend-api",  "load": "high",   "count": 2, "prefix": "api"},
            {"architecture": "cache",        "load": "normal", "count": 1, "prefix": "redis"},
        ],
    },
    "overprovisioned": {
        "description": (
            "Worst-case scenario: all pods are drastically overprovisioned (idle load). "
            "Useful for verifying the analysis engine flags every pod correctly."
        ),
        "pods": [
            {"architecture": "frontend",    "load": "idle", "count": 3, "prefix": "web"},
            {"architecture": "backend-api", "load": "idle", "count": 3, "prefix": "api"},
            {"architecture": "database",    "load": "idle", "count": 1, "prefix": "db"},
            {"architecture": "worker",      "load": "idle", "count": 2, "prefix": "worker"},
            {"architecture": "cache",       "load": "idle", "count": 1, "prefix": "cache"},
        ],
    },
    "peak-load": {
        "description": (
            "All services running at high load (e.g. Black Friday or end-of-quarter crunch). "
            "Verifies the analysis engine does NOT flag well-utilised pods."
        ),
        "pods": [
            {"architecture": "frontend",    "load": "high", "count": 3, "prefix": "web"},
            {"architecture": "backend-api", "load": "high", "count": 3, "prefix": "api"},
            {"architecture": "database",    "load": "high", "count": 1, "prefix": "db"},
            {"architecture": "worker",      "load": "high", "count": 2, "prefix": "worker"},
        ],
    },
}


# ---------------------------------------------------------------------------
# Public helpers – catalogue access
# ---------------------------------------------------------------------------

def list_architectures() -> dict[str, dict]:
    """Return the catalogue of available architecture profiles (metadata only)."""
    return {
        key: {
            "name": profile["name"],
            "description": profile["description"],
            "typical_cpu_request_millicores": list(profile["cpu_request_range"]),
            "typical_memory_request_mib": list(profile["memory_request_range"]),
            "load_profiles": list(profile["cpu_usage_ratios"].keys()),
        }
        for key, profile in ARCHITECTURES.items()
    }


def list_scenarios() -> dict[str, dict]:
    """Return the catalogue of predefined named scenarios (metadata only)."""
    return {
        name: {
            "description": scenario["description"],
            "pod_groups": scenario["pods"],
            "total_pods": sum(p["count"] for p in scenario["pods"]),
        }
        for name, scenario in SCENARIOS.items()
    }


# ---------------------------------------------------------------------------
# Pod generation
# ---------------------------------------------------------------------------

def _generate_pod(
    rng: random.Random,
    architecture: str,
    load: str,
    pod_name: str,
) -> dict:
    """Generate a single synthetic pod dict with the given architecture and load profile.

    Returns a pod dict compatible with the flat-list format accepted by
    ``optik8s metrics analyze --input``::

        {
            "name": str,
            "architecture": str,
            "load_profile": str,
            "cpu_requested": float,   # millicores
            "cpu_used": float,        # millicores
            "memory_requested": float, # MiB
            "memory_used": float,     # MiB
        }
    """
    profile = ARCHITECTURES[architecture]
    cpu_req = round(rng.uniform(*profile["cpu_request_range"]))
    mem_req = round(rng.uniform(*profile["memory_request_range"]), 1)

    chosen_load = rng.choice(["idle", "normal", "high"]) if load == "mixed" else load

    cpu_ratio = rng.uniform(*profile["cpu_usage_ratios"][chosen_load])
    mem_ratio = rng.uniform(*profile["memory_usage_ratios"][chosen_load])

    return {
        "name": pod_name,
        "architecture": architecture,
        "load_profile": chosen_load,
        "cpu_requested": cpu_req,
        "cpu_used": round(cpu_req * cpu_ratio, 1),
        "memory_requested": mem_req,
        "memory_used": round(mem_req * mem_ratio, 1),
    }


def generate_scenario(
    architectures: Optional[list[str]] = None,
    num_pods: int = 8,
    load: str = "mixed",
    seed: Optional[int] = None,
) -> list[dict]:
    """Generate a flat list of synthetic pods for an ad-hoc scenario.

    Parameters
    ----------
    architectures:
        List of architecture keys to draw from (default: all available).
    num_pods:
        Total number of pods to generate (must be ≥ 1).
    load:
        Load profile applied to all pods: ``"idle"``, ``"normal"``, ``"high"``,
        or ``"mixed"`` (each pod independently picks a random load level).
    seed:
        Integer random seed for reproducibility.  Pass ``None`` (default) to
        get a different result on every call.

    Returns
    -------
    list[dict]
        Flat list of pod dicts ready for ``optik8s metrics analyze --input``.

    Raises
    ------
    ValueError
        If an unknown architecture key or load profile is supplied, or if
        ``num_pods`` is less than 1.
    """
    if architectures is None:
        architectures = list(ARCHITECTURES.keys())
    else:
        unknown = [a for a in architectures if a not in ARCHITECTURES]
        if unknown:
            raise ValueError(f"Unknown architecture(s): {', '.join(unknown)}")

    if load not in LOAD_PROFILES:
        raise ValueError(
            f"Unknown load profile '{load}'. Choose from: {', '.join(LOAD_PROFILES)}"
        )

    if num_pods < 1:
        raise ValueError("num_pods must be at least 1")

    rng = random.Random(seed)
    pods = []
    for i in range(num_pods):
        arch = rng.choice(architectures)
        pod_name = f"{arch}-{i + 1}"
        pods.append(_generate_pod(rng, arch, load, pod_name))
    return pods


def _build_scenario_pods(scenario: dict, seed: Optional[int] = None) -> list[dict]:
    """Build a pod list from a predefined scenario definition."""
    rng = random.Random(seed)
    pods = []
    for entry in scenario["pods"]:
        arch = entry["architecture"]
        load = entry["load"]
        count = entry["count"]
        prefix = entry.get("prefix", arch)
        for i in range(count):
            pods.append(_generate_pod(rng, arch, load, f"{prefix}-{i + 1}"))
    return pods


# ---------------------------------------------------------------------------
# Formatted-metrics conversion (mirrors CLI helper to avoid circular import)
# ---------------------------------------------------------------------------

def _pods_to_formatted_metrics(pods: list[dict]) -> dict:
    """Convert a flat pod list to the structured format expected by the rules engine.

    This mirrors ``_simple_pods_to_formatted_metrics`` from the CLI but lives
    here to avoid a circular import between the simulator and the CLI module.
    """
    deployments = []
    for idx, p in enumerate(pods):
        name = p.get("name", f"pod-{idx}")
        cpu_req = float(p.get("cpu_requested") or 0)
        cpu_used = float(p.get("cpu_used") or 0)
        mem_req = float(p.get("memory_requested") or 0)
        mem_used = float(p.get("memory_used") or 0)

        cpu_pct = round((cpu_used / cpu_req) * 100, 1) if cpu_req > 0 else None
        mem_pct = round((mem_used / mem_req) * 100, 1) if mem_req > 0 else None

        pod_entry = {
            "name": name,
            "cpu": {
                "usage_millicores": cpu_used,
                "requested_millicores": cpu_req,
                "limit_millicores": None,
                "usage_pct_of_requested": cpu_pct,
            },
            "memory": {
                "usage_mib": mem_used,
                "requested_mib": mem_req,
                "limit_mib": None,
                "usage_pct_of_requested": mem_pct,
            },
        }
        deployments.append({
            "deployment": name,
            "namespace": "default",
            "pods": [pod_entry],
        })

    return {
        "collected_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "prometheus_url": "simulator",
        "summary": {
            "total_deployments": len(deployments),
            "total_pods": len(pods),
            "namespaces": ["default"],
        },
        "deployments": deployments,
    }


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------

def run_scenario(
    scenario_name: Optional[str] = None,
    architectures: Optional[list[str]] = None,
    num_pods: int = 8,
    load: str = "mixed",
    seed: Optional[int] = None,
    cpu_threshold_pct: float = 30.0,
    memory_threshold_pct: float = 30.0,
) -> dict:
    """Generate a workload scenario and run the rules-engine analysis.

    Either specify a ``scenario_name`` to use a predefined scenario, or supply
    ``architectures`` / ``num_pods`` / ``load`` to build a custom one.

    Parameters
    ----------
    scenario_name:
        Name of a predefined scenario (see :func:`list_scenarios`).
        When given, ``architectures``, ``num_pods``, and ``load`` are ignored.
    architectures:
        List of architecture keys to draw pods from (ad-hoc mode only).
    num_pods:
        Number of pods to generate (ad-hoc mode only, default: 8).
    load:
        Load profile for all pods (ad-hoc mode only, default: ``"mixed"``).
    seed:
        Random seed for reproducibility.
    cpu_threshold_pct:
        CPU overprovision threshold passed to the rules engine (default: 30 %).
    memory_threshold_pct:
        Memory overprovision threshold passed to the rules engine (default: 30 %).

    Returns
    -------
    dict
        The full analysis result dict (same shape as :func:`~optik8s.core.rules.analyze`)
        extended with a ``"scenario"`` key holding the generated pod list.

    Raises
    ------
    ValueError
        If an unknown ``scenario_name`` is provided.
    """
    from optik8s.core import rules as rules_ops  # avoid top-level circular import

    if scenario_name is not None:
        if scenario_name not in SCENARIOS:
            raise ValueError(
                f"Unknown scenario '{scenario_name}'. "
                f"Available: {', '.join(SCENARIOS)}"
            )
        pods = _build_scenario_pods(SCENARIOS[scenario_name], seed=seed)
    else:
        pods = generate_scenario(
            architectures=architectures,
            num_pods=num_pods,
            load=load,
            seed=seed,
        )

    formatted = _pods_to_formatted_metrics(pods)
    analysis = rules_ops.analyze(
        formatted,
        cpu_threshold_pct=cpu_threshold_pct,
        memory_threshold_pct=memory_threshold_pct,
    )
    analysis["scenario"] = pods
    return analysis
