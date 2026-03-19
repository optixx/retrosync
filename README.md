![Logo](https://github.com/optixx/retrosync/raw/main/assets/img/logo.png)

# Retrosync

Retrosync is a Python CLI tool for syncing [RetroArch](https://retroarch.com/) content from your desktop to other devices like a Steam Deck or iOS.

It handles playlists, ROMs, BIOS files, favorites, and thumbnails — and can also rebuild your local playlists from your ROM folders.

---

## Features

### Sync
- Playlists
- Favorites
- ROMs
- BIOS files
- Thumbnails

### Transport support
- `ssh` → Steam Deck / Linux targets
- `webdav` → iOS / remote storage
- `filesystem` → local export for manual transfer

### Local tooling
- Rebuild playlists by scanning ROM folders
- Generate `.m3u` files for multi-disk games
- Regex-based filtering and normalization
- Thumbnail matching + download workflow

---

## Installation

### Using `uv` (recommended)

```sh
git clone https://github.com/optixx/retrosync.git
cd retrosync

uv venv --python 3.12
source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv sync --all-groups --python 3.12
