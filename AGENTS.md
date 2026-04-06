# Retrosync Agent Notes

## Setup
- Use `uv`, not `pip`, for normal local work: `uv sync --all-groups --python 3.12`.
- `make bootstrap` is destructive: it deletes `.venv` and all `__pycache__` directories before recreating the venv.
- Fast checks that match CI behavior are:
  - `uv run ruff check .`
  - `uv run --group test pytest tests/ -rP`
- For a focused test run, use standard pytest selection, e.g. `uv run --group test pytest tests/test_app_controller.py -k persists`.

## Entry Points
- `retrosync.py` is the main CLI entrypoint and the file the PyInstaller workflows build.
- `--gui` from `retrosync.py` launches the Dear PyGui app in `retrosync_gui/app.py`.
- `retrosync_core/` holds the sync engine: config normalization, jobs, transports, runner, and Rich CLI progress.
- `retrosync_app/` is the GUI-facing layer: controller, persisted state, runtime context loading, and preview-plan construction.

## Quirks
- CLI defaults to `--config-file=steamdeck.conf`; do not assume config comes from env vars.
- `--name` is fuzzy-matched via `rank_system_matches`; without `--yes`, the CLI prompts the user to choose a matched playlist.
- `--transport` selects the mode (`filesystem|ssh|webdav`), while `--transport-unix` / `--transport-windows` force the implementation.
- GUI state persists to `.cache/retrosync/gui-state.json`; previous runs can change startup behavior.
- Thumbnail update jobs cache remote directory listings in `.cache/retrosync/thumbnail-index` for 24 hours.
- `--refresh-thumbnail-cache` bypasses cache reads. `--no-thumbnail-cache` disables both cache reads and writes.
- `pyproject.toml` still has stale `tool.pyright.include = ["app", "core", "test"]`; the real packages are `retrosync_app`, `retrosync_core`, and `retrosync_gui`.
