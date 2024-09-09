[![Supported Python Versions](https://img.shields.io/pypi/pyversions/rich/13.2.0)]


Retrosync is a Python script to sync [Retroarch](https://retroarch.com] playlists and roms from your desktop computer to your Steamdeck.

![Features](https://github.com/textualize/rich/raw/master/imgs/features.png)


- Sync Retroarch playlists from Desktop to Steamdeck
- Sync roms files and purge unused roms on remote
- Sync bios files
- Sync thumbnail images
- Re-create local playlist by scanning local folders
- Support for xml dat archives
- Configure your cores per system
- Sync roms to a local mounted SDcard


## Compatibility

Retrosync works with Linux, macOS and relies on installed tools like _ssh_ and _rsync_.

## Installing

Install the dependencies using the included Makefile, that utilize [UV](https://github.com/astral-sh/uv) to install the dependencies into a virtualenv.

```sh
make install
```


## Configuration

By default the steamdeck.toml is used. You can change the used configfile via the command switch `--config-file`  to provide a different filename.

In this sample configuration a sample setup is provided for the local desktop Retroarch related files and your local rom locations. The same goes for the remote side for your steamdeck. The details can look a a bit different for your Steadeck depending on how you installed Retroarch. In this case Retroarch was installed via [Emudeck](https://www.emudeck.com/) using [Flatpak](https://flatpak.org/).


```toml
[default]
hostname = "steamdeck"

local_playlists = "/Users/david/Library/Application Support/RetroArch/playlists"
local_bios = "/Users/david/Library/Application Support/RetroArch/system"
local_roms = "/Users/david/Dropbox/Software/Roms"
local_cores = "/Users/david/Library/Application Support/RetroArch/cores"
local_thumbnails = "/Users/david/Library/Application Support/RetroArch/thumbnails"
local_cores_suffix = ".dylib"

remote_playlists = "/home/deck/.var/app/org.libretro.RetroArch/config/retroarch/playlists"
remote_bios = "/home/deck/Emulation/bios"
remote_roms = "/run/media/deck/Steamdeck/roms"
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

....
```




## Usage

To sync all you playlists (that have configured in your toml configfile) and roms to your Steamdeck just run:

```sh
python retrosync.py --sync-roms --sync-playlists
```

So see whats all the details that are happening you can add `--debug` to write detailed output to `debug.log` file.

```sh
python retrosync.py --sync-roms --sync-playlists --debug
```

To sync  all local resource (playlists, roms, bios files and thumbnails) to your Steamdeck

```sh
python retrosync.py --all
```

The actions for playlist and rom can be scoped to one system, like so:

```sh
python retrosync.py --debug --sync-roms --sync-playlists  --name "psx"
Do you want to continue with playlists 'Sony - PlayStation.lpl' ? [y/N]:
```

You can add an `--yes` to skip the prompt.

```sh
python retrosync.py --debug --sync-roms --sync-playlists  --name "psx" --yes
```


To update and re-create a local playlist by scanning the local rom folder.

```sh
python retrosync.py --debug  --update-playlists  --name "psx"
```

You can sync roms to local mounded SDcard

```sh
python retrosync.py  --debug --sync-roms-local /Volumes/Steamdeck/roms
```

