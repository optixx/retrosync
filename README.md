![Supported Python Versions](https://img.shields.io/pypi/pyversions/rich/13.2.0)


![Logo](https://github.com/optixx/retrosync/raw/main/assets/img/logo.png)

Retrosync is a Python script to sync [Retroarch](https://retroarch.com) playlists and ROMs from your desktop computer to your Steam Deck or iOS based devices.

## Features syncing to Steamdeck
1. Synchronize via SSH RetroArch playlists, favorites from Desktop to Steam Deck
2. Synchronize via SSH ROMs, Bios files and thumbnail

## Features syncing to iOS device
1. Synchronize RetroArch playlists, favorites from Desktop to local folder
2. Synchronize ROMs, Bios files and thumbnail to local folder
3. Due to to missing SSH support on iOS device prepared files need to be synced with the Finder, iCloud Sync or 3rd party
solutions like [LocalSend](https://localsend.org)

## Features for all target device
1. Update and recreate local playlists by scanning local folders
2. Create local m3u files using detection and normalizing regular expressions
3. Support for XML DAT archives
4. Configure your local and remote cores according to each system

![Demo](https://github.com/optixx/retrosync/raw/main/assets/img/demo.gif)

## Compatibility

 Retrosync functions with macOS, Linux, and Windows, relying on previously installed tools such as Secure Shell (SSH) and Rsync, or utilizing pure Python Secure Shell implementations. This means that you need to enable SSH on your Steam Deck if you have not done so already; you can follow this guide for [instructions](https://shendrick.net/Gaming/2022/05/30/sshonsteamdeck.html).

The primary distinction between the two target device groups lies in the fact that the Steam Deck can be synchronized directly via the SSH protocol, while iOS devices require the use of Finder or iCloud Sync for data transfer. Consequently, all RetroArch-related files are prepared locally, with paths and configurations altered to suit the target system, but stored within a local folder intended for later transfer to the designated devices. This local sync feature offers an optimal solution for syncing onto SD cards or external disks, facilitating the transfer of larger collections to the Steam Deck without incurring slower network traffic.





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
# Explain the method for copying data, whether it's local or remote.
target = "remote"
hostname = "192.168.1.100"
username = "deck"
password = "<password>"

src_playlists = "~/Library/Application Support/RetroArch/playlists"
src_bios = "~/Library/Application Support/RetroArch/system"
src_config = "~/Library/Application Support/RetroArch/config/"
src_roms = "~/Documents/Roms"
src_cores = "~/Library/Application Support/RetroArch/cores"
src_thumbnails = "~/Library/Application Support/RetroArch/thumbnails"
src_cores_suffix = ".dylib"

# Provide the specific location where to sync data to
dest_playlists = "/home/deck/.var/app/org.libretro.RetroArch/config/retroarch/playlists"
dest_bios = "/home/deck/Emulation/bios"
dest_roms = "/home/deck/Emulation/roms"
dest_thumbnails = "/home/deck/.var/app/org.libretro.RetroArch/config/retroarch/thumbnails"

# Actual Destinations on the Target Device
target_roms =  "/home/deck/Emulation/roms"
target_cores = "/home/deck/.var/app/org.libretro.RetroArch/config/retroarch/cores"
target_cores_suffix = ".so"


[[playlists]]
name = "Atari - 2600.lpl"
src_folder = "Atari - 2600"
dest_folder = "atari2600"
src_core_path = "stella_libretro"
src_core_name = "Atari - 2600 (Stella)"
dest_core_path = "stella_libretro"
dest_core_name = "Atari - 2600 (Stella)"

[[playlists]]
name = "Atari - 7800.lpl"
src_folder = "Atari - 7800"
dest_folder = "atari7800"
src_core_path = "prosystem_libretro"
src_core_name = "Atari - 7800 (ProSystem)"
dest_core_path = "prosystem_libretro"
dest_core_name = "Atari - 7800 (ProSystem)"

[[playlists]]
name = "Sharp - X68000.lpl"
src_folder = "Sharp - X68000"
# Make sure to include all zip files matching the "FD" pattern
src_whitelist = '.*FD.*\.zip$'
# Exclude all files that hint hard disk images
src_blacklist = '.*HD.*\.zip$'
dest_folder = "x68000"
src_core_path = "px68k_libretro"
src_core_name = "Sharp - X68000 (PX68k)"
dest_core_path = "px68k_libretro"
dest_core_name = "Sharp - X68000 (PX68k)"
disabled = false

[[playlists]]
name = "Commodore - Amiga.lpl"
src_folder = "Commodore - Amiga"
# Make sure to include all m3u list
src_whitelist = '\.m3u$'
# Don't add single adf images
src_blacklist = '\.adf$'
# Create fresh m3u files while updating playlists
src_create_m3u = true
# Only include adf images in m3u files
src_m3u_whitelist = '\.adf$'
# Normalizing pattern for files like "Agony (Disk 1 of 3).adf"
src_m3u_pattern = '(.*)(\(Disk \d of \d\)).*\.adf'
dest_folder = "amiga"
src_core_path = "puae_libretro"
src_core_name = "Commodore - Amiga (PUAE)"
dest_core_path = "puae_libretro"
dest_core_name = "Commodore - Amiga (PUAE)"
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
