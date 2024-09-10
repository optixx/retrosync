![Supported Python Versions](https://img.shields.io/pypi/pyversions/rich/13.2.0)


![Logo]
![Logo](https://github.com/toptixx/retrosync/raw/master/assets/img/logo.png)
Retrosync is a Python script to sync [Retroarch](https://retroarch.com) playlists and ROMs from your desktop computer to your Steam Deck.


## Features
- Sync Retroarch playlists from Desktop to Steam Deck
- Sync ROM files and purge unused ROMs on remote
- Sync BIOS files
- Sync thumbnail images
- Re-create local playlists by scanning local folders
- Support for XML DAT archives
- Configure your cores per system
- Sync ROMs to a locally mounted SD card

## Compatibility

Retrosync works with Linux and macOS, and relies on installed tools like _ssh_ and _rsync_.

## Installing

Install the dependencies using the included Makefile, that utilize [UV](https://github.com/astral-sh/uv) to install the dependencies into a virtualenv.

```sh
make install
```

## Configuration

By default, the `steamdeck.toml` configuration file is used. You can change the config file via the command switch `--config-file` to provide a different filename.

In this sample configuration, a setup is provided for the local desktop Retroarch-related files and your local ROM locations. The same goes for the remote side on your Steam Deck. The details may look a bit different on your Steam Deck, depending on how you installed Retroarch. In this case, Retroarch was installed via [Emudeck](https://www.emudeck.com/) using [Flatpak](https://flatpak.org/).


```toml
[default]
hostname = "steamdeck"

local_playlists = "~/Library/Application Support/RetroArch/playlists"
local_bios = "~/Library/Application Support/RetroArch/system"
local_roms = "~/Documents/Roms"
local_cores = "~/Library/Application Support/RetroArch/cores"
local_thumbnails = "~/Library/Application Support/RetroArch/thumbnails"
local_cores_suffix = ".dylib"

remote_playlists = "/home/deck/.var/app/org.libretro.RetroArch/config/retroarch/playlists"
remote_bios = "/home/deck/Emulation/bios"
remote_roms = "/home/deck/Emulation/roms"
remote_cores = "/home/deck/.var/app/org.libretro.RetroArch/config/retroarch/cores"
remote_thumbnails = "/home/deck/.var/app/org.libretro.RetroArch/config/retroarch/thumbnails"
remote_cores_suffix = ".so"

[[playlists]]
name = "Atari - 2600.lpl"
local_folder = "Atari - 2600"
remote_folder = "atari2600"
core_path =  "prosystem_libretro"
core_name = "Atari - 2600 (Stella)"


[[playlists]]
name = "Atari - 7800.lpl"
local_folder = "Atari - 7800"
remote_folder = "atari7800"
core_path =  "prosystem_libretro"
core_name = "Atari - 7800 (ProSystem)"

```

## Usage

To sync all your playlists (that are configured in your TOML config file) and ROMs to your Steam Deck, just run:

```sh
python retrosync.py --sync-roms --sync-playlists
```

To see all the details of what is happening, you can add `--debug` to write detailed output to the `debug.log` file.

```sh
python retrosync.py --sync-roms --sync-playlists --debug
```

To sync all local resources (playlists, ROMs, BIOS files, and thumbnails) to your Steam Deck:

```sh
python retrosync.py --all
```

The actions for playlists and ROMs can be scoped to one system, like so:

```sh
python retrosync.py --debug --sync-roms --sync-playlists --name "psx"
Do you want to continue with playlists 'Sony - PlayStation.lpl'? [y/N]:
```

You can add `--yes` to skip the prompt.

```sh
python retrosync.py --debug --sync-roms --sync-playlists --name "psx" --yes
```

To update and re-create a local playlist by scanning the local ROM folder:

```sh
python retrosync.py --debug --update-playlists --name "psx"
```

You can sync ROMs to a locally mounted SD card:

```sh
python retrosync.py --debug --sync-roms-local /Volumes/Steamdeck/roms
```
