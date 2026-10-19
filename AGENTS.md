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
- `sync steam` (SteamLibrarySync) writes Steam files under `dest_steam_dir` (`shortcuts.vdf`, `cloudstorage/cloud-storage-namespace-*.json`, `config/grid/`) with SRM-compatible formats from `retrosync_core/steam.py`. It only ever modifies entries tagged with the `steam_collection_prefix` (default `RetroSync`) and collection ids prefixed `retrosync-`, so EmuDeck/SRM entries coexist; originals are backed up as `*.retrosync-bak`.
- Steam shortcuts launch through EmuDeck's launcher scripts in `~/Emulation/tools/launchers` (args live in the Exe field, `LaunchOptions` empty, `StartDir` = the launchers dir — mirrors the working EmuDeck entries). Per-playlist `steam_emulator` picks a standalone emulator; the launcher table is `EMULATOR_LAUNCHERS` in `retrosync_core/steam.py`.
- Collections are per-system only (`RetroSync / <System>`); the flat umbrella `RetroSync` is opt-in via `steam_umbrella_collection`. Steam collections cannot nest.
- **Collections are cloud-authoritative in current Steam** (established experimentally 2026-10-19): a running or restarting Steam ignores foreign writes to `cloud-storage-namespace-*.json`, consumes `.modified.json` markers without adopting, ignores the `user-collections` cache in `localconfig.vdf` (output-only — Steam rewrites it from memory), ignores shortcut tags for collections, and rejects even exact-SRM-format entries. Adoption only happens during a full client-cloud reconcile (log: `[AppID 7] Starting sync` with a platform change) — e.g. account last synced on Windows. retrosync still writes the cloud-storage files (harmless, correct on adopt) plus shortcuts + artwork (local, apply on restart). Don't re-attempt file-based collection creation; the runner's `finalize()` hook prints the honest guidance. `--reboot-deck` reboots the target via `run_remote` (lifecycle commands must use `transport.run_remote` — `run_capture` executes LOCALLY, which once made pgrep/shutdown hit the Mac instead of the Deck).
- Launch conventions were live-verified for retroarch/dolphin/duckstation/flycast/pcsx2/ppsspp/rpcs3/ryujinx/supermodel (8/8 real game launches through the actual launcher scripts); `mupen` (RMG) is unverified — its script is `rosaliesmupengui.sh`. Appids are `crc32(exe + appname) | 0x80000000` — changing `steam_emulator`/`steam_launch_command`/labels changes appids, so old entries get pruned and artwork re-copied.
- `steam` is deliberately not part of `sync all` or the `run` presets.
- `--dry-run` for steam sync still performs read-only remote queries (ssh `base64`/`ls`/`test`) to report accurately; only writes are suppressed.
- The Deck's Emulation directory is `~/Emulation` (capital E).
