"""Rules engine – detect overprovisioned Kubernetes pods.

Analyses the formatted metrics produced by
:func:`~optik8s.core.metrics.format_metrics_for_analysis` and generates
human-readable recommendations together with estimated monthly cost savings.

Threshold defaults
------------------
A pod is considered **overprovisioned** on a resource when its actual usage is
below a configurable percentage of the requested amount:

  CPU:    default threshold = 30 % of requested
  Memory: default threshold = 30 % of requested

Cost model
----------
Savings are estimated using approximate on-demand cloud pricing for managed
Kubernetes nodes (configurable):

  CPU:    $0.048 / vCPU / hour
  Memory: $0.006 / GiB / hour
"""

from __future__ import annotations

import datetime

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

CPU_OVERPROVISION_THRESHOLD_PCT: float = 30.0
"""A pod's CPU usage must be below this percentage of its requested CPU to be
flagged as overprovisioned."""

MEMORY_OVERPROVISION_THRESHOLD_PCT: float = 30.0
"""A pod's memory usage must be below this percentage of its requested memory
to be flagged as overprovisioned."""

# Approximate on-demand cloud cost (USD) per resource unit per hour.
# These are intentionally conservative estimates suitable for quick sizing.
CPU_COST_PER_CORE_HOUR: float = 0.048   # USD / vCPU / hour
MEMORY_COST_PER_GIB_HOUR: float = 0.006  # USD / GiB / hour

_HOURS_PER_MONTH: float = 24 * 30  # 720 hours


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _monthly_cpu_savings_usd(waste_millicores: float) -> float:
    """Estimate monthly USD savings from reducing wasted CPU millicores."""
    return (waste_millicores / 1000.0) * CPU_COST_PER_CORE_HOUR * _HOURS_PER_MONTH


def _monthly_mem_savings_usd(waste_mib: float) -> float:
    """Estimate monthly USD savings from reducing wasted memory MiB."""
    return (waste_mib / 1024.0) * MEMORY_COST_PER_GIB_HOUR * _HOURS_PER_MONTH


def _analyze_pod(
    pod: dict,
    cpu_threshold: float,
    memory_threshold: float,
) -> dict:
    """Analyse a single pod and return an annotated pod dict."""
    cpu = pod.get("cpu", {})
    mem = pod.get("memory", {})

    cpu_pct = cpu.get("usage_pct_of_requested")
    mem_pct = mem.get("usage_pct_of_requested")
    cpu_requested = cpu.get("requested_millicores")
    mem_requested = mem.get("requested_mib")
    cpu_usage = cpu.get("usage_millicores")
    mem_usage = mem.get("usage_mib")

    # --- CPU analysis ---
    cpu_overprovisioned = (
        cpu_pct is not None
        and cpu_requested is not None
        and cpu_requested > 0
        and cpu_pct < cpu_threshold
    )
    cpu_waste_millicores = 0.0
    cpu_recommended_millicores = None
    if cpu_overprovisioned and cpu_usage is not None and cpu_requested is not None:
        # Recommend requesting slightly above actual usage (10 % buffer)
        cpu_recommended_millicores = round(cpu_usage * 1.1, 1)
        cpu_waste_millicores = max(0.0, cpu_requested - cpu_recommended_millicores)

    # --- Memory analysis ---
    mem_overprovisioned = (
        mem_pct is not None
        and mem_requested is not None
        and mem_requested > 0
        and mem_pct < memory_threshold
    )
    mem_waste_mib = 0.0
    mem_recommended_mib = None
    if mem_overprovisioned and mem_usage is not None and mem_requested is not None:
        mem_recommended_mib = round(mem_usage * 1.1, 2)
        mem_waste_mib = max(0.0, mem_requested - mem_recommended_mib)

    monthly_savings = round(
        _monthly_cpu_savings_usd(cpu_waste_millicores)
        + _monthly_mem_savings_usd(mem_waste_mib),
        2,
    )

    return {
        "name": pod["name"],
        "cpu": {
            **cpu,
            "overprovisioned": cpu_overprovisioned,
            "recommended_request_millicores": cpu_recommended_millicores,
            "waste_millicores": round(cpu_waste_millicores, 1) if cpu_overprovisioned else None,
        },
        "memory": {
            **mem,
            "overprovisioned": mem_overprovisioned,
            "recommended_request_mib": mem_recommended_mib,
            "waste_mib": round(mem_waste_mib, 2) if mem_overprovisioned else None,
        },
        "estimated_monthly_savings_usd": monthly_savings,
    }


def _deployment_severity(cpu_overprovisioned: bool, mem_overprovisioned: bool) -> str:
    """Return a severity label based on which resources are overprovisioned."""
    if cpu_overprovisioned and mem_overprovisioned:
        return "high"
    if cpu_overprovisioned or mem_overprovisioned:
        return "medium"
    return "low"


