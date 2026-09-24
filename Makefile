# AI Command Center: developer shortcuts. Run from WSL (needs: docker, uv, pnpm).
SHELL := /bin/bash
.DEFAULT_GOAL := help

COMPOSE := docker compose
DEV     := docker compose -f compose.yml -f compose.dev.yml
BACKEND := cd backend &&
WEB     := cd apps/web &&

.PHONY: help
help: ## List targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

# ─── Setup ────────────────────────────────────────────────────────────────
.PHONY: env install
env: ## Create .env with random secrets (no-op if it exists)
	@scripts/init-env.sh

install: ## Install backend + web dependencies locally
	$(BACKEND) uv sync
	$(WEB) pnpm install --frozen-lockfile

# ─── Stack ────────────────────────────────────────────────────────────────
.PHONY: up dev down logs ps operator migrate smoke
up: env ## Build and start the full stack (production-like images)
	$(COMPOSE) up -d --build --wait

dev: env ## Start with hot reload (api: uvicorn --reload, web: next dev)
	$(DEV) up -d --build --wait

down: ## Stop the stack (keeps data)
	$(DEV) down

logs: ## Follow logs (all services)
	$(COMPOSE) logs -f --tail=100

ps: ## Service status
	$(COMPOSE) ps

operator: ## Create the operator account: make operator EMAIL=you@example.com
	@test -n "$(EMAIL)" || (echo "usage: make operator EMAIL=you@example.com" && exit 1)
	$(COMPOSE) exec api acc create-operator --email "$(EMAIL)"

migrate: ## Apply database migrations
	$(COMPOSE) run --rm migrate

smoke: ## End-to-end smoke test through Caddy (stack must be running)
	@scripts/smoke.sh

# ─── Quality ──────────────────────────────────────────────────────────────
.PHONY: lint fmt test test-backend test-web gen-client check-client
lint: ## Lint + typecheck + architecture contracts (backend and web)
	$(BACKEND) uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run lint-imports
	$(WEB) pnpm lint && pnpm typecheck

fmt: ## Auto-format backend
	$(BACKEND) uv run ruff format . && uv run ruff check --fix .

test: test-backend test-web ## Run all tests

test-backend: ## Backend unit + integration tests (starts dev postgres/redis)
	@scripts/test-backend.sh

test-web: ## Web unit tests
	$(WEB) pnpm test

gen-client: ## Regenerate OpenAPI schema + typed web client from the backend
	$(BACKEND) uv run acc openapi -o ../apps/web/openapi/schema.json
	$(WEB) pnpm gen:api

check-client: gen-client ## Fail if the committed API client is stale
	@git diff --exit-code -- apps/web/openapi apps/web/src/lib/api/schema.d.ts \
	  || (echo "API client is stale: run 'make gen-client' and commit." && exit 1)
