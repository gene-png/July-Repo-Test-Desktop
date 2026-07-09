# SHIELD by Kentro v2.0 — developer/ops entry points.
#
# These targets wrap the Python venv and the ops scripts so common flows are a
# single command. They assume:
#   - a Python venv at .venv (create with `python -m venv .venv && .venv/bin/pip
#     install -e apps/api[dev]`), OR override PY=... to point at another python.
#   - a running Postgres reachable via DATABASE_URL (docker compose up -d db).
#
# Load env from a dotenv file with:  make seed ENV_FILE=.env.e2e

PY ?= .venv/bin/python
ENV_FILE ?=
API_DIR := apps/api

# Optionally source an env file (e.g. .env.e2e) before a recipe.
ifneq ($(ENV_FILE),)
  ENV_PREFIX := set -a && . ./$(ENV_FILE) && set +a &&
else
  ENV_PREFIX :=
endif

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@echo "SHIELD make targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | sort \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Override the interpreter with PY=... and source a dotenv with ENV_FILE=..."
	@echo "Example: make seed ENV_FILE=.env.e2e"

.PHONY: seed
seed: ## Seed the database: demo fixtures + CSF and ZT questionnaire loaders
	@echo ">> seeding demo data"
	$(ENV_PREFIX) cd $(API_DIR) && ../../$(PY) scripts/seed_demo.py
	@echo ">> loading CSF tier questionnaires"
	$(ENV_PREFIX) cd $(API_DIR) && ../../$(PY) -m scripts.load_csf_tier_questionnaires
	@echo ">> loading ZT questionnaires"
	$(ENV_PREFIX) cd $(API_DIR) && ../../$(PY) -m scripts.load_zt_questionnaires
	@echo ">> seed complete"

.PHONY: backup
backup: ## Back up Postgres + object store to backups/<timestamp>/
	$(ENV_PREFIX) PYTHON_BIN=$(PY) bash scripts/backup.sh

.PHONY: restore-drill
restore-drill: ## Prove backups round-trip: seed marker, back up, restore to scratch DB, assert
	$(ENV_PREFIX) $(PY) scripts/restore_drill.py
