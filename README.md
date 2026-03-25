# OptiK8s

> Spin up a local **KIND** or cloud **AWS EKS** Kubernetes cluster, deploy sample workloads, collect resource metrics, detect overprovisioned pods, and get AI-powered cost-saving recommendations – all from a single CLI or a web dashboard.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start (no Kubernetes required)](#quick-start-no-kubernetes-required)
- [CLI Usage](#cli-usage)
  - [Cluster management](#cluster-management)
  - [App deployment](#app-deployment)
  - [Metrics & Analysis](#metrics--analysis)
  - [Workload Simulator](#workload-simulator)
  - [AI-Powered Summaries](#ai-powered-summaries)
  - [Web UI](#web-ui)
  - [Tool status](#tool-status)
- [Sample Datasets](#sample-datasets)
- [Web Dashboard](#web-dashboard)
- [Cluster Configurations](#cluster-configurations)
  - [KIND (local)](#kind-local)
  - [EKS (AWS)](#eks-aws)
- [Sample Apps](#sample-apps)
- [Development](#development)
- [Makefile shortcuts](#makefile-shortcuts)

---

## Overview

`optik8s` lets you:

| Goal | How |
|---|---|
| Spin up a local 3-node KIND cluster | `optik8s cluster create kind` |
| Spin up a managed AWS EKS cluster | `optik8s cluster create eks` |
| Deploy sample workloads (Node.js, Python, Java, Postgres, Redis) | `optik8s app deploy <name>` |
| Collect per-pod CPU & memory metrics from Prometheus | `optik8s metrics collect` |
| Detect overprovisioned pods and estimate savings | `optik8s metrics analyze` |
| Generate AI plain-English cost recommendations | `optik8s metrics summarize` |
| Simulate workloads offline (no cluster needed) | `optik8s simulate run --scenario ecommerce` |
| Manage everything from a browser | `optik8s ui` |

---

## Architecture

```
optik8s/
├── optik8s/           # Python package
│   ├── cli/main.py         # Click CLI entry-point
│   ├── ui/                 # Flask web dashboard
│   │   ├── app.py          # Flask routes & API
│   │   ├── templates/      # Jinja2 HTML templates
│   │   └── static/         # CSS + JS
│   └── core/
│       ├── cluster.py      # KIND / EKS operations (via kind, eksctl, kubectl)
│       ├── apps.py         # App deployment operations (via kubectl)
│       ├── metrics.py      # Prometheus metrics collector
│       ├── rules.py        # Overprovision rules engine
│       ├── ai.py           # OpenAI summarization layer
│       └── simulator.py    # Synthetic workload generator
├── clusters/
│   ├── kind/cluster-config.yaml   # 3-node KIND config with ingress ports
│   └── eks/cluster-config.yaml    # eksctl managed node-group config
├── apps/                   # Sample applications
│   ├── nodejs-web/k8s/     # Node.js Express (Deployment + Service)
│   ├── python-api/k8s/     # Python FastAPI  (Deployment + Service)
│   ├── java-spring/k8s/    # Spring PetClinic (Deployment + Service)
│   ├── postgres-db/k8s/    # PostgreSQL 16   (Deployment + Service)
│   └── redis-cache/k8s/    # Redis 7         (Deployment + Service)
├── samples/                # Pre-generated pod datasets for offline testing
│   ├── ecommerce.json      # E-commerce platform (mostly idle, overprovisioned)
│   ├── overprovisioned.json # Worst-case: all pods idle
│   ├── peak-load.json      # All pods under high load (no flags expected)
│   ├── saas-platform.json  # SaaS: mix of idle and normal loads
│   ├── data-pipeline.json  # Data/ML pipeline: idle between processing windows
│   └── microservices.json  # Microservices mesh: normal + high load mix
├── pods.json               # Minimal 3-pod example for quick-start demos
├── tests/                  # pytest test suite
├── Makefile
└── pyproject.toml
```

---

## Prerequisites

| Tool | Purpose | Install |
|---|---|---|
| **Python ≥ 3.9** | Run the CLI & UI | [python.org](https://www.python.org/) |
| **Docker** | Required by KIND | [docs.docker.com](https://docs.docker.com/get-docker/) |
| **kind** | Local K8s clusters | [kind.sigs.k8s.io](https://kind.sigs.k8s.io/docs/user/quick-start/) |
| **kubectl** | Apply K8s manifests | [kubernetes.io](https://kubernetes.io/docs/tasks/tools/) |
| **eksctl** | AWS EKS clusters *(optional)* | [eksctl.io](https://eksctl.io/installation/) |
| **AWS CLI** | AWS credentials *(optional)* | [aws.amazon.com](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) |

Run `optik8s tools` to check which tools are detected.

---

## Installation

```bash
# Clone the repo
git clone https://github.com/47cloud/whatwillaido.git
cd optik8s

# Install (creates the `optik8s` command)
pip install -e .

# Verify
optik8s --version
optik8s --help
```

> **No Kubernetes needed to get started.** The metrics analysis and workload simulator work entirely offline using local JSON files. You only need kind/kubectl/eksctl when creating real clusters.

---

## Quick Start (no Kubernetes required)

Try the analysis engine immediately using the bundled sample datasets:

```bash
# Analyse a pre-generated overprovisioned workload
optik8s metrics analyze --input samples/overprovisioned.json

# Simulate an e-commerce platform and run the analysis in one step
optik8s simulate run --scenario ecommerce

# Generate a custom scenario (10 pods, idle load) and save to a file
optik8s simulate generate --pods 10 --load idle --output /tmp/scenario.json

# Analyse the generated file
optik8s metrics analyze --input /tmp/scenario.json
```

---

## CLI Usage

### Cluster management

```bash
# Create a local 3-node KIND cluster (uses clusters/kind/cluster-config.yaml)
optik8s cluster create kind

# Create a KIND cluster with a custom name
optik8s cluster create kind --name my-dev

# Create a managed EKS cluster (uses clusters/eks/cluster-config.yaml)
optik8s cluster create eks

# Create EKS in a different region
optik8s cluster create eks --name prod --region eu-west-1

# List all clusters (KIND + EKS)
optik8s cluster list

# Show nodes for the current context
optik8s cluster info

# Switch kubectl context to a cluster
optik8s cluster use kind --name optik8s
optik8s cluster use eks  --name optik8s

# Delete a cluster
optik8s cluster delete kind --name optik8s
optik8s cluster delete eks  --name optik8s --region us-east-1
```

### App deployment

```bash
# List available sample apps
optik8s app list

# Deploy a single app to the current cluster
optik8s app deploy nodejs-web
optik8s app deploy python-api
optik8s app deploy java-spring
optik8s app deploy postgres-db
optik8s app deploy redis-cache

# Deploy all apps at once
optik8s app deploy --all

# Deploy into a specific cluster context or namespace
optik8s app deploy nodejs-web --context kind-optik8s --namespace staging

# Check pod status for all apps
optik8s app status

# Remove an app
optik8s app remove nodejs-web
```

### Metrics & Analysis

Collect per-pod CPU and memory metrics from Prometheus, detect overprovisioned pods, and estimate monthly cost savings.

```bash
# Port-forward Prometheus first (if running in-cluster)
kubectl port-forward svc/kube-prometheus-stack-prometheus 9090:9090 -n monitoring

# Collect raw pod metrics from Prometheus and print as JSON
optik8s metrics collect

# Save metrics to a file
optik8s metrics collect --output /tmp/metrics.json

# Detect overprovisioned pods and print a recommendations table
optik8s metrics analyze

# Analyse a local JSON file (no Prometheus required)
optik8s metrics analyze --input pods.json
optik8s metrics analyze --input samples/ecommerce.json

# Save the analysis result as JSON
optik8s metrics analyze --json --input samples/overprovisioned.json

# Adjust overprovision thresholds (default: CPU < 30%, Memory < 30%)
optik8s metrics analyze --cpu-threshold 20 --memory-threshold 25 --input pods.json

# Format metrics as a structured document for further processing
optik8s metrics format --output /tmp/formatted.json
```

### Workload Simulator

Generate synthetic pod metrics to test the analysis engine without a live Kubernetes cluster.

```bash
# List available architecture profiles and predefined scenarios
optik8s simulate list

# Run a predefined scenario and show analysis results
optik8s simulate run --scenario ecommerce
optik8s simulate run --scenario overprovisioned
optik8s simulate run --scenario peak-load    # should show 0 overprovisioned
optik8s simulate run --scenario saas-platform
optik8s simulate run --scenario data-pipeline
optik8s simulate run --scenario microservices

# Use a fixed seed for reproducible results
optik8s simulate run --scenario ecommerce --seed 42

# Build a custom ad-hoc scenario
optik8s simulate run --architecture frontend --architecture backend-api --pods 6 --load idle

# Generate a scenario as JSON and analyse it separately
optik8s simulate generate --pods 10 --load idle --output /tmp/scenario.json
optik8s metrics analyze --input /tmp/scenario.json

# Output the simulation result as raw JSON
optik8s simulate run --scenario overprovisioned --json
```

### AI-Powered Summaries

Generate plain-English explanations and actionable advice using the OpenAI API.

```bash
# Set your OpenAI API key (required)
export OPENAI_API_KEY=sk-...

# Summarise overprovisioned pods from Prometheus
optik8s metrics summarize

# Summarise from a local file
optik8s metrics summarize --input samples/overprovisioned.json

# Use a specific model
optik8s metrics summarize --input pods.json --model gpt-4o

# Output the full result as JSON (analysis + AI summaries)
optik8s metrics summarize --json --input samples/saas-platform.json
```

> If `OPENAI_API_KEY` is not set the command gracefully falls back to the built-in rule-based messages.

### Web UI

```bash
# Start the dashboard on http://127.0.0.1:5000
optik8s ui

# Custom host/port
optik8s ui --host 0.0.0.0 --port 8080

# Debug mode
optik8s ui --debug
```

### Tool status

```bash
optik8s tools
```

---

## Sample Datasets

The `samples/` directory contains pre-generated pod metric files for offline testing.  Use them with `optik8s metrics analyze --input <file>` – no Prometheus or Kubernetes required.

| File | Pods | Description |
|---|---|---|
| `samples/ecommerce.json` | 7 | E-commerce platform at off-peak (mostly idle → overprovisioned) |
| `samples/overprovisioned.json` | 10 | Worst-case: all pods idle, heavily overprovisioned |
| `samples/peak-load.json` | 9 | All services at high load (no overprovisioning expected) |
| `samples/saas-platform.json` | 10 | Multi-tenant SaaS: mix of idle and normal loads |
| `samples/data-pipeline.json` | 6 | Data/ML pipeline idle between processing windows |
| `samples/microservices.json` | 8 | Microservices mesh at normal + high load |

The root-level `pods.json` is a minimal three-pod example used in tests and quick-start demos.

```bash
# Try each sample
optik8s metrics analyze --input samples/ecommerce.json
optik8s metrics analyze --input samples/overprovisioned.json
optik8s metrics analyze --input samples/peak-load.json
```

---

## Web Dashboard

The dashboard provides a point-and-click interface for everything the CLI can do:

- **CLI Tool Status** – shows which tools (kind, kubectl, eksctl, docker, aws) are installed.
- **Clusters panel** – lists existing KIND and EKS clusters; one-click switch context or delete.
- **Create Cluster form** – choose KIND or EKS, set a name (and region for EKS), then click *Create*.
- **Nodes panel** – refresh to see nodes and their ready status for the selected context.
- **App cards** – one card per sample app with *Deploy* and *Remove* buttons; status refreshes automatically.
- **Target context selector** – all app operations target the selected kubectl context.

---

## Cluster Configurations

### KIND (local)

`clusters/kind/cluster-config.yaml` creates a **3-node cluster** (1 control-plane + 2 workers):

| Feature | Detail |
|---|---|
| Control-plane nodes | 1 (with `ingress-ready=true` label) |
| Worker nodes | 2 |
| HTTP port | `localhost:8080` → container port 80 |
| HTTPS port | `localhost:8443` → container port 443 |
| Pod subnet | `10.244.0.0/16` |
| Service subnet | `10.96.0.0/12` |

Customise the file freely before running `cluster create kind`.

### EKS (AWS)

`clusters/eks/cluster-config.yaml` creates a **managed node group** cluster:

| Feature | Detail |
|---|---|
| Kubernetes version | 1.29 |
| Node type | t3.medium |
| Min / desired / max nodes | 1 / 2 / 4 |
| Networking | Private subnets |
| IAM OIDC | Enabled (for IRSA) |
| Logging | api, audit, authenticator, controllerManager, scheduler |
| Node IAM add-ons | ECR image builder, ALB controller, CloudWatch |

> **Cost note:** EKS clusters incur AWS charges. Remember to delete the cluster when you are done.

---

## Sample Apps

| Key | Image | Port | Category | Description |
|---|---|---|---|---|
| `nodejs-web` | `node:20-alpine` | 3000 | frontend | Node.js Express web server |
| `python-api` | `python:3.12-slim` | 8000 | backend | FastAPI REST service |
| `java-spring` | `springio/petclinic:latest` | 8080 | backend | Spring PetClinic (JVM workload) |
| `postgres-db` | `postgres:16-alpine` | 5432 | database | PostgreSQL 16 |
| `redis-cache` | `redis:7-alpine` | 6379 | cache | Redis 7 in-memory store |

All apps are deployed as a **Deployment + ClusterIP Service** pair. You can port-forward to reach them:

```bash
kubectl port-forward svc/nodejs-web 3000:80
kubectl port-forward svc/python-api 8000:80
kubectl port-forward svc/java-spring 8080:80
kubectl port-forward svc/postgres-db 5432:5432
kubectl port-forward svc/redis-cache 6379:6379
```

---

## Development

```bash
# Install the package in editable mode
pip install -e .

# Run the full test suite
pip install pytest pyyaml
pytest tests/ -v

# Run the analysis engine against a sample dataset (no cluster needed)
optik8s metrics analyze --input samples/overprovisioned.json

# Run the simulator
optik8s simulate run --scenario ecommerce --seed 42

# Run the UI in debug mode
optik8s ui --debug
```

---

## Makefile shortcuts

```bash
make install        # pip install -e .
make run-ui         # OptiK8s ui
make kind-create    # create default KIND cluster
make kind-delete    # delete default KIND cluster
make eks-create     # create default EKS cluster
make eks-delete     # delete default EKS cluster
make clusters       # OptiK8s cluster list
make apps           # OptiK8s app list
make deploy-all     # OptiK8s app deploy --all
make test           # pytest tests/
make clean          # remove build artefacts
```
