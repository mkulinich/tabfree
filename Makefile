SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE  := docker compose -f infra/docker/compose.yaml
TOFU     ?= tofu
TOFU_DIR := infra/terraform/envs/sandbox
TOFU_PROD := infra/terraform/envs/production

.PHONY: help setup lint format typecheck test check dev-up dev-down dev-logs demo demo-down infra-validate openapi docs clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Install Python (uv) and Node (npm) dependencies
	uv sync --all-packages
	npm install

lint: ## Lint and format-check Python and TypeScript
	uv run ruff check .
	uv run ruff format --check .
	npm run lint

format: ## Auto-format and auto-fix Python
	uv run ruff format .
	uv run ruff check --fix .

typecheck: ## Run mypy and tsc
	uv run mypy
	npm run typecheck

test: ## Run Python and TypeScript tests
	uv run pytest
	npm test

check: lint typecheck test ## Everything CI runs

dev-up: ## Start the local stack (needs Docker)
	$(COMPOSE) up -d --wait

dev-down: ## Stop the local stack
	$(COMPOSE) down

dev-logs: ## Follow local stack logs
	$(COMPOSE) logs -f

demo: ## Run TabFree on sample data at http://localhost:8765 (needs Docker)
	docker compose -f infra/docker/demo.compose.yaml up --build -d --wait

demo-down: ## Stop the demo and delete its data
	docker compose -f infra/docker/demo.compose.yaml down -v

infra-validate: ## Check formatting and validate the sandbox and production IaC (no AWS calls)
	$(TOFU) fmt -check -recursive infra/terraform
	$(TOFU) -chdir=$(TOFU_DIR) init -backend=false -input=false
	$(TOFU) -chdir=$(TOFU_DIR) validate
	$(TOFU) -chdir=$(TOFU_PROD) init -backend=false -input=false
	$(TOFU) -chdir=$(TOFU_PROD) validate

openapi: ## Write the API reference (docs/reference/openapi.json)
	uv run python -m tabfree_api.openapi docs/reference/openapi.json

docs: openapi ## Build the documentation site into site/ (needs mkdocs-material: uv tool install mkdocs-material)
	mkdocs build --strict

clean: ## Remove local caches and build output
	rm -rf .venv node_modules .mypy_cache .ruff_cache .pytest_cache packages/*/dist apps/*/dist
