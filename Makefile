homebrew: ## Install macos depencies via homebrew
	brew install uv
	brew install direnv

bootstrap: ## Create virtual env
	find . -type d -name "__pycache__" -exec rm -rf {} +
	test -d .venv && rm -rvf .venv
	uv venv --python 3.12
	direnv reload

sync: ## Install project dependencies with uv
	uv sync --all-groups --python 3.12

install: homebrew bootstrap sync ## Install all depencies

run-tests: ## Run tests
	uv run pytest tests/ -rP

check-ruff: ## Run ruff checks
	uv run ruff check --output-format=github .

help: ## Show help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n"} /^[$$()% 0-9a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
