.PHONY: help seed-emojis

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

seed-emojis: ## Upload assets/emojis/ files as application emojis (idempotent)
	uv run python scripts/seed_emojis.py
