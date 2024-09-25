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
            raise NotImplementedError


class TransportUnix:
    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        logger.debug(f"TransportUnix::__ctor__: dry_run={self.dry_run}")

    def execute(self, cmd):
        execute(cmd, self.dry_run)

    def copy_file(self, local_filename: Path, remote_filename: Path):
        hostname = self.default.get("hostname")
        cmd = f'scp "{local_filename}" "{hostname}:{remote_filename}"'
        self.execute(cmd)

    def ensure_remote_dir_exists(self, remote_directory: Path):
        hostname = self.default.get("hostname")
        cmd = f"ssh {hostname} \"mkdir '{remote_directory}'\""
        self.execute(cmd)

    def copy_files(self, local_path: Path, remote_path: Path, whitelist: list):
        hostname = self.default.get("hostname")
        if whitelist:
            includes = ""
            for item in whitelist:
                includes += f'--include="*{item}" '

            cmd = f'rsync --outbuf=L --progress --recursive --verbose --human-readable {includes} --exclude="*" "{local_path}/" "{hostname}:{remote_path}"'
        else:
            cmd = f'rsync --outbuf=L --progress --recursive --verbose --human-readable "{local_path}/" "{hostname}:{remote_path}"'
        self.execute(cmd)

    def ensure_local_dir_exists(self, local_directory: Path):
        cmd = f"mkdir '{local_directory}'"
        self.execute(cmd)

    def copy_local_files(
        self, local_source_path: Path, local_destination_path: Path, whitelist: list
    ):
        self.ensure_local_dir_exists(local_destination_path)
        if whitelist:
            includes = ""
            for item in whitelist:
                includes += f'--include="*{item}" '

            cmd = f'rsync --outbuf=L --progress --recursive --verbose --human-readable {includes} --exclude="*" "{local_source_path}/" "{local_destination_path}"'
        else:
            cmd = f'rsync --outbuf=L --progress --recursive --verbose --human-readable "{local_source_path}/" "{local_destination_path}"'
        self.execute(cmd)


class TransportWindows:
    def __init__(self, default, dry_run):
        self.default = default
        self.dry_run = dry_run
        self.ssh = paramiko.SSHClient()
        self.ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.connected = False
        logger.debug(f"TransportWindows::__ctor__: dry_run={self.dry_run}")

    def connect(self):
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
        try:
            remote_file_attr = self.sftp.stat(str(remote_filename))
            local_file_attr = local_filename.stat()
            if remote_file_attr.st_mtime and int(local_file_attr.st_mtime) > int(
                remote_file_attr.st_mtime
            ):
                if not self.dry_run:
                    self.sftp.put(str(local_filename), str(remote_filename))
                logger.debug(
                    f"TransportWindows::copy_file: Uploaded newer file {local_filename} to {remote_filename}"
                )
        except FileNotFoundError:
            if not self.dry_run:
                self.sftp.put(str(local_filename), str(remote_filename))
            logger.debug(
                f"TransportWindows::copy_file: not found on remote. Uploaded  {local_filename} to {remote_filename}"
            )

    def ensure_remote_dir_exists(self, remote_directory: Path):
        try:
            self.sftp.stat(str(remote_directory))
        except FileNotFoundError:
            if not self.dry_run:
                self.sftp.mkdir(str(remote_directory))
            logger.debug(f"TransportWindows::ensure_remote_dir_exists: created {remote_directory}")

    def copy_files(self, local_path: Path, remote_path: Path, whitelist: list):
        self.connect()
        self.ensure_remote_dir_exists(remote_path)

        for local_filename in local_path.iterdir():
            remote_filename = remote_path / local_filename.name

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

            try:
                remote_file_attr = self.sftp.stat(str(remote_filename))
                local_file_attr = local_filename.stat()
                if remote_file_attr.st_mtime and int(local_file_attr.st_mtime) > int(
                    remote_file_attr.st_mtime
                ):
                    if not self.dry_run:
                        self.sftp.put(str(local_filename), str(remote_filename))
                    logger.debug(
                        f"TransportWindows::copy_files: Uploaded newer file {local_filename} to {remote_filename}"
                    )
            except FileNotFoundError:
                if not self.dry_run:
                    self.sftp.put(str(local_filename), str(remote_filename))
                logger.debug(
                    f"TransportWindows::copy_files: not found on remote. Uploaded  {local_filename} to {remote_filename}"
                )

    def ensure_local_dir_exists(self, local_directory: Path):
        raise NotImplementedError

    def copy_local_files(
        self, local_source_path: Path, local_destination_path: Path, whitelist: list
    ):
        raise NotImplementedError


def check_platform():
    current_platform = platform.system()
    if current_platform not in ["Darwin", "Linux", "Windows"]:
        print(
            f"This script only runs on macOS, Linux or Windows but you're using {current_platform}. Exiting..."
        )
        sys.exit(1)
    else:
        print(f"Running on {current_platform}. Proceeding...")


