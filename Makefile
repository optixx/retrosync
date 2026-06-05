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

test: ## Run tests
	uv run --group test pytest tests/ -rP

lint: ## Run ruff lint checks
	uv run ruff check --output-format=github .

lint-fix: ## Run ruff lint checks and apply fixes
	uv run ruff check --fix .

check-format: ## Check formatting with ruff
	uv run ruff format --check .

format: ## Format with ruff
	uv run ruff format .

check-ruff: lint ## Run ruff checks

help: ## Show help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n"} /^[$$()% 0-9a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
