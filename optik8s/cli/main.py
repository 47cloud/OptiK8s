"""OptiK8s CLI – manage KIND/EKS clusters and deploy sample workloads.

Usage examples
--------------
  optik8s cluster create kind
  optik8s cluster create eks --name my-cluster --region eu-west-1
  optik8s cluster list
  optik8s cluster delete kind --name optik8s
  optik8s app list
  optik8s app deploy nodejs-web
  optik8s app deploy --all
  optik8s app remove nodejs-web
  optik8s app status
  optik8s ui
"""

from __future__ import annotations

import json
import pathlib
import sys

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from optik8s.core import cluster as cluster_ops
from optik8s.core import apps as app_ops
from optik8s.core import monitoring as monitoring_ops
from optik8s.core import metrics as metrics_ops
from optik8s.core import rules as rules_ops
from optik8s.core import ai as ai_ops
from optik8s.core import simulator as simulator_ops

console = Console()


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="optik8s")
def cli():
    """OptiK8s – spin up KIND/EKS clusters and deploy sample workloads."""


# ---------------------------------------------------------------------------
# cluster group
# ---------------------------------------------------------------------------

@cli.group()
def cluster():
    """Manage Kubernetes clusters (KIND and EKS)."""


@cluster.command("create")
@click.argument("provider", type=click.Choice(["kind", "eks"], case_sensitive=False))
@click.option("--name", default="optik8s", show_default=True, help="Cluster name")
@click.option("--region", default="us-east-1", show_default=True, help="AWS region (EKS only)")
@click.option("--config", "config_path", default=None, help="Path to custom cluster config file")
def cluster_create(provider: str, name: str, region: str, config_path: str):
    """Create a Kubernetes cluster.

    PROVIDER is either 'kind' (local) or 'eks' (AWS EKS).
    """
    provider = provider.lower()
    console.print(Panel(
        f"[bold green]Creating {provider.upper()} cluster:[/] [cyan]{name}[/]",
        expand=False,
    ))

    if provider == "kind":
        if not _check_tool("kind"):
            sys.exit(1)
        result = cluster_ops.kind_create(name=name, config_path=config_path)
    else:
        if not _check_tool("eksctl"):
            sys.exit(1)
        result = cluster_ops.eks_create(name=name, region=region, config_path=config_path)

    if result["success"]:
        console.print(f"[bold green]✓[/] Cluster [cyan]{name}[/] created successfully.")
    else:
        console.print(f"[bold red]✗[/] Failed to create cluster [cyan]{name}[/].")
        sys.exit(1)


@cluster.command("delete")
@click.argument("provider", type=click.Choice(["kind", "eks"], case_sensitive=False))
@click.option("--name", default="optik8s", show_default=True, help="Cluster name")
@click.option("--region", default="us-east-1", show_default=True, help="AWS region (EKS only)")
@click.confirmation_option(prompt="Are you sure you want to delete this cluster?")
def cluster_delete(provider: str, name: str, region: str):
    """Delete a Kubernetes cluster."""
    provider = provider.lower()
    if provider == "kind":
        result = cluster_ops.kind_delete(name=name)
    else:
        result = cluster_ops.eks_delete(name=name, region=region)

    if result["success"]:
        console.print(f"[bold green]✓[/] Cluster [cyan]{name}[/] deleted.")
    else:
        stderr = result.get("stderr", "")
        console.print(f"[bold red]✗[/] Failed to delete cluster: {stderr}")
        sys.exit(1)


@cluster.command("list")
def cluster_list():
    """List all known clusters (KIND and EKS)."""
    all_clusters = cluster_ops.list_all_clusters()

    table = Table(title="Kubernetes Clusters", box=box.ROUNDED)
    table.add_column("Provider", style="bold cyan")
    table.add_column("Name")
    table.add_column("Context")

    current_ctx = cluster_ops.get_current_context()
    all_contexts = cluster_ops.list_contexts()

    for provider, names in all_clusters.items():
        if not names:
            table.add_row(provider.upper(), "[dim]none[/dim]", "")
            continue
        for cname in names:
            # Try to identify the matching kubectl context
            ctx = _find_context(provider, cname, all_contexts)
            is_active = "★ " if ctx == current_ctx else ""
            table.add_row(provider.upper(), cname, f"{is_active}{ctx}")

    console.print(table)

    if current_ctx:
        console.print(f"\n[dim]Current context:[/dim] [cyan]{current_ctx}[/cyan]")


@cluster.command("info")
@click.option("--context", "ctx", default=None, help="Kubectl context (default: current)")
def cluster_info(ctx: str):
    """Show node information for a cluster."""
    active_ctx = ctx or cluster_ops.get_current_context()
    if not active_ctx:
        console.print("[red]No active kubectl context found.[/red]")
        sys.exit(1)

    nodes = cluster_ops.get_nodes(context=ctx)
    if not nodes:
        console.print("[yellow]No nodes found (or kubectl is not configured).[/yellow]")
        return

    table = Table(title=f"Nodes in [cyan]{active_ctx}[/]", box=box.ROUNDED)
    table.add_column("Name")
    table.add_column("Role")
    table.add_column("Ready", justify="center")

    for node in nodes:
        ready_icon = "[green]✓[/green]" if node["ready"] else "[red]✗[/red]"
        table.add_row(node["name"], ", ".join(node["roles"]), ready_icon)

    console.print(table)


