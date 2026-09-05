# Local development shortcuts. `make help` lists targets.
# Commands use env/bin so you do not have to activate the venv first.

PYTHON_BIN ?= python3.11
VENV := env
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
MANAGE := $(PYTHON) manage.py
DB_NAME ?= english_practice

.DEFAULT_GOAL := help

.PHONY: help setup install env-file db migrate superuser run worker test lint schema check

help: ## List available targets
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make <target>\n\n"} \
		/^[a-zA-Z_-]+:.*?##/ { printf "  %-12s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

setup: install env-file db migrate ## Create the venv, .env, database, and apply migrations

install: $(PYTHON) ## Install Python dependencies into env/
	$(PIP) install -r requirements-dev.txt

$(PYTHON):
	$(PYTHON_BIN) -m venv $(VENV)

env-file: .env ## Copy .env.example to .env when .env is missing

.env:
	cp .env.example .env

db: ## Create the local PostgreSQL database if it does not exist
	createdb $(DB_NAME) 2>/dev/null || true

migrate: ## Apply database migrations
	$(MANAGE) migrate $(ARGS)

superuser: ## Create a Django superuser (interactive)
	$(MANAGE) createsuperuser

run: ## Start the API on http://127.0.0.1:8000/
	$(MANAGE) runserver $(ARGS)

worker: ## Start the Celery worker (needs Redis)
	$(PYTHON) -m celery -A config worker -l info $(ARGS)

test: ## Run the test suite
	$(PYTHON) -m pytest -q $(ARGS)

lint: ## Lint with ruff
	$(PYTHON) -m ruff check .

schema: ## Regenerate the committed OpenAPI schema
	$(MANAGE) spectacular --file schema.yml --validate

check: lint test ## Lint and run tests
