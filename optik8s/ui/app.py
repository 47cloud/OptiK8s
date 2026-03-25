"""Flask web dashboard for OptiK8s."""

from __future__ import annotations

import json
import os
import secrets
from flask import Flask, render_template, request, jsonify

from optik8s.core import cluster as cluster_ops
from optik8s.core import apps as app_ops
from optik8s.core import monitoring as monitoring_ops
from optik8s.core import metrics as metrics_ops
from optik8s.core import rules as rules_ops
from optik8s.core import ai as ai_ops
from optik8s.core import simulator as simulator_ops


def create_app() -> Flask:
    """Application factory."""
    flask_app = Flask(__name__, template_folder="templates", static_folder="static")
    flask_app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)

    # ── Pages ────────────────────────────────────────────────────────────────

    @flask_app.route("/")
    def index():
        clusters = cluster_ops.list_all_clusters()
        current_ctx = cluster_ops.get_current_context()
        contexts = cluster_ops.list_contexts()
        apps = app_ops.list_apps()
        versions = cluster_ops.tool_versions()
        return render_template(
            "index.html",
            clusters=clusters,
            current_ctx=current_ctx,
            contexts=contexts,
            apps=apps,
            versions=versions,
        )

    # ── Cluster API endpoints ─────────────────────────────────────────────────

    @flask_app.route("/api/cluster/create", methods=["POST"])
    def api_cluster_create():
        data = request.get_json(force=True)
        provider = data.get("provider", "kind").lower()
        name = data.get("name", "optik8s")
        region = data.get("region", "us-east-1")
        config_path = data.get("config_path") or None

        if provider == "kind":
            result = cluster_ops.kind_create(name=name, config_path=config_path)
        elif provider == "eks":
            result = cluster_ops.eks_create(name=name, region=region, config_path=config_path)
        else:
            return jsonify({"success": False, "error": f"Unknown provider: {provider}"}), 400

        return jsonify(result)

    @flask_app.route("/api/cluster/delete", methods=["POST"])
    def api_cluster_delete():
        data = request.get_json(force=True)
        provider = data.get("provider", "kind").lower()
        name = data.get("name", "optik8s")
        region = data.get("region", "us-east-1")

        if provider == "kind":
            result = cluster_ops.kind_delete(name=name)
        elif provider == "eks":
            result = cluster_ops.eks_delete(name=name, region=region)
        else:
            return jsonify({"success": False, "error": f"Unknown provider: {provider}"}), 400

        return jsonify(result)

    @flask_app.route("/api/cluster/list")
    def api_cluster_list():
        return jsonify(cluster_ops.list_all_clusters())

    @flask_app.route("/api/cluster/nodes")
    def api_cluster_nodes():
        ctx = request.args.get("context")
        nodes = cluster_ops.get_nodes(context=ctx)
        return jsonify({"nodes": nodes, "context": ctx or cluster_ops.get_current_context()})

    @flask_app.route("/api/cluster/use", methods=["POST"])
    def api_cluster_use():
        data = request.get_json(force=True)
        provider = data.get("provider", "kind").lower()
        name = data.get("name", "optik8s")

        if provider == "kind":
            result = cluster_ops.kind_set_context(name=name)
        else:
            import subprocess
            res = subprocess.run(
                ["aws", "eks", "update-kubeconfig", "--name", name],
                capture_output=True, text=True,
            )
            result = {"success": res.returncode == 0, "stderr": res.stderr}

        return jsonify(result)

    # ── App API endpoints ─────────────────────────────────────────────────────

    @flask_app.route("/api/app/list")
    def api_app_list():
        return jsonify(app_ops.list_apps())

    @flask_app.route("/api/app/deploy", methods=["POST"])
    def api_app_deploy():
        data = request.get_json(force=True)
        app_name = data.get("app")
        ctx = data.get("context")
        namespace = data.get("namespace", "default")
        deploy_all = data.get("all", False)

        if deploy_all:
            results = app_ops.deploy_all_apps(context=ctx, namespace=namespace)
            return jsonify(results)

        if not app_name:
            return jsonify({"success": False, "error": "app name required"}), 400

        result = app_ops.deploy_app(app_name, context=ctx, namespace=namespace)
        return jsonify(result)

    @flask_app.route("/api/app/remove", methods=["POST"])
    def api_app_remove():
        data = request.get_json(force=True)
        app_name = data.get("app")
        ctx = data.get("context")
        namespace = data.get("namespace", "default")

        if not app_name:
            return jsonify({"success": False, "error": "app name required"}), 400

        result = app_ops.remove_app(app_name, context=ctx, namespace=namespace)
        return jsonify(result)

    @flask_app.route("/api/app/status")
    def api_app_status():
        ctx = request.args.get("context")
        namespace = request.args.get("namespace", "default")
        statuses = app_ops.get_all_app_statuses(context=ctx, namespace=namespace)
        return jsonify(statuses)

    # ── Monitoring API endpoints ──────────────────────────────────────────────

    @flask_app.route("/api/monitoring/install", methods=["POST"])
    def api_monitoring_install():
        data = request.get_json(force=True)
        namespace = data.get("namespace", "monitoring")
        ctx = data.get("context")
        values_path = data.get("values_path") or None
        result = monitoring_ops.install_prometheus(
            namespace=namespace, context=ctx, values_path=values_path
        )
        return jsonify(result)

    @flask_app.route("/api/monitoring/uninstall", methods=["POST"])
    def api_monitoring_uninstall():
        data = request.get_json(force=True)
        namespace = data.get("namespace", "monitoring")
        ctx = data.get("context")
        result = monitoring_ops.uninstall_prometheus(namespace=namespace, context=ctx)
        return jsonify(result)

    @flask_app.route("/api/monitoring/status")
    def api_monitoring_status():
        namespace = request.args.get("namespace", "monitoring")
        ctx = request.args.get("context")
        status = monitoring_ops.get_prometheus_status(namespace=namespace, context=ctx)
        urls = monitoring_ops.get_prometheus_urls(namespace=namespace, context=ctx)
        return jsonify({**status, "urls": urls})

    # ── Metrics API endpoints ─────────────────────────────────────────────────

    @flask_app.route("/api/metrics/pods")
    def api_metrics_pods():
        prometheus_url = request.args.get(
            "prometheus_url", metrics_ops.DEFAULT_PROMETHEUS_URL
        )
        namespace = request.args.get("namespace") or None
        result = metrics_ops.collect_pod_metrics(
            prometheus_url=prometheus_url,
            namespace=namespace,
        )
        return jsonify(result)

    @flask_app.route("/api/metrics/analysis")
    def api_metrics_analysis():
        """Return formatted metrics structured for AI analysis.

        Query parameters:
          prometheus_url  – Prometheus base URL (default: http://localhost:9090)
          namespace       – Filter by Kubernetes namespace (optional)
        """
        prometheus_url = request.args.get(
            "prometheus_url", metrics_ops.DEFAULT_PROMETHEUS_URL
        )
        namespace = request.args.get("namespace") or None
        result = metrics_ops.format_metrics_for_analysis(
            prometheus_url=prometheus_url,
            namespace=namespace,
        )
        return jsonify(result)

    @flask_app.route("/api/metrics/recommendations")
    def api_metrics_recommendations():
        """Run the rules engine and return overprovision recommendations.

        Query parameters:
          prometheus_url    – Prometheus base URL (default: http://localhost:9090)
          namespace         – Filter by Kubernetes namespace (optional)
          cpu_threshold     – CPU overprovision threshold % (default: 30)
          memory_threshold  – Memory overprovision threshold % (default: 30)
        """
        prometheus_url = request.args.get(
            "prometheus_url", metrics_ops.DEFAULT_PROMETHEUS_URL
        )
        namespace = request.args.get("namespace") or None
        try:
            cpu_threshold = float(
                request.args.get("cpu_threshold", rules_ops.CPU_OVERPROVISION_THRESHOLD_PCT)
            )
            memory_threshold = float(
                request.args.get(
                    "memory_threshold", rules_ops.MEMORY_OVERPROVISION_THRESHOLD_PCT
                )
            )
        except (TypeError, ValueError):
            return jsonify({"error": "cpu_threshold and memory_threshold must be numbers"}), 400

        formatted = metrics_ops.format_metrics_for_analysis(
            prometheus_url=prometheus_url,
            namespace=namespace,
        )
        result = rules_ops.analyze(
            formatted,
            cpu_threshold_pct=cpu_threshold,
            memory_threshold_pct=memory_threshold,
        )
        return jsonify(result)

    @flask_app.route("/api/metrics/ai_summary")
    def api_metrics_ai_summary():
        """Run the rules engine and return AI-generated plain-English summaries.

        Query parameters:
          prometheus_url    – Prometheus base URL (default: http://localhost:9090)
          namespace         – Filter by Kubernetes namespace (optional)
          cpu_threshold     – CPU overprovision threshold % (default: 30)
          memory_threshold  – Memory overprovision threshold % (default: 30)
          model             – OpenAI model (default: gpt-3.5-turbo)
        """
        prometheus_url = request.args.get(
            "prometheus_url", metrics_ops.DEFAULT_PROMETHEUS_URL
        )
        namespace = request.args.get("namespace") or None
        model = request.args.get("model", ai_ops.DEFAULT_MODEL)
        try:
            cpu_threshold = float(
                request.args.get("cpu_threshold", rules_ops.CPU_OVERPROVISION_THRESHOLD_PCT)
            )
            memory_threshold = float(
                request.args.get(
                    "memory_threshold", rules_ops.MEMORY_OVERPROVISION_THRESHOLD_PCT
                )
            )
        except (TypeError, ValueError):
            return jsonify({"error": "cpu_threshold and memory_threshold must be numbers"}), 400

        formatted = metrics_ops.format_metrics_for_analysis(
            prometheus_url=prometheus_url,
            namespace=namespace,
        )
        analysis = rules_ops.analyze(
            formatted,
            cpu_threshold_pct=cpu_threshold,
            memory_threshold_pct=memory_threshold,
        )
        ai_result = ai_ops.summarize_recommendations(analysis, model=model)
        return jsonify({**analysis, "ai_summary": ai_result})

    # ── Simulate API endpoints ────────────────────────────────────────────────

    @flask_app.route("/api/simulate/architectures")
    def api_simulate_architectures():
        """Return the catalogue of available architecture profiles.

        Response: JSON object mapping architecture key → metadata dict.
        """
        return jsonify(simulator_ops.list_architectures())

    @flask_app.route("/api/simulate/scenarios")
    def api_simulate_scenarios():
        """Return the catalogue of predefined named scenarios.

        Response: JSON object mapping scenario name → metadata dict.
        """
        return jsonify(simulator_ops.list_scenarios())

    @flask_app.route("/api/simulate/run", methods=["POST"])
    def api_simulate_run():
        """Generate a workload scenario and run the analysis engine.

        Request body (JSON, all fields optional):
          scenario          – name of a predefined scenario
          architectures     – list of architecture keys (ad-hoc mode)
          num_pods          – number of pods to generate (default: 8)
          load              – load profile: idle | normal | high | mixed (default)
          seed              – integer random seed for reproducibility
          cpu_threshold     – CPU overprovision threshold % (default: 30)
          memory_threshold  – memory overprovision threshold % (default: 30)

        Response: analysis result extended with a ``"scenario"`` key containing
        the generated pod list.
        """
        data = request.get_json(force=True) or {}

        scenario_name = data.get("scenario") or None
        architectures = data.get("architectures") or None
        num_pods = int(data.get("num_pods", 8))
        load = data.get("load", "mixed")
        seed = data.get("seed")
        if seed is not None:
            try:
                seed = int(seed)
            except (TypeError, ValueError):
                return jsonify({"error": "seed must be an integer"}), 400

        try:
            cpu_threshold = float(
                data.get("cpu_threshold", rules_ops.CPU_OVERPROVISION_THRESHOLD_PCT)
            )
            memory_threshold = float(
                data.get("memory_threshold", rules_ops.MEMORY_OVERPROVISION_THRESHOLD_PCT)
            )
        except (TypeError, ValueError):
            return jsonify({"error": "cpu_threshold and memory_threshold must be numbers"}), 400

        try:
            result = simulator_ops.run_scenario(
                scenario_name=scenario_name,
                architectures=architectures,
                num_pods=num_pods,
                load=load,
                seed=seed,
                cpu_threshold_pct=cpu_threshold,
                memory_threshold_pct=memory_threshold,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        return jsonify(result)

    return flask_app