@cluster.command("use")
@click.argument("provider", type=click.Choice(["kind", "eks"], case_sensitive=False))
@click.option("--name", default="optik8s", show_default=True)
def cluster_use(provider: str, name: str):
    """Switch kubectl context to the named cluster."""
    provider = provider.lower()
    if provider == "kind":
        result = cluster_ops.kind_set_context(name=name)
        if result["success"]:
            console.print(f"[green]✓[/green] Switched to KIND cluster [cyan]{name}[/]")
        else:
            console.print(f"[red]✗[/red] {result.get('stderr', 'Failed')}")
            sys.exit(1)
    else:
        # eksctl writes kubeconfig automatically on create; use kubectl directly
        import subprocess
        res = subprocess.run(
            ["aws", "eks", "update-kubeconfig", "--name", name],
            capture_output=True, text=True,
        )
        if res.returncode == 0:
            console.print(f"[green]✓[/green] Switched to EKS cluster [cyan]{name}[/]")
        else:
            console.print(f"[red]✗[/red] {res.stderr}")
            sys.exit(1)


# ---------------------------------------------------------------------------
# app group
# ---------------------------------------------------------------------------

@cli.group()
def app():
    """Deploy and manage sample workloads."""


@app.command("list")
def app_list():
    """List all available sample applications."""
    apps = app_ops.list_apps()

    table = Table(title="Available Sample Apps", box=box.ROUNDED)
    table.add_column("Key", style="bold cyan", no_wrap=True)
    table.add_column("Name")
    table.add_column("Category", justify="center")
    table.add_column("Tech")
    table.add_column("Port", justify="right")
    table.add_column("Description")

    for key, info in apps.items():
        table.add_row(
            key,
            info["name"],
            info.get("category", "—"),
            ", ".join(info.get("tech", [])),
            str(info.get("port", "—")),
            info.get("description", ""),
        )

    console.print(table)


@app.command("deploy")
@click.argument("app_name", required=False)
@click.option("--all", "deploy_all", is_flag=True, help="Deploy all sample apps")
@click.option("--context", "ctx", default=None, help="Kubectl context to deploy into")
@click.option("--namespace", default="default", show_default=True)
def app_deploy(app_name: str, deploy_all: bool, ctx: str, namespace: str):
    """Deploy a sample app (or all apps) to the current cluster.

    APP_NAME is the app key as shown by 'app list' (e.g. nodejs-web).
    """
    if not deploy_all and not app_name:
        console.print("[red]Provide an app name or use --all.[/red]")
        sys.exit(1)

    if deploy_all:
        results = app_ops.deploy_all_apps(context=ctx, namespace=namespace)
        _print_app_results(results, action="Deploying")
    else:
        result = app_ops.deploy_app(app_name, context=ctx, namespace=namespace)
        _print_single_result(app_name, result, action="Deployed")


@app.command("remove")
@click.argument("app_name")
@click.option("--context", "ctx", default=None)
@click.option("--namespace", default="default", show_default=True)
@click.confirmation_option(prompt="Remove this app from the cluster?")
def app_remove(app_name: str, ctx: str, namespace: str):
    """Remove a sample app from the cluster."""
    result = app_ops.remove_app(app_name, context=ctx, namespace=namespace)
    _print_single_result(app_name, result, action="Removed")


@app.command("status")
@click.option("--context", "ctx", default=None)
@click.option("--namespace", default="default", show_default=True)
def app_status(ctx: str, namespace: str):
    """Show pod status for all deployed sample apps."""
    statuses = app_ops.get_all_app_statuses(context=ctx, namespace=namespace)

    table = Table(title="App Status", box=box.ROUNDED)
    table.add_column("App", style="bold cyan")
    table.add_column("Deployed", justify="center")
    table.add_column("Pods")

    for app_name, status in statuses.items():
        deployed = "[green]✓[/green]" if status.get("deployed") else "[dim]✗[/dim]"
        pods_info = ", ".join(
            f"{p['name']} ({p['phase']})" for p in status.get("pods", [])
        ) or "[dim]none[/dim]"
        table.add_row(app_name, deployed, pods_info)

    console.print(table)


# ---------------------------------------------------------------------------
# monitoring group
# ---------------------------------------------------------------------------

@cli.group()
def monitoring():
    """Manage the Prometheus + Grafana monitoring stack (via Helm)."""


@monitoring.command("install")
@click.option(
    "--namespace", default="monitoring", show_default=True,
    help="Kubernetes namespace for the monitoring stack",
)
@click.option("--context", "ctx", default=None, help="Kubectl context to deploy into")
@click.option("--values", "values_path", default=None, help="Path to custom Helm values file")
def monitoring_install(namespace: str, ctx: str, values_path: str):
    """Install Prometheus and Grafana using Helm (kube-prometheus-stack).

    Deploys Prometheus, Grafana, node-exporter, and kube-state-metrics into
    the target cluster.  Run ``monitoring status`` to check progress.

    Access Grafana (admin/admin) after pods are Ready:

      kubectl port-forward svc/kube-prometheus-stack-grafana 3000:80 -n monitoring
    """
    if not _check_tool("helm"):
        sys.exit(1)

    console.print(Panel(
        "[bold green]Installing Prometheus + Grafana[/bold green]\n"
        f"Namespace: [cyan]{namespace}[/cyan]  |  Chart: kube-prometheus-stack",
        expand=False,
    ))

    result = monitoring_ops.install_prometheus(
        namespace=namespace,
        context=ctx,
        values_path=values_path,
    )
    if result["success"]:
        console.print("[bold green]✓[/bold green] Monitoring stack installed.")
        console.print(
            "\n[dim]Run[/dim] [cyan]optik8s monitoring status[/cyan] "
            "[dim]to check pod readiness.[/dim]"
        )
        console.print(
            "[dim]Access Grafana (admin / admin):[/dim]\n"
            "  [cyan]kubectl port-forward svc/kube-prometheus-stack-grafana "
            f"3000:80 -n {namespace}[/cyan]"
        )
    else:
        console.print(
            f"[bold red]✗[/bold red] Installation failed: "
            f"{result.get('error', 'see output above')}"
        )
        sys.exit(1)


