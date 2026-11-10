.DEFAULT_GOAL := help
.PHONY: help sync lock hooks lint format test check-requirements fetch silver gold dashboard clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

sync:  ## Create .venv and install the project and dev dependencies
	uv sync --all-groups

lock:  ## Re-resolve uv.lock and re-export requirements.txt
	uv lock
	./scripts/export-requirements.sh
	@echo "uv.lock and requirements.txt regenerated"

check-requirements:  ## Fail if requirements.txt has drifted from uv.lock
	./scripts/check-requirements.sh

hooks:  ## Install the git pre-commit hooks
	uv run pre-commit install

lint:  ## Lint without modifying anything
	uv run ruff check .
	uv run ruff format --check .

format:  ## Apply formatting and autofixes
	uv run ruff check --fix .
	uv run ruff format .

test:  ## Run the test suite
	uv run pytest

fetch:  ## Download and unpack the upstream archive
	uv run dzdb --verbose download
	uv run dzdb --verbose extract

silver:  ## Build the silver tables (skips when nothing changed)
	uv run dzdb --verbose silver

gold:  ## Build the gold tables and export Parquet for the dashboard
	uv run dzdb --verbose gold

dashboard:  ## Run the Streamlit dashboard (needs `make gold` first)
	uv run --extra dashboard streamlit run streamlit_app.py

clean:  ## Remove everything derived, keeping the downloaded archive
	rm -rf data/interim data/gold data/death_zone.duckdb