def check_executable_exists(executable_name):
    executable_path = shutil.which(executable_name)
    if not executable_path:
        print(f"Executable '{executable_name}' not found.")
        sys.exit(-1)


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


def execute(cmd, dry_run):
    logger.debug("execute: cmd=%s", cmd)
    if dry_run:
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


def backup_file(file_path):
    original_file = Path(file_path)
    backup_file = original_file.with_suffix(original_file.suffix + ".backup")
    backup_file.write_bytes(original_file.read_bytes())
    logger.debug(f"backup_file: created {backup_file}")
    return str(backup_file)


def build_file_map(local_rom_dir, dat_file):
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


def create_m3u(playlist, local_rom_dir, dry_run):
    logger.debug("create_m3u: Create m3u files")
    m3u_pattern = playlist.get("local_m3u_pattern")
    m3u_whitelist = playlist.get("local_m3u_whitelist")
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
        if not dry_run:
            with open(m3u_file, "w") as f:
                logger.debug(f"create_m3u: Create  {str(m3u_file)}")
                for filename in sorted(list_files):
                    f.write(f"{filename.name}\n")


def update_playlist(default, playlist, dry_run):
    def make_item(file):
        stem = str(Path(file).stem)
        new_item = copy.copy(item_tpl)
        new_item["path"] = file
        new_item["label"] = name_map.get(stem, stem)
        new_item["db_name"] = local.name
        return new_item

    name = playlist.get("name")
    logger.debug(f"migrate_playlist: name={name}")
    local = Path(default.get("local_playlists")) / name
    if not dry_run:
        backup_file(local)

    with open(local) as file:
        data = json.load(file)

    local_rom_dir = Path(default.get("local_roms")) / playlist.get("local_folder")

    core_path = Path(default.get("local_cores")) / playlist.get("core_path")
    core_path = core_path.with_suffix(default.get("local_cores_suffix"))
    data["default_core_path"] = str(core_path)
    data["default_core_name"] = playlist.get("core_name")
    data["scan_content_dir"] = str(local_rom_dir)
    data["scan_dat_file_path"] = str(local_rom_dir)

    if playlist.get("local_create_m3u"):
        create_m3u(playlist, local_rom_dir, dry_run)

    whitelist = playlist.get("local_whitelist", False)
    blacklist = playlist.get("local_blacklist", False)
    name_map = build_file_map(local_rom_dir, playlist.get("local_dat_file", ""))
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
                items.append(make_item(file))
        else:
            items.append(make_item(file))

    data["items"] = items
    doc = json.dumps(data, indent=2)
    logger.debug(json.dumps(data, indent=2))
    if not dry_run:
        with open(str(local), "w") as new_file:
            new_file.write(doc)


def migrate_playlist(default, playlist, temp_file, _dry_run):
    name = playlist.get("name")
    logger.debug(f"migrate_playlist: name={name}")
    local = Path(default.get("local_playlists")) / name
    with open(local) as file:
        data = json.load(file)

    core_path = (
        data["default_core_path"]
        .replace(default.get("local_cores_suffix"), default.get("remote_cores_suffix"))
        .replace(default.get("local_cores"), default.get("remote_cores"))
    )
    local_rom_dir = Path(default.get("local_roms")) / playlist.get("local_folder")
    remote_rom_dir = Path(default.get("remote_roms")) / playlist.get("remote_folder")
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


def copy_playlist(default, transport, playlist, temp_file):
    name = playlist.get("name")
    logger.debug(f"copy_playlist: name={name}")
    transport.copy_file(Path(temp_file.name), Path(default.get("remote_playlists")) / name)


def sync_roms(default, transport, playlist, sync_roms_local):
    name = playlist.get("name")
    logger.debug(f"sync_roms: name={name}")
    local_rom_dir = Path(default.get("local_roms")) / playlist.get("local_folder")
    if sync_roms_local:
        remote_rom_dir = Path(sync_roms_local) / playlist.get("remote_folder")
        transport.copy_local_files(local_rom_dir, remote_rom_dir)
    else:
        remote_rom_dir = Path(default.get("remote_roms")) / playlist.get("remote_folder")
        transport.copy_files(local_rom_dir, remote_rom_dir)


def sync_bios(default, transport):
    logger.info("sync_bios:")
    transport.copy_files(
        Path(default.get("local_bios")),
        Path(default.get("remote_bios")),
        [".zip", ".bin", ".img", ".rom"],
    )


def sync_thumbnails(default, transport):
    logger.info("sync_thumbnails:")
    transport.copy_files(
        Path(default.get("local_thumbnails")), Path(default.get("remote_thumbnails")), []
    )