@monitoring.command("uninstall")
@click.option("--namespace", default="monitoring", show_default=True)
@click.option("--context", "ctx", default=None)
@click.confirmation_option(prompt="Remove the Prometheus + Grafana monitoring stack?")
def monitoring_uninstall(namespace: str, ctx: str):
    """Uninstall the Prometheus + Grafana Helm release."""
    result = monitoring_ops.uninstall_prometheus(namespace=namespace, context=ctx)
    if result["success"]:
        console.print("[bold green]✓[/bold green] Monitoring stack removed.")
    else:
        console.print(
            f"[bold red]✗[/bold red] Uninstall failed: "
            f"{result.get('stderr', result.get('error', ''))}"
        )
        sys.exit(1)


@monitoring.command("status")
@click.option("--namespace", default="monitoring", show_default=True)
@click.option("--context", "ctx", default=None)
def monitoring_status(namespace: str, ctx: str):
    """Show pod status for the Prometheus + Grafana monitoring stack."""
    status = monitoring_ops.get_prometheus_status(namespace=namespace, context=ctx)

    if status.get("error"):
        console.print(f"[yellow]{status['error']}[/yellow]")
        return

    if not status.get("installed"):
        console.print(
            "[yellow]No monitoring pods found in namespace "
            f"[cyan]{namespace}[/cyan].\n"
            "Run [bold]optik8s monitoring install[/bold] first.[/yellow]"
        )
        return

    table = Table(
        title=f"Monitoring Stack – namespace [cyan]{namespace}[/cyan]",
        box=box.ROUNDED,
    )
    table.add_column("Pod", style="bold cyan")
    table.add_column("Phase")
    table.add_column("Ready", justify="center")

    for pod in status["pods"]:
        phase_color = "green" if pod["phase"] == "Running" else "yellow"
        table.add_row(
            pod["name"],
            f"[{phase_color}]{pod['phase']}[/{phase_color}]",
            pod["ready"],
        )

    console.print(table)

    urls = monitoring_ops.get_prometheus_urls(namespace=namespace, context=ctx)
    if urls.get("grafana") or urls.get("prometheus"):
        console.print("\n[bold]Access commands:[/bold]")
        if urls.get("prometheus"):
            console.print(f"  Prometheus → [cyan]{urls['prometheus']}[/cyan]")
        if urls.get("grafana"):
            console.print(
                f"  Grafana    → [cyan]{urls['grafana']}[/cyan]  "
                "[dim](admin / admin)[/dim]"
            )


# ---------------------------------------------------------------------------
# metrics group
# ---------------------------------------------------------------------------

@cli.group()
def metrics():
    """Collect per-pod resource metrics from Prometheus."""


@metrics.command("collect")
@click.option(
    "--prometheus-url",
    default=metrics_ops.DEFAULT_PROMETHEUS_URL,
    show_default=True,
    help="Prometheus base URL (e.g. http://localhost:9090)",
)
@click.option("--namespace", default=None, help="Filter by Kubernetes namespace")
@click.option(
    "--output", "output_path", default=None,
    help="Write JSON output to this file (prints to stdout when omitted)",
)
def metrics_collect(prometheus_url: str, namespace: str, output_path: str):
    """Collect per-pod CPU and memory metrics from Prometheus.

    Connects to the Prometheus HTTP API and returns CPU usage, memory usage,
    and resource requests/limits for every running pod.

    Start a port-forward first if Prometheus is in-cluster:

      kubectl port-forward svc/kube-prometheus-stack-prometheus 9090:9090 -n monitoring
    """
    result = metrics_ops.collect_pod_metrics(
        prometheus_url=prometheus_url,
        namespace=namespace,
    )

    payload = json.dumps(result, indent=2)

    if output_path:
        pathlib.Path(output_path).write_text(payload)
        console.print(
            f"[bold green]✓[/bold green] Metrics written to [cyan]{output_path}[/cyan] "
            f"([dim]{len(result['pods'])} pod(s)[/dim])"
        )
    else:
        console.print(payload)


@metrics.command("format")
@click.option(
    "--prometheus-url",
    default=metrics_ops.DEFAULT_PROMETHEUS_URL,
    show_default=True,
    help="Prometheus base URL (e.g. http://localhost:9090)",
)
@click.option("--namespace", default=None, help="Filter by Kubernetes namespace")
@click.option(
    "--output", "output_path", default=None,
    help="Write JSON output to file (default: print to stdout)",
)
def metrics_format(prometheus_url: str, namespace: str, output_path: str):
    """Format metrics as human-readable JSON for AI analysis.

    Produces a structured JSON document grouped by deployment with:

    \b
      - Deployment name and namespace
      - CPU usage vs requested (millicores + % of request)
      - Memory usage vs requested (MiB + % of request)
      - Summary: total deployments, pods, and namespaces

    Start a port-forward first if Prometheus is in-cluster:

      kubectl port-forward svc/kube-prometheus-stack-prometheus 9090:9090 -n monitoring
    """
    result = metrics_ops.format_metrics_for_analysis(
        prometheus_url=prometheus_url,
        namespace=namespace,
    )

    payload = json.dumps(result, indent=2)

    if output_path:
        pathlib.Path(output_path).write_text(payload)
        console.print(
            f"[bold green]✓[/bold green] Formatted metrics written to "
            f"[cyan]{output_path}[/cyan] "
            f"([dim]{result['summary']['total_deployments']} deployment(s), "
            f"{result['summary']['total_pods']} pod(s)[/dim])"
        )
    else:
        console.print(payload)


