#!/bin/bash

if ! command -v uv &>/dev/null; then
  echo "uv is not installed. Please install uv and try again."
  exit 1
fi

uv venv --python 3.12
source .venv/bin/activate

echo "Installing required Python packages with uv..."
uv sync --all-groups --python 3.12

echo "Setup complete."
