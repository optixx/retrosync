homebrew: ## Install macos depencies via homebrew
	brew install uv
	brew install direnv

bootstrap: ## Create virtual env
	find . -type d -name "__pycache__" -exec rm -rf {} +
	test -d .venv && rm -rvf .venv
	uv venv
	direnv reload

pip: ## Install pip depencies
	poetry lock
	POETRY_WARNINGS_EXPORT=false poetry export --without-hashes --with dev --with test -f requirements.txt --output requirements.txt
	uv pip install --no-deps -r requirements.txt
	poetry install --only-root

install: homebrew bootstrap pip ## Install all depencies

run-tests: ## Run tests
	PYTHONPATH=. pytest tests/ -rP

check-ruff: ## Run ruff checks
	ruff check --output-format=github .

help: ## Show help message
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage:\n  make \033[36m\033[0m\n"} /^[$$()% 0-9a-zA-Z_-]+:.*?##/ { printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2 } /^##@/ { printf "\n\033[1m%s\033[0m\n", substr($$0, 5) } ' $(MAKEFILE_LIST)
