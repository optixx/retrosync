![Logo](https://github.com/optixx/retrosync/raw/main/assets/img/logo.png)

# Retrosync

Retrosync syncs [RetroArch](https://retroarch.com/) content from a desktop library to targets like a Steam Deck, iOS device, or local export directory.

It handles playlists, ROMs, BIOS files, thumbnails, and shader presets. It can also rebuild local playlists and drive a thumbnail matching workflow from your existing library.

## Features

### Sync
- Playlists
- ROMs
- BIOS files
- Thumbnails
- Shader presets

### Update
- Rebuild playlists by scanning ROM folders
- Match thumbnails against remote catalogs
- Optionally download matched thumbnails and rewrite labels

### Transport support
- `ssh` for Steam Deck or Linux targets
- `webdav` for iOS or remote storage
- `filesystem` for local export and manual transfer

WebDAV tuning keys:
- `webdav_max_workers` limits concurrent uploads. Use `1` for fragile targets.
- `webdav_timeout_seconds` increases the per-request timeout for slow uploads.
- `webdav_retries` retries transient WebDAV network failures.
- `webdav_retry_delay_seconds` pauses briefly between retry attempts.

## Installation

### Using `uv`

```sh
git clone https://github.com/optixx/retrosync.git
cd retrosync
uv sync --all-groups --python 3.12
```

## CLI Overview

The CLI is now organized by intent instead of a large flat flag set.

```sh
retrosync.py gui
retrosync.py list playlists
retrosync.py sync roms --system psx
retrosync.py sync all
retrosync.py update playlists --system psx
retrosync.py update thumbnails --system psx --apply
retrosync.py run full
```

### Command groups

- `sync <playlists|bios|thumbnails|roms|shaders|all>` runs copy-oriented sync tasks.
- `update <playlists|thumbnails|all>` runs local metadata and asset update tasks.
- `run <sync|update|full>` runs explicit multi-action presets.
- `list <playlists|systems>` shows configured systems and source ROM counts.
- `gui` launches the Dear PyGui interface.

### Shared options

- `--config-file` selects the TOML config file. The default remains `steamdeck.conf`.
- `--system` fuzzy-matches a single playlist and narrows the run to that system.
- `--yes` accepts the best fuzzy match without prompting.
- `--dry-run` builds the plan and runs the workflow without writing changes.
- `--transport filesystem|ssh|webdav` overrides the transport mode from config.
- `--transport-impl auto|unix|windows` chooses the transport implementation variant.
- `--debug` enables debug logging to `debug.log`.

### Thumbnail update options

These options only apply to commands that include thumbnail updates:

- `--apply` downloads matched assets and rewrites labels.
- `--refresh-thumbnail-cache` bypasses cached remote thumbnail listings.
- `--no-thumbnail-cache` disables cache reads and writes for the run.

## Typical usage

Sync a single system to the configured target:

```sh
retrosync.py sync all --system "Sony - PlayStation" --yes
```

Refresh local playlists without touching the target device:

```sh
retrosync.py update playlists --system psx --yes
```

Preview a full workflow:

```sh
retrosync.py run full --dry-run --yes
```

Download thumbnail matches after previewing them:

```sh
retrosync.py update thumbnails --system psx --yes --apply
```
