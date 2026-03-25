"""Cluster management for KIND (local) and EKS (AWS) Kubernetes clusters.

All operations shell out to the relevant CLI tools:
  - kind    https://kind.sigs.k8s.io/
  - eksctl  https://eksctl.io/
  - kubectl https://kubernetes.io/docs/reference/kubectl/
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Optional

# Root of the clusters/ directory relative to this file's location
_REPO_ROOT = Path(__file__).parent.parent.parent
CLUSTERS_DIR = _REPO_ROOT / "clusters"


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
# KIND
# ---------------------------------------------------------------------------

def kind_create(name: str = "optik8s", config_path: Optional[str] = None) -> dict:
    """Create a KIND cluster.

    Uses the bundled clusters/kind/cluster-config.yaml by default.
    """
    cmd = ["kind", "create", "cluster", "--name", name]
    if config_path:
        cmd += ["--config", config_path]
    else:
        default_config = CLUSTERS_DIR / "kind" / "cluster-config.yaml"
        if default_config.exists():
            cmd += ["--config", str(default_config)]

    result = _run(cmd, capture=False)
    return {"success": result.returncode == 0, "returncode": result.returncode}


def kind_delete(name: str = "optik8s") -> dict:
    """Delete a KIND cluster."""
    result = _run(["kind", "delete", "cluster", "--name", name])
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def kind_list() -> list[str]:
    """Return a list of existing KIND cluster names."""
    result = _run(["kind", "get", "clusters"])
    if result.returncode != 0:
        return []
    return [c.strip() for c in result.stdout.splitlines() if c.strip()]


def kind_set_context(name: str = "optik8s") -> dict:
    """Export the kubeconfig for a KIND cluster so kubectl uses it."""
    result = _run(["kind", "export", "kubeconfig", "--name", name])
    return {"success": result.returncode == 0, "stderr": result.stderr}


# ---------------------------------------------------------------------------
# EKS
# ---------------------------------------------------------------------------

def eks_create(
    name: str = "optik8s",
    region: str = "us-east-1",
    config_path: Optional[str] = None,
) -> dict:
    """Create an EKS cluster using eksctl.

    If *config_path* is provided, that file is used verbatim.
    Otherwise the bundled clusters/eks/cluster-config.yaml template is used
    (name and region are passed as overrides via --name/--region flags when the
    template does not hard-code them).
    """
    if config_path:
        cmd = ["eksctl", "create", "cluster", "-f", config_path]
    else:
        default_config = CLUSTERS_DIR / "eks" / "cluster-config.yaml"
        if default_config.exists():
            cmd = ["eksctl", "create", "cluster", "-f", str(default_config)]
        else:
            cmd = [
                "eksctl", "create", "cluster",
                "--name", name,
                "--region", region,
                "--nodes", "2",
                "--node-type", "t3.medium",
                "--managed",
            ]

    result = _run(cmd, capture=False)
    return {"success": result.returncode == 0, "returncode": result.returncode}


def eks_delete(name: str = "optik8s", region: str = "us-east-1") -> dict:
    """Delete an EKS cluster using eksctl."""
    result = _run(
        ["eksctl", "delete", "cluster", "--name", name, "--region", region],
        capture=False,
    )
    return {"success": result.returncode == 0, "returncode": result.returncode}


def eks_list(region: str = "us-east-1") -> list[str]:
    """Return a list of EKS cluster names in *region*."""
    result = _run(
        ["eksctl", "get", "cluster", "--region", region, "--output", "json"]
    )
    if result.returncode != 0:
        return []
    try:
        clusters = json.loads(result.stdout)
        names: list[str] = []
        for c in clusters:
            name = (
                c.get("Name")
                or c.get("name")
                or c.get("metadata", {}).get("name", "")
            )
            if name:
                names.append(name)
        return names
    except (json.JSONDecodeError, TypeError, AttributeError):
        return []


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------

def list_all_clusters() -> dict:
    """Return clusters grouped by provider."""
    return {
        "kind": kind_list() if _tool_available("kind") else [],
        "eks": eks_list() if _tool_available("eksctl") else [],
    }


def get_current_context() -> str:
    """Return the currently active kubectl context name."""
    result = _run(["kubectl", "config", "current-context"])
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def list_contexts() -> list[str]:
    """Return all kubectl context names."""
    result = _run(
        ["kubectl", "config", "get-contexts", "-o", "name"]
    )
    if result.returncode != 0:
        return []
    return [c.strip() for c in result.stdout.splitlines() if c.strip()]


def get_nodes(context: Optional[str] = None) -> list[dict]:
    """Return node info for the given (or current) kubectl context."""
    cmd = ["kubectl", "get", "nodes", "-o", "json"]
    if context:
        cmd += ["--context", context]

    result = _run(cmd)
    if result.returncode != 0:
        return []

    try:
        data = json.loads(result.stdout)
        nodes = []
        for item in data.get("items", []):
            name = item["metadata"]["name"]
            conditions = item.get("status", {}).get("conditions", [])
            ready = any(
                c["type"] == "Ready" and c["status"] == "True"
                for c in conditions
            )
            _prefix = "node-role.kubernetes.io/"
            roles = [
                k[len(_prefix):]
                for k in item["metadata"].get("labels", {})
                if k.startswith(_prefix)
            ]
            nodes.append({
                "name": name,
                "ready": ready,
                "roles": roles or ["worker"],
            })
        return nodes
    except (json.JSONDecodeError, KeyError):
        return []


def tool_versions() -> dict:
    """Return version strings for the CLI tools used."""
    versions: dict[str, str] = {}
    for tool, args in [
        ("kind", ["kind", "version"]),
        ("kubectl", ["kubectl", "version", "--client", "--short"]),
        ("eksctl", ["eksctl", "version"]),
        ("helm", ["helm", "version", "--short"]),
        ("docker", ["docker", "--version"]),
        ("aws", ["aws", "--version"]),
    ]:
        try:
            result = subprocess.run(args, capture_output=True, text=True)
            if result.returncode == 0:
                output = (result.stdout.strip() or result.stderr.strip())
                versions[tool] = output.splitlines()[0] if output else "installed"
            else:
                versions[tool] = "not found"
        except FileNotFoundError:
            versions[tool] = "not found"
    return versions
