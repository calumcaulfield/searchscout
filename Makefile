.DEFAULT_GOAL := help
PY := PYTHONPATH=src python

.PHONY: help install demo search plan test lint fmt typecheck check web clean

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install: ## Install the package and dev dependencies
	python -m pip install --upgrade pip
	python -m pip install -e ".[dev]"

demo: ## End-to-end against the bundled synthetic catalogue: plan, apply, rollback
	$(PY) -m searchscout.demo_walkthrough

search: ## Find products containing a term (read-only)
	$(PY) -m searchscout.cli search cotton

plan: ## Preview a bulk edit without writing anything
	$(PY) -m searchscout.cli plan cotton "organic cotton"

web: ## Run the review UI at http://localhost:5001
	FLASK_APP=searchscout.web.app $(PY) -m flask run --port 5001

test: ## Run the test suite
	$(PY) -m pytest tests

lint: ## Lint
	ruff check src tests

fmt: ## Format and auto-fix
	ruff format src tests
	ruff check --fix src tests

typecheck: ## Static type checking
	mypy src

check: lint typecheck test ## Everything CI runs

clean: ## Remove caches and generated artefacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache dist build *.egg-info var