@metrics.command("analyze")
@click.option(
    "--prometheus-url",
    default=metrics_ops.DEFAULT_PROMETHEUS_URL,
    show_default=True,
    help="Prometheus base URL (e.g. http://localhost:9090)",
)
@click.option("--namespace", default=None, help="Filter by Kubernetes namespace")
@click.option(
    "--cpu-threshold", default=rules_ops.CPU_OVERPROVISION_THRESHOLD_PCT,
    show_default=True, type=float,
    help="Flag pods whose CPU usage is below this % of requested as overprovisioned",
)
@click.option(
    "--memory-threshold", default=rules_ops.MEMORY_OVERPROVISION_THRESHOLD_PCT,
    show_default=True, type=float,
    help="Flag pods whose memory usage is below this % of requested as overprovisioned",
)
@click.option(
    "--output", "output_path", default=None,
    help="Write JSON output to file (default: print recommendations to stdout)",
)
@click.option(
    "--json", "as_json", is_flag=True, default=False,
    help="Output raw JSON instead of a human-readable table",
)
@click.option(
    "--input", "input_path", default=None,
    help=(
        "Read pod metrics from a JSON file instead of querying Prometheus. "
        "Accepts either a flat list of pods "
        '(e.g. [{"name":"x","cpu_requested":500,"cpu_used":90,...}]) '
        "or the full formatted-metrics document."
    ),
)
def metrics_analyze(
    prometheus_url: str,
    namespace: str,
    cpu_threshold: float,
    memory_threshold: float,
    output_path: str,
    as_json: bool,
    input_path: str,
):
    """Detect overprovisioned pods and estimate cost savings.

    Collects metrics from Prometheus, applies threshold rules, and prints
    actionable recommendations with estimated monthly cost savings.

    \b
    Default thresholds (configurable via options):
      CPU usage    < 30% of requested → overprovisioned
      Memory usage < 30% of requested → overprovisioned

    Start a port-forward first if Prometheus is in-cluster:

      kubectl port-forward svc/kube-prometheus-stack-prometheus 9090:9090 -n monitoring

    You can also analyse a local JSON file instead of querying Prometheus:

      optik8s metrics analyze --input pods.json
    """
    if input_path:
        try:
            raw = json.loads(pathlib.Path(input_path).read_text())
        except FileNotFoundError:
            console.print(f"[red]File not found:[/red] {input_path}")
            sys.exit(1)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid JSON in {input_path}:[/red] {exc}")
            sys.exit(1)
        try:
            formatted = _simple_pods_to_formatted_metrics(raw) if isinstance(raw, list) else raw
        except (TypeError, ValueError) as exc:
            console.print(f"[red]Failed to parse pod data:[/red] {exc}")
            sys.exit(1)
    else:
        formatted = metrics_ops.format_metrics_for_analysis(
            prometheus_url=prometheus_url,
            namespace=namespace,
        )

    result = rules_ops.analyze(
        formatted,
        cpu_threshold_pct=cpu_threshold,
        memory_threshold_pct=memory_threshold,
    )

    if output_path:
        pathlib.Path(output_path).write_text(json.dumps(result, indent=2))
        console.print(
            f"[bold green]✓[/bold green] Analysis written to [cyan]{output_path}[/cyan] "
            f"([dim]{result['summary']['overprovisioned_deployments']} overprovisioned "
            f"deployment(s)[/dim])"
        )
        return

    if as_json:
        click.echo(json.dumps(result, indent=2))
        return

    # Human-readable output
    summary = result["summary"]
    recs = result["recommendations"]

    console.print(Panel(
        f"[bold]Overprovision Analysis[/bold]\n"
        f"Thresholds – CPU: [cyan]{result['thresholds']['cpu_pct']:.0f}%[/cyan]  "
        f"Memory: [cyan]{result['thresholds']['memory_pct']:.0f}%[/cyan]",
        expand=False,
    ))

    table = Table(title="Summary", box=box.ROUNDED)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")
    table.add_row("Total deployments", str(summary["total_deployments"]))
    table.add_row("Total pods", str(summary["total_pods"]))
    table.add_row(
        "Overprovisioned deployments",
        f"[yellow]{summary['overprovisioned_deployments']}[/yellow]",
    )
    table.add_row(
        "Overprovisioned pods",
        f"[yellow]{summary['overprovisioned_pods']}[/yellow]",
    )
    table.add_row(
        "Est. monthly savings",
        f"[green]${summary['estimated_monthly_savings_usd']:.2f}[/green]",
    )
    console.print(table)

    _print_pods_table(formatted, result)

    if not recs:
        console.print("[green]✓ No overprovisioned deployments detected.[/green]")
        return

    for rec in recs:
        sev_color = {"high": "red", "medium": "yellow", "low": "cyan"}.get(
            rec["severity"], "white"
        )
        console.print(Panel(
            rec["message"],
            title=(
                f"[{sev_color}]{rec['severity'].upper()}[/{sev_color}]  "
                f"{rec['deployment']} / {rec['namespace']}"
            ),
            expand=False,
        ))


