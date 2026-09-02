.PHONY: help install up down logs migrate test lint typecheck check evaluate deck openapi samples scans clean

help:
	@grep -E '^[a-z-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies with Poetry
	poetry install

up:  ## Start the full stack
	docker compose up -d --build

down:  ## Stop the stack, keeping volumes
	docker compose down

logs:  ## Follow the API logs
	docker compose logs -f api

migrate:  ## Apply database migrations
	poetry run alembic upgrade head

test:  ## Run the test suite
	poetry run pytest -q

lint:  ## Lint with ruff
	poetry run ruff check .

typecheck:  ## Type check with mypy
	poetry run mypy src mock_erp

check: lint typecheck test  ## Lint, type check and test

evaluate:  ## Score extraction against the ground-truth answer key
	poetry run python -m evaluation.run_evaluation --mode extraction

deck:  ## Rebuild the presentation deck from the latest evaluation numbers
	poetry run python scripts/generate_deck.py

openapi:  ## Regenerate docs/openapi.json from the live app
	poetry run python -c "import json; from invoice_agent.main import create_app; \
	json.dump(create_app().openapi(), open('docs/openapi.json','w'), indent=2)"

samples:  ## Regenerate the sample invoices
	poetry run python scripts/generate_samples.py

scans:  ## Make image-only scanned copies of the samples to exercise the OCR path
	poetry run python scripts/make_scanned_samples.py

clean:  ## Remove caches and build artifacts
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	rm -rf .pytest_cache .ruff_cache .mypy_cache .coverage htmlcov
