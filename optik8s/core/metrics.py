"""Metrics collector – pulls per-pod resource metrics from Prometheus HTTP API.

Queries the Prometheus ``/api/v1/query`` instant-query endpoint to fetch:

  - CPU usage      (cores)   – ``rate(container_cpu_usage_seconds_total[5m])``
  - Memory usage   (bytes)   – ``container_memory_working_set_bytes``
  - CPU requests   (cores)   – ``kube_pod_container_resource_requests{resource="cpu"}``
  - CPU limits     (cores)   – ``kube_pod_container_resource_limits{resource="cpu"}``
  - Memory requests (bytes)  – ``kube_pod_container_resource_requests{resource="memory"}``
  - Memory limits   (bytes)  – ``kube_pod_container_resource_limits{resource="memory"}``

Metrics are returned as JSON-serialisable dicts (one entry per pod).

Use ``format_metrics_for_analysis`` to convert raw metrics into a human-readable
structure (millicores, MiB, usage-vs-request percentages) grouped by deployment
and ready for AI consumption.
"""

from __future__ import annotations

import datetime
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

DEFAULT_PROMETHEUS_URL = "http://localhost:9090"

# ---------------------------------------------------------------------------
# PromQL queries (aggregated per pod/namespace)
# ---------------------------------------------------------------------------

_QUERIES: dict[str, str] = {
    "cpu_usage": (
        'sum by (pod, namespace) ('
        '  rate(container_cpu_usage_seconds_total{container!="", container!="POD"}[5m])'
        ')'
    ),
    "memory_usage": (
        'sum by (pod, namespace) ('
        '  container_memory_working_set_bytes{container!="", container!="POD"}'
        ')'
    ),
    "cpu_requests": (
        'sum by (pod, namespace) ('
        '  kube_pod_container_resource_requests{resource="cpu", container!=""}'
        ')'
    ),
    "cpu_limits": (
        'sum by (pod, namespace) ('
        '  kube_pod_container_resource_limits{resource="cpu", container!=""}'
        ')'
    ),
    "memory_requests": (
        'sum by (pod, namespace) ('
        '  kube_pod_container_resource_requests{resource="memory", container!=""}'
        ')'
    ),
    "memory_limits": (
        'sum by (pod, namespace) ('
        '  kube_pod_container_resource_limits{resource="memory", container!=""}'
        ')'
    ),
}

# PromQL queries used to resolve pod → deployment name
_QUERY_POD_OWNER = 'kube_pod_owner{owner_kind="ReplicaSet"}'
_QUERY_RS_OWNER = 'kube_replicaset_owner{owner_kind="Deployment"}'

# Regex matching a Kubernetes hash suffix (ReplicaSet: 9-10 chars, pod: 5 chars)
_RS_HASH_RE = re.compile(r"^[a-z0-9]{8,10}$")
_POD_HASH_RE = re.compile(r"^[a-z0-9]{5}$")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _prometheus_query(base_url: str, query: str, timeout: int = 10) -> list[dict]:
    """Execute an instant PromQL query and return the ``result`` list.

    Only ``http`` and ``https`` schemes are accepted to prevent SSRF.
    Returns an empty list on any connection or parse error so callers always
    receive a well-typed value.
    """
    parsed = urllib.parse.urlparse(base_url)
    if parsed.scheme not in ("http", "https"):
        return []

    url = f"{base_url.rstrip('/')}/api/v1/query"
    params = urllib.parse.urlencode({"query": query})
    full_url = f"{url}?{params}"

    try:
        with urllib.request.urlopen(full_url, timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read().decode())
            if body.get("status") == "success":
                return body.get("data", {}).get("result", [])
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        pass
    return []


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def collect_pod_metrics(
    prometheus_url: str = DEFAULT_PROMETHEUS_URL,
    namespace: Optional[str] = None,
    timeout: int = 10,
) -> dict:
    """Collect per-pod CPU and memory metrics from Prometheus.

    Parameters
    ----------
    prometheus_url:
        Base URL of the Prometheus server (default: ``http://localhost:9090``).
    namespace:
        When provided, only pods in this Kubernetes namespace are returned.
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    dict
        ``{
            "prometheus_url": str,
            "pods": [
                {
                    "name": str,
                    "namespace": str,
                    "cpu_usage_cores": float | None,
                    "memory_usage_bytes": float | None,
                    "cpu_requests_cores": float | None,
                    "cpu_limits_cores": float | None,
                    "memory_requests_bytes": float | None,
                    "memory_limits_bytes": float | None,
                },
                ...
            ]
        }``
    """
    raw: dict[str, dict[tuple[str, str], float]] = {}

    for metric_name, query in _QUERIES.items():
        raw[metric_name] = {}
        rows = _prometheus_query(prometheus_url, query, timeout=timeout)
        for row in rows:
            pod = row.get("metric", {}).get("pod", "")
            ns = row.get("metric", {}).get("namespace", "")
            if not pod:
                continue
            if namespace and ns != namespace:
                continue
            try:
                value = float(row["value"][1])
            except (KeyError, IndexError, ValueError):
                continue
            raw[metric_name][(pod, ns)] = value

    # Union of all (pod, namespace) pairs seen across every metric query.
    all_pods: set[tuple[str, str]] = set()
    for metric_results in raw.values():
        all_pods.update(metric_results.keys())

    pods = [
        {
            "name": pod_name,
            "namespace": ns,
            "cpu_usage_cores": raw["cpu_usage"].get((pod_name, ns)),
            "memory_usage_bytes": raw["memory_usage"].get((pod_name, ns)),
            "cpu_requests_cores": raw["cpu_requests"].get((pod_name, ns)),
            "cpu_limits_cores": raw["cpu_limits"].get((pod_name, ns)),
            "memory_requests_bytes": raw["memory_requests"].get((pod_name, ns)),
            "memory_limits_bytes": raw["memory_limits"].get((pod_name, ns)),
        }
        for pod_name, ns in sorted(all_pods)
    ]

    return {
        "prometheus_url": prometheus_url,
        "pods": pods,
    }


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _to_millicores(cores: Optional[float]) -> Optional[float]:
    """Convert CPU cores to millicores, rounded to 1 decimal place."""
    if cores is None:
        return None
    return round(cores * 1000, 1)


