"""Sample application deployment management.

Each sample app lives under apps/<app-name>/k8s/ and contains standard
Kubernetes manifests (deployment.yaml, service.yaml, etc.).

Deployment is done by shelling out to ``kubectl apply -f <dir>``.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).parent.parent.parent
APPS_DIR = _REPO_ROOT / "apps"

# ---------------------------------------------------------------------------
# App catalogue
# ---------------------------------------------------------------------------

AVAILABLE_APPS: dict[str, dict] = {
    "nodejs-web": {
        "name": "Node.js Web Server",
        "description": "Simple Node.js Express web server – simulates a lightweight stateless frontend.",
        "tech": ["Node.js", "Express"],
        "image": "node:20-alpine",
        "port": 3000,
        "category": "frontend",
    },
    "python-api": {
        "name": "Python REST API",
        "description": "Python FastAPI service – simulates a backend microservice workload.",
        "tech": ["Python", "FastAPI"],
        "image": "python:3.12-slim",
        "port": 8000,
        "category": "backend",
    },
    "java-spring": {
        "name": "Java Spring Boot",
        "description": "Spring Boot application – simulates a heavier JVM-based workload.",
        "tech": ["Java 21", "Spring Boot"],
        "image": "ghcr.io/spring-projects/spring-petclinic:latest",
        "port": 8080,
        "category": "backend",
    },
    "postgres-db": {
        "name": "PostgreSQL Database",
        "description": "PostgreSQL 16 – relational database backend.",
        "tech": ["PostgreSQL 16"],
        "image": "postgres:16-alpine",
        "port": 5432,
        "category": "database",
    },
    "redis-cache": {
        "name": "Redis Cache",
        "description": "Redis 7 – in-memory data store / cache.",
        "tech": ["Redis 7"],
        "image": "redis:7-alpine",
        "port": 6379,
        "category": "cache",
    },
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(
    cmd: list[str],
    context: Optional[str] = None,
    namespace: str = "default",
) -> subprocess.CompletedProcess:
    """Run a kubectl command, injecting --context and --namespace if given."""
    if context:
        cmd = cmd[:1] + ["--context", context] + cmd[1:]
    if namespace and namespace != "default":
        cmd += ["-n", namespace]
    return subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_apps() -> dict[str, dict]:
    """Return the catalogue of available sample apps."""
    return AVAILABLE_APPS


def deploy_app(
    app_name: str,
    context: Optional[str] = None,
    namespace: str = "default",
) -> dict:
    """Deploy *app_name* to the cluster by applying its K8s manifests."""
    if app_name not in AVAILABLE_APPS:
        return {"success": False, "error": f"Unknown app: {app_name}. "
                f"Available: {', '.join(AVAILABLE_APPS)}"}

    app_dir = APPS_DIR / app_name / "k8s"
    if not app_dir.exists():
        return {"success": False, "error": f"Manifests not found at: {app_dir}"}

    result = _run(
        ["kubectl", "apply", "-f", str(app_dir)],
        context=context,
        namespace=namespace,
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def remove_app(
    app_name: str,
    context: Optional[str] = None,
    namespace: str = "default",
) -> dict:
    """Remove *app_name* from the cluster."""
    if app_name not in AVAILABLE_APPS:
        return {"success": False, "error": f"Unknown app: {app_name}"}

    app_dir = APPS_DIR / app_name / "k8s"
    if not app_dir.exists():
        return {"success": False, "error": f"Manifests not found at: {app_dir}"}

    result = _run(
        ["kubectl", "delete", "-f", str(app_dir), "--ignore-not-found=true"],
        context=context,
        namespace=namespace,
    )
    return {
        "success": result.returncode == 0,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def get_app_status(
    app_name: str,
    context: Optional[str] = None,
    namespace: str = "default",
) -> dict:
    """Return pod status information for *app_name*."""
    if app_name not in AVAILABLE_APPS:
        return {"deployed": False, "pods": [], "error": f"Unknown app: {app_name}"}

    cmd = ["kubectl", "get", "pods", "-l", f"app={app_name}", "-o", "json"]
    result = _run(cmd, context=context, namespace=namespace)

    if result.returncode != 0:
        return {"deployed": False, "pods": [], "error": result.stderr}

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
        return {"deployed": len(pods) > 0, "pods": pods}
    except (json.JSONDecodeError, KeyError):
        return {"deployed": False, "pods": []}


def get_all_app_statuses(
    context: Optional[str] = None,
    namespace: str = "default",
) -> dict[str, dict]:
    """Return status for every app in the catalogue."""
    return {
        app_name: get_app_status(app_name, context=context, namespace=namespace)
        for app_name in AVAILABLE_APPS
    }


def deploy_all_apps(
    context: Optional[str] = None,
    namespace: str = "default",
) -> dict[str, dict]:
    """Deploy every available sample app and return per-app results."""
    return {
        app_name: deploy_app(app_name, context=context, namespace=namespace)
        for app_name in AVAILABLE_APPS
    }