def expand_config(default):
    for item in [
        "local_playlists",
        "local_bios",
        "local_roms",
        "local_cores",
        "local_thumbnails",
        "remote_playlists",
        "remote_bios",
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
    help="Sync all files (ROMs, playlists, BIOS files and thumbnails)",
)
@click.option(
    "--sync-playlists",
    "-p",
    "do_sync_playlists",
    is_flag=True,
    help="Sync playlist files",
)
@click.option("--sync-bios", "-b", "do_sync_bios", is_flag=True, help="Sync BIOS files")
@click.option(
    "--sync-thumbnails",
    "-t",
    "do_sync_thumbails",
    is_flag=True,
    help="Sync thumbnails files",
)
@click.option("--sync-roms", "-r", "do_sync_roms", is_flag=True, help="Sync ROMs files")
@click.option(
    "--update-playlists",
    "-u",
    "do_update_playlists",
    is_flag=True,
    help="Update local playlist files with the results from scanning the local ROM folders",
)
@click.option(
    "--sync-roms-local",
    "-l",
    default=None,
    help="Sync ROMs to local path, to e.g sync to mounted SDcard)",
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
@click.option("--transport-unix", "force_transport", flag_value="unix", default=False)
@click.option("--transport-windows", "force_transport", flag_value="windows", default=False)
@click.option("--yes", is_flag=True, help="Skip prompt inputs")
def main(
    do_all,
    do_sync_playlists,
    do_sync_bios,
    do_sync_thumbails,
    do_sync_roms,
    do_update_playlists,
    sync_roms_local,
    system_name,
    config_file,
    dry_run,
    do_debug,
    force_transport,
    yes,
):
    global logger
    check_platform()
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

    for command in ["ssh", "scp", "rsync"]:
        check_executable_exists(command)

    if do_all:
        do_sync_playlists = do_sync_roms = do_sync_bios = do_sync_thumbails = (
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

    jobs = {}
    if do_sync_bios:
        jobs["bios"] = {"name": "BIOS", "handler": "sync_bios"}

    if do_sync_thumbails:
        jobs["thumbnails"] = {"name": "Thumbnails", "handler": "sync_thumbnails"}

    system_step_actions = {}
    if do_update_playlists:
        system_step_actions["update_playlist"] = {"name": "Update Playlist"}

    if do_sync_playlists:
        system_step_actions["sync_playlists"] = {"name": "Sync Playlist"}

    if do_sync_roms:
        system_step_actions["sync_roms"] = {"name": "Sync ROMs"}

    if system_name:
        playlists = [p for p in config.get("playlists", []) if p.get("name") == system_name]
    else:
        playlists = config.get("playlists", [])
    if not any(
        [
            do_sync_playlists,
            do_sync_roms,
            do_sync_bios,
            do_sync_thumbails,
            do_update_playlists,
        ]
    ):
        sys.exit(1)

    print(force_transport)

    transport = Transport(default, dry_run, force_transport)

    systems = {}
    for _, playlist in enumerate(playlists):
        name = Path(playlist.get("name")).stem
        if not playlist.get("disabled", False):
            systems[name] = {"name": name, "playlist": playlist}

    overall_task_id = overall_progress.add_task("", total=len(jobs) + len(systems))

    with Live(progress_group):
        for (
            idx,
            key,
        ) in enumerate(jobs.keys()):
            name = jobs[key]["name"]
            handler = globals()[jobs[key]["handler"]]
            top_descr = "[bold #AAAAAA](%d out of %d jobs done)" % (
                idx,
                len(jobs),
            )
            overall_progress.update(overall_task_id, description=top_descr)
            current_task_id = current_system_progress.add_task(f"Run job {name}")
            system_steps_task_id = system_steps_progress.add_task("", total=2, name=name)
            system_steps_progress.update(system_steps_task_id, advance=1)
            handler(default, transport)
            if dry_run:
                time.sleep(0.2)
            system_steps_progress.update(system_steps_task_id, advance=1)
            system_steps_progress.update(system_steps_task_id, visible=False)
            current_system_progress.stop_task(current_task_id)
            current_system_progress.update(
                current_task_id, description=f"[bold green]{name} synced!"
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
                system_steps_task_id = system_steps_progress.add_task(
                    "", total=len(system_step_actions), name=name
                )
                for action_key in system_step_actions:
                    action = system_step_actions[action_key]["name"]
                    step_task_id = step_progress.add_task("", action=action, name=name)
                    if dry_run:
                        time.sleep(0.2)

                    if do_update_playlists:
                        update_playlist(default, playlist, dry_run)

                    if do_sync_playlists:
                        with tempfile.NamedTemporaryFile() as temp_file:
                            migrate_playlist(default, playlist, temp_file, dry_run)
                            copy_playlist(default, transport, playlist, temp_file)

                    if do_sync_roms:
                        sync_roms(default, transport, playlist, sync_roms_local)

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