def _to_mib(bytes_val: Optional[float]) -> Optional[float]:
    """Convert bytes to MiB, rounded to 2 decimal places."""
    if bytes_val is None:
        return None
    return round(bytes_val / (1024 * 1024), 2)


def _usage_pct(usage: Optional[float], requested: Optional[float]) -> Optional[float]:
    """Return usage as a percentage of requested, or None when either value is absent."""
    if usage is None or requested is None or requested == 0:
        return None
    return round((usage / requested) * 100, 1)


def _infer_deployment_name(pod_name: str) -> str:
    """Infer the deployment name from a Kubernetes pod name.

    Kubernetes Deployment pods follow the naming convention::

        <deployment>-<replicaset-hash>-<pod-hash>

    where the ReplicaSet hash is 8–10 lowercase alphanumeric characters and the
    pod hash is exactly 5 lowercase alphanumeric characters.  This function
    strips those two trailing segments when they match that pattern.

    Falls back to stripping just the last segment if it looks like a pod hash,
    and finally returns the original name unchanged.
    """
    parts = pod_name.split("-")
    if len(parts) >= 3:
        has_pod_hash = _POD_HASH_RE.match(parts[-1])
        has_rs_hash = _RS_HASH_RE.match(parts[-2])
        if has_pod_hash and has_rs_hash:
            return "-".join(parts[:-2])
    if len(parts) >= 2 and _POD_HASH_RE.match(parts[-1]):
        return "-".join(parts[:-1])
    return pod_name


def _get_deployment_names(
    prometheus_url: str,
    namespace: Optional[str],
    timeout: int,
) -> dict[tuple[str, str], str]:
    """Return a mapping of ``(pod_name, namespace) → deployment_name``.

    Resolves the chain pod → ReplicaSet → Deployment using the
    ``kube_pod_owner`` and ``kube_replicaset_owner`` Prometheus metrics exposed
    by *kube-state-metrics*.  Returns an empty dict when the metrics are
    unavailable (e.g. kube-state-metrics is not installed).
    """
    # pod → replicaset
    pod_to_rs: dict[tuple[str, str], str] = {}
    for row in _prometheus_query(prometheus_url, _QUERY_POD_OWNER, timeout=timeout):
        metric = row.get("metric", {})
        pod = metric.get("pod", "")
        ns = metric.get("namespace", "")
        rs_name = metric.get("owner_name", "")
        if pod and ns and rs_name:
            if namespace and ns != namespace:
                continue
            pod_to_rs[(pod, ns)] = rs_name

    # replicaset → deployment
    rs_to_deploy: dict[tuple[str, str], str] = {}
    for row in _prometheus_query(prometheus_url, _QUERY_RS_OWNER, timeout=timeout):
        metric = row.get("metric", {})
        rs = metric.get("replicaset", "")
        ns = metric.get("namespace", "")
        deploy_name = metric.get("owner_name", "")
        if rs and ns and deploy_name:
            rs_to_deploy[(rs, ns)] = deploy_name

    # join: pod → deployment
    result: dict[tuple[str, str], str] = {}
    for (pod, ns), rs in pod_to_rs.items():
        deploy = rs_to_deploy.get((rs, ns))
        if deploy:
            result[(pod, ns)] = deploy
    return result


