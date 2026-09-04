.PHONY: help seed-emojis test run lint format

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

seed-emojis: ## Upload assets/emojis/ files as application emojis (idempotent)
	uv run python scripts/seed_emojis.py

test: ## Run the test suite
	uv run pytest -q

lint: ## Lint and format-check with ruff
	uv run ruff check src tests scripts
	uv run ruff format --check src tests scripts

format: ## Auto-fix lint issues and format with ruff
	uv run ruff check --fix src tests scripts
	uv run ruff format src tests scripts

run: ## Run the bot
	uv run python -m leaf_valley