@metrics.command("summarize")
@click.option(
    "--prometheus-url",
    default=metrics_ops.DEFAULT_PROMETHEUS_URL,
    show_default=True,
    help="Prometheus base URL (e.g. http://localhost:9090)",
)
@click.option("--namespace", default=None, help="Filter by Kubernetes namespace")
@click.option(
    "--cpu-threshold", default=rules_ops.CPU_OVERPROVISION_THRESHOLD_PCT,
    show_default=True, type=float,
    help="Flag pods whose CPU usage is below this % of requested as overprovisioned",
)
@click.option(
    "--memory-threshold", default=rules_ops.MEMORY_OVERPROVISION_THRESHOLD_PCT,
    show_default=True, type=float,
    help="Flag pods whose memory usage is below this % of requested as overprovisioned",
)
@click.option(
    "--input", "input_path", default=None,
    help=(
        "Read pod metrics from a JSON file instead of querying Prometheus. "
        "Accepts either a flat list of pods or the full formatted-metrics document."
    ),
)
@click.option(
    "--api-key", "api_key", default=None, envvar="OPENAI_API_KEY",
    help="OpenAI API key (defaults to OPENAI_API_KEY env var)",
)
@click.option(
    "--model", default=ai_ops.DEFAULT_MODEL, show_default=True,
    help="OpenAI model to use for summarization",
)
@click.option(
    "--json", "as_json", is_flag=True, default=False,
    help="Output raw JSON instead of formatted panels",
)
def metrics_summarize(
    prometheus_url: str,
    namespace: str,
    cpu_threshold: float,
    memory_threshold: float,
    input_path: str,
    api_key: str,
    model: str,
    as_json: bool,
):
    """Generate AI-powered plain-English summaries of overprovisioned deployments.

    Runs the rules engine to detect overprovisioned pods and then sends the
    results to the OpenAI API to generate human-readable, actionable explanations.

    \b
    Requires OPENAI_API_KEY to be set (or passed via --api-key).

    Examples:
      optik8s metrics summarize
      optik8s metrics summarize --input pods.json
      optik8s metrics summarize --json
    """
    if input_path:
        try:
            raw = json.loads(pathlib.Path(input_path).read_text())
        except FileNotFoundError:
            console.print(f"[red]File not found:[/red] {input_path}")
            sys.exit(1)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid JSON in {input_path}:[/red] {exc}")
            sys.exit(1)
        try:
            formatted = _simple_pods_to_formatted_metrics(raw) if isinstance(raw, list) else raw
        except (TypeError, ValueError) as exc:
            console.print(f"[red]Failed to parse pod data:[/red] {exc}")
            sys.exit(1)
    else:
        formatted = metrics_ops.format_metrics_for_analysis(
            prometheus_url=prometheus_url,
            namespace=namespace,
        )

    analysis = rules_ops.analyze(
        formatted,
        cpu_threshold_pct=cpu_threshold,
        memory_threshold_pct=memory_threshold,
    )

    ai_result = ai_ops.summarize_recommendations(
        analysis,
        api_key=api_key,
        model=model,
    )

    if as_json:
        click.echo(json.dumps({**analysis, "ai_summary": ai_result}, indent=2))
        return

    # Human-readable output
    summary = analysis["summary"]
    console.print(Panel(
        f"[bold]AI Overprovision Summary[/bold]\n"
        f"Model: [cyan]{ai_result['model']}[/cyan]  "
        f"Deployments analysed: [cyan]{summary['total_deployments']}[/cyan]  "
        f"Overprovisioned: [yellow]{summary['overprovisioned_deployments']}[/yellow]  "
        f"Est. savings: [green]${summary['estimated_monthly_savings_usd']:.2f}/mo[/green]",
        expand=False,
    ))

    if ai_result.get("error"):
        console.print(
            f"[yellow]⚠ AI summarization unavailable:[/yellow] {ai_result['error']}\n"
            "[dim]Falling back to rule-based messages below.[/dim]"
        )
        # Fall back to rule-based messages
        recs = analysis.get("recommendations", [])
        if not recs:
            console.print("[green]✓ No overprovisioned deployments detected.[/green]")
            return
        for rec in recs:
            sev_color = {"high": "red", "medium": "yellow", "low": "cyan"}.get(
                rec["severity"], "white"
            )
            console.print(Panel(
                rec["message"],
                title=(
                    f"[{sev_color}]{rec['severity'].upper()}[/{sev_color}]  "
                    f"{rec['deployment']} / {rec['namespace']}"
                ),
                expand=False,
            ))
        return

    summaries = ai_result.get("summaries", {})
    if not summaries:
        console.print("[green]✓ No overprovisioned deployments detected.[/green]")
        return

    for rec in analysis.get("recommendations", []):
        deploy = rec["deployment"]
        ns = rec["namespace"]
        sev_color = {"high": "red", "medium": "yellow", "low": "cyan"}.get(
            rec["severity"], "white"
        )
        explanation = summaries.get(deploy, rec["message"])
        console.print(Panel(
            explanation,
            title=(
                f"[{sev_color}]{rec['severity'].upper()}[/{sev_color}]  "
                f"{deploy} / {ns}  "
                f"[dim](~${rec['estimated_monthly_savings_usd']:.2f}/mo)[/dim]"
            ),
            expand=False,
        ))


# ---------------------------------------------------------------------------
# simulate group
# ---------------------------------------------------------------------------

@cli.group()
def simulate():
    """Generate synthetic workload scenarios to test the analysis engine."""


@simulate.command("list")
def simulate_list():
    """List available architecture profiles and predefined scenarios."""
    archs = simulator_ops.list_architectures()
    scenarios = simulator_ops.list_scenarios()

    arch_table = Table(title="Architecture Profiles", box=box.ROUNDED)
    arch_table.add_column("Key", style="bold cyan", no_wrap=True)
    arch_table.add_column("Name")
    arch_table.add_column("CPU Request (m)", justify="right")
    arch_table.add_column("Memory Request (MiB)", justify="right")
    arch_table.add_column("Description")

    for key, info in archs.items():
        cpu_lo, cpu_hi = info["typical_cpu_request_millicores"]
        mem_lo, mem_hi = info["typical_memory_request_mib"]
        arch_table.add_row(
            key,
            info["name"],
            f"{cpu_lo}–{cpu_hi}",
            f"{mem_lo}–{mem_hi}",
            info["description"],
        )

    console.print(arch_table)
    console.print()

    sc_table = Table(title="Predefined Scenarios", box=box.ROUNDED)
    sc_table.add_column("Name", style="bold cyan", no_wrap=True)
    sc_table.add_column("Pods", justify="right")
    sc_table.add_column("Description")

    for name, info in scenarios.items():
        sc_table.add_row(name, str(info["total_pods"]), info["description"])

    console.print(sc_table)
    console.print(
        "\n[dim]Run[/dim] [cyan]optik8s simulate generate --help[/cyan] "
        "[dim]or[/dim] [cyan]optik8s simulate run --help[/cyan] "
        "[dim]for usage details.[/dim]"
    )


