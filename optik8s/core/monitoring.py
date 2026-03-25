"""Prometheus and Grafana monitoring stack management.

Uses Helm to install the ``kube-prometheus-stack`` chart, which bundles:

  - Prometheus        – metrics collection and storage
  - Grafana           – dashboards for CPU, memory, and pod metrics
  - node-exporter     – per-node CPU / memory / disk metrics
  - kube-state-metrics – Kubernetes object state metrics

Any pod annotated with ``prometheus.io/scrape: "true"`` is scraped
automatically (see ``monitoring/prometheus/values.yaml``).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).parent.parent.parent
MONITORING_DIR = _REPO_ROOT / "monitoring"

HELM_RELEASE_NAME = "kube-prometheus-stack"
HELM_CHART = "prometheus-community/kube-prometheus-stack"
HELM_REPO_NAME = "prometheus-community"
HELM_REPO_URL = "https://prometheus-community.github.io/helm-charts"
MONITORING_NAMESPACE = "monitoring"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(
    cmd: list[str],
    capture: bool = True,
    check: bool = False,
) -> subprocess.CompletedProcess:
    """Run a command and return the result."""
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
    )


def _tool_available(name: str) -> bool:
    """Return True if *name* is on PATH."""
    return shutil.which(name) is not None


# ---------------------------------------------------------------------------
# Helm repo management
# ---------------------------------------------------------------------------

def helm_repo_add() -> dict:
    """Add the prometheus-community Helm repo and update it.

    Idempotent: succeeds even if the repo is already registered.
    """
    add_result = _run(
        ["helm", "repo", "add", HELM_REPO_NAME, HELM_REPO_URL]
    )
    if add_result.returncode != 0 and "already exists" not in add_result.stderr:
        return {"success": False, "stderr": add_result.stderr}

    update_result = _run(["helm", "repo", "update", HELM_REPO_NAME])
    return {
        "success": update_result.returncode == 0,
        "stderr": update_result.stderr,
    }


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def install_prometheus(
    namespace: str = MONITORING_NAMESPACE,
    context: Optional[str] = None,
    values_path: Optional[str] = None,
) -> dict:
    """Install Prometheus and Grafana via Helm (kube-prometheus-stack).

    Runs ``helm upgrade --install`` so the command is idempotent – calling it
    again on an existing release performs an in-place upgrade.

    Parameters
    ----------
    namespace:
        Kubernetes namespace to deploy into (created automatically).
    context:
        kubectl context to target; defaults to the active context.
    values_path:
        Path to a custom Helm values file.  Falls back to
        ``monitoring/prometheus/values.yaml`` when omitted.
    """
    if not _tool_available("helm"):
        return {"success": False, "error": "'helm' not found on PATH. Install Helm first."}
    if not _tool_available("kubectl"):
        return {"success": False, "error": "'kubectl' not found on PATH."}

    repo_result = helm_repo_add()
    if not repo_result["success"]:
        return {
            "success": False,
            "error": f"Helm repo setup failed: {repo_result.get('stderr', '')}",
        }

    default_values = MONITORING_DIR / "prometheus" / "values.yaml"
    cmd = [
        "helm", "upgrade", "--install",
        HELM_RELEASE_NAME, HELM_CHART,
        "--namespace", namespace,
        "--create-namespace",
    ]
    if context:
        cmd += ["--kube-context", context]
    if values_path:
        cmd += ["-f", values_path]
    elif default_values.exists():
        cmd += ["-f", str(default_values)]

    result = _run(cmd, capture=False)
    return {"success": result.returncode == 0, "returncode": result.returncode}


def uninstall_prometheus(
    namespace: str = MONITORING_NAMESPACE,
    context: Optional[str] = None,
) -> dict:
    """Uninstall the Prometheus / Grafana Helm release."""
    if not _tool_available("helm"):
        return {"success": False, "error": "'helm' not found on PATH."}

    cmd = ["helm", "uninstall", HELM_RELEASE_NAME, "--namespace", namespace]
    if context:
        cmd += ["--kube-context", context]

    result = _run(cmd)
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_prometheus_status(
    namespace: str = MONITORING_NAMESPACE,
    context: Optional[str] = None,
) -> dict:
    """Return pod status for the monitoring stack.

    Returns a dict with keys ``installed`` (bool), ``pods`` (list),
    and optionally ``error`` (str).
    """
    if not _tool_available("kubectl"):
        return {"installed": False, "pods": [], "error": "'kubectl' not found on PATH."}

    cmd = ["kubectl", "get", "pods", "-n", namespace, "-o", "json"]
    if context:
        cmd = cmd[:1] + ["--context", context] + cmd[1:]

    result = _run(cmd)
    if result.returncode != 0:
        return {"installed": False, "pods": [], "error": result.stderr}

    try:
        data = json.loads(result.stdout)
        pods = []
        for item in data.get("items", []):
            pod_name = item["metadata"]["name"]
            phase = item.get("status", {}).get("phase", "Unknown")
            ready_containers = sum(
                1 for cs in item.get("status", {}).get("containerStatuses", [])
                if cs.get("ready")
            )
            total_containers = len(item.get("spec", {}).get("containers", []))
            pods.append({
                "name": pod_name,
                "phase": phase,
                "ready": f"{ready_containers}/{total_containers}",
            })
        return {"installed": len(pods) > 0, "pods": pods, "namespace": namespace}
    except (json.JSONDecodeError, KeyError):
        return {"installed": False, "pods": []}


def get_prometheus_urls(
    namespace: str = MONITORING_NAMESPACE,
    context: Optional[str] = None,
) -> dict:
    """Return ``kubectl port-forward`` commands for Prometheus and Grafana.

    These commands can be run to access the UIs locally:

    - Prometheus: http://localhost:9090
    - Grafana:    http://localhost:3000  (admin / admin)
    """
    if not _tool_available("kubectl"):
        return {"prometheus": None, "grafana": None}

    cmd = ["kubectl", "get", "services", "-n", namespace, "-o", "json"]
    if context:
        cmd = cmd[:1] + ["--context", context] + cmd[1:]

    result = _run(cmd)
    if result.returncode != 0:
        return {"prometheus": None, "grafana": None}

    urls: dict[str, Optional[str]] = {"prometheus": None, "grafana": None}
    try:
        data = json.loads(result.stdout)
        for svc in data.get("items", []):
            name = svc["metadata"]["name"]
            ports = svc.get("spec", {}).get("ports", [])
            if not ports:
                continue
            port = ports[0]["port"]
            if "grafana" in name:
                urls["grafana"] = (
                    f"kubectl port-forward svc/{name} 3000:{port} -n {namespace}"
                )
            elif "prometheus" in name and "alertmanager" not in name and "operator" not in name:
                urls["prometheus"] = (
                    f"kubectl port-forward svc/{name} 9090:{port} -n {namespace}"
                )
    except (json.JSONDecodeError, KeyError):
        pass

    return urls
