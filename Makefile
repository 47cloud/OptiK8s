.PHONY: install install-dev run-ui run-cli-help lint test clean

install:
	pip install -e .

install-dev:
	pip install -e ".[dev]"

run-ui:
	optik8s ui

run-cli-help:
	optik8s --help

lint:
	@which flake8 >/dev/null 2>&1 && flake8 optik8s/ || echo "flake8 not installed, skipping"

test:
	@which pytest >/dev/null 2>&1 && pytest tests/ -v || echo "pytest not installed, skipping"

clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null; true
	find . -type f -name "*.pyc" -delete 2>/dev/null; true
	rm -rf *.egg-info dist build .pytest_cache 2>/dev/null; true

# ── Cluster shortcuts ─────────────────────────────────────────────────────────
kind-create:
	optik8s cluster create kind

kind-delete:
	optik8s cluster delete kind

eks-create:
	optik8s cluster create eks

eks-delete:
	optik8s cluster delete eks

clusters:
	optik8s cluster list

# ── App shortcuts ─────────────────────────────────────────────────────────────
apps:
	optik8s app list

deploy-all:
	optik8s app deploy --all

# ── Monitoring shortcuts ──────────────────────────────────────────────────────
monitoring-install:
	optik8s monitoring install

monitoring-uninstall:
	optik8s monitoring uninstall

monitoring-status:
	optik8s monitoring status