def _build_recommendation_message(
    deployment: str,
    namespace: str,
    issues: list,
    cpu_savings: float,
    mem_savings: float,
    monthly_savings: float,
) -> str:
    """Build a human-readable recommendation message for a deployment."""
    lines = [
        f"Deployment '{deployment}' in namespace '{namespace}' is overprovisioned "
        f"because its resource requests are set much higher than what the application "
        f"actually uses."
    ]

    if "cpu_overprovisioned" in issues:
        lines.append(
            f"  \u2022 CPU: usage is below {CPU_OVERPROVISION_THRESHOLD_PCT:.0f}% of requested. "
            f"Lowering the CPU request to match actual usage would save "
            f"approximately ${cpu_savings:.2f}/month."
        )
    if "memory_overprovisioned" in issues:
        lines.append(
            f"  \u2022 Memory: usage is below {MEMORY_OVERPROVISION_THRESHOLD_PCT:.0f}% of requested. "
            f"Reducing the memory request to match actual usage would save "
            f"approximately ${mem_savings:.2f}/month."
        )

    lines.append(f"  Estimated total monthly savings: ~${monthly_savings:.2f}")
    lines.append(
        "  Tip: Enable a Horizontal Pod Autoscaler (HPA) to automatically scale the "
        "number of pods up or down based on real-time resource demand."
    )
    lines.append(
        "  Tip: For workloads that are idle outside business hours, consider idle "
        "scaling or scale-to-zero to eliminate costs when the application is not in use."
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze(
    formatted_metrics: dict,
    cpu_threshold_pct: float = CPU_OVERPROVISION_THRESHOLD_PCT,
    memory_threshold_pct: float = MEMORY_OVERPROVISION_THRESHOLD_PCT,
) -> dict:
    """Run the rules engine on pre-formatted metrics.

    Parameters
    ----------
    formatted_metrics:
        The dict produced by
        :func:`~optik8s.core.metrics.format_metrics_for_analysis`.
    cpu_threshold_pct:
        CPU usage must be below this percentage of the requested amount for the
        pod to be flagged as overprovisioned (default: 30 %).
    memory_threshold_pct:
        Memory usage must be below this percentage of the requested amount for
        the pod to be flagged as overprovisioned (default: 30 %).

    Returns
    -------
    dict
        ``{
            "analyzed_at": str,           # ISO-8601 UTC timestamp
            "thresholds": {
                "cpu_pct": float,
                "memory_pct": float
            },
            "summary": {
                "total_deployments": int,
                "total_pods": int,
                "overprovisioned_deployments": int,
                "overprovisioned_pods": int,
                "estimated_monthly_savings_usd": float
            },
            "recommendations": [
                {
                    "deployment": str,
                    "namespace": str,
                    "severity": "high" | "medium" | "low",
                    "issues": [str, ...],
                    "message": str,
                    "estimated_monthly_savings_usd": float,
                    "pods": [ ... ]
                },
                ...
            ]
        }``
    """
    deployments = formatted_metrics.get("deployments", [])
    recommendations = []
    total_pods = 0
    overprovisioned_pods = 0
    total_savings = 0.0

    for deployment_entry in deployments:
        deploy_name = deployment_entry.get("deployment", "")
        namespace = deployment_entry.get("namespace", "")
        pods = deployment_entry.get("pods", [])

        analyzed_pods = []
        deploy_cpu_overprovisioned = False
        deploy_mem_overprovisioned = False
        deploy_cpu_savings = 0.0
        deploy_mem_savings = 0.0

        for pod in pods:
            total_pods += 1
            analyzed = _analyze_pod(pod, cpu_threshold_pct, memory_threshold_pct)
            analyzed_pods.append(analyzed)

            if analyzed["cpu"]["overprovisioned"] or analyzed["memory"]["overprovisioned"]:
                overprovisioned_pods += 1

            if analyzed["cpu"]["overprovisioned"]:
                deploy_cpu_overprovisioned = True
                waste_mc = analyzed["cpu"].get("waste_millicores") or 0.0
                deploy_cpu_savings += _monthly_cpu_savings_usd(waste_mc)

            if analyzed["memory"]["overprovisioned"]:
                deploy_mem_overprovisioned = True
                waste_mib = analyzed["memory"].get("waste_mib") or 0.0
                deploy_mem_savings += _monthly_mem_savings_usd(waste_mib)

        deploy_savings = round(deploy_cpu_savings + deploy_mem_savings, 2)
        total_savings += deploy_savings

        if deploy_cpu_overprovisioned or deploy_mem_overprovisioned:
            issues = []
            if deploy_cpu_overprovisioned:
                issues.append("cpu_overprovisioned")
            if deploy_mem_overprovisioned:
                issues.append("memory_overprovisioned")

            severity = _deployment_severity(
                deploy_cpu_overprovisioned, deploy_mem_overprovisioned
            )
            message = _build_recommendation_message(
                deploy_name, namespace, issues,
                round(deploy_cpu_savings, 2),
                round(deploy_mem_savings, 2),
                deploy_savings,
            )

            recommendations.append({
                "deployment": deploy_name,
                "namespace": namespace,
                "severity": severity,
                "issues": issues,
                "message": message,
                "estimated_monthly_savings_usd": deploy_savings,
                "pods": analyzed_pods,
            })

    return {
        "analyzed_at": datetime.datetime.now(datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        ),
        "thresholds": {
            "cpu_pct": cpu_threshold_pct,
            "memory_pct": memory_threshold_pct,
        },
        "summary": {
            "total_deployments": len(deployments),
            "total_pods": total_pods,
            "overprovisioned_deployments": len(recommendations),
            "overprovisioned_pods": overprovisioned_pods,
            "estimated_monthly_savings_usd": round(total_savings, 2),
        },
        "recommendations": recommendations,
    }
