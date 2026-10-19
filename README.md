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
- Steam library entries (shortcuts, collections, and artwork on the target)

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
retrosync.py sync steam --system psx
retrosync.py update playlists --system psx
retrosync.py update thumbnails --system psx --apply
retrosync.py run full
```

### Command groups

- `sync <playlists|bios|thumbnails|roms|shaders|steam|all>` runs copy-oriented sync tasks.
- `update <playlists|thumbnails|all>` runs local metadata and asset update tasks.
- `run <sync|update|full>` runs explicit multi-action presets.
- `list <playlists|systems>` shows configured systems and source ROM counts.
- `gui` launches the Dear PyGui interface.

`sync steam` is deliberately not part of `all` or the `run` presets — it writes
Steam library files on the target and is meant to be an explicit step.

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

### Steam sync options

This option only applies to commands that include the `steam` target:

- `--reboot-deck` reboots the target after the Steam library sync.

## Steam library integration

`retrosync.py sync steam` creates a Steam library entry for every game in the
synced playlists — like Steam ROM Manager, but driven by your playlists (labels,
m3u multi-disc games, and filters included) and running over plain SSH while the
Deck is in gaming mode.

- Games launch through the EmuDeck launcher scripts in
  `~/Emulation/tools/launchers` (the same way EmuDeck's own Steam entries do).
  RetroArch is the default; per-playlist `steam_emulator` switches to a
  standalone emulator, and `steam_launch_command` allows a fully custom
  template.
- Every game joins a `RetroSync / <System>` collection (e.g.
  `RetroSync / Arcade - FBNeo`). Steam collections are flat — there is no
  nesting — so the per-system collections are siblings in the Collections
  list. An optional umbrella collection with all games can be enabled with
  `steam_umbrella_collection = true`. Entries created by EmuDeck or Steam ROM
  Manager are never touched.
- Artwork comes from the synced RetroArch thumbnails: boxart becomes the cover
  image and in-game snaps become hero images.
- Each run writes a `shortcuts.vdf.retrosync-bak` backup next to the original.
- **Visibility**: shortcuts and artwork appear after the next Steam restart.
  Collections are cloud-authoritative in current Steam clients: retrosync
  writes the cloud-storage files so the collections materialize when Steam
  performs a full cloud reconcile — observed when the account was last synced
  from another platform (e.g. after using Steam on a Windows PC and returning
  to the Deck). To get a collection immediately, create it once via
  right-click → *Add to a collection*; retrosync keeps the matching
  cloud-storage state ready. `--reboot-deck` reboots the target after the sync.

Supported `steam_emulator` values (launcher script + arguments used):

| Value | Script | Systems |
|---|---|---|
| `retroarch` (default) | `retroarch.sh -L {core} "{path}"` | everything else |
| `dolphin` | `dolphin-emu.sh -b -e "{path}"` | GameCube, Wii |
| `duckstation` | `duckstation.sh -batch -fullscreen "{path}"` | PlayStation |
| `flycast` | `flycast.sh "{path}"` | Dreamcast |
| `pcsx2` | `pcsx2-qt.sh -batch -fullscreen "{path}"` | PlayStation 2 |
| `ppsspp` | `ppsspp.sh -f -g "{path}"` | PSP |
| `mupen` | `rosaliesmupengui.sh "{path}"` | N64 via Rosalie's Mupen GUI |
| `rpcs3` | `rpcs3.sh --no-gui "{path}"` | PlayStation 3 |
| `ryujinx` | `ryujinx.sh --fullscreen "{path}"` | Switch |
| `supermodel` | `supermodel.sh "{path}"` | Model 3 |

Configuration keys (all optional except `dest_steam_dir`):

```toml
[default]
dest_steam_dir = "/home/deck/.steam/steam"   # Steam root on the target
# steam_user = "12345678"                    # SteamID3, needed with multiple accounts
# steam_launchers_dir = "/home/deck/Emulation/tools/launchers"
# steam_collection_prefix = "RetroSync"
# steam_umbrella_collection = false          # also tag a flat "RetroSync" collection with all games
# steam_reboot_deck = false                  # reboot the target after `sync steam`

# per playlist:
# [[playlists]]
# steam_category = "Arcade FBNeo"            # defaults to the playlist name
# steam_emulator = "dolphin"                 # standalone emulator instead of RetroArch
# steam_launch_command = 'my-wrapper.sh --core {core} "{path}"'
```

The `mupen` entry (Rosalie's Mupen GUI) is the only launcher template not
verified against a live Steam entry — prefer `retroarch` for N64 unless you
have confirmed RMG's CLI on your device.

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

Create Steam library entries for a synced system:

```sh
retrosync.py sync steam --system "Sony - PlayStation" --yes
```
