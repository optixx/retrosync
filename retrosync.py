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
                return TransportBaseUnix.getInstance(default, dry_run)
            elif force_transport == "windows":
                return TransportRemoteWindows.getInstance(default, dry_run)
            else:
                raise NotImplementedError

        current_platform = platform.system()
        if current_platform in ["Darwin", "Linux"]:
            return TransportBaseUnix.getInstance(default, dry_run)
        elif current_platform in ["Windows"]:
            return TransportRemoteWindows.getInstance(default, dry_run)
        else:
            print(
                f"This script only runs on macOS, Linux or Windows but you're using {current_platform}. Exiting..."
            )
            sys.exit(1)


class TransportBase:
    def guess_file_count(self, src_path: Path, whitelist: list, recursive=False):
        cnt = 0
        if recursive:
            generator = src_path.rglob("*")
        else:
            generator = src_path.glob("*")
        for filename in generator:
            if whitelist:
                for item in whitelist:
                    if filename.suffix == item:
                        cnt += 1
                        break
            else:
                cnt += 1

        return cnt


class TransportBaseUnix(TransportBase):
    @staticmethod
    def getInstance(default, dry_run):
        if default.get("target") == "remote":
            return TransportRemoteUnix(default, dry_run)
        else:
            return TransportLocalUnix(default, dry_run)

    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        logger.debug(
            f"TransportBaseUnix::__ctor__: dry_run={self.dry_run} target={default.get("target")}"
        )
        self.check()

    def check_executable_exists(self, executable_name):
        executable_path = shutil.which(executable_name)
        if not executable_path:
            print(f"Executable '{executable_name}' not found.")
            sys.exit(-1)

    def check(self):
        pass

    def ensure_dir_exists(self, path_directory: Path):
        pass

    def command_prefix(self):
        return ""

    def build_dest(self, path: Path):
        return f'"{path}"'

    def execute(self, cmd):
        logger.debug(f"execute: cmd={cmd}")
        if self.dry_run:
            return
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
        poll = select.poll()
        poll.register(p.stdout, select.POLLIN | select.POLLHUP)  # pyright: ignore
        poll.register(p.stderr, select.POLLIN | select.POLLHUP)  # pyright: ignore
        pollc = 2
        events = poll.poll()
        while pollc > 0 and len(events) > 0:
            for event in events:
                (rfd, event) = event
                if event & select.POLLIN:
                    if rfd == p.stdout.fileno():  # pyright: ignore
                        lines = p.stdout.readlines()  # pyright: ignore
                        for line in lines:
                            logger.debug("execute: stdout=%s", line)
                    if rfd == p.stderr.fileno():  # pyright: ignore
                        line = p.stderr.readline()  # pyright: ignore
                        logger.debug("execute: stderr=%s", line)
                if event & select.POLLHUP:
                    poll.unregister(rfd)
                    pollc = pollc - 1
                if pollc > 0:
                    events = poll.poll()
        p.wait()

    def copy_files(
        self,
        src_path: Path,
        dest_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        self.ensure_dir_exists(dest_path)
        args = "--outbuf=L --progress --verbose --human-readable --recursive --size-only --delete"
        if whitelist:
            for item in whitelist:
                args += f'--include="*{item}" '
            args += '--exclude="*" '
        cmd = f'{self.command_prefix()} rsync {args} "{src_path}/" {self.build_dest(dest_path)}'
        self.execute(cmd)


class TransportLocalUnix(TransportBaseUnix):
    def check(self):
        for command in ["rsync"]:
            self.check_executable_exists(command)

    def ensure_dir_exists(self, path_directory: Path):
        if not self.dry_run:
            if not path_directory.is_dir():
                path_directory.mkdir(parents=True)

    def copy_file(self, src_filename: Path, dest_filename: Path):
        self.ensure_dir_exists(dest_filename.parent)
        if not self.dry_run:
            shutil.copy(src_filename, dest_filename)


class TransportRemoteUnix(TransportBaseUnix):
    def check(self):
        for command in ["ssh", "scp", "rsync", "sshpass"]:
            self.check_executable_exists(command)

    def command_prefix(self):  # pyright: ignore
        password = self.default.get("password")
        return f'sshpass -p "{password}"'

    def build_dest(self, path):
        hostname = self.default.get("hostname")
        username = self.default.get("username")
        return f'"{username}@{hostname}:{path}"'

    def copy_file(self, src_filename: Path, dest_filename: Path):
        cmd = f'{self.command_prefix()} scp "{src_filename}" {self.build_dest(dest_filename)}'
        self.execute(cmd)

    def ensure_dir_exists(self, path_directory: Path):
        hostname = self.default.get("hostname")
        username = self.default.get("username")
        cmd = f"{self.command_prefix()} ssh {username}@{hostname} \"mkdir '{path_directory}'\""
        self.execute(cmd)


class TransportBaseWindows(TransportBase):
    @staticmethod
    def getInstance(default, dry_run):
        if default.get("target") == "remote":
            return TransportRemoteWindows(default, dry_run)
        else:
            return TransportLocalWindows(default, dry_run)


class TransportLocalWindows(TransportBaseWindows):
    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        logger.debug(f"TransportLocalWindows::__ctor__: dry_run={self.dry_run}")

    def check(self):
        pass

    def ensure_dir_exists(self, path_directory: Path):
        if not self.dry_run:
            if not path_directory.is_dir():
                path_directory.mkdir(parents=True)

    def copy_file(self, src_filename: Path, dest_filename: Path):
        self.ensure_dir_exists(dest_filename.parent)
        if not self.dry_run:
            shutil.copy(src_filename, dest_filename)

    def copy_files(
        self,
        src_path: Path,
        dest_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        guessed_len = self.guess_file_count(src_path, whitelist, recursive)
        logger.debug(f"TransportLocalWindows::copy_files: {src_path} -> {dest_path}")
        self.ensure_dir_exists(dest_path)
        cnt = 1
        for item in src_path.iterdir():
            logger.debug(f"TransportLocaleWindows::copy_files: [{cnt}/{guessed_len}] {item.name}")
            if callback:
                callback()
            cnt += 1
            s = item
            d = dest_path / item.name
            if s.is_dir():
                self.copy_files(s, d, whitelist, recursive, callback)
            else:
                if not self.dry_run:
                    shutil.copy2(s, d)


class TransportRemoteWindows(TransportBaseWindows):
    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.sftp = None
        self.connected = False
        logger.debug(f"TransportRemoteWindows::__ctor__: dry_run={self.dry_run}")

    def connect(self):
        if self.dry_run:
            return
        if self.connected:
            return
        logger.debug("TransportRemoteWindows::connect start")
        self.ssh.connect(
            self.default.get("hostname"),
            username=self.default.get("username"),
            password=self.default.get("password"),
        )
        logger.debug("TransportRemoteWindows::connect connected")
        self.sftp = self.ssh.open_sftp()
        logger.debug("TransportRemoteWindows::connect sftp opened")
        self.connected = True

    def copy_file(self, src_filename: Path, dest_filename: Path):
        self.connect()
        if self.dry_run:
            logger.debug(
                f"TransportRemoteWindows::copy_file: dry-run {src_filename} to {dest_filename}"
            )
            return

        try:
            dest_file_attr = self.sftp.stat(str(dest_filename))
            src_file_attr = src_filename.stat()
            if (
                int(src_file_attr.st_mtime) > int(dest_file_attr.st_mtime)  # type: ignore
                or src_file_attr.st_size != dest_file_attr.st_size
            ):
                self.sftp.put(str(src_filename), str(dest_filename))
                logger.debug(
                    f"TransportRemoteWindows::copy_file: newer {src_filename} to {dest_filename}"
                )
        except FileNotFoundError:
            self.sftp.put(str(src_filename), str(dest_filename))
            logger.debug(
                f"TransportRemoteWindows::copy_file: created {src_filename} to {dest_filename}"
            )

    def ensure_dir_exists(self, dest_directory: Path):
        logger.debug(f"TransportRemoteWindows::ensure_dir_exists: check {dest_directory}")
        if self.dry_run:
            logger.debug(f"TransportRemoteWindows::ensure_dir_exists: created {dest_directory}")
            return

        try:
            self.sftp.stat(str(dest_directory))
        except FileNotFoundError:
            self.sftp.mkdir(str(dest_directory))
            logger.debug(f"TransportRemoteWindows::ensure_dir_exists: created {dest_directory}")

    def copy_files(
        self,
        src_path: Path,
        dest_path: Path,
        whitelist: list,
        recursive: bool = False,
        callback=None,
    ):
        guessed_len = self.guess_file_count(src_path, whitelist, recursive)
        logger.debug(f"TransportRemoteWindows::copy_files: {src_path} -> {dest_path}")
        self.connect()
        self.ensure_dir_exists(dest_path)
        cnt = 1

        for _, src_filename in enumerate(src_path.iterdir()):
            logger.debug(
                f"TransportRemoteWindows::copy_files: [{cnt}/{guessed_len}] {src_filename.name}"
            )
            if callback:
                callback()
            dest_filename = dest_path / src_filename.name

            if src_filename.is_dir():
                if recursive:
                    self.copy_files(
                        src_filename, dest_path / src_filename.name, whitelist, recursive
                    )
                else:
                    continue
            else:
                cnt += 1
                if whitelist:
                    match = False
                    for item in whitelist:
                        if src_filename.suffix == item:
                            match = True
                            break
                    if not match:
                        logger.debug(
                            f"TransportRemoteWindows::copy_files: not whitelist match {src_filename}"
                        )
                        continue

                if self.dry_run:
                    logger.debug(
                        f"TransportRemoteWindows::copy_files: dry-run {src_filename} to {dest_filename}"
                    )
                    continue

                try:
                    dest_file_attr = self.sftp.stat(str(dest_filename))
                    src_file_attr = src_filename.stat()
                    if (
                        int(src_file_attr.st_mtime) > int(dest_file_attr.st_mtime)
                        or src_file_attr.st_size != dest_file_attr.st_size
                    ):
                        self.sftp.put(str(src_filename), str(dest_filename))
                        logger.debug(
                            f"TransportRemoteWindows::copy_files: newer/size {src_filename} to {dest_filename}"
                        )
                except FileNotFoundError:
                    self.sftp.put(str(src_filename), str(dest_filename))
                    logger.debug(
                        f"TransportRemoteWindows::copy_files: create {src_filename} to {dest_filename}"
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
        self.src = Path(self.default.get("src_bios"))
        self.dst = Path(self.default.get("dest_bios"))
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
        self.src = Path(self.default.get("src_thumbnails"))
        self.dst = Path(self.default.get("dest_thumbnails"))
        self.size = self.transport.guess_file_count(self.src, [], True)


class FavoritesSync(BiosSync):
    name = "Favorites"

    def setup(self):
        self.src = Path(self.default.get("src_config")) / "content_favorites.lpl"
        self.dst = Path(self.default.get("dest_config")) / "content_favorites.lpl"
        self.size = 1

    def do(self):
        with tempfile.NamedTemporaryFile() as temp_file:
            self.migrate(
                self.src,
                temp_file,
            )
            self.transport.copy_file(
                Path(temp_file.name),
                self.dst,
            )

    def migrate(self, favorites_file, temp_file):
        def find_playlist(playlists, src_core_name):
            for p in playlists:
                if p.get("src_core_name") == src_core_name:
                    return p
            print(f"Can not find core {src_core_name}")
            raise AssertionError()

        logger.debug(f"migrate: filename={favorites_file}")
        with open(favorites_file) as file:
            data = json.load(file)

        items = []
        src_items = data["items"]
        src_items_len = len(src_items)
        for idx, item in enumerate(src_items):
            new_item = copy.copy(item)
            playlist = find_playlist(self.playlists, new_item["core_name"])
            dest_rom_dir = Path(self.default.get("target_roms")) / playlist.get("dest_folder")
            src_path = new_item["path"].split("#")[0]
            src_name = Path(src_path).name
            new_path = dest_rom_dir / src_name
            new_item["path"] = str(new_path)
            core_path = (
                new_item["core_path"]
                .replace(
                    self.default.get("src_cores_suffix"), self.default.get("target_cores_suffix")
                )
                .replace(self.default.get("src_cores"), self.default.get("target_cores"))
            )
            new_item["core_path"] = core_path
            logger.debug(f"migrate: Convert [{idx+1}/{src_items_len}] path={src_name}")
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
        self.src = Path(self.default.get("src_roms")) / self.playlist.get("src_folder")
        self.dst = Path(self.default.get("dest_roms")) / self.playlist.get("dest_folder")
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
        local = Path(self.default.get("src_playlists")) / name
        with open(local) as file:
            data = json.load(file)

        core_path = (
            data["default_core_path"]
            .replace(self.default.get("src_cores_suffix"), self.default.get("target_cores_suffix"))
            .replace(self.default.get("src_cores"), self.default.get("target_cores"))
        )
        src_rom_dir = Path(self.default.get("src_roms")) / self.playlist.get("src_folder")
        src_rom_alt_dir = Path(self.default.get("src_roms_alt")) / self.playlist.get("src_folder")
        target_rom_dir = Path(self.default.get("target_roms")) / self.playlist.get("dest_folder")
        data["default_core_path"] = core_path
        data["scan_content_dir"] = str(target_rom_dir)
        data["scan_dat_file_path"] = ""

        items = []
        src_items = data["items"]
        src_items_len = len(src_items)
        for idx, item in enumerate(src_items):
            new_item = copy.copy(item)
            new_item["core_name"] = "DETECT"
            new_item["core_path"] = "DETECT"
            src_path = new_item["path"].split("#")[0]
            src_name = Path(src_path).name
            logger.debug(f"migrate_playlist: Convert [{idx+1}/{src_items_len}] path={src_name}")
            new_path = src_path.replace(str(src_rom_dir), str(target_rom_dir))
            new_path = new_path.replace(str(src_rom_alt_dir), str(target_rom_dir))
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
                Path(temp_file.name), Path(self.default.get("dest_playlists")) / name
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

    def create_m3u(self, src_rom_dir):
        logger.debug("create_m3u: Create m3u files")
        m3u_pattern = self.playlist.get("src_m3u_pattern")
        m3u_whitelist = self.playlist.get("src_m3u_whitelist")
        files = defaultdict(list)
        all_files = Path(src_rom_dir)
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
            m3u_file = Path(src_rom_dir) / f"{base_name}.m3u"
            if not self.transport.dry_run:
                with open(m3u_file, "w") as f:
                    logger.debug(f"create_m3u: Create  {str(m3u_file)}")
                    for filename in sorted(list_files):
                        f.write(f"{filename.name}\n")

    def build_file_map(self, src_rom_dir, dat_file):
        name_map = {}
        if not dat_file:
            return name_map
        dat_file = src_rom_dir / dat_file
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
        local = Path(self.default.get("src_playlists")) / name
        if not self.transport.dry_run:
            self.backup_file(local)

        with open(local) as file:
            data = json.load(file)

        src_rom_dir = Path(self.default.get("src_roms")) / self.playlist.get("src_folder")

        core_path = Path(self.default.get("src_cores")) / self.playlist.get("src_core_path")
        core_path = core_path.with_suffix(self.default.get("src_cores_suffix"))
        data["default_core_path"] = str(core_path)
        data["default_core_name"] = self.playlist.get("src_core_name")
        data["scan_content_dir"] = str(src_rom_dir)
        data["scan_dat_file_path"] = str(src_rom_dir)

        if self.playlist.get("src_create_m3u"):
            self.create_m3u(src_rom_dir)

        whitelist = self.playlist.get("src_whitelist", False)
        blacklist = self.playlist.get("src_blacklist", False)
        self.name_map = self.build_file_map(src_rom_dir, self.playlist.get("src_dat_file", ""))
        items = []
        files = glob.glob(str(src_rom_dir / "*"))
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
        needle = system_name.lower()
        dt1 = 1_000
        dt1_name = None
        dt2 = 1_000
        dt2_name = None
        for playlist in playlists:
            if playlist.get("disabled", False):
                logger.debug("disabled")
                continue
            playlist_name = playlist.get("name")
            n1 = playlist_name.lower()
            n2 = playlist.get("dest_folder").lower()
            d1 = Levenshtein.distance(n1, needle, weights=(1, 1, 2))
            d2 = Levenshtein.distance(n2, needle, weights=(1, 1, 2))
            logger.debug(f"system: {system_name} {n1}:{d1}   {n2}:{d2}")
            if d1 < dt1 and d1 < len(playlist_name):
                logger.debug(f"set d1: {d1}  {playlist_name}")
                dt1 = d1
                dt1_name = playlist_name
            if d2 < dt2 and d2 < len(playlist_name):
                logger.debug(f"set d2: {d2}  {playlist_name}")
                dt2 = d2
                dt2_name = playlist_name
        if dt1 < dt2:
            logger.debug(f"return dt1 {dt1_name}")
            return dt1_name
        else:
            logger.debug(f"return dt2 {dt2_name}")
            return dt2_name


def expand_config(default):
    for item in [
        "src_playlists",
        "src_bios",
        "src_config",
        "src_roms",
        "src_roms_alt",
        "src_cores",
        "src_thumbnails",
        "dest_playlists",
        "dest_bios",
        "dest_config",
        "dest_roms",
        "dest_thumbnails",
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