@simulate.command("generate")
@click.option(
    "--architecture", "architectures", default=None, multiple=True,
    type=click.Choice(list(simulator_ops.ARCHITECTURES.keys()), case_sensitive=False),
    help=(
        "Architecture type(s) to include.  Repeat the flag for multiple types. "
        "Defaults to all available types."
    ),
)
@click.option(
    "--pods", "num_pods", default=8, show_default=True, type=int,
    help="Number of pods to generate.",
)
@click.option(
    "--load",
    default="mixed", show_default=True,
    type=click.Choice(list(simulator_ops.LOAD_PROFILES), case_sensitive=False),
    help=(
        "Load profile: idle (typically <15 %% of request), normal (20–65 %%), "
        "high (>=40 %%), or mixed (each pod picks randomly). "
        "Exact ratios vary by architecture type."
    ),
)
@click.option(
    "--seed", default=None, type=int,
    help="Random seed for reproducible scenarios.",
)
@click.option(
    "--output", "output_path", default=None,
    help="Write JSON output to this file (default: print to stdout).",
)
def simulate_generate(
    architectures: tuple,
    num_pods: int,
    load: str,
    seed: int,
    output_path: str,
):
    """Generate a synthetic pod scenario and output it as JSON.

    The output is a flat JSON array of pod objects compatible with:

      optik8s metrics analyze --input <file>

    \b
    Examples:
      optik8s simulate generate --load idle --pods 10
      optik8s simulate generate --architecture frontend --architecture backend-api --seed 42
      optik8s simulate generate --load mixed --output scenario.json
    """
    arch_list = list(architectures) if architectures else None
    try:
        pods = simulator_ops.generate_scenario(
            architectures=arch_list,
            num_pods=num_pods,
            load=load,
            seed=seed,
        )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    payload = json.dumps(pods, indent=2)

    if output_path:
        pathlib.Path(output_path).write_text(payload)
        console.print(
            f"[bold green]✓[/bold green] Scenario with [cyan]{len(pods)}[/cyan] pod(s) "
            f"written to [cyan]{output_path}[/cyan]"
        )
    else:
        click.echo(payload)


@simulate.command("run")
@click.option(
    "--scenario",
    default=None,
    type=click.Choice(list(simulator_ops.SCENARIOS.keys()), case_sensitive=False),
    help="Use a predefined named scenario.",
)
@click.option(
    "--architecture", "architectures", default=None, multiple=True,
    type=click.Choice(list(simulator_ops.ARCHITECTURES.keys()), case_sensitive=False),
    help=(
        "Architecture type(s) for an ad-hoc scenario (ignored when --scenario is used). "
        "Repeat the flag for multiple types."
    ),
)
@click.option(
    "--pods", "num_pods", default=8, show_default=True, type=int,
    help="Number of pods (ad-hoc mode only, ignored when --scenario is used).",
)
@click.option(
    "--load",
    default="mixed", show_default=True,
    type=click.Choice(list(simulator_ops.LOAD_PROFILES), case_sensitive=False),
    help="Load profile (ad-hoc mode only).",
)
@click.option(
    "--seed", default=None, type=int,
    help="Random seed for reproducible scenarios.",
)
@click.option(
    "--cpu-threshold", default=rules_ops.CPU_OVERPROVISION_THRESHOLD_PCT,
    show_default=True, type=float,
    help="Flag pods whose CPU usage is below this %% of requested as overprovisioned.",
)
@click.option(
    "--memory-threshold", default=rules_ops.MEMORY_OVERPROVISION_THRESHOLD_PCT,
    show_default=True, type=float,
    help="Flag pods whose memory usage is below this %% of requested as overprovisioned.",
)
@click.option(
    "--json", "as_json", is_flag=True, default=False,
    help="Output raw JSON instead of a human-readable table.",
)
def simulate_run(
    scenario: str,
    architectures: tuple,
    num_pods: int,
    load: str,
    seed: int,
    cpu_threshold: float,
    memory_threshold: float,
    as_json: bool,
):
    """Generate a synthetic workload scenario and run the analysis engine.

    Use --scenario for a predefined architecture pattern, or build a custom
    scenario with --architecture / --pods / --load.

    \b
    Examples:
      optik8s simulate run --scenario ecommerce
      optik8s simulate run --scenario overprovisioned --seed 42
      optik8s simulate run --architecture frontend --architecture backend-api --load idle
      optik8s simulate run --pods 20 --load mixed --json
    """
    arch_list = list(architectures) if architectures else None
    try:
        result = simulator_ops.run_scenario(
            scenario_name=scenario,
            architectures=arch_list,
            num_pods=num_pods,
            load=load,
            seed=seed,
            cpu_threshold_pct=cpu_threshold,
            memory_threshold_pct=memory_threshold,
        )
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        sys.exit(1)

    pods = result.pop("scenario", [])

    if as_json:
        result["scenario"] = pods
        click.echo(json.dumps(result, indent=2))
        return

    # ── Human-readable output ──────────────────────────────────────────────
    scenario_label = scenario if scenario else "ad-hoc"
    load_label = load if not scenario else "scenario"
    summary = result["summary"]

    console.print(Panel(
        f"[bold]Simulated Workload Analysis[/bold]\n"
        f"Scenario: [cyan]{scenario_label}[/cyan]  "
        f"Load: [cyan]{load_label}[/cyan]  "
        f"Pods: [cyan]{len(pods)}[/cyan]  "
        f"Seed: [cyan]{seed if seed is not None else 'random'}[/cyan]",
        expand=False,
    ))

    # Summary table
    sm_table = Table(title="Summary", box=box.ROUNDED)
    sm_table.add_column("Metric", style="bold")
    sm_table.add_column("Value", justify="right")
    sm_table.add_row("Total pods", str(summary["total_pods"]))
    sm_table.add_row(
        "Overprovisioned pods",
        f"[yellow]{summary['overprovisioned_pods']}[/yellow]",
    )
    sm_table.add_row(
        "Overprovisioned deployments",
        f"[yellow]{summary['overprovisioned_deployments']}[/yellow]",
    )
    sm_table.add_row(
        "Est. monthly savings",
        f"[green]${summary['estimated_monthly_savings_usd']:.2f}[/green]",
    )
    console.print(sm_table)

    # Per-pod scenario table
    pod_table = Table(title="Generated Pods", box=box.ROUNDED, show_lines=True)
    pod_table.add_column("Pod", style="bold", no_wrap=True)
    pod_table.add_column("Architecture", style="dim")
    pod_table.add_column("Load", justify="center")
    pod_table.add_column("CPU Req (m)", justify="right")
    pod_table.add_column("CPU Used (m)", justify="right")
    pod_table.add_column("CPU %", justify="right")
    pod_table.add_column("Mem Req (MiB)", justify="right")
    pod_table.add_column("Mem Used (MiB)", justify="right")
    pod_table.add_column("Mem %", justify="right")

    for pod in pods:
        cpu_req = pod.get("cpu_requested", 0)
        cpu_used = pod.get("cpu_used", 0)
        mem_req = pod.get("memory_requested", 0)
        mem_used = pod.get("memory_used", 0)
        cpu_pct = round((cpu_used / cpu_req) * 100, 1) if cpu_req > 0 else 0.0
        mem_pct = round((mem_used / mem_req) * 100, 1) if mem_req > 0 else 0.0

        load_profile = pod.get("load_profile", "")
        load_color = {"idle": "dim", "normal": "yellow", "high": "green"}.get(
            load_profile, "white"
        )
        cpu_color = "red" if cpu_pct < cpu_threshold else "green"
        mem_color = "red" if mem_pct < memory_threshold else "green"

        pod_table.add_row(
            pod["name"],
            pod.get("architecture", ""),
            f"[{load_color}]{load_profile}[/{load_color}]",
            f"{cpu_req:.0f}",
            f"{cpu_used:.1f}",
            f"[{cpu_color}]{cpu_pct:.1f}%[/{cpu_color}]",
            f"{mem_req:.0f}",
            f"{mem_used:.1f}",
            f"[{mem_color}]{mem_pct:.1f}%[/{mem_color}]",
        )

    console.print(pod_table)

    # Recommendations
    recs = result.get("recommendations", [])
    if not recs:
        console.print("[green]✓ No overprovisioned deployments detected.[/green]")
        return

    for rec in recs:
        sev_color = {"high": "red", "medium": "yellow", "low": "cyan"}.get(
            rec["severity"], "white"
        )
        console.print(Panel(
            rec["message"],
            title=(
                f"[{sev_color}]{rec['severity'].upper()}[/{sev_color}]  "
                f"{rec['deployment']} / {rec['namespace']}"
            ),
            expand=False,
        ))