# ---------------------------------------------------------------------------
# Public API – formatted metrics
# ---------------------------------------------------------------------------

def format_metrics_for_analysis(
    prometheus_url: str = DEFAULT_PROMETHEUS_URL,
    namespace: Optional[str] = None,
    timeout: int = 10,
) -> dict:
    """Format pod metrics as human-readable JSON structured for AI analysis.

    Builds on :func:`collect_pod_metrics` and adds:

    - **Deployment name** resolved via ``kube_pod_owner`` /
      ``kube_replicaset_owner`` Prometheus metrics (with a naming-convention
      fallback when those metrics are unavailable).
    - **Human-readable units**: CPU in *millicores*, memory in *MiB*.
    - **Usage-vs-requested percentages** for both CPU and memory.
    - **Deployment-level grouping** so AI models can reason about workloads
      rather than individual pods.
    - **Summary statistics** (totals, namespace list) at the top level.

    Parameters
    ----------
    prometheus_url:
        Base URL of the Prometheus server (default: ``http://localhost:9090``).
    namespace:
        When provided, only pods in this Kubernetes namespace are returned.
    timeout:
        HTTP request timeout in seconds.

    Returns
    -------
    dict
        ``{
            "collected_at": str,          # ISO-8601 UTC timestamp
            "prometheus_url": str,
            "summary": {
                "total_deployments": int,
                "total_pods": int,
                "namespaces": [str, ...]
            },
            "deployments": [
                {
                    "deployment": str,
                    "namespace": str,
                    "pods": [
                        {
                            "name": str,
                            "cpu": {
                                "usage_millicores": float | None,
                                "requested_millicores": float | None,
                                "limit_millicores": float | None,
                                "usage_pct_of_requested": float | None
                            },
                            "memory": {
                                "usage_mib": float | None,
                                "requested_mib": float | None,
                                "limit_mib": float | None,
                                "usage_pct_of_requested": float | None
                            }
                        },
                        ...
                    ]
                },
                ...
            ]
        }``
    """
    raw = collect_pod_metrics(prometheus_url=prometheus_url, namespace=namespace, timeout=timeout)
    deploy_map = _get_deployment_names(prometheus_url, namespace, timeout)

    # Build per-deployment buckets
    deployments: dict[tuple[str, str], list[dict]] = {}

    for pod in raw["pods"]:
        pod_key = (pod["name"], pod["namespace"])
        deploy_name = deploy_map.get(pod_key) or _infer_deployment_name(pod["name"])
        bucket_key = (deploy_name, pod["namespace"])

        pod_entry = {
            "name": pod["name"],
            "cpu": {
                "usage_millicores": _to_millicores(pod["cpu_usage_cores"]),
                "requested_millicores": _to_millicores(pod["cpu_requests_cores"]),
                "limit_millicores": _to_millicores(pod["cpu_limits_cores"]),
                "usage_pct_of_requested": _usage_pct(
                    pod["cpu_usage_cores"], pod["cpu_requests_cores"]
                ),
            },
            "memory": {
                "usage_mib": _to_mib(pod["memory_usage_bytes"]),
                "requested_mib": _to_mib(pod["memory_requests_bytes"]),
                "limit_mib": _to_mib(pod["memory_limits_bytes"]),
                "usage_pct_of_requested": _usage_pct(
                    pod["memory_usage_bytes"], pod["memory_requests_bytes"]
                ),
            },
        }

        deployments.setdefault(bucket_key, []).append(pod_entry)

    deployment_list = [
        {
            "deployment": deploy_name,
            "namespace": ns,
            "pods": sorted(pods, key=lambda p: p["name"]),
        }
        for (deploy_name, ns), pods in sorted(deployments.items())
    ]

    namespaces = sorted({d["namespace"] for d in deployment_list})

    return {
        "collected_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "prometheus_url": prometheus_url,
        "summary": {
            "total_deployments": len(deployment_list),
            "total_pods": len(raw["pods"]),
            "namespaces": namespaces,
        },
        "deployments": deployment_list,
    }
