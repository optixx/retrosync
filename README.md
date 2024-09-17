![Supported Python Versions](https://img.shields.io/pypi/pyversions/rich/13.2.0)


![Logo](https://github.com/optixx/retrosync/raw/main/assets/img/logo.png)

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



![Demo](https://github.com/optixx/retrosync/raw/main/assets/img/demo.gif)

## Compatibility

Retrosync works with Linux and macOS, and relies on installed tools like _ssh_ and _rsync_.

## Installing

Install the dependencies using the included Makefile, that utilize [UV](https://github.com/astral-sh/uv) to install the dependencies into a virtualenv.

```sh
make install
```

## Configuration

 By default, the `steamdeck.toml` configuration file is employed. You can alter the configuration file by utilizing the command switch `--config-file` to specify an alternate filename.

In this sample configuration, a setup is provided for your local desktop's Retroarch-related files and your local ROM locations, as well as for the remote side on your Steam Deck. The appearance of these details may vary slightly on your Steam Deck, depending on how you installed Retroarch. For instance, in this scenario, Retroarch was installed through Emudeck (<https://www.emudeck.com/>) using Flatpak (<https://flatpak.org/>)).
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
core_path =  "stellalibretro"
core_name = "Atari - 2600 (Stella)"


[[playlists]]
name = "Atari - 7800.lpl"
local_folder = "Atari - 7800"
remote_folder = "atari7800"
core_path =  "prosystem_libretro"
core_name = "Atari - 7800 (ProSystem)"

[[playlists]]
name = "Sharp - X68000.lpl"
local_folder = "Sharp - X68000"
local_whitelist = '.*FD.*\.zip'
local_blacklist = '.*HD.*\.zip'
remote_folder = "x68000"
core_path = "px68k_libretro"
core_name = "Sharp - X68000 (PX68k)"
disabled = false

[[playlists]]
name = "Commodore - Amiga.lpl"
local_folder = "Commodore - Amiga"
local_whitelist = '\.m3u$'
local_blacklist = '\.adf$'
local_create_m3u = true
local_m3u_whitelist = '\.adf$'
local_m3u_pattern = '(.*)(\(Disk \d of \d\)).*\.adf'
remote_folder = "amiga"
core_path = "puae_libretro"
core_name = "Commodore - Amiga (PUAE)"
disabled = false

```

## Usage



![Usage](https://github.com/optixx/retrosync/raw/main/assets/img/usage.png)

 To synchronize all your playlists (as defined in your TOML configuration file) and ROMs onto your Steam Deck, simply execute the following command:

```sh
python retrosync.py --sync-roms --sync-playlists
```

 To view extensive information regarding current operations, include `--debug` in your command to generate detailed logs within a `debug.log` file.

```sh
python retrosync.py --sync-roms --sync-playlists --debug
```

 To synchronize all local resources such as playlists, game ROMs, BIOS files, and thumbnails to your Steam Deck:

```sh

python retrosync.py --all
```

 Playlist and ROM actions can be limited to a single system, as follows:

```sh
python retrosync.py --debug --sync-roms --sync-playlists --name "psx"
Do you want to continue with playlists 'Sony - PlayStation.lpl'? [y/N]:
```

 You have the option to include `--yes` in order to bypass the prompt.

```sh
python retrosync.py --debug --sync-roms --sync-playlists --name "psx" --yes
```

 To refresh and recreate a local playlist by scanning your ROM folder:

```sh
python retrosync.py --debug --update-playlists --name "psx"
```

 Synchronize your ROMs onto a locally attached SD card is possible:

```sh
python retrosync.py --debug --sync-roms-local /Volumes/Steamdeck/roms
```