# ---------------------------------------------------------------------------
# ui command
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default="127.0.0.1", show_default=True)
@click.option("--port", default=5000, show_default=True)
@click.option("--debug", is_flag=True, default=False)
def ui(host: str, port: int, debug: bool):
    """Launch the OptiK8s web dashboard."""
    from optik8s.ui.app import create_app

    console.print(Panel(
        f"[bold green]Starting OptiK8s UI[/bold green]\n"
        f"Open [link=http://{host}:{port}]http://{host}:{port}[/link] in your browser.",
        expand=False,
    ))
    flask_app = create_app()
    flask_app.run(host=host, port=port, debug=debug)


# ---------------------------------------------------------------------------
# tools command
# ---------------------------------------------------------------------------

@cli.command("tools")
def tools():
    """Show versions of required CLI tools."""
    versions = cluster_ops.tool_versions()

    table = Table(title="CLI Tool Versions", box=box.ROUNDED)
    table.add_column("Tool", style="bold cyan")
    table.add_column("Version / Status")

    for tool, version in versions.items():
        color = "dim" if version == "not found" else "green"
        table.add_row(tool, f"[{color}]{version}[/{color}]")

    console.print(table)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _simple_pods_to_formatted_metrics(pods: list) -> dict:
    """Convert a simple flat pod list to the format expected by rules_ops.analyze().

    Accepts entries with the shape::

        {
            "name": str,
            "cpu_requested": float,   # millicores
            "cpu_used": float,        # millicores
            "memory_requested": float, # MiB
            "memory_used": float,     # MiB
        }

    Each pod is treated as its own single-pod deployment in the ``default``
    namespace so the rules engine can process it without changes.

    Raises :exc:`ValueError` if a required numeric field contains a non-numeric
    value.
    """
    deployments = []
    for idx, p in enumerate(pods):
        name = p.get("name", f"pod-{idx}")
        try:
            cpu_req = float(p.get("cpu_requested") or 0)
            cpu_used = float(p.get("cpu_used") or 0)
            mem_req = float(p.get("memory_requested") or 0)
            mem_used = float(p.get("memory_used") or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Pod '{name}' (index {idx}) contains a non-numeric resource value: {exc}"
            ) from exc

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
        "collected_at": "",
        "prometheus_url": "",
        "summary": {
            "total_deployments": len(deployments),
            "total_pods": len(pods),
            "namespaces": ["default"],
        },
        "deployments": deployments,
    }


def _fmt_mc(val) -> str:
    """Format a millicores value for display (e.g. '500m') or '—' if None."""
    return f"{val:.0f}m" if val is not None else "—"


def _fmt_mib(val) -> str:
    """Format a MiB value for display (e.g. '512 MiB') or '—' if None."""
    return f"{val:.0f} MiB" if val is not None else "—"


def _fmt_pct(val, color: str) -> str:
    """Format a percentage value with Rich colour markup or '—' if None."""
    if val is None:
        return "—"
    return f"[{color}]{val:.1f}%[/{color}]"


