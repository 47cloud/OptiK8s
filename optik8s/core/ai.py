"""AI summarization – generate human-readable explanations for pod recommendations.

Uses the OpenAI Chat Completions API to generate concise, actionable plain-English
summaries of overprovisioned Kubernetes deployment recommendations produced by
:func:`~optik8s.core.rules.analyze`.

The API key is read from the ``OPENAI_API_KEY`` environment variable unless
supplied explicitly.  All network errors are handled gracefully so that the rest
of the application continues to work when the AI API is unavailable.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Optional

OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"

DEFAULT_MODEL = "gpt-3.5-turbo"
"""OpenAI model used by default.  Override with the ``model`` parameter."""

_SYSTEM_PROMPT = (
    "You are a Kubernetes cost-optimization assistant helping both technical and "
    "non-technical users understand their cloud spend. "
    "You receive JSON data describing overprovisioned Kubernetes deployments and their metrics. "
    "For each deployment, write a clear (3–5 sentence) plain-English explanation that: "
    "(1) states that the deployment is overprovisioned because its CPU and/or memory requests "
    "are set much higher than what the application actually uses, referencing the specific "
    "metric values from the data; "
    "(2) explains why this matters in terms of wasted cost; "
    "(3) suggests right-sizing the resource requests to match actual usage; "
    "(4) recommends enabling a Horizontal Pod Autoscaler (HPA) to automatically adjust "
    "pod count based on real demand; "
    "(5) suggests idle scaling or scale-to-zero for workloads that are inactive outside "
    "business hours to eliminate costs during idle periods. "
    "Use simple, non-technical language that a business owner or junior developer can understand. "
    "Be specific about resource values and estimated cost savings from the data provided. "
    "Respond with a JSON object mapping each deployment name to its explanation string."
)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_user_prompt(analysis_result: dict) -> str:
    """Build a compact user prompt from an :func:`~rules.analyze` result."""
    summary = analysis_result.get("summary", {})
    recs = analysis_result.get("recommendations", [])

    slim_recs = [
        {
            "deployment": rec["deployment"],
            "namespace": rec["namespace"],
            "severity": rec["severity"],
            "issues": rec["issues"],
            "estimated_monthly_savings_usd": rec["estimated_monthly_savings_usd"],
            "message": rec["message"],
            "pods": [
                {
                    "name": pod["name"],
                    "cpu_requested_millicores": pod.get("cpu", {}).get("requested_millicores"),
                    "cpu_used_millicores": pod.get("cpu", {}).get("usage_millicores"),
                    "cpu_usage_pct": pod.get("cpu", {}).get("usage_pct_of_requested"),
                    "memory_requested_mib": pod.get("memory", {}).get("requested_mib"),
                    "memory_used_mib": pod.get("memory", {}).get("usage_mib"),
                    "memory_usage_pct": pod.get("memory", {}).get("usage_pct_of_requested"),
                }
                for pod in rec.get("pods", [])
            ],
        }
        for rec in recs
    ]

    return json.dumps(
        {"summary": summary, "recommendations": slim_recs},
        indent=2,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def summarize_recommendations(
    analysis_result: dict,
    api_key: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    timeout: int = 30,
) -> dict:
    """Generate AI-powered plain-English summaries for overprovisioned deployments.

    Parameters
    ----------
    analysis_result:
        The dict returned by :func:`~optik8s.core.rules.analyze`.
    api_key:
        OpenAI API key.  Falls back to the ``OPENAI_API_KEY`` environment
        variable when not supplied explicitly.
    model:
        OpenAI model identifier (default: ``gpt-3.5-turbo``).
    timeout:
        HTTP request timeout in seconds (default: 30).

    Returns
    -------
    dict
        ``{
            "model": str,
            "summaries": {
                "<deployment-name>": str,   # plain-English explanation
                ...
            },
            "error": str | None,            # set when the API call fails
        }``
    """
    resolved_key = api_key or os.environ.get("OPENAI_API_KEY", "")
    if not resolved_key:
        return {
            "model": model,
            "summaries": {},
            "error": (
                "AI API key not configured. "
                "Set the OPENAI_API_KEY environment variable."
            ),
        }

    recs = analysis_result.get("recommendations", [])
    if not recs:
        return {"model": model, "summaries": {}, "error": None}

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(analysis_result)},
        ],
        "temperature": 0.3,
    }).encode()

    req = urllib.request.Request(
        OPENAI_API_URL,
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {resolved_key}",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            body = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        try:
            err_body = json.loads(exc.read().decode())
            err_msg = err_body.get("error", {}).get("message", str(exc))
        except Exception:
            err_msg = str(exc)
        return {"model": model, "summaries": {}, "error": f"OpenAI API error: {err_msg}"}
    except (urllib.error.URLError, OSError) as exc:
        return {"model": model, "summaries": {}, "error": f"AI API unavailable: {exc}"}
    except json.JSONDecodeError as exc:
        return {"model": model, "summaries": {}, "error": f"Invalid response from AI API: {exc}"}

    # Extract the assistant message content
    try:
        content = body["choices"][0]["message"]["content"]
        summaries = json.loads(content)
        if not isinstance(summaries, dict):
            raise ValueError("Expected a JSON object")
    except (KeyError, IndexError, json.JSONDecodeError, ValueError):
        # Model didn't return valid JSON – use the raw text as a generic summary
        content_raw = (
            body.get("choices", [{}])[0].get("message", {}).get("content", "")
        )
        summaries = {rec["deployment"]: content_raw for rec in recs}

    return {"model": model, "summaries": summaries, "error": None}
