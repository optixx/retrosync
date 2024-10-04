#!/usr/bin/env python3

__author__ = "Optixx"
__license__ = "MIT"
__version__ = "0.1.0"
__maintainer__ = "David Voswinkel"
__email__ = "david@optixx.org"

import subprocess
import select
import shutil
import tempfile
import toml
import logging
import copy
import click
import json
import glob
import sys
import platform
import time
import re
import paramiko
import Levenshtein
from lxml import etree
from pathlib import Path
from collections import defaultdict
from rich.console import Group
from rich.panel import Panel
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)


logger = logging.getLogger()

item_tpl = {
    "path": "",
    "label": "",
    "core_path": "DETECT",
    "core_name": "DETECT",
    "crc32": "00000000|crc",
    "db_name": "",
}

current_system_progress = Progress(
    TimeElapsedColumn(),
    TextColumn("{task.description}"),
)

step_progress = Progress(
    TextColumn("  "),
    TimeElapsedColumn(),
    TextColumn("[bold purple]{task.fields[action]}"),
    SpinnerColumn("simpleDots"),
)

system_steps_progress = Progress(
    TextColumn("[bold blue]Progress for system {task.fields[name]}: {task.percentage:.0f}%"),
    BarColumn(),
    TextColumn("({task.completed} of {task.total} steps done)"),
)

overall_progress = Progress(TimeElapsedColumn(), BarColumn(), TextColumn("{task.description}"))

progress_group = Group(
    Panel(Group(current_system_progress, step_progress, system_steps_progress)),
    overall_progress,
)


class Transport:
    def __new__(cls, default, dry_run, force_transport):
        if force_transport:
            if force_transport == "unix":
                return TransportUnix(default, dry_run)
            elif force_transport == "windows":
                return TransportWindows(default, dry_run)
            else:
                raise NotImplementedError

        current_platform = platform.system()
        if current_platform in ["Darwin", "Linux"]:
            return TransportUnix(default, dry_run)
        elif current_platform in ["Windows"]:
            return TransportWindows(default, dry_run)
        else:
            print(
                f"This script only runs on macOS, Linux or Windows but you're using {current_platform}. Exiting..."
            )
            sys.exit(1)


class TransportBase:
    def guess_file_count(self, local_path: Path, whitelist: list, recursive=False):
        cnt = 0
        if recursive:
            generator = local_path.rglob("*")
        else:
            generator = local_path.glob("*")
        for filename in generator:
            if whitelist:
                for item in whitelist:
                    if filename.suffix == item:
                        cnt += 1
                        break
            else:
                cnt += 1

        return cnt