def _print_pods_table(formatted_metrics: dict, result: dict) -> None:
    """Render a colour-coded per-pod resource table from an analyze() result.

    Shows ALL pods – healthy pods appear in green; overprovisioned pods appear
    in yellow (one resource overprovisioned) or red (both overprovisioned).
    """
    # Build a lookup of analyzed pod data keyed by pod name from recommendations
    analyzed: dict[str, dict] = {}
    pod_deployment: dict[str, tuple[str, str]] = {}  # name → (deployment, namespace)
    for rec in result.get("recommendations", []):
        deploy = rec["deployment"]
        ns = rec["namespace"]
        for pod in rec.get("pods", []):
            analyzed[pod["name"]] = pod
            pod_deployment[pod["name"]] = (deploy, ns)

    # Collect all pods from formatted metrics (including healthy ones)
    all_rows: list[tuple[str, str, dict, dict | None]] = []
    for dep in formatted_metrics.get("deployments", []):
        deploy = dep.get("deployment", "")
        ns = dep.get("namespace", "")
        for raw_pod in dep.get("pods", []):
            pname = raw_pod["name"]
            analyzed_pod = analyzed.get(pname)
            all_rows.append((deploy, ns, raw_pod, analyzed_pod))

    if not all_rows:
        return

    table = Table(title="Pod Resource Analysis", box=box.ROUNDED, show_lines=True)
    table.add_column("Pod", style="bold", no_wrap=True)
    table.add_column("Namespace", style="dim", no_wrap=True)
    table.add_column("CPU Used / Req", justify="right")
    table.add_column("CPU %", justify="right")
    table.add_column("CPU Suggest", justify="right")
    table.add_column("Mem Used / Req", justify="right")
    table.add_column("Mem %", justify="right")
    table.add_column("Mem Suggest", justify="right")
    table.add_column("Savings/mo", justify="right")
    table.add_column("Status", justify="center")

    for deploy, ns, raw_pod, analyzed_pod in all_rows:
        # Use analyzed data when available (overprovisioned pods), else raw data
        if analyzed_pod:
            cpu = analyzed_pod.get("cpu", {})
            mem = analyzed_pod.get("memory", {})
            savings = analyzed_pod.get("estimated_monthly_savings_usd", 0.0)
        else:
            cpu = raw_pod.get("cpu", {})
            mem = raw_pod.get("memory", {})
            savings = 0.0

        cpu_used = cpu.get("usage_millicores")
        cpu_req = cpu.get("requested_millicores")
        cpu_pct = cpu.get("usage_pct_of_requested")
        cpu_suggest = cpu.get("recommended_request_millicores")
        cpu_over = cpu.get("overprovisioned", False)

        mem_used = mem.get("usage_mib")
        mem_req = mem.get("requested_mib")
        mem_pct = mem.get("usage_pct_of_requested")
        mem_suggest = mem.get("recommended_request_mib")
        mem_over = mem.get("overprovisioned", False)

        # Determine severity / colour
        if cpu_over and mem_over:
            row_color = "red"
            status = "[bold red]⚠ HIGH[/bold red]"
        elif cpu_over or mem_over:
            row_color = "yellow"
            status = "[bold yellow]⚠ WARN[/bold yellow]"
        else:
            row_color = "green"
            status = "[bold green]✓ OK[/bold green]"

        cpu_used_req = (
            f"{_fmt_mc(cpu_used)} / {_fmt_mc(cpu_req)}"
            if cpu_req is not None else "—"
        )
        mem_used_req = (
            f"{_fmt_mib(mem_used)} / {_fmt_mib(mem_req)}"
            if mem_req is not None else "—"
        )

        cpu_suggest_str = (
            f"[{row_color}]{_fmt_mc(cpu_suggest)}[/{row_color}]"
            if cpu_over else "[green]—[/green]"
        )
        mem_suggest_str = (
            f"[{row_color}]{_fmt_mib(mem_suggest)}[/{row_color}]"
            if mem_over else "[green]—[/green]"
        )

        savings_str = (
            f"[green]$0.00[/green]"
            if savings == 0.0
            else f"[{row_color}]${savings:.2f}[/{row_color}]"
        )

        table.add_row(
            raw_pod["name"],
            ns,
            cpu_used_req,
            _fmt_pct(cpu_pct, row_color),
            cpu_suggest_str,
            mem_used_req,
            _fmt_pct(mem_pct, row_color),
            mem_suggest_str,
            savings_str,
            status,
        )

    console.print(table)


def _check_tool(name: str) -> bool:
    """Warn and return False if *name* is not on PATH."""
    import shutil as _shutil
    if _shutil.which(name) is None:
        console.print(
            f"[red]'{name}' not found on PATH.[/red] "
            f"Please install it before running this command."
        )
        return False
    return True


def _find_context(provider: str, cluster_name: str, contexts: list[str]) -> str:
    """Try to match a cluster to a kubectl context name."""
    for ctx in contexts:
        if cluster_name in ctx:
            return ctx
    return ""


def _print_single_result(app_name: str, result: dict, action: str = "Action"):
    if result.get("success"):
        console.print(f"[bold green]✓[/bold green] {action} [cyan]{app_name}[/cyan]")
        if result.get("stdout"):
            console.print(result["stdout"].strip(), style="dim")
    else:
        console.print(f"[bold red]✗[/bold red] Failed: {result.get('error') or result.get('stderr', '')}")


def _print_app_results(results: dict[str, dict], action: str = "Action"):
    table = Table(title=f"{action} Results", box=box.ROUNDED)
    table.add_column("App", style="bold cyan")
    table.add_column("Status", justify="center")
    table.add_column("Output")

    for app_name, result in results.items():
        if result.get("success"):
            status = "[green]✓[/green]"
            output = (result.get("stdout") or "").strip().splitlines()
            output_str = "\n".join(output[:3])  # cap to 3 lines
        else:
            status = "[red]✗[/red]"
            output_str = result.get("error") or result.get("stderr", "")
        table.add_row(app_name, status, output_str)

    console.print(table)
