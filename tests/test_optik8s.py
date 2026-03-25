"""Tests for the OptiK8s core modules and CLI."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from optik8s.cli.main import cli
from optik8s.core import apps as app_ops
from optik8s.core import cluster as cluster_ops
from optik8s.core import metrics as metrics_ops
from optik8s.core import monitoring as monitoring_ops
from optik8s.core import rules as rules_ops
from optik8s.core import ai as ai_ops
from optik8s.core import simulator as simulator_ops

REPO_ROOT = Path(__file__).parent.parent


# ── App catalogue ────────────────────────────────────────────────────────────

class TestAppCatalogue:
    def test_list_apps_returns_dict(self):
        apps = app_ops.list_apps()
        assert isinstance(apps, dict)
        assert len(apps) > 0

    def test_all_apps_have_required_fields(self):
        for key, info in app_ops.list_apps().items():
            assert "name" in info, f"{key} missing 'name'"
            assert "description" in info, f"{key} missing 'description'"
            assert "port" in info, f"{key} missing 'port'"
            assert "tech" in info, f"{key} missing 'tech'"
            assert isinstance(info["tech"], list), f"{key} 'tech' must be a list"

    def test_unknown_app_deploy_returns_error(self):
        result = app_ops.deploy_app("nonexistent-app-xyz")
        assert result["success"] is False
        assert "nonexistent-app-xyz" in result["error"]

    def test_unknown_app_remove_returns_error(self):
        result = app_ops.remove_app("nonexistent-app-xyz")
        assert result["success"] is False

    def test_unknown_app_status_returns_not_deployed(self):
        result = app_ops.get_app_status("nonexistent-app-xyz")
        assert result["deployed"] is False

    def test_all_apps_have_k8s_manifests(self):
        apps_dir = REPO_ROOT / "apps"
        for key in app_ops.AVAILABLE_APPS:
            k8s_dir = apps_dir / key / "k8s"
            assert k8s_dir.exists(), f"Missing k8s dir for {key}: {k8s_dir}"
            yamls = list(k8s_dir.glob("*.yaml"))
            assert yamls, f"No YAML manifests found for {key}"


# ── Cluster helpers ──────────────────────────────────────────────────────────

class TestClusterHelpers:
    def test_list_all_clusters_returns_dict_with_providers(self):
        # Just verify the shape – tools may not be installed in CI
        with patch.object(cluster_ops, "_tool_available", return_value=False):
            clusters = cluster_ops.list_all_clusters()
        assert "kind" in clusters
        assert "eks" in clusters
        assert isinstance(clusters["kind"], list)
        assert isinstance(clusters["eks"], list)

    def test_tool_versions_returns_dict(self):
        versions = cluster_ops.tool_versions()
        assert isinstance(versions, dict)
        assert "kind" in versions
        assert "kubectl" in versions
        assert "eksctl" in versions
        # All values should be strings
        for v in versions.values():
            assert isinstance(v, str)

    def test_kind_list_graceful_when_not_installed(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            clusters = cluster_ops.kind_list()
        assert clusters == []

    def test_eks_list_graceful_on_bad_json(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not json"
        with patch("subprocess.run", return_value=mock_result):
            clusters = cluster_ops.eks_list()
        assert clusters == []

    def test_get_nodes_graceful_when_kubectl_fails(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            nodes = cluster_ops.get_nodes()
        assert nodes == []

    def test_get_current_context_graceful_when_not_configured(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            ctx = cluster_ops.get_current_context()
        assert ctx == ""


# ── CLI tests ────────────────────────────────────────────────────────────────

class TestCLI:
    def setup_method(self):
        self.runner = CliRunner()

    def test_cli_help(self):
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "optik8s" in result.output.lower()

    def test_cluster_help(self):
        result = self.runner.invoke(cli, ["cluster", "--help"])
        assert result.exit_code == 0
        assert "create" in result.output
        assert "delete" in result.output
        assert "list" in result.output

    def test_app_help(self):
        result = self.runner.invoke(cli, ["app", "--help"])
        assert result.exit_code == 0
        assert "deploy" in result.output
        assert "remove" in result.output

    def test_app_list_command(self):
        result = self.runner.invoke(cli, ["app", "list"])
        assert result.exit_code == 0
        # Should list known apps
        for key in app_ops.AVAILABLE_APPS:
            assert key in result.output

    def test_cluster_list_command_runs(self):
        """cluster list should not crash even when no tools are installed."""
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = self.runner.invoke(cli, ["cluster", "list"])
        assert result.exit_code == 0

    def test_tools_command(self):
        result = self.runner.invoke(cli, ["tools"])
        assert result.exit_code == 0
        assert "kind" in result.output

    def test_app_deploy_requires_name_or_all(self):
        result = self.runner.invoke(cli, ["app", "deploy"])
        assert result.exit_code != 0 or "Provide an app name" in result.output

    def test_cluster_create_invalid_provider(self):
        result = self.runner.invoke(cli, ["cluster", "create", "gke"])
        assert result.exit_code != 0


# ── Flask UI ─────────────────────────────────────────────────────────────────

class TestFlaskUI:
    def setup_method(self):
        from optik8s.ui.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def _mock_subprocess(self, returncode=1):
        mock_result = MagicMock()
        mock_result.returncode = returncode
        mock_result.stdout = ""
        mock_result.stderr = ""
        return mock_result

    def test_index_loads(self):
        with patch("subprocess.run", return_value=self._mock_subprocess()):
            resp = self.client.get("/")
        assert resp.status_code == 200
        assert b"optik8s" in resp.data

    def test_api_app_list(self):
        resp = self.client.get("/api/app/list")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "nodejs-web" in data

    def test_api_cluster_list(self):
        with patch("subprocess.run", return_value=self._mock_subprocess()):
            resp = self.client.get("/api/cluster/list")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "kind" in data
        assert "eks" in data

    def test_api_app_deploy_requires_app(self):
        resp = self.client.post(
            "/api/app/deploy",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_api_app_remove_requires_app(self):
        resp = self.client.post(
            "/api/app/remove",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_api_cluster_create_invalid_provider(self):
        resp = self.client.post(
            "/api/cluster/create",
            json={"provider": "gke", "name": "test"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_api_app_deploy_unknown_app(self):
        with patch("subprocess.run", return_value=self._mock_subprocess()):
            resp = self.client.post(
                "/api/app/deploy",
                json={"app": "nonexistent-xyz"},
                content_type="application/json",
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is False

    def test_api_app_status(self):
        with patch("subprocess.run", return_value=self._mock_subprocess()):
            resp = self.client.get("/api/app/status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        # All apps should appear in the response
        for key in app_ops.AVAILABLE_APPS:
            assert key in data


# ── Configuration files ──────────────────────────────────────────────────────

class TestConfigFiles:
    def test_kind_config_exists(self):
        path = REPO_ROOT / "clusters" / "kind" / "cluster-config.yaml"
        assert path.exists(), f"KIND config not found: {path}"

    def test_eks_config_exists(self):
        path = REPO_ROOT / "clusters" / "eks" / "cluster-config.yaml"
        assert path.exists(), f"EKS config not found: {path}"

    def test_kind_config_is_valid_yaml(self):
        import yaml
        path = REPO_ROOT / "clusters" / "kind" / "cluster-config.yaml"
        with open(path) as f:
            doc = yaml.safe_load(f)
        assert doc["kind"] == "Cluster"

    def test_eks_config_is_valid_yaml(self):
        import yaml
        path = REPO_ROOT / "clusters" / "eks" / "cluster-config.yaml"
        with open(path) as f:
            doc = yaml.safe_load(f)
        assert doc["kind"] == "ClusterConfig"

    def test_all_app_k8s_manifests_are_valid_yaml(self):
        import yaml
        apps_dir = REPO_ROOT / "apps"
        for app_key in app_ops.AVAILABLE_APPS:
            for yaml_path in (apps_dir / app_key / "k8s").glob("*.yaml"):
                with open(yaml_path) as f:
                    # Use safe_load_all for multi-document YAML
                    docs = list(yaml.safe_load_all(f))
                assert docs, f"Empty YAML: {yaml_path}"
                for doc in docs:
                    if doc is not None:
                        assert "apiVersion" in doc or "kind" in doc, \
                            f"Invalid K8s manifest: {yaml_path}"


# ── Monitoring ───────────────────────────────────────────────────────────────

class TestMonitoringModule:
    def test_monitoring_constants(self):
        assert monitoring_ops.HELM_RELEASE_NAME == "kube-prometheus-stack"
        assert monitoring_ops.HELM_REPO_NAME == "prometheus-community"
        assert monitoring_ops.MONITORING_NAMESPACE == "monitoring"

    def test_monitoring_dir_exists(self):
        assert monitoring_ops.MONITORING_DIR.exists(), \
            f"Monitoring directory not found: {monitoring_ops.MONITORING_DIR}"

    def test_prometheus_values_file_exists(self):
        values = monitoring_ops.MONITORING_DIR / "prometheus" / "values.yaml"
        assert values.exists(), f"Helm values file not found: {values}"

    def test_prometheus_values_is_valid_yaml(self):
        import yaml
        values_path = monitoring_ops.MONITORING_DIR / "prometheus" / "values.yaml"
        with open(values_path) as f:
            doc = yaml.safe_load(f)
        assert isinstance(doc, dict)
        assert "prometheus" in doc
        assert "grafana" in doc

    def test_install_prometheus_missing_helm(self):
        with patch.object(monitoring_ops, "_tool_available", return_value=False):
            result = monitoring_ops.install_prometheus()
        assert result["success"] is False
        assert "helm" in result["error"].lower()

    def test_uninstall_prometheus_missing_helm(self):
        with patch.object(monitoring_ops, "_tool_available", return_value=False):
            result = monitoring_ops.uninstall_prometheus()
        assert result["success"] is False
        assert "helm" in result["error"].lower()

    def test_get_prometheus_status_missing_kubectl(self):
        with patch.object(monitoring_ops, "_tool_available", return_value=False):
            status = monitoring_ops.get_prometheus_status()
        assert status["installed"] is False
        assert "kubectl" in status["error"].lower()

    def test_get_prometheus_status_graceful_on_kubectl_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "namespace not found"
        with patch("subprocess.run", return_value=mock_result):
            status = monitoring_ops.get_prometheus_status()
        assert status["installed"] is False
        assert status["pods"] == []

    def test_get_prometheus_status_parses_pods(self):
        pod_json = json.dumps({
            "items": [{
                "metadata": {"name": "prometheus-0"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True}],
                },
                "spec": {"containers": [{}]},
            }]
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = pod_json
        with patch("subprocess.run", return_value=mock_result):
            status = monitoring_ops.get_prometheus_status()
        assert status["installed"] is True
        assert len(status["pods"]) == 1
        assert status["pods"][0]["name"] == "prometheus-0"
        assert status["pods"][0]["phase"] == "Running"

    def test_get_prometheus_urls_missing_kubectl(self):
        with patch.object(monitoring_ops, "_tool_available", return_value=False):
            urls = monitoring_ops.get_prometheus_urls()
        assert urls["prometheus"] is None
        assert urls["grafana"] is None

    def test_get_prometheus_urls_parses_services(self):
        svc_json = json.dumps({
            "items": [
                {
                    "metadata": {"name": "kube-prometheus-stack-prometheus"},
                    "spec": {"ports": [{"port": 9090}]},
                },
                {
                    "metadata": {"name": "kube-prometheus-stack-grafana"},
                    "spec": {"ports": [{"port": 80}]},
                },
            ]
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = svc_json
        with patch("subprocess.run", return_value=mock_result):
            urls = monitoring_ops.get_prometheus_urls()
        assert urls["prometheus"] is not None
        assert "9090" in urls["prometheus"]
        assert urls["grafana"] is not None
        assert "3000" in urls["grafana"]

    def test_helm_repo_add_graceful_on_failure(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "connection refused"
        with patch("subprocess.run", return_value=mock_result):
            result = monitoring_ops.helm_repo_add()
        assert result["success"] is False


class TestMonitoringCLI:
    def setup_method(self):
        self.runner = CliRunner()

    def test_monitoring_help(self):
        result = self.runner.invoke(cli, ["monitoring", "--help"])
        assert result.exit_code == 0
        assert "install" in result.output
        assert "uninstall" in result.output
        assert "status" in result.output

    def test_monitoring_install_no_helm(self):
        with patch.object(monitoring_ops, "_tool_available", return_value=False):
            result = self.runner.invoke(cli, ["monitoring", "install"])
        assert result.exit_code != 0

    def test_monitoring_status_no_pods(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps({"items": []})
        with patch("subprocess.run", return_value=mock_result):
            result = self.runner.invoke(cli, ["monitoring", "status"])
        assert result.exit_code == 0
        assert "install" in result.output.lower()

    def test_monitoring_status_with_pods(self):
        pod_json = json.dumps({
            "items": [{
                "metadata": {"name": "prometheus-0"},
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"ready": True}],
                },
                "spec": {"containers": [{}]},
            }]
        })
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = pod_json
        with patch("subprocess.run", return_value=mock_result):
            result = self.runner.invoke(cli, ["monitoring", "status"])
        assert result.exit_code == 0
        assert "prometheus-0" in result.output


class TestMonitoringFlaskUI:
    def setup_method(self):
        from optik8s.ui.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def _mock_subprocess(self, returncode=1, stdout=""):
        mock_result = MagicMock()
        mock_result.returncode = returncode
        mock_result.stdout = stdout
        mock_result.stderr = ""
        return mock_result

    def test_api_monitoring_status_returns_json(self):
        with patch("subprocess.run", return_value=self._mock_subprocess(0, json.dumps({"items": []}))):
            resp = self.client.get("/api/monitoring/status")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "installed" in data
        assert "pods" in data
        assert "urls" in data

    def test_api_monitoring_install_no_helm(self):
        with patch.object(monitoring_ops, "_tool_available", return_value=False):
            resp = self.client.post(
                "/api/monitoring/install",
                json={},
                content_type="application/json",
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is False

    def test_api_monitoring_uninstall_no_helm(self):
        with patch.object(monitoring_ops, "_tool_available", return_value=False):
            resp = self.client.post(
                "/api/monitoring/uninstall",
                json={},
                content_type="application/json",
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["success"] is False


# ── Metrics collector ────────────────────────────────────────────────────────

def _make_prom_response(results: list[dict]) -> bytes:
    """Build a minimal Prometheus instant-query HTTP response body."""
    body = {
        "status": "success",
        "data": {"resultType": "vector", "result": results},
    }
    return json.dumps(body).encode()


def _prom_row(pod: str, namespace: str, value: float) -> dict:
    return {"metric": {"pod": pod, "namespace": namespace}, "value": [1700000000, str(value)]}


class TestMetricsCollector:
    def test_default_prometheus_url(self):
        assert metrics_ops.DEFAULT_PROMETHEUS_URL == "http://localhost:9090"

    def test_collect_returns_required_keys(self):
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([])
            mock_open.return_value = cm
            result = metrics_ops.collect_pod_metrics()
        assert "prometheus_url" in result
        assert "pods" in result
        assert isinstance(result["pods"], list)

    def test_collect_parses_pod_metrics(self):
        row = _prom_row("my-pod", "default", 0.25)
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            result = metrics_ops.collect_pod_metrics()
        assert len(result["pods"]) == 1
        pod = result["pods"][0]
        assert pod["name"] == "my-pod"
        assert pod["namespace"] == "default"
        # At least one numeric metric should be populated
        assert any(
            pod[k] is not None
            for k in (
                "cpu_usage_cores",
                "memory_usage_bytes",
                "cpu_requests_cores",
                "cpu_limits_cores",
                "memory_requests_bytes",
                "memory_limits_bytes",
            )
        )

    def test_collect_filters_by_namespace(self):
        rows = [
            _prom_row("pod-a", "ns-1", 0.1),
            _prom_row("pod-b", "ns-2", 0.2),
        ]
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response(rows)
            mock_open.return_value = cm
            result = metrics_ops.collect_pod_metrics(namespace="ns-1")
        names = [p["name"] for p in result["pods"]]
        assert "pod-a" in names
        assert "pod-b" not in names

    def test_collect_graceful_on_connection_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = metrics_ops.collect_pod_metrics()
        assert result["pods"] == []

    def test_collect_rejects_non_http_scheme(self):
        # SSRF protection: file:// and other non-http(s) URLs must be rejected
        result = metrics_ops.collect_pod_metrics(prometheus_url="file:///etc/passwd")
        assert result["pods"] == []

    def test_collect_rejects_custom_scheme(self):
        result = metrics_ops.collect_pod_metrics(prometheus_url="ftp://attacker.example.com")
        assert result["pods"] == []

    def test_collect_graceful_on_bad_json(self):
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = b"not json"
            mock_open.return_value = cm
            result = metrics_ops.collect_pod_metrics()
        assert result["pods"] == []

    def test_collect_skips_rows_without_pod_label(self):
        row = {"metric": {"namespace": "default"}, "value": [1700000000, "1.0"]}
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            result = metrics_ops.collect_pod_metrics()
        assert result["pods"] == []

    def test_result_is_json_serialisable(self):
        row = _prom_row("pod-x", "kube-system", 0.05)
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            result = metrics_ops.collect_pod_metrics()
        # Should not raise
        serialised = json.dumps(result)
        assert "pod-x" in serialised


class TestMetricsCLI:
    def setup_method(self):
        self.runner = CliRunner()

    def test_metrics_help(self):
        result = self.runner.invoke(cli, ["metrics", "--help"])
        assert result.exit_code == 0
        assert "collect" in result.output

    def test_metrics_collect_help(self):
        result = self.runner.invoke(cli, ["metrics", "collect", "--help"])
        assert result.exit_code == 0
        assert "prometheus" in result.output.lower()

    def test_metrics_collect_no_pods(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = self.runner.invoke(cli, ["metrics", "collect"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["pods"] == []

    def test_metrics_collect_with_pods(self):
        row = _prom_row("web-pod", "default", 0.3)
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            result = self.runner.invoke(cli, ["metrics", "collect"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert any(p["name"] == "web-pod" for p in data["pods"])

    def test_metrics_collect_output_file(self, tmp_path):
        import urllib.error
        out_file = tmp_path / "metrics.json"
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = self.runner.invoke(
                cli, ["metrics", "collect", "--output", str(out_file)]
            )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "pods" in data


class TestMetricsFlaskUI:
    def setup_method(self):
        from optik8s.ui.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_api_metrics_pods_returns_json(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            resp = self.client.get("/api/metrics/pods")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "pods" in data
        assert "prometheus_url" in data

    def test_api_metrics_pods_with_data(self):
        row = _prom_row("api-pod", "production", 0.5)
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            resp = self.client.get("/api/metrics/pods")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert any(p["name"] == "api-pod" for p in data["pods"])

    def test_api_metrics_pods_namespace_filter(self):
        rows = [
            _prom_row("pod-a", "ns-1", 0.1),
            _prom_row("pod-b", "ns-2", 0.2),
        ]
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response(rows)
            mock_open.return_value = cm
            resp = self.client.get("/api/metrics/pods?namespace=ns-1")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        names = [p["name"] for p in data["pods"]]
        assert "pod-a" in names
        assert "pod-b" not in names

    def test_api_metrics_pods_custom_prometheus_url(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            resp = self.client.get(
                "/api/metrics/pods?prometheus_url=http://prometheus.example.com:9090"
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["prometheus_url"] == "http://prometheus.example.com:9090"


# ── Metrics formatting ───────────────────────────────────────────────────────

class TestMetricsFormatting:
    """Unit tests for the format_metrics_for_analysis helpers and function."""

    def test_to_millicores(self):
        assert metrics_ops._to_millicores(0.5) == 500.0
        assert metrics_ops._to_millicores(0.001) == 1.0
        assert metrics_ops._to_millicores(None) is None

    def test_to_mib(self):
        assert metrics_ops._to_mib(1024 * 1024) == 1.0
        assert metrics_ops._to_mib(512 * 1024 * 1024) == 512.0
        assert metrics_ops._to_mib(None) is None

    def test_usage_pct(self):
        assert metrics_ops._usage_pct(0.25, 1.0) == 25.0
        assert metrics_ops._usage_pct(0.5, 0.5) == 100.0
        assert metrics_ops._usage_pct(None, 1.0) is None
        assert metrics_ops._usage_pct(0.5, None) is None
        assert metrics_ops._usage_pct(0.5, 0.0) is None

    def test_infer_deployment_name_full_pattern(self):
        # Standard Deployment pod: <deploy>-<rs-hash>-<pod-hash>
        assert metrics_ops._infer_deployment_name("my-app-7d6d5fbc9b-xzk2p") == "my-app"
        assert metrics_ops._infer_deployment_name("nodejs-web-abc12345de-abcde") == "nodejs-web"

    def test_infer_deployment_name_single_hash(self):
        # One trailing hash segment
        assert metrics_ops._infer_deployment_name("my-app-abcde") == "my-app"

    def test_infer_deployment_name_no_hash(self):
        # No recognisable hash – name returned unchanged
        assert metrics_ops._infer_deployment_name("mypod") == "mypod"
        assert metrics_ops._infer_deployment_name("my-pod-name") == "my-pod-name"

    def test_format_returns_required_top_level_keys(self):
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([])
            mock_open.return_value = cm
            result = metrics_ops.format_metrics_for_analysis()
        assert "collected_at" in result
        assert "prometheus_url" in result
        assert "summary" in result
        assert "deployments" in result

    def test_format_summary_fields(self):
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([])
            mock_open.return_value = cm
            result = metrics_ops.format_metrics_for_analysis()
        summary = result["summary"]
        assert "total_deployments" in summary
        assert "total_pods" in summary
        assert "namespaces" in summary
        assert isinstance(summary["namespaces"], list)

    def test_format_groups_by_deployment(self):
        # Two pods from the same deployment, one from another
        rows = [
            _prom_row("web-7d6d5fbc9b-aaaaa", "default", 0.1),
            _prom_row("web-7d6d5fbc9b-bbbbb", "default", 0.2),
            _prom_row("api-abc12345de-ccccc", "default", 0.3),
        ]
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response(rows)
            mock_open.return_value = cm
            result = metrics_ops.format_metrics_for_analysis()
        deploy_names = {d["deployment"] for d in result["deployments"]}
        assert "web" in deploy_names
        assert "api" in deploy_names
        # "web" deployment should have two pods
        web = next(d for d in result["deployments"] if d["deployment"] == "web")
        assert len(web["pods"]) == 2

    def test_format_pod_has_cpu_and_memory_sections(self):
        row = _prom_row("svc-abcde12345-xzk2p", "prod", 0.5)
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            result = metrics_ops.format_metrics_for_analysis()
        assert len(result["deployments"]) == 1
        pod = result["deployments"][0]["pods"][0]
        assert "name" in pod
        assert "cpu" in pod
        assert "memory" in pod
        cpu = pod["cpu"]
        assert "usage_millicores" in cpu
        assert "requested_millicores" in cpu
        assert "limit_millicores" in cpu
        assert "usage_pct_of_requested" in cpu
        mem = pod["memory"]
        assert "usage_mib" in mem
        assert "requested_mib" in mem
        assert "limit_mib" in mem
        assert "usage_pct_of_requested" in mem

    def test_format_computes_millicores(self):
        """CPU metrics should be expressed in millicores (cores × 1000)."""
        # Patch collect_pod_metrics directly so we can control exact values
        fake_pods = [{
            "name": "app-abcdefghij-xzk2p",
            "namespace": "default",
            "cpu_usage_cores": 0.25,
            "cpu_requests_cores": 0.5,
            "cpu_limits_cores": 1.0,
            "memory_usage_bytes": None,
            "memory_requests_bytes": None,
            "memory_limits_bytes": None,
        }]
        with patch.object(
            metrics_ops, "collect_pod_metrics",
            return_value={"prometheus_url": "http://localhost:9090", "pods": fake_pods}
        ), patch.object(metrics_ops, "_get_deployment_names", return_value={}):
            result = metrics_ops.format_metrics_for_analysis()
        pod = result["deployments"][0]["pods"][0]
        assert pod["cpu"]["usage_millicores"] == 250.0
        assert pod["cpu"]["requested_millicores"] == 500.0
        assert pod["cpu"]["limit_millicores"] == 1000.0
        assert pod["cpu"]["usage_pct_of_requested"] == 50.0

    def test_format_computes_mib(self):
        """Memory metrics should be expressed in MiB."""
        fake_pods = [{
            "name": "app-abcdefghij-xzk2p",
            "namespace": "default",
            "cpu_usage_cores": None,
            "cpu_requests_cores": None,
            "cpu_limits_cores": None,
            "memory_usage_bytes": 128 * 1024 * 1024,
            "memory_requests_bytes": 256 * 1024 * 1024,
            "memory_limits_bytes": 512 * 1024 * 1024,
        }]
        with patch.object(
            metrics_ops, "collect_pod_metrics",
            return_value={"prometheus_url": "http://localhost:9090", "pods": fake_pods}
        ), patch.object(metrics_ops, "_get_deployment_names", return_value={}):
            result = metrics_ops.format_metrics_for_analysis()
        pod = result["deployments"][0]["pods"][0]
        assert pod["memory"]["usage_mib"] == 128.0
        assert pod["memory"]["requested_mib"] == 256.0
        assert pod["memory"]["limit_mib"] == 512.0
        assert pod["memory"]["usage_pct_of_requested"] == 50.0

    def test_format_uses_prometheus_deployment_name(self):
        """When kube_pod_owner data is available, use the resolved deployment name."""
        fake_pods = [{
            "name": "my-service-rs123abc45-pdabc",
            "namespace": "default",
            "cpu_usage_cores": 0.1,
            "cpu_requests_cores": 0.2,
            "cpu_limits_cores": 0.5,
            "memory_usage_bytes": None,
            "memory_requests_bytes": None,
            "memory_limits_bytes": None,
        }]
        with patch.object(
            metrics_ops, "collect_pod_metrics",
            return_value={"prometheus_url": "http://localhost:9090", "pods": fake_pods}
        ), patch.object(
            metrics_ops, "_get_deployment_names",
            return_value={("my-service-rs123abc45-pdabc", "default"): "my-service"}
        ):
            result = metrics_ops.format_metrics_for_analysis()
        assert result["deployments"][0]["deployment"] == "my-service"

    def test_format_filters_by_namespace(self):
        rows = [
            _prom_row("pod-a-abcdefghij-xzk2p", "ns-1", 0.1),
            _prom_row("pod-b-abcdefghij-xzk2p", "ns-2", 0.2),
        ]
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response(rows)
            mock_open.return_value = cm
            result = metrics_ops.format_metrics_for_analysis(namespace="ns-1")
        all_ns = {d["namespace"] for d in result["deployments"]}
        assert all_ns == {"ns-1"}

    def test_format_is_json_serialisable(self):
        row = _prom_row("app-abcdefghij-xzk2p", "default", 0.3)
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            result = metrics_ops.format_metrics_for_analysis()
        serialised = json.dumps(result)
        assert "deployments" in serialised

    def test_format_collected_at_is_utc_string(self):
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([])
            mock_open.return_value = cm
            result = metrics_ops.format_metrics_for_analysis()
        assert result["collected_at"].endswith("Z")
        # Should parse as a valid ISO-8601 datetime
        import datetime
        datetime.datetime.strptime(result["collected_at"], "%Y-%m-%dT%H:%M:%SZ")


class TestMetricsFormatCLI:
    def setup_method(self):
        self.runner = CliRunner()

    def test_metrics_format_help(self):
        result = self.runner.invoke(cli, ["metrics", "format", "--help"])
        assert result.exit_code == 0
        assert "prometheus" in result.output.lower()

    def test_metrics_format_no_pods(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = self.runner.invoke(cli, ["metrics", "format"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "deployments" in data
        assert "summary" in data
        assert data["summary"]["total_pods"] == 0

    def test_metrics_format_with_pods(self):
        row = _prom_row("web-abcdefghij-xzk2p", "default", 0.3)
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            result = self.runner.invoke(cli, ["metrics", "format"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["total_pods"] == 1
        assert len(data["deployments"]) == 1
        pod = data["deployments"][0]["pods"][0]
        assert "cpu" in pod
        assert "memory" in pod

    def test_metrics_format_output_file(self, tmp_path):
        import urllib.error
        out_file = tmp_path / "analysis.json"
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = self.runner.invoke(
                cli, ["metrics", "format", "--output", str(out_file)]
            )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "deployments" in data
        assert "summary" in data


class TestMetricsAnalysisFlaskUI:
    def setup_method(self):
        from optik8s.ui.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_api_metrics_analysis_returns_json(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            resp = self.client.get("/api/metrics/analysis")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "deployments" in data
        assert "summary" in data
        assert "collected_at" in data
        assert "prometheus_url" in data

    def test_api_metrics_analysis_with_pods(self):
        row = _prom_row("api-abcdefghij-xzk2p", "production", 0.5)
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response([row])
            mock_open.return_value = cm
            resp = self.client.get("/api/metrics/analysis")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["summary"]["total_pods"] == 1
        assert len(data["deployments"]) == 1
        pod = data["deployments"][0]["pods"][0]
        assert "cpu" in pod
        assert "memory" in pod

    def test_api_metrics_analysis_namespace_filter(self):
        rows = [
            _prom_row("pod-a-abcdefghij-xzk2p", "ns-1", 0.1),
            _prom_row("pod-b-abcdefghij-xzk2p", "ns-2", 0.2),
        ]
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_prom_response(rows)
            mock_open.return_value = cm
            resp = self.client.get("/api/metrics/analysis?namespace=ns-1")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        all_ns = {d["namespace"] for d in data["deployments"]}
        assert all_ns == {"ns-1"}

    def test_api_metrics_analysis_custom_prometheus_url(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            resp = self.client.get(
                "/api/metrics/analysis?prometheus_url=http://prom.example.com:9090"
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["prometheus_url"] == "http://prom.example.com:9090"


# ── Rules engine helpers ─────────────────────────────────────────────────────

def _make_formatted_metrics(deployments: list) -> dict:
    """Build a minimal formatted-metrics dict suitable for rules_ops.analyze()."""
    total_pods = sum(len(d.get("pods", [])) for d in deployments)
    namespaces = sorted({d.get("namespace", "") for d in deployments})
    return {
        "collected_at": "2024-03-24T12:00:00Z",
        "prometheus_url": "http://localhost:9090",
        "summary": {
            "total_deployments": len(deployments),
            "total_pods": total_pods,
            "namespaces": namespaces,
        },
        "deployments": deployments,
    }


def _make_pod(
    name: str,
    cpu_usage_mc: float | None,
    cpu_requested_mc: float | None,
    mem_usage_mib: float | None,
    mem_requested_mib: float | None,
) -> dict:
    """Build a pod entry matching format_metrics_for_analysis output."""
    def pct(u, r):
        if u is None or r is None or r == 0:
            return None
        return round((u / r) * 100, 1)

    return {
        "name": name,
        "cpu": {
            "usage_millicores": cpu_usage_mc,
            "requested_millicores": cpu_requested_mc,
            "limit_millicores": None,
            "usage_pct_of_requested": pct(cpu_usage_mc, cpu_requested_mc),
        },
        "memory": {
            "usage_mib": mem_usage_mib,
            "requested_mib": mem_requested_mib,
            "limit_mib": None,
            "usage_pct_of_requested": pct(mem_usage_mib, mem_requested_mib),
        },
    }


# ── TestRulesEngine ──────────────────────────────────────────────────────────

class TestRulesEngine:
    """Unit tests for rules_ops.analyze() and its helpers."""

    def test_default_thresholds(self):
        assert rules_ops.CPU_OVERPROVISION_THRESHOLD_PCT == 30.0
        assert rules_ops.MEMORY_OVERPROVISION_THRESHOLD_PCT == 30.0

    def test_cost_constants_positive(self):
        assert rules_ops.CPU_COST_PER_CORE_HOUR > 0
        assert rules_ops.MEMORY_COST_PER_GIB_HOUR > 0

    def test_analyze_empty_metrics_returns_required_keys(self):
        result = rules_ops.analyze(_make_formatted_metrics([]))
        assert "analyzed_at" in result
        assert "thresholds" in result
        assert "summary" in result
        assert "recommendations" in result

    def test_analyze_empty_metrics_summary_zeros(self):
        result = rules_ops.analyze(_make_formatted_metrics([]))
        s = result["summary"]
        assert s["total_deployments"] == 0
        assert s["total_pods"] == 0
        assert s["overprovisioned_deployments"] == 0
        assert s["overprovisioned_pods"] == 0
        assert s["estimated_monthly_savings_usd"] == 0.0

    def test_analyze_thresholds_reflected_in_output(self):
        result = rules_ops.analyze(_make_formatted_metrics([]), cpu_threshold_pct=20.0, memory_threshold_pct=40.0)
        assert result["thresholds"]["cpu_pct"] == 20.0
        assert result["thresholds"]["memory_pct"] == 40.0

    def test_analyzed_at_is_utc_string(self):
        import datetime
        result = rules_ops.analyze(_make_formatted_metrics([]))
        assert result["analyzed_at"].endswith("Z")
        datetime.datetime.strptime(result["analyzed_at"], "%Y-%m-%dT%H:%M:%SZ")

    def test_well_utilized_pod_not_flagged(self):
        # CPU at 80% of requested → not overprovisioned
        pod = _make_pod("web-abc", 80.0, 100.0, 200.0, 250.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        assert result["summary"]["overprovisioned_deployments"] == 0
        assert result["summary"]["overprovisioned_pods"] == 0
        assert len(result["recommendations"]) == 0

    def test_cpu_overprovisioned_pod_flagged(self):
        # CPU at 10% of requested (< 30% threshold)
        pod = _make_pod("web-abc", 10.0, 100.0, 200.0, 250.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        assert result["summary"]["overprovisioned_pods"] == 1
        assert len(result["recommendations"]) == 1
        rec = result["recommendations"][0]
        assert "cpu_overprovisioned" in rec["issues"]
        assert rec["severity"] == "medium"

    def test_memory_overprovisioned_pod_flagged(self):
        # Memory at 10% of requested (< 30% threshold)
        pod = _make_pod("web-abc", 80.0, 100.0, 20.0, 200.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        assert result["summary"]["overprovisioned_pods"] == 1
        rec = result["recommendations"][0]
        assert "memory_overprovisioned" in rec["issues"]
        assert rec["severity"] == "medium"

    def test_both_overprovisioned_gives_high_severity(self):
        # CPU 10% and Memory 10% of requested
        pod = _make_pod("web-abc", 10.0, 100.0, 20.0, 200.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        rec = result["recommendations"][0]
        assert "cpu_overprovisioned" in rec["issues"]
        assert "memory_overprovisioned" in rec["issues"]
        assert rec["severity"] == "high"

    def test_recommendation_message_content(self):
        pod = _make_pod("web-abc", 10.0, 100.0, 20.0, 200.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        msg = result["recommendations"][0]["message"]
        assert "web" in msg
        assert "default" in msg
        assert "CPU" in msg
        assert "Memory" in msg
        assert "$" in msg

    def test_recommendation_message_explains_root_cause(self):
        # Message must explain that requests are too high vs actual usage
        pod = _make_pod("web-abc", 10.0, 100.0, 20.0, 200.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        msg = result["recommendations"][0]["message"]
        assert "requests" in msg.lower()
        assert "actual" in msg.lower() or "uses" in msg.lower()

    def test_recommendation_message_includes_autoscaling_tip(self):
        # Message must mention HPA / autoscaling
        pod = _make_pod("web-abc", 10.0, 100.0, 20.0, 200.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        msg = result["recommendations"][0]["message"]
        assert "autoscal" in msg.lower() or "hpa" in msg.lower() or "horizontal pod" in msg.lower()

    def test_recommendation_message_includes_idle_scaling_tip(self):
        # Message must mention idle scaling or scale-to-zero
        pod = _make_pod("web-abc", 10.0, 100.0, 20.0, 200.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        msg = result["recommendations"][0]["message"]
        assert "idle" in msg.lower() or "scale-to-zero" in msg.lower() or "scale to zero" in msg.lower()

    def test_estimated_savings_are_positive_when_overprovisioned(self):
        pod = _make_pod("web-abc", 10.0, 200.0, 20.0, 400.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        assert result["summary"]["estimated_monthly_savings_usd"] > 0
        assert result["recommendations"][0]["estimated_monthly_savings_usd"] > 0

    def test_no_savings_when_no_usage_data(self):
        # No usage data → can't determine waste, but usage_pct is None so not flagged
        pod = _make_pod("web-abc", None, 100.0, None, 200.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        assert result["summary"]["overprovisioned_pods"] == 0
        assert result["summary"]["estimated_monthly_savings_usd"] == 0.0

    def test_no_savings_when_no_request_data(self):
        # No request data → can't compute percentage → not flagged
        pod = _make_pod("web-abc", 10.0, None, 20.0, None)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        assert result["summary"]["overprovisioned_pods"] == 0

    def test_multiple_deployments_counted_correctly(self):
        pod_over = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        pod_ok = _make_pod("api-abc", 80.0, 100.0, 150.0, 200.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod_over]},
            {"deployment": "api", "namespace": "default", "pods": [pod_ok]},
        ])
        result = rules_ops.analyze(metrics)
        assert result["summary"]["total_deployments"] == 2
        assert result["summary"]["total_pods"] == 2
        assert result["summary"]["overprovisioned_deployments"] == 1
        assert result["summary"]["overprovisioned_pods"] == 1

    def test_analyzed_pod_has_recommended_request(self):
        pod = _make_pod("web-abc", 10.0, 200.0, 20.0, 400.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        analyzed_pod = result["recommendations"][0]["pods"][0]
        assert analyzed_pod["cpu"]["recommended_request_millicores"] is not None
        assert analyzed_pod["cpu"]["recommended_request_millicores"] < 200.0
        assert analyzed_pod["memory"]["recommended_request_mib"] is not None
        assert analyzed_pod["memory"]["recommended_request_mib"] < 400.0

    def test_custom_threshold_changes_detection(self):
        # Pod at 25% CPU of requested: flagged at 30% threshold but not at 20%
        pod = _make_pod("web-abc", 25.0, 100.0, 200.0, 250.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result_flagged = rules_ops.analyze(metrics, cpu_threshold_pct=30.0)
        result_ok = rules_ops.analyze(metrics, cpu_threshold_pct=20.0)
        assert result_flagged["summary"]["overprovisioned_pods"] == 1
        assert result_ok["summary"]["overprovisioned_pods"] == 0

    def test_result_is_json_serialisable(self):
        pod = _make_pod("web-abc", 10.0, 200.0, 20.0, 400.0)
        metrics = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        result = rules_ops.analyze(metrics)
        serialised = json.dumps(result)
        assert "recommendations" in serialised

    def test_monthly_cpu_savings_formula(self):
        # 1000 millicores waste × $0.048/core/hr × 720 hr/month = $34.56
        expected = (1000 / 1000) * rules_ops.CPU_COST_PER_CORE_HOUR * 720
        assert abs(rules_ops._monthly_cpu_savings_usd(1000) - expected) < 0.001

    def test_monthly_mem_savings_formula(self):
        # 1024 MiB = 1 GiB waste × $0.006/GiB/hr × 720 hr/month = $4.32
        expected = (1024 / 1024) * rules_ops.MEMORY_COST_PER_GIB_HOUR * 720
        assert abs(rules_ops._monthly_mem_savings_usd(1024) - expected) < 0.001

    def test_deployment_severity_both(self):
        assert rules_ops._deployment_severity(True, True) == "high"

    def test_deployment_severity_cpu_only(self):
        assert rules_ops._deployment_severity(True, False) == "medium"

    def test_deployment_severity_mem_only(self):
        assert rules_ops._deployment_severity(False, True) == "medium"

    def test_deployment_severity_none(self):
        assert rules_ops._deployment_severity(False, False) == "low"


# ── TestRulesCLI ─────────────────────────────────────────────────────────────

class TestRulesCLI:
    def setup_method(self):
        self.runner = CliRunner()

    def _fake_formatted_metrics(self, deployments=None):
        return _make_formatted_metrics(deployments or [])

    def test_metrics_analyze_help(self):
        result = self.runner.invoke(cli, ["metrics", "analyze", "--help"])
        assert result.exit_code == 0
        assert "overprovisioned" in result.output.lower()

    def test_metrics_analyze_no_pods(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = self.runner.invoke(cli, ["metrics", "analyze", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "recommendations" in data
        assert data["summary"]["overprovisioned_deployments"] == 0

    def test_metrics_analyze_with_overprovisioned_pod(self):
        pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake):
            result = self.runner.invoke(cli, ["metrics", "analyze", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["overprovisioned_deployments"] == 1
        assert len(data["recommendations"]) == 1

    def test_metrics_analyze_human_readable_output(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = self.runner.invoke(cli, ["metrics", "analyze"])
        assert result.exit_code == 0
        assert "Summary" in result.output or "No overprovisioned" in result.output

    def test_metrics_analyze_output_file(self, tmp_path):
        import urllib.error
        out_file = tmp_path / "recs.json"
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = self.runner.invoke(
                cli, ["metrics", "analyze", "--output", str(out_file)]
            )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert "recommendations" in data

    def test_metrics_analyze_custom_thresholds(self):
        # Pod at 25% CPU: flagged when threshold=30, not flagged when threshold=20
        pod = _make_pod("web-abc", 25.0, 100.0, 200.0, 250.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake):
            result = self.runner.invoke(
                cli, ["metrics", "analyze", "--cpu-threshold", "20", "--json"]
            )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["overprovisioned_deployments"] == 0

    def test_metrics_analyze_pods_table_shown(self):
        """Human-readable output includes the per-pod table for overprovisioned pods."""
        pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake):
            result = self.runner.invoke(cli, ["metrics", "analyze"])
        assert result.exit_code == 0
        assert "Pod Resource Analysis" in result.output
        assert "web-abc" in result.output
        assert "CPU" in result.output
        assert "Mem" in result.output

    def test_metrics_analyze_pods_table_healthy_pod_shown(self):
        """Healthy pods appear in the per-pod table with OK status."""
        over_pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        healthy_pod = _make_pod("web-healthy", 80.0, 100.0, 160.0, 200.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [over_pod]},
            {"deployment": "api", "namespace": "default", "pods": [healthy_pod]},
        ])
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake):
            result = self.runner.invoke(cli, ["metrics", "analyze"])
        assert result.exit_code == 0
        assert "Pod Resource Analysis" in result.output
        assert "web-abc" in result.output
        assert "web-healthy" in result.output
        # Healthy pod should show OK status
        assert "OK" in result.output
        # Overprovisioned pod should show a warning
        assert "HIGH" in result.output or "WARN" in result.output

    def test_metrics_analyze_input_flag_simple_format(self, tmp_path):
        """--input reads a flat pod list and produces correct analysis."""
        sample = [
            {"name": "auth-service", "cpu_requested": 500, "cpu_used": 90,
             "memory_requested": 512, "memory_used": 140},
            {"name": "payment-service", "cpu_requested": 1000, "cpu_used": 850,
             "memory_requested": 1024, "memory_used": 900},
            {"name": "worker", "cpu_requested": 300, "cpu_used": 50,
             "memory_requested": 256, "memory_used": 80},
        ]
        input_file = tmp_path / "pods.json"
        input_file.write_text(json.dumps(sample))
        result = self.runner.invoke(
            cli, ["metrics", "analyze", "--input", str(input_file)]
        )
        assert result.exit_code == 0
        # auth-service and worker are overprovisioned; payment-service is healthy
        assert "auth-service" in result.output
        assert "payment-service" in result.output
        assert "worker" in result.output
        # Summary table should mention 2 overprovisioned deployments
        assert "2" in result.output

    def test_metrics_analyze_input_flag_json_output(self, tmp_path):
        """--input with --json produces valid JSON with correct summary."""
        sample = [
            {"name": "auth-service", "cpu_requested": 500, "cpu_used": 90,
             "memory_requested": 512, "memory_used": 140},
        ]
        input_file = tmp_path / "pods.json"
        input_file.write_text(json.dumps(sample))
        result = self.runner.invoke(
            cli, ["metrics", "analyze", "--input", str(input_file), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["overprovisioned_deployments"] == 1
        assert len(data["recommendations"]) == 1
        assert data["recommendations"][0]["deployment"] == "auth-service"

    def test_metrics_analyze_input_no_prometheus_call(self, tmp_path):
        """Using --input must not query Prometheus."""
        sample = [{"name": "svc", "cpu_requested": 100, "cpu_used": 10,
                   "memory_requested": 100, "memory_used": 10}]
        input_file = tmp_path / "pods.json"
        input_file.write_text(json.dumps(sample))
        with patch.object(metrics_ops, "format_metrics_for_analysis") as mock_fmt:
            result = self.runner.invoke(
                cli, ["metrics", "analyze", "--input", str(input_file), "--json"]
            )
        assert result.exit_code == 0
        mock_fmt.assert_not_called()


# ── TestRulesFlaskUI ─────────────────────────────────────────────────────────

class TestRulesFlaskUI:
    def setup_method(self):
        from optik8s.ui.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_api_recommendations_returns_json(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            resp = self.client.get("/api/metrics/recommendations")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "recommendations" in data
        assert "summary" in data
        assert "thresholds" in data
        assert "analyzed_at" in data

    def test_api_recommendations_with_overprovisioned_pod(self):
        pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake):
            resp = self.client.get("/api/metrics/recommendations")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["summary"]["overprovisioned_deployments"] == 1
        assert len(data["recommendations"]) == 1
        assert data["recommendations"][0]["severity"] == "high"

    def test_api_recommendations_namespace_filter(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            resp = self.client.get("/api/metrics/recommendations?namespace=production")
        assert resp.status_code == 200

    def test_api_recommendations_custom_thresholds(self):
        # Pod at 25% CPU: not flagged when threshold=20
        pod = _make_pod("web-abc", 25.0, 100.0, 200.0, 250.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake):
            resp = self.client.get(
                "/api/metrics/recommendations?cpu_threshold=20&memory_threshold=20"
            )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["summary"]["overprovisioned_deployments"] == 0
        assert data["thresholds"]["cpu_pct"] == 20.0

    def test_api_recommendations_invalid_threshold_returns_400(self):
        resp = self.client.get("/api/metrics/recommendations?cpu_threshold=notanumber")
        assert resp.status_code == 400

    def test_api_recommendations_estimated_savings_positive(self):
        pod = _make_pod("web-abc", 5.0, 200.0, 10.0, 400.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake):
            resp = self.client.get("/api/metrics/recommendations")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["summary"]["estimated_monthly_savings_usd"] > 0


# ── AI summarization ─────────────────────────────────────────────────────────

def _make_openai_response(summaries: dict) -> bytes:
    """Build a minimal OpenAI Chat Completions HTTP response body."""
    body = {
        "choices": [{
            "message": {"role": "assistant", "content": json.dumps(summaries)}
        }]
    }
    return json.dumps(body).encode()


def _make_analysis_with_overprovisioned_pod():
    """Return a rules.analyze() result with one overprovisioned deployment."""
    pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
    metrics = _make_formatted_metrics([
        {"deployment": "web", "namespace": "default", "pods": [pod]}
    ])
    return rules_ops.analyze(metrics)


class TestAISummarization:
    """Unit tests for ai_ops.summarize_recommendations()."""

    def test_default_model_constant(self):
        assert ai_ops.DEFAULT_MODEL == "gpt-3.5-turbo"

    def test_no_api_key_returns_error(self):
        result = ai_ops.summarize_recommendations(
            _make_analysis_with_overprovisioned_pod(),
            api_key="",
        )
        assert result["error"] is not None
        assert "OPENAI_API_KEY" in result["error"]
        assert result["summaries"] == {}

    def test_no_api_key_env_returns_error(self):
        with patch.dict("os.environ", {}, clear=True):
            result = ai_ops.summarize_recommendations(
                _make_analysis_with_overprovisioned_pod(),
                api_key=None,
            )
        assert result["error"] is not None
        assert result["summaries"] == {}

    def test_no_recommendations_returns_empty_summaries(self):
        empty_analysis = rules_ops.analyze(_make_formatted_metrics([]))
        result = ai_ops.summarize_recommendations(empty_analysis, api_key="test-key")
        assert result["error"] is None
        assert result["summaries"] == {}

    def test_successful_api_call_returns_summaries(self):
        analysis = _make_analysis_with_overprovisioned_pod()
        fake_summaries = {"web": "The web deployment is severely overprovisioned on CPU."}
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_openai_response(fake_summaries)
            mock_open.return_value = cm
            result = ai_ops.summarize_recommendations(analysis, api_key="sk-test")
        assert result["error"] is None
        assert result["summaries"] == fake_summaries
        assert result["model"] == ai_ops.DEFAULT_MODEL

    def test_custom_model_is_passed_through(self):
        analysis = _make_analysis_with_overprovisioned_pod()
        fake_summaries = {"web": "Summary text."}
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_openai_response(fake_summaries)
            mock_open.return_value = cm
            result = ai_ops.summarize_recommendations(
                analysis, api_key="sk-test", model="gpt-4o"
            )
        assert result["model"] == "gpt-4o"

    def test_http_error_returns_error_message(self):
        import urllib.error
        analysis = _make_analysis_with_overprovisioned_pod()
        error_body = json.dumps({"error": {"message": "Invalid API key"}}).encode()
        http_err = urllib.error.HTTPError(
            url="https://api.openai.com/v1/chat/completions",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=MagicMock(read=MagicMock(return_value=error_body)),
        )
        with patch("urllib.request.urlopen", side_effect=http_err):
            result = ai_ops.summarize_recommendations(analysis, api_key="sk-bad")
        assert result["error"] is not None
        assert "Invalid API key" in result["error"]
        assert result["summaries"] == {}

    def test_connection_error_returns_error_message(self):
        import urllib.error
        analysis = _make_analysis_with_overprovisioned_pod()
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            result = ai_ops.summarize_recommendations(analysis, api_key="sk-test")
        assert result["error"] is not None
        assert "AI API unavailable" in result["error"]
        assert result["summaries"] == {}

    def test_non_json_response_falls_back_gracefully(self):
        analysis = _make_analysis_with_overprovisioned_pod()
        body = json.dumps({
            "choices": [{"message": {"role": "assistant", "content": "plain text, not JSON"}}]
        }).encode()
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = body
            mock_open.return_value = cm
            result = ai_ops.summarize_recommendations(analysis, api_key="sk-test")
        assert result["error"] is None
        # Falls back to mapping each deployment to the raw text
        assert "web" in result["summaries"]
        assert result["summaries"]["web"] == "plain text, not JSON"

    def test_result_is_json_serialisable(self):
        analysis = _make_analysis_with_overprovisioned_pod()
        fake_summaries = {"web": "Reduce CPU requests."}
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_openai_response(fake_summaries)
            mock_open.return_value = cm
            result = ai_ops.summarize_recommendations(analysis, api_key="sk-test")
        serialised = json.dumps(result)
        assert "summaries" in serialised

    def test_build_user_prompt_contains_deployment_info(self):
        analysis = _make_analysis_with_overprovisioned_pod()
        prompt = ai_ops._build_user_prompt(analysis)
        data = json.loads(prompt)
        assert "recommendations" in data
        assert any(r["deployment"] == "web" for r in data["recommendations"])

    def test_build_user_prompt_includes_pod_resource_details(self):
        # User prompt must include per-pod actual/requested values for AI context
        analysis = _make_analysis_with_overprovisioned_pod()
        prompt = ai_ops._build_user_prompt(analysis)
        data = json.loads(prompt)
        rec = next(r for r in data["recommendations"] if r["deployment"] == "web")
        assert "pods" in rec
        assert len(rec["pods"]) > 0
        pod_data = rec["pods"][0]
        assert "cpu_requested_millicores" in pod_data
        assert "cpu_used_millicores" in pod_data
        assert "cpu_usage_pct" in pod_data
        assert "memory_requested_mib" in pod_data
        assert "memory_used_mib" in pod_data
        assert "memory_usage_pct" in pod_data

    def test_env_var_api_key_is_used(self):
        analysis = _make_analysis_with_overprovisioned_pod()
        fake_summaries = {"web": "Reduce CPU."}
        with patch.dict("os.environ", {"OPENAI_API_KEY": "sk-from-env"}), \
             patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = lambda s: s
            cm.__exit__ = MagicMock(return_value=False)
            cm.read.return_value = _make_openai_response(fake_summaries)
            mock_open.return_value = cm
            result = ai_ops.summarize_recommendations(analysis)
        assert result["error"] is None
        assert result["summaries"] == fake_summaries


class TestAISummarizeCLI:
    def setup_method(self):
        self.runner = CliRunner()

    def test_metrics_summarize_help(self):
        result = self.runner.invoke(cli, ["metrics", "summarize", "--help"])
        assert result.exit_code == 0
        assert "AI" in result.output or "summary" in result.output.lower()

    def test_metrics_summarize_no_api_key_shows_warning(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")), \
             patch.dict("os.environ", {}, clear=True):
            result = self.runner.invoke(cli, ["metrics", "summarize", "--api-key", ""])
        assert result.exit_code == 0
        assert "unavailable" in result.output.lower() or "not configured" in result.output.lower()

    def test_metrics_summarize_with_ai_response(self):
        pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        fake_summaries = {"web": "The web deployment is overprovisioned. Reduce CPU requests."}
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake), \
             patch.object(ai_ops, "summarize_recommendations", return_value={
                 "model": "gpt-3.5-turbo", "summaries": fake_summaries, "error": None
             }):
            result = self.runner.invoke(cli, ["metrics", "summarize"])
        assert result.exit_code == 0
        assert "web" in result.output
        assert "overprovisioned" in result.output.lower()

    def test_metrics_summarize_json_output(self):
        pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        fake_summaries = {"web": "The web deployment is overprovisioned."}
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake), \
             patch.object(ai_ops, "summarize_recommendations", return_value={
                 "model": "gpt-3.5-turbo", "summaries": fake_summaries, "error": None
             }):
            result = self.runner.invoke(cli, ["metrics", "summarize", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "recommendations" in data
        assert "ai_summary" in data
        assert data["ai_summary"]["summaries"] == fake_summaries

    def test_metrics_summarize_no_pods(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")), \
             patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test"}), \
             patch.object(ai_ops, "summarize_recommendations", return_value={
                 "model": "gpt-3.5-turbo", "summaries": {}, "error": None
             }):
            result = self.runner.invoke(cli, ["metrics", "summarize"])
        assert result.exit_code == 0
        assert "No overprovisioned" in result.output

    def test_metrics_summarize_input_file(self, tmp_path):
        sample = [
            {"name": "auth-service", "cpu_requested": 500, "cpu_used": 90,
             "memory_requested": 512, "memory_used": 140},
        ]
        input_file = tmp_path / "pods.json"
        input_file.write_text(json.dumps(sample))
        fake_summaries = {"auth-service": "auth-service is using only 18% of its requested CPU."}
        with patch.object(ai_ops, "summarize_recommendations", return_value={
            "model": "gpt-3.5-turbo", "summaries": fake_summaries, "error": None
        }):
            result = self.runner.invoke(
                cli, ["metrics", "summarize", "--input", str(input_file)]
            )
        assert result.exit_code == 0
        assert "auth-service" in result.output

    def test_metrics_summarize_ai_error_falls_back_to_rules(self):
        pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake), \
             patch.object(ai_ops, "summarize_recommendations", return_value={
                 "model": "gpt-3.5-turbo",
                 "summaries": {},
                 "error": "AI API unavailable: Connection refused",
             }):
            result = self.runner.invoke(cli, ["metrics", "summarize"])
        assert result.exit_code == 0
        # Should fall back to rule-based message
        assert "web" in result.output


class TestAISummarizeFlaskUI:
    def setup_method(self):
        from optik8s.ui.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_api_ai_summary_returns_json(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")), \
             patch.object(ai_ops, "summarize_recommendations", return_value={
                 "model": "gpt-3.5-turbo", "summaries": {}, "error": None
             }):
            resp = self.client.get("/api/metrics/ai_summary")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "recommendations" in data
        assert "ai_summary" in data
        assert "summaries" in data["ai_summary"]

    def test_api_ai_summary_with_overprovisioned_pod(self):
        pod = _make_pod("web-abc", 5.0, 100.0, 10.0, 200.0)
        fake = _make_formatted_metrics([
            {"deployment": "web", "namespace": "default", "pods": [pod]}
        ])
        fake_summaries = {"web": "The web deployment has excessive CPU."}
        with patch.object(metrics_ops, "format_metrics_for_analysis", return_value=fake), \
             patch.object(ai_ops, "summarize_recommendations", return_value={
                 "model": "gpt-3.5-turbo", "summaries": fake_summaries, "error": None
             }):
            resp = self.client.get("/api/metrics/ai_summary")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["summary"]["overprovisioned_deployments"] == 1
        assert data["ai_summary"]["summaries"] == fake_summaries

    def test_api_ai_summary_propagates_ai_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")), \
             patch.object(ai_ops, "summarize_recommendations", return_value={
                 "model": "gpt-3.5-turbo",
                 "summaries": {},
                 "error": "AI API unavailable: Connection refused",
             }):
            resp = self.client.get("/api/metrics/ai_summary")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ai_summary"]["error"] is not None

    def test_api_ai_summary_invalid_threshold_returns_400(self):
        resp = self.client.get("/api/metrics/ai_summary?cpu_threshold=notanumber")
        assert resp.status_code == 400

    def test_api_ai_summary_custom_model_passed(self):
        import urllib.error
        captured = {}

        def fake_summarize(analysis, model=ai_ops.DEFAULT_MODEL, **kwargs):
            captured["model"] = model
            return {"model": model, "summaries": {}, "error": None}

        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")), \
             patch.object(ai_ops, "summarize_recommendations", side_effect=fake_summarize):
            self.client.get("/api/metrics/ai_summary?model=gpt-4o")
        assert captured.get("model") == "gpt-4o"


# ── Simulator core ───────────────────────────────────────────────────────────

class TestSimulatorCatalogue:
    """Tests for the simulator architecture / scenario catalogues."""

    def test_list_architectures_returns_all_keys(self):
        archs = simulator_ops.list_architectures()
        assert isinstance(archs, dict)
        expected = {
            "frontend", "backend-api", "database", "cache",
            "worker", "batch", "ml-inference", "microservice",
        }
        assert set(archs.keys()) == expected

    def test_architecture_entry_has_required_fields(self):
        for key, info in simulator_ops.list_architectures().items():
            assert "name" in info, f"{key} missing 'name'"
            assert "description" in info, f"{key} missing 'description'"
            assert "typical_cpu_request_millicores" in info, f"{key} missing cpu range"
            assert "typical_memory_request_mib" in info, f"{key} missing mem range"
            assert "load_profiles" in info, f"{key} missing load_profiles"
            assert set(info["load_profiles"]) >= {"idle", "normal", "high"}

    def test_list_scenarios_returns_all_predefined(self):
        scenarios = simulator_ops.list_scenarios()
        assert isinstance(scenarios, dict)
        expected = {
            "ecommerce", "saas-platform", "data-pipeline",
            "microservices", "overprovisioned", "peak-load",
        }
        assert set(scenarios.keys()) == expected

    def test_scenario_entry_has_required_fields(self):
        for name, info in simulator_ops.list_scenarios().items():
            assert "description" in info, f"{name} missing 'description'"
            assert "total_pods" in info, f"{name} missing 'total_pods'"
            assert info["total_pods"] > 0, f"{name} has no pods"


class TestSimulatorGenerateScenario:
    """Tests for simulator_ops.generate_scenario()."""

    def test_returns_list(self):
        pods = simulator_ops.generate_scenario(num_pods=5)
        assert isinstance(pods, list)
        assert len(pods) == 5

    def test_pod_has_required_fields(self):
        pods = simulator_ops.generate_scenario(num_pods=3)
        for pod in pods:
            assert "name" in pod
            assert "architecture" in pod
            assert "load_profile" in pod
            assert "cpu_requested" in pod
            assert "cpu_used" in pod
            assert "memory_requested" in pod
            assert "memory_used" in pod

    def test_cpu_used_less_than_or_equal_to_requested(self):
        # cpu_used should never substantially exceed cpu_requested.
        # A 5% tolerance guards against floating-point rounding in round().
        pods = simulator_ops.generate_scenario(num_pods=20, load="high", seed=42)
        for pod in pods:
            assert pod["cpu_used"] <= pod["cpu_requested"] * 1.05, (
                f"Pod '{pod['name']}': cpu_used={pod['cpu_used']} > "
                f"cpu_requested={pod['cpu_requested']}"
            )

    def test_memory_values_positive(self):
        pods = simulator_ops.generate_scenario(num_pods=10)
        for pod in pods:
            assert pod["memory_requested"] > 0
            assert pod["memory_used"] >= 0

    def test_seed_produces_deterministic_output(self):
        pods1 = simulator_ops.generate_scenario(num_pods=6, seed=99)
        pods2 = simulator_ops.generate_scenario(num_pods=6, seed=99)
        assert pods1 == pods2

    def test_different_seeds_produce_different_output(self):
        pods1 = simulator_ops.generate_scenario(num_pods=8, seed=1)
        pods2 = simulator_ops.generate_scenario(num_pods=8, seed=2)
        assert pods1 != pods2

    def test_load_idle_produces_low_usage(self):
        # Idle pods should use <30 % of CPU requested on average
        pods = simulator_ops.generate_scenario(num_pods=20, load="idle", seed=7)
        for pod in pods:
            if pod["cpu_requested"] > 0:
                ratio = pod["cpu_used"] / pod["cpu_requested"]
                assert ratio < 0.30, (
                    f"Idle pod '{pod['name']}' has cpu ratio {ratio:.2f} ≥ 0.30"
                )

    def test_load_high_produces_high_usage(self):
        # High load pods should use >40 % of CPU requested
        pods = simulator_ops.generate_scenario(num_pods=20, load="high", seed=7)
        avg_ratio = sum(
            p["cpu_used"] / p["cpu_requested"]
            for p in pods if p["cpu_requested"] > 0
        ) / len(pods)
        assert avg_ratio > 0.40, f"High-load avg cpu ratio {avg_ratio:.2f} ≤ 0.40"

    def test_architecture_filter(self):
        pods = simulator_ops.generate_scenario(
            architectures=["frontend"], num_pods=10, seed=5
        )
        for pod in pods:
            assert pod["architecture"] == "frontend"

    def test_multiple_architectures_used(self):
        pods = simulator_ops.generate_scenario(
            architectures=["frontend", "backend-api"], num_pods=20, seed=3
        )
        arch_set = {p["architecture"] for p in pods}
        assert len(arch_set) > 1  # both should appear in 20 pods

    def test_invalid_architecture_raises(self):
        with pytest.raises(ValueError, match="Unknown architecture"):
            simulator_ops.generate_scenario(architectures=["nonexistent"])

    def test_invalid_load_profile_raises(self):
        with pytest.raises(ValueError, match="load profile"):
            simulator_ops.generate_scenario(load="turbo")

    def test_num_pods_zero_raises(self):
        with pytest.raises(ValueError, match="num_pods"):
            simulator_ops.generate_scenario(num_pods=0)

    def test_output_is_json_serialisable(self):
        pods = simulator_ops.generate_scenario(num_pods=5)
        serialised = json.dumps(pods)
        assert "cpu_requested" in serialised

    def test_mixed_load_uses_all_profiles(self):
        # With enough pods, mixed mode should produce all three load profiles
        pods = simulator_ops.generate_scenario(num_pods=30, load="mixed", seed=1)
        profiles_seen = {p["load_profile"] for p in pods}
        assert "idle" in profiles_seen
        assert "normal" in profiles_seen
        assert "high" in profiles_seen


class TestSimulatorRunScenario:
    """Tests for simulator_ops.run_scenario()."""

    def test_run_returns_required_keys(self):
        result = simulator_ops.run_scenario(num_pods=5, load="idle", seed=1)
        assert "analyzed_at" in result
        assert "summary" in result
        assert "recommendations" in result
        assert "thresholds" in result
        assert "scenario" in result

    def test_scenario_key_contains_pod_list(self):
        result = simulator_ops.run_scenario(num_pods=5, seed=42)
        assert isinstance(result["scenario"], list)
        assert len(result["scenario"]) == 5

    def test_predefined_scenario_overprovisioned(self):
        # The 'overprovisioned' scenario uses idle load everywhere →
        # the analysis engine should flag most pods.
        result = simulator_ops.run_scenario(scenario_name="overprovisioned", seed=0)
        assert result["summary"]["overprovisioned_pods"] > 0

    def test_predefined_scenario_peak_load_no_flags(self):
        # The 'peak-load' scenario uses high load everywhere →
        # the analysis engine should NOT flag any pods.
        result = simulator_ops.run_scenario(scenario_name="peak-load", seed=0)
        assert result["summary"]["overprovisioned_deployments"] == 0

    def test_idle_scenario_detected_as_overprovisioned(self):
        # Custom idle scenario → should be flagged
        result = simulator_ops.run_scenario(load="idle", num_pods=6, seed=10)
        assert result["summary"]["overprovisioned_pods"] > 0

    def test_high_load_scenario_not_flagged(self):
        # Custom high-load scenario with architectures that are definitively >30% at high load.
        # (cache is intentionally excluded since it has inherently low CPU even at high load)
        result = simulator_ops.run_scenario(
            architectures=["backend-api", "worker"],
            load="high",
            num_pods=6,
            seed=10,
        )
        assert result["summary"]["overprovisioned_deployments"] == 0

    def test_unknown_scenario_raises(self):
        with pytest.raises(ValueError, match="Unknown scenario"):
            simulator_ops.run_scenario(scenario_name="nonexistent-xyz")

    def test_result_is_json_serialisable(self):
        result = simulator_ops.run_scenario(num_pods=4, seed=1)
        serialised = json.dumps(result)
        assert "summary" in serialised

    def test_custom_thresholds_respected(self):
        # At threshold=80 %, even normal-load pods should be flagged
        result = simulator_ops.run_scenario(
            load="normal", num_pods=10, seed=5, cpu_threshold_pct=80.0
        )
        assert result["summary"]["overprovisioned_pods"] > 0

    def test_ecommerce_scenario_has_correct_pod_count(self):
        result = simulator_ops.run_scenario(scenario_name="ecommerce", seed=0)
        # ecommerce scenario: 3 + 2 + 1 + 1 = 7 pods
        assert len(result["scenario"]) == 7


class TestSimulatorPredefinedScenarios:
    """Verify all predefined scenarios produce valid analysis results."""

    def test_all_predefined_scenarios_run_without_error(self):
        for name in simulator_ops.SCENARIOS:
            result = simulator_ops.run_scenario(scenario_name=name, seed=42)
            assert result["summary"]["total_pods"] > 0, f"Scenario '{name}' has no pods"

    def test_all_predefined_scenarios_match_pod_count(self):
        scenarios = simulator_ops.list_scenarios()
        for name, meta in scenarios.items():
            result = simulator_ops.run_scenario(scenario_name=name, seed=42)
            assert len(result["scenario"]) == meta["total_pods"], (
                f"Scenario '{name}' expected {meta['total_pods']} pods, "
                f"got {len(result['scenario'])}"
            )


# ── Simulator CLI ─────────────────────────────────────────────────────────────

class TestSimulateCLI:
    def setup_method(self):
        self.runner = CliRunner()

    def test_simulate_help(self):
        result = self.runner.invoke(cli, ["simulate", "--help"])
        assert result.exit_code == 0
        assert "list" in result.output
        assert "generate" in result.output
        assert "run" in result.output

    def test_simulate_list_shows_architectures(self):
        result = self.runner.invoke(cli, ["simulate", "list"])
        assert result.exit_code == 0
        # All architecture keys should appear
        for key in simulator_ops.ARCHITECTURES:
            assert key in result.output

    def test_simulate_list_shows_scenarios(self):
        result = self.runner.invoke(cli, ["simulate", "list"])
        assert result.exit_code == 0
        for name in simulator_ops.SCENARIOS:
            assert name in result.output

    def test_simulate_generate_outputs_json(self):
        result = self.runner.invoke(cli, ["simulate", "generate", "--pods", "5"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 5

    def test_simulate_generate_seed_deterministic(self):
        r1 = self.runner.invoke(cli, ["simulate", "generate", "--pods", "4", "--seed", "7"])
        r2 = self.runner.invoke(cli, ["simulate", "generate", "--pods", "4", "--seed", "7"])
        assert r1.exit_code == 0
        assert r2.exit_code == 0
        assert json.loads(r1.output) == json.loads(r2.output)

    def test_simulate_generate_architecture_filter(self):
        result = self.runner.invoke(
            cli, ["simulate", "generate", "--architecture", "frontend", "--pods", "5"]
        )
        assert result.exit_code == 0
        pods = json.loads(result.output)
        for pod in pods:
            assert pod["architecture"] == "frontend"

    def test_simulate_generate_load_idle(self):
        result = self.runner.invoke(
            cli, ["simulate", "generate", "--load", "idle", "--pods", "10", "--seed", "1"]
        )
        assert result.exit_code == 0
        pods = json.loads(result.output)
        for pod in pods:
            assert pod["load_profile"] == "idle"

    def test_simulate_generate_output_file(self, tmp_path):
        out_file = tmp_path / "scenario.json"
        result = self.runner.invoke(
            cli, ["simulate", "generate", "--pods", "4", "--output", str(out_file)]
        )
        assert result.exit_code == 0
        assert out_file.exists()
        data = json.loads(out_file.read_text())
        assert isinstance(data, list)
        assert len(data) == 4

    def test_simulate_run_predefined_scenario(self):
        result = self.runner.invoke(
            cli, ["simulate", "run", "--scenario", "overprovisioned", "--seed", "42"]
        )
        assert result.exit_code == 0
        # Should contain the summary panel and pod table
        assert "Simulated Workload Analysis" in result.output
        assert "overprovisioned" in result.output.lower()

    def test_simulate_run_json_output(self):
        result = self.runner.invoke(
            cli, ["simulate", "run", "--load", "idle", "--pods", "5", "--seed", "1", "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert "summary" in data
        assert "recommendations" in data
        assert "scenario" in data

    def test_simulate_run_peak_load_no_recommendations(self):
        result = self.runner.invoke(
            cli,
            ["simulate", "run", "--scenario", "peak-load", "--seed", "1", "--json"],
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["overprovisioned_deployments"] == 0

    def test_simulate_run_idle_shows_recommendations(self):
        result = self.runner.invoke(
            cli,
            ["simulate", "run", "--load", "idle", "--pods", "5", "--seed", "1"],
        )
        assert result.exit_code == 0
        # Should show recommendation panels (MEDIUM or HIGH)
        assert "MEDIUM" in result.output or "HIGH" in result.output

    def test_simulate_run_help(self):
        result = self.runner.invoke(cli, ["simulate", "run", "--help"])
        assert result.exit_code == 0
        assert "--scenario" in result.output
        assert "--load" in result.output
        assert "--seed" in result.output

    def test_simulate_generate_help(self):
        result = self.runner.invoke(cli, ["simulate", "generate", "--help"])
        assert result.exit_code == 0
        assert "--architecture" in result.output
        assert "--pods" in result.output
        assert "--load" in result.output


# ── Simulator Flask UI ────────────────────────────────────────────────────────

class TestSimulateFlaskUI:
    def setup_method(self):
        from optik8s.ui.app import create_app
        self.app = create_app()
        self.client = self.app.test_client()
        self.app.config["TESTING"] = True

    def test_api_simulate_architectures_returns_json(self):
        resp = self.client.get("/api/simulate/architectures")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for key in simulator_ops.ARCHITECTURES:
            assert key in data

    def test_api_simulate_scenarios_returns_json(self):
        resp = self.client.get("/api/simulate/scenarios")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for name in simulator_ops.SCENARIOS:
            assert name in data

    def test_api_simulate_run_default(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "summary" in data
        assert "recommendations" in data
        assert "scenario" in data

    def test_api_simulate_run_predefined_scenario(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={"scenario": "overprovisioned", "seed": 42},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["summary"]["overprovisioned_pods"] > 0

    def test_api_simulate_run_peak_load_no_flags(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={"scenario": "peak-load", "seed": 0},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["summary"]["overprovisioned_deployments"] == 0

    def test_api_simulate_run_custom_pods(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={"num_pods": 10, "load": "idle", "seed": 1},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert len(data["scenario"]) == 10

    def test_api_simulate_run_architectures_filter(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={"architectures": ["frontend"], "num_pods": 5, "seed": 1},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        for pod in data["scenario"]:
            assert pod["architecture"] == "frontend"

    def test_api_simulate_run_invalid_scenario_returns_400(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={"scenario": "nonexistent-xyz"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data

    def test_api_simulate_run_invalid_load_returns_400(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={"load": "turbo"},
            content_type="application/json",
        )
        assert resp.status_code == 400
        data = json.loads(resp.data)
        assert "error" in data

    def test_api_simulate_run_invalid_threshold_returns_400(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={"cpu_threshold": "not-a-number"},
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_api_simulate_run_seed_deterministic(self):
        payload = {"num_pods": 8, "seed": 42, "load": "mixed"}
        r1 = self.client.post(
            "/api/simulate/run", json=payload, content_type="application/json"
        )
        r2 = self.client.post(
            "/api/simulate/run", json=payload, content_type="application/json"
        )
        assert r1.status_code == 200
        assert r2.status_code == 200
        d1 = json.loads(r1.data)
        d2 = json.loads(r2.data)
        assert d1["scenario"] == d2["scenario"]

    def test_api_simulate_run_custom_threshold(self):
        # With threshold=80 %, normal-load pods should be flagged
        resp = self.client.post(
            "/api/simulate/run",
            json={"load": "normal", "num_pods": 10, "seed": 5, "cpu_threshold": 80},
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["summary"]["overprovisioned_pods"] > 0

    def test_api_simulate_run_result_is_json_serialisable(self):
        resp = self.client.post(
            "/api/simulate/run",
            json={"num_pods": 3, "seed": 1},
            content_type="application/json",
        )
        assert resp.status_code == 200
        # The response was already parsed without error above
        data = json.loads(resp.data)
        assert "analyzed_at" in data


# ── Sample datasets ───────────────────────────────────────────────────────────

SAMPLES_DIR = REPO_ROOT / "samples"

SAMPLE_FILES = [
    "ecommerce.json",
    "overprovisioned.json",
    "peak-load.json",
    "saas-platform.json",
    "data-pipeline.json",
    "microservices.json",
]


class TestSampleDatasets:
    """Verify that all bundled sample JSON files are valid and produce correct analysis."""

    def setup_method(self):
        self.runner = CliRunner()

    def test_samples_directory_exists(self):
        assert SAMPLES_DIR.exists(), f"samples/ directory not found: {SAMPLES_DIR}"

    def test_all_sample_files_exist(self):
        for filename in SAMPLE_FILES:
            path = SAMPLES_DIR / filename
            assert path.exists(), f"Sample file missing: {path}"

    def test_all_sample_files_are_valid_json(self):
        for filename in SAMPLE_FILES:
            path = SAMPLES_DIR / filename
            data = json.loads(path.read_text())
            assert isinstance(data, list), f"{filename}: expected a JSON array"
            assert len(data) > 0, f"{filename}: array must not be empty"

    def test_all_sample_pods_have_required_fields(self):
        required = {"name", "cpu_requested", "cpu_used", "memory_requested", "memory_used"}
        for filename in SAMPLE_FILES:
            pods = json.loads((SAMPLES_DIR / filename).read_text())
            for pod in pods:
                missing = required - set(pod.keys())
                assert not missing, (
                    f"{filename}: pod '{pod.get('name', '?')}' missing fields {missing}"
                )

    def test_all_sample_pods_have_positive_resource_values(self):
        for filename in SAMPLE_FILES:
            pods = json.loads((SAMPLES_DIR / filename).read_text())
            for pod in pods:
                assert pod["cpu_requested"] > 0, (
                    f"{filename}: pod '{pod['name']}' has non-positive cpu_requested"
                )
                assert pod["memory_requested"] > 0, (
                    f"{filename}: pod '{pod['name']}' has non-positive memory_requested"
                )
                assert pod["cpu_used"] >= 0, (
                    f"{filename}: pod '{pod['name']}' has negative cpu_used"
                )
                assert pod["memory_used"] >= 0, (
                    f"{filename}: pod '{pod['name']}' has negative memory_used"
                )

    def test_overprovisioned_sample_produces_flags(self):
        """The overprovisioned.json dataset must trigger overprovisioning warnings."""
        result = self.runner.invoke(
            cli, ["metrics", "analyze", "--input", str(SAMPLES_DIR / "overprovisioned.json"), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["overprovisioned_pods"] > 0, (
            "overprovisioned.json should have at least one overprovisioned pod"
        )

    def test_peak_load_sample_produces_no_flags(self):
        """The peak-load.json dataset must NOT trigger overprovisioning warnings."""
        result = self.runner.invoke(
            cli, ["metrics", "analyze", "--input", str(SAMPLES_DIR / "peak-load.json"), "--json"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert data["summary"]["overprovisioned_deployments"] == 0, (
            "peak-load.json should have no overprovisioned deployments"
        )

    def test_ecommerce_sample_pods_count(self):
        """ecommerce.json must have 7 pods matching the predefined scenario."""
        pods = json.loads((SAMPLES_DIR / "ecommerce.json").read_text())
        assert len(pods) == 7

    def test_all_samples_are_analyzable_via_cli(self):
        """All sample files must work with 'metrics analyze --input' without errors."""
        for filename in SAMPLE_FILES:
            result = self.runner.invoke(
                cli,
                ["metrics", "analyze", "--input", str(SAMPLES_DIR / filename), "--json"],
            )
            assert result.exit_code == 0, (
                f"{filename}: CLI exited with non-zero code\n{result.output}"
            )
            data = json.loads(result.output)
            assert "summary" in data, f"{filename}: missing 'summary' in output"
            assert "recommendations" in data, f"{filename}: missing 'recommendations' in output"