class TransportUnix(TransportBase):
    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        logger.debug(f"TransportUnix::__ctor__: dry_run={self.dry_run}")
        self.check()

    def check_executable_exists(self, executable_name):
        executable_path = shutil.which(executable_name)
        if not executable_path:
            print(f"Executable '{executable_name}' not found.")
            sys.exit(-1)

    def check(self):
        for command in ["ssh", "scp", "rsync", "sshpass"]:
            self.check_executable_exists(command)

    def execute(self, cmd):
        password = self.default.get("password")
        logger.debug("execute: cmd=%s", cmd.replace(password, "***"))
        if self.dry_run:
            return
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        poll = select.poll()
        poll.register(p.stdout, select.POLLIN | select.POLLHUP)
        poll.register(p.stderr, select.POLLIN | select.POLLHUP)
        pollc = 2
        events = poll.poll()
        while pollc > 0 and len(events) > 0:
            for event in events:
                (rfd, event) = event
                if event & select.POLLIN:
                    if rfd == p.stdout.fileno():
                        lines = p.stdout.readlines()
                        for line in lines:
                            logger.debug("execute: stdout=%s", line)
                    if rfd == p.stderr.fileno():
                        line = p.stderr.readline()
                        logger.debug("execute: stderr=%s", line)
                if event & select.POLLHUP:
                    poll.unregister(rfd)
                    pollc = pollc - 1
                if pollc > 0:
                    events = poll.poll()
        p.wait()

    def copy_file(self, local_filename: Path, remote_filename: Path):
        hostname = self.default.get("hostname")
        username = self.default.get("username")
        password = self.default.get("password")
        cmd = f'sshpass -p "{password}" scp "{local_filename}" "{username}@{hostname}:{remote_filename}"'
        self.execute(cmd)

    def ensure_remote_dir_exists(self, remote_directory: Path):
        hostname = self.default.get("hostname")
        username = self.default.get("username")
        password = self.default.get("password")
        cmd = f'sshpass -p "{password}"  ssh {username}@{hostname} "mkdir \'{remote_directory}\'"'
        self.execute(cmd)

    def copy_files(
        self,
        local_path: Path,
        remote_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        hostname = self.default.get("hostname")
        username = self.default.get("username")
        password = self.default.get("password")
        args = "--outbuf=L --progress --verbose --human-readable --recursive --size-only --delete"
        if whitelist:
            for item in whitelist:
                args += f'--include="*{item}" '
            args += '--exclude="*" '
        cmd = f'sshpass -p "{password}" rsync {args} "{local_path}/" "{username}@{hostname}:{remote_path}"'
        self.execute(cmd)


class TransportWindows(TransportBase):
    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.connected = False
        logger.debug(f"TransportWindows::__ctor__: dry_run={self.dry_run}")

    def connect(self):
        if self.dry_run:
            return
        if self.connected:
            return
        logger.debug("TransportWindows::connect start")
        self.ssh.connect(
            self.default.get("hostname"),
            username=self.default.get("username"),
            password=self.default.get("password"),
        )
        logger.debug("TransportWindows::connect connected")
        self.sftp = self.ssh.open_sftp()
        logger.debug("TransportWindows::connect sftp opened")
        self.connected = True

    def copy_file(self, local_filename: Path, remote_filename: Path):
        self.connect()
        if self.dry_run:
            logger.debug(
                f"TransportWindows::copy_file: dry-run {local_filename} to {remote_filename}"
            )
            return

        try:
            remote_file_attr = self.sftp.stat(str(remote_filename))
            local_file_attr = local_filename.stat()
            if (
                int(local_file_attr.st_mtime) > int(remote_file_attr.st_mtime)
                or local_file_attr.st_size != remote_file_attr.st_size
            ):
                self.sftp.put(str(local_filename), str(remote_filename))
                logger.debug(
                    f"TransportWindows::copy_file: newer {local_filename} to {remote_filename}"
                )
        except FileNotFoundError:
            self.sftp.put(str(local_filename), str(remote_filename))
            logger.debug(
                f"TransportWindows::copy_file: created {local_filename} to {remote_filename}"
            )

    def ensure_remote_dir_exists(self, remote_directory: Path):
        logger.debug(f"TransportWindows::ensure_remote_dir_exists: check {remote_directory}")
        if self.dry_run:
            logger.debug(f"TransportWindows::ensure_remote_dir_exists: created {remote_directory}")
            return

        try:
            self.sftp.stat(str(remote_directory))
        except FileNotFoundError:
            self.sftp.mkdir(str(remote_directory))
            logger.debug(f"TransportWindows::ensure_remote_dir_exists: created {remote_directory}")

    def copy_files(
        self,
        local_path: Path,
        remote_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        guessed_len = self.guess_file_count(local_path, whitelist, recursive)
        logger.debug(f"TransportWindows::copy_files: {local_path} -> {remote_path}")
        self.connect()
        self.ensure_remote_dir_exists(remote_path)
        cnt = 1

        for _, local_filename in enumerate(local_path.iterdir()):
            logger.debug(
                f"TransportWindows::copy_files: [{cnt}/{guessed_len}] {local_filename.name}"
            )
            if callback:
                callback()
            remote_filename = remote_path / local_filename.name

            if local_filename.is_dir():
                if recursive:
                    self.copy_files(
                        local_filename, remote_path / local_filename.name, whitelist, recursive
                    )
                else:
                    continue
            else:
                cnt += 1
                if whitelist:
                    match = False
                    for item in whitelist:
                        if local_filename.suffix == item:
                            match = True
                            break
                    if not match:
                        logger.debug(
                            f"TransportWindows::copy_files: not whitelist match {local_filename}"
                        )
                        continue

                if self.dry_run:
                    logger.debug(
                        f"TransportWindows::copy_files: dry-run {local_filename} to {remote_filename}"
                    )
                    continue

                try:
                    remote_file_attr = self.sftp.stat(str(remote_filename))
                    local_file_attr = local_filename.stat()
                    if (
                        int(local_file_attr.st_mtime) > int(remote_file_attr.st_mtime)
                        or local_file_attr.st_size != remote_file_attr.st_size
                    ):
                        self.sftp.put(str(local_filename), str(remote_filename))
                        logger.debug(
                            f"TransportWindows::copy_files: newer/size {local_filename} to {remote_filename}"
                        )
                except FileNotFoundError:
                    self.sftp.put(str(local_filename), str(remote_filename))
                    logger.debug(
                        f"TransportWindows::copy_files: create {local_filename} to {remote_filename}"
                    )


class JobBase:
    pass


class GlobalJob(JobBase):
    def __init__(self, default, playlists, transport):
        self.default = default
        self.playlists = playlists
        self.transport = transport
        self.size = 1
        self.setup()

    def setup(self):
        pass


class BiosSync(GlobalJob):
    name = "BIOS"

    def setup(self):
        self.src = Path(self.default.get("local_bios"))
        self.dst = Path(self.default.get("remote_bios"))
        self.size = self.transport.guess_file_count(self.src, [], True)

    def do(self):
        self.transport.copy_files(
            self.src,
            self.dst,
            whitelist=[],
            recursive=True,
        )


class ThumbnailsSync(BiosSync):
    name = "Thumbnails"

    def setup(self):
        self.src = Path(self.default.get("local_thumbnails"))
        self.dst = Path(self.default.get("remote_thumbnails"))
        self.size = self.transport.guess_file_count(self.src, [], True)


class FavoritesSync(BiosSync):
    name = "Favorites"

    def do(self):
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate(
                Path(self.default.get("local_config")) / "content_favorites.lpl",
                temp_file,
            )
            self.transport.copy_file(
                Path(temp_file.name),
                Path(self.default.get("remote_config")) / "content_favorites.lpl",
            )

    def migrate(self, favorites_file, temp_file):
        def find_playlist(playlists, core_name):
            for p in playlists:
                if p.get("core_name") == core_name:
                    return p
            raise AssertionError()

        logger.debug(f"migrate: filename={favorites_file}")
        with open(favorites_file) as file:
            data = json.load(file)

        items = []
        local_items = data["items"]
        local_items_len = len(local_items)
        for idx, item in enumerate(local_items):
            new_item = copy.copy(item)
            playlist = find_playlist(self.playlists, new_item["core_name"])
            remote_rom_dir = Path(self.default.get("remote_roms")) / playlist.get("remote_folder")
            local_path = new_item["path"].split("#")[0]
            local_name = Path(local_path).name
            new_path = remote_rom_dir / local_name
            new_item["path"] = str(new_path)
            core_path = (
                new_item["core_path"]
                .replace(
                    self.default.get("local_cores_suffix"), self.default.get("remote_cores_suffix")
                )
                .replace(self.default.get("local_cores"), self.default.get("remote_cores"))
            )
            new_item["core_path"] = core_path
            logger.debug(f"migrate: Convert [{idx+1}/{local_items_len}] path={local_name}")
            items.append(new_item)

        data["items"] = items
        doc = json.dumps(data)
        logger.debug(json.dumps(data, indent=2))
        temp_file.write(doc.encode("utf-8"))
        temp_file.flush()
        temp_file.seek(0)


class SystemJob(JobBase):
    def __init__(self, default, transport):
        self.default = default
        self.transport = transport
        self.size = 1


class RomSyncJob(SystemJob):
    name = "Sync ROMs"

    def setup(self, playlist):
        self.playlist = playlist
        self.src = Path(self.default.get("local_roms")) / self.playlist.get("local_folder")
        self.dst = Path(self.default.get("remote_roms")) / self.playlist.get("remote_folder")
        self.size = self.transport.guess_file_count(self.src, [], True)

    def do(self, callback):
        self.transport.copy_files(
            self.src, self.dst, whitelist=[], recursive=True, callback=callback
        )


class PlaylistSyncJob(SystemJob):
    name = "Sync Playlist"

    def setup(self, playlist):
        self.playlist = playlist
        self.size = 1

    def migrate_playlist(self, temp_file):
        name = self.playlist.get("name")
        logger.debug(f"migrate_playlist: name={name}")
        local = Path(self.default.get("local_playlists")) / name
        with open(local) as file:
            data = json.load(file)

        core_path = (
            data["default_core_path"]
            .replace(
                self.default.get("local_cores_suffix"), self.default.get("remote_cores_suffix")
            )
            .replace(self.default.get("local_cores"), self.default.get("remote_cores"))
        )
        local_rom_dir = Path(self.default.get("local_roms")) / self.playlist.get("local_folder")
        remote_rom_dir = Path(self.default.get("remote_roms")) / self.playlist.get("remote_folder")
        data["default_core_path"] = core_path
        data["scan_content_dir"] = str(remote_rom_dir)
        data["scan_dat_file_path"] = ""

        items = []
        local_items = data["items"]
        local_items_len = len(local_items)
        for idx, item in enumerate(local_items):
            new_item = copy.copy(item)
            new_item["core_name"] = "DETECT"
            new_item["core_path"] = "DETECT"
            local_path = new_item["path"].split("#")[0]
            local_name = Path(local_path).name
            logger.debug(f"migrate_playlist: Convert [{idx+1}/{local_items_len}] path={local_name}")
            new_path = local_path.replace(str(local_rom_dir), str(remote_rom_dir))
            new_item["path"] = new_path
            items.append(new_item)

        data["items"] = items
        doc = json.dumps(data)
        logger.debug(json.dumps(data, indent=2))
        temp_file.write(doc.encode("utf-8"))
        temp_file.flush()
        temp_file.seek(0)

    def do(self, _callback):
        name = self.playlist.get("name")
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate_playlist(temp_file)
            self.transport.copy_file(
                Path(temp_file.name), Path(self.default.get("remote_playlists")) / name
            )


class PlaylistUpdatecJob(SystemJob):
    name = "Update Playlist"

    def setup(self, playlist):
        self.playlist = playlist
        self.size = 1

    def backup_file(self, file_path):
        original_file = Path(file_path)
        backup_file = original_file.with_suffix(original_file.suffix + ".backup")
        backup_file.write_bytes(original_file.read_bytes())
        logger.debug(f"backup_file: created {backup_file}")
        return str(backup_file)

    def make_item(self, local, file):
        stem = str(Path(file).stem)
        new_item = copy.copy(item_tpl)
        new_item["path"] = file
        new_item["label"] = self.name_map.get(stem, stem)
        new_item["db_name"] = local.name
        return new_item

    def create_m3u(self, local_rom_dir):
        logger.debug("create_m3u: Create m3u files")
        m3u_pattern = self.playlist.get("local_m3u_pattern")
        m3u_whitelist = self.playlist.get("local_m3u_whitelist")
        files = defaultdict(list)
        all_files = Path(local_rom_dir)
        for filename in all_files.iterdir():
            if re.compile(m3u_whitelist).search(str(filename)):
                e = re.compile(m3u_pattern)
                m = e.match(str(filename))
                if m:
                    base_name = m.groups()[0].strip()
                else:
                    base_name = filename.stem
                files[base_name].append(filename)
        for base_name, list_files in files.items():
            m3u_file = Path(local_rom_dir) / f"{base_name}.m3u"
            if not self.transport.dry_run:
                with open(m3u_file, "w") as f:
                    logger.debug(f"create_m3u: Create  {str(m3u_file)}")
                    for filename in sorted(list_files):
                        f.write(f"{filename.name}\n")

    def build_file_map(self, local_rom_dir, dat_file):
        name_map = {}
        if not dat_file:
            return name_map
        dat_file = local_rom_dir / dat_file
        with open(dat_file) as fd:
            data = fd.read()
        root = etree.fromstring(data)
        for game in root.xpath("//game"):
            description = game.findtext("description")
            if description:
                name_map[game.attrib["name"]] = description
                continue
            if game.attrib.get("parent"):
                continue
            identity = game.findall("identity")
            title = identity[0].findtext("title")
            if title:
                name_map[game.attrib["name"]] = title
        return name_map

    def do(self, _callback):
        name = self.playlist.get("name")
        logger.debug(f"migrate_playlist: name={name}")
        local = Path(self.default.get("local_playlists")) / name
        if not self.transport.dry_run:
            self.backup_file(local)

        with open(local) as file:
            data = json.load(file)

        local_rom_dir = Path(self.default.get("local_roms")) / self.playlist.get("local_folder")

        core_path = Path(self.default.get("local_cores")) / self.playlist.get("core_path")
        core_path = core_path.with_suffix(self.default.get("local_cores_suffix"))
        data["default_core_path"] = str(core_path)
        data["default_core_name"] = self.playlist.get("core_name")
        data["scan_content_dir"] = str(local_rom_dir)
        data["scan_dat_file_path"] = str(local_rom_dir)

        if self.playlist.get("local_create_m3u"):
            self.create_m3u(local_rom_dir)

        whitelist = self.playlist.get("local_whitelist", False)
        blacklist = self.playlist.get("local_blacklist", False)
        self.name_map = self.build_file_map(local_rom_dir, self.playlist.get("local_dat_file", ""))
        items = []
        files = glob.glob(str(local_rom_dir / "*"))
        files.sort()
        files_len = len(files)

        file_list = []
        # First Pass
        for idx, file in enumerate(files):
            logger.debug(
                f"update_playlist: Update first pass [{idx+1}/{files_len}] path={Path(file).name}"
            )
            if Path(file).is_dir():
                subs = glob.glob(str(Path(file) / "*"))
                for sub in subs:
                    file_list.append(sub)
            else:
                file_list.append(file)

        # Second Pass
        files_len = len(file_list)
        for idx, file in enumerate(file_list):
            logger.debug(
                f"update_playlist: Update second pass [{idx+1}/{files_len}] path={Path(file).name}"
            )

            if blacklist:
                if re.compile(blacklist).search(file):
                    logger.debug(f"update_playlist: Skip {Path(file).name} is blacklisted")
                    continue

            if whitelist:
                if re.compile(whitelist).search(file):
                    logger.debug(f"update_playlist: Add {Path(file).name} is whitelisted")
                    items.append(self.make_item(local, file))
            else:
                items.append(self.make_item(local, file))

        data["items"] = items
        doc = json.dumps(data, indent=2)
        logger.debug(json.dumps(data, indent=2))
        if not self.transport.dry_run:
            with open(str(local), "w") as new_file:
                new_file.write(doc)


def match_system(system_name, playlists):
    if system_name:
        dt1 = 1_000
        dt1_name = None
        dt2 = 1_000
        dt2_name = None
        for playlist in playlists:
            if playlist.get("disabled", False):
                continue
            n1 = playlist.get("name")
            n2 = playlist.get("remote_folder")
            d1 = Levenshtein.distance(n1, system_name, weights=(1, 1, 2))
            d2 = Levenshtein.distance(n2, system_name, weights=(1, 1, 2))
            if d1 < dt1:
                dt1 = d1
                dt1_name = n1
            if d2 < dt2:
                dt2 = d2
                dt2_name = n1
        if dt1 < dt2:
            return dt1_name
        else:
            return dt2_name


def expand_config(default):
    for item in [
        "local_playlists",
        "local_bios",
        "local_config",
        "local_roms",
        "local_cores",
        "local_thumbnails",
        "remote_playlists",
        "remote_bios",
        "remote_config",
        "remote_roms",
        "remote_cores",
        "remote_thumbnails",
    ]:
        default[item] = str(Path(default.get(item)).expanduser())
    return default


@click.command()
@click.option(
    "--all",
    "-a",
    "do_all",
    is_flag=True,
    help="Sync all files (ROMs, playlists,favorites, BIOS files and thumbnails)",
)
@click.option(
    "--sync-playlists",
    "-p",
    "do_sync_playlists",
    is_flag=True,
    help="Sync playlist files",
)
@click.option(
    "--sync-bios", "-b", "do_sync_bios", is_flag=True, help="Sync BIOS files to target system"
)
@click.option(
    "--sync-favorites",
    "-f",
    "do_sync_favorites",
    is_flag=True,
    help="Sync sync favorites files  to target system",
)
@click.option(
    "--sync-thumbnails",
    "-t",
    "do_sync_thumbails",
    is_flag=True,
    help="Sync thumbnails files",
)
@click.option(
    "--sync-roms", "-r", "do_sync_roms", is_flag=True, help="Sync ROMs files to target system"
)
@click.option(
    "--update-playlists",
    "-u",
    "do_update_playlists",
    is_flag=True,
    help="Update local playlist files by scanning the ROM directories for results",
)
@click.option(
    "--name",
    "-n",
    "system_name",
    default=None,
    help="Filter and process only one specific system",
)
@click.option(
    "--config-file",
    "-c",
    "config_file",
    default="steamdeck.conf",
    help="Use config file",
)
@click.option("--dry-run", "-D", is_flag=True, help="Dry run, don't sync or create anything")
@click.option(
    "--debug",
    "-d",
    "do_debug",
    is_flag=True,
    help="Enable debug logging to debug.log logfile",
)
@click.option(
    "--transport-unix",
    "force_transport",
    flag_value="unix",
    default=False,
    help="Compel usage of scp and rsync command-line utilities (faster)",
)
@click.option(
    "--transport-windows",
    "force_transport",
    flag_value="windows",
    default=False,
    help="Utilize Python's implementation of the SSH transport (slower)",
)
@click.option("--yes", is_flag=True, help="Skip prompt inputs by saying yes to everything")
def main(
    do_all,
    do_sync_playlists,
    do_sync_bios,
    do_sync_favorites,
    do_sync_thumbails,
    do_sync_roms,
    do_update_playlists,
    system_name,
    config_file,
    dry_run,
    do_debug,
    force_transport,
    yes,
):
    global logger
    if do_debug:
        logging.basicConfig(
            filename="debug.log",
            filemode="a",
            level=logging.DEBUG,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
        logger = logging.getLogger()
    else:
        logging.basicConfig(
            level=logging.WARN,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
        logger = logging.getLogger()
        logger.disabled = True

    if do_all:
        do_sync_playlists = do_sync_roms = do_sync_bios = do_sync_favorites = do_sync_thumbails = (
            do_update_playlists
        ) = True

    config = toml.load(config_file)
    default = expand_config(config.get("default"))
    playlists = config.get("playlists", [])
    if system_name:
        system_name = match_system(system_name, playlists)
        if not yes:
            if not click.confirm(f"Do you want to continue with playlists '{system_name}' ?"):
                sys.exit(-1)

    jobs = []
    transport = Transport(default, dry_run, force_transport)
    if do_sync_bios:
        jobs.append(BiosSync(default, playlists, transport))

    if do_sync_favorites:
        jobs.append(FavoritesSync(default, playlists, transport))

    if do_sync_thumbails:
        jobs.append(ThumbnailsSync(default, playlists, transport))

    system_jobs = []
    if do_update_playlists:
        system_jobs.append(PlaylistUpdatecJob(default, transport))

    if do_sync_playlists:
        system_jobs.append(PlaylistSyncJob(default, transport))

    if do_sync_roms:
        system_jobs.append(RomSyncJob(default, transport))

    if system_name:
        playlists = [p for p in config.get("playlists", []) if p.get("name") == system_name]
    else:
        playlists = config.get("playlists", [])

    if not any(
        [
            do_sync_playlists,
            do_sync_roms,
            do_sync_bios,
            do_sync_favorites,
            do_sync_thumbails,
            do_update_playlists,
        ]
    ):
        sys.exit(1)

    systems = {}
    for _, playlist in enumerate(playlists):
        name = Path(playlist.get("name")).stem
        if not playlist.get("disabled", False):
            systems[name] = {"name": name, "playlist": playlist}

    jobs_size = sum([j.size for j in jobs])
    system_size = sum([j.size for j in system_jobs])
    overall_task_id = overall_progress.add_task("", total=jobs_size + system_size)
    with Live(progress_group):
        for (
            idx,
            job,
        ) in enumerate(jobs):
            top_descr = "[bold #AAAAAA](%d out of %d jobs done)" % (
                idx,
                len(jobs),
            )
            overall_progress.update(overall_task_id, description=top_descr)
            current_task_id = current_system_progress.add_task(f"Run job {job.name}")
            system_steps_task_id = system_steps_progress.add_task("", total=2, name=job.name)
            system_steps_progress.update(system_steps_task_id, advance=1)
            job.do()
            if dry_run:
                time.sleep(0.2)
            system_steps_progress.update(system_steps_task_id, advance=1)
            system_steps_progress.update(system_steps_task_id, visible=False)
            current_system_progress.stop_task(current_task_id)
            current_system_progress.update(
                current_task_id, description=f"[bold green]{job.name} synced!"
            )
            overall_progress.update(overall_task_id, advance=1)

        overall_progress.update(
            overall_task_id,
            description=f"[bold green]{len(jobs)} jobs processed, done!",
        )

        if do_update_playlists or do_sync_playlists or do_sync_roms:
            for (
                idx,
                key,
            ) in enumerate(systems.keys()):
                name = systems[key]["name"]
                playlist = systems[key]["playlist"]
                logger.info("main: Process %s", playlist.get("name"))
                top_descr = "[bold #AAAAAA](%d out of %d systems synced)" % (
                    idx,
                    len(systems),
                )
                overall_progress.update(overall_task_id, description=top_descr)
                current_task_id = current_system_progress.add_task(f"Syncing system {name}")

                system_jobs_size = 0
                for job in system_jobs:
                    job.setup(playlist)
                    system_jobs_size += job.size

                system_steps_task_id = system_steps_progress.add_task(
                    "", total=system_jobs_size, name=name
                )
                for job in system_jobs:
                    step_task_id = step_progress.add_task("", action=job.name, name=name)
                    if do_debug:
                        time.sleep(1)

                    job.setup(playlist)
                    job.do(
                        lambda system_steps_task_id=system_steps_task_id: system_steps_progress.update(
                            system_steps_task_id, advance=1
                        )
                    )

                    step_progress.update(step_task_id, advance=1)
                    step_progress.stop_task(step_task_id)
                    step_progress.update(step_task_id, visible=False)
                    system_steps_progress.update(system_steps_task_id, advance=1)

                if dry_run:
                    time.sleep(0.2)
                system_steps_progress.update(system_steps_task_id, visible=False)
                current_system_progress.stop_task(current_task_id)
                current_system_progress.update(
                    current_task_id, description=f"[bold green]{name} synced!"
                )
                overall_progress.update(overall_task_id, advance=1)

            overall_progress.update(
                overall_task_id,
                description=f"[bold green]{len(systems)} systems processed, done!",
            )


if __name__ == "__main__":
    main()
