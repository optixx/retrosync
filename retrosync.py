#!/usr/bin/env python3

__author__ = "Optixx"
__license__ = "MIT"
__version__ = "0.1.0"
__maintainer__ = "David Voswinkel"
__email__ = "david@optixx.org"

import os
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
metadata_suffixes = [".cue", ".m3u"]

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
    TextColumn(
        "[bold blue]Progress for system {task.fields[name]}: {task.percentage:.0f}%"
    ),
    BarColumn(),
    TextColumn("({task.completed} of {task.total} steps done)"),
)

overall_progress = Progress(
    TimeElapsedColumn(), BarColumn(), TextColumn("{task.description}")
)

progress_group = Group(
    Panel(Group(current_system_progress, step_progress, system_steps_progress)),
    overall_progress,
)


def check_platform():
    current_platform = platform.system()
    if current_platform not in ["Darwin", "Linux"]:
        print(
            f"This script only runs on macOS or Linux, but you're using {current_platform}. Exiting..."
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
    p = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True
    )
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


def find_dat(local_rom_dir):
    name_map = {}
    files = glob.glob(str(local_rom_dir / "*.dat"))
    if not len(files) == 1:
        return name_map
    dat_file = files.pop()
    with open(dat_file, "r") as fd:
        data = fd.read()
    root = etree.fromstring(data)
    for game in root.xpath("//game"):
        name_map[game.attrib["name"]] = game.findtext("description")
    return name_map


def find_metadata(local_rom_dir):
    files = glob.glob(str(local_rom_dir / "*"))
    files.sort()
    suffixes = defaultdict(int)
    names = defaultdict(int)
    for file in files:
        suffixes[Path(file).suffix] += 1
        names[Path(file).stem] += 1

    # Are there any metadata files
    # And do we have multiple files with the same stem?
    if (
        set(metadata_suffixes).issubset(suffixes.keys())
        and max(list(set(names.values()))) >= 2
    ):
        return True
    return False


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

    with open(local, "r") as file:
        data = json.load(file)

    local_rom_dir = Path(default.get("local_roms")) / playlist.get("local_folder")
    assert os.path.isdir(local_rom_dir)

    core_path = Path(default.get("local_cores")) / playlist.get("core_path")
    core_path = core_path.with_suffix(default.get("local_cores_suffix"))
    data["default_core_path"] = str(core_path)
    data["default_core_name"] = playlist.get("core_name")
    data["scan_content_dir"] = str(local_rom_dir)
    data["scan_dat_file_path"] = str(local_rom_dir)

    prefer_metadata_files = find_metadata(local_rom_dir)
    name_map = find_dat(local_rom_dir)
    items = []
    files = glob.glob(str(local_rom_dir / "*"))
    files.sort()
    files_len = len(files)
    for idx, file in enumerate(files):
        logger.debug(f"update_playlist: Update [{idx+1}/{files_len}] path={file}")
        if Path(file).is_dir():
            subs = glob.glob(str(Path(file) / "*.cue"))
            if len(subs) == 1:
                file = subs.pop()
                items.append(make_item(file))
                continue

            subs = glob.glob(str(Path(file) / "*FD*.zip"))
            if len(subs) > 0:
                for file in subs:
                    items.append(make_item(file))
                continue

            subs = glob.glob(str(Path(file) / "*.zip"))
            if len(subs) > 0:
                for file in subs:
                    items.append(make_item(file))
                continue

        if prefer_metadata_files:
            if Path(file).suffix not in metadata_suffixes:
                continue
        items.append(make_item(file))

    data["items"] = items
    doc = json.dumps(data, indent=2)
    logger.debug(json.dumps(data, indent=2))
    if not dry_run:
        with open(str(local), "w") as new_file:
            new_file.write(doc)


def migrate_playlist(default, playlist, temp_file, dry_run):
    name = playlist.get("name")
    logger.debug(f"migrate_playlist: name={name}")
    local = Path(default.get("local_playlists")) / name
    with open(local, "r") as file:
        data = json.load(file)

    core_path = (
        data["default_core_path"]
        .replace(default.get("local_cores_suffix"), default.get("remote_cores_suffix"))
        .replace(default.get("local_cores"), default.get("remote_cores"))
    )
    local_rom_dir = Path(default.get("local_roms")) / playlist.get("local_folder")
    assert os.path.isdir(local_rom_dir)
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
        local_name = os.path.basename(local_path)
        logger.debug(
            f"migrate_playlist: Convert [{idx+1}/{local_items_len}] path={local_name}"
        )
        assert os.path.isfile(local_path)
        new_path = local_path.replace(str(local_rom_dir), str(remote_rom_dir))
        new_item["path"] = new_path
        items.append(new_item)

    data["items"] = items
    doc = json.dumps(data)
    logger.debug(json.dumps(data, indent=2))
    assert str(local_rom_dir) not in doc
    temp_file.write(doc.encode("utf-8"))
    temp_file.flush()
    temp_file.seek(0)


def copy_playlist(default, playlist, temp_file, dry_run):
    name = playlist.get("name")
    logger.debug(f"copy_playlist: name={name}")
    hostname = default.get("hostname")
    remote = Path(default.get("remote_playlists")) / name
    cmd = f"ssh {hostname} \"cp -v '{remote}' '{remote}.bak'\""
    execute(cmd, dry_run)
    cmd = f'scp "{temp_file.name}" "{hostname}:{remote}"'
    execute(cmd, dry_run)


def sync_roms(default, playlist, sync_roms_local, dry_run):
    name = playlist.get("name")
    logger.debug(f"sync_roms: name={name}")
    hostname = default.get("hostname")
    local_rom_dir = Path(default.get("local_roms")) / playlist.get("local_folder")
    if not sync_roms_local:
        remote_rom_dir = Path(default.get("remote_roms")) / playlist.get(
            "remote_folder"
        )
    else:
        assert os.path.isdir(str(sync_roms_local))
        remote_rom_dir = Path(sync_roms_local) / playlist.get("remote_folder")

    assert os.path.isdir(local_rom_dir)
    if not sync_roms_local:
        cmd = f"ssh {hostname} \"mkdir '{remote_rom_dir}'\""
        execute(cmd, dry_run)
        cmd = f'rsync --outbuf=L --recursive --progress --verbose --human-readable --size-only --ignore-times --delete --exclude="media" --exclude="*.txt" "{local_rom_dir}/" "{hostname}:{remote_rom_dir}"'
        execute(cmd, dry_run)
    else:
        cmd = f"mkdir '{remote_rom_dir}'"
        execute(cmd, dry_run)
        cmd = f'rsync --outbuf=L --progress --recursive --verbose --human-readable --size-only --ignore-times --delete --exclude="media" --exclude="*.txt" "{local_rom_dir}/" "{remote_rom_dir}"'
        execute(cmd, dry_run)


def sync_bios(default, dry_run):
    logger.info("sync_bios:")
    hostname = default.get("hostname")
    local_bios = Path(default.get("local_bios"))
    remote_bios = Path(default.get("remote_bios"))
    assert os.path.isdir(local_bios)
    cmd = f'rsync --outbuf=L --progress --recursive --progress --verbose --human-readable --include="*.zip" --include="*.bin" --include="*.img" --include="*.rom" --exclude="*" "{local_bios}/" "{hostname}:{remote_bios}"'
    execute(cmd, dry_run)


def sync_thumbnails(default, dry_run):
    logger.info("sync_thumbnails:")
    hostname = default.get("hostname")
    local_bios = Path(default.get("local_thumbnails"))
    remote_bios = Path(default.get("remote_thumbnails"))
    assert os.path.isdir(local_bios)
    cmd = f'rsync --outbuf=L --progress --recursive --progress --verbose --human-readable --delete "{local_bios}/" "{hostname}:{remote_bios}"'
    execute(cmd, dry_run)


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
@click.option(
    "--dry-run", "-D", is_flag=True, help="Dry run, don't sync or create anything"
)
@click.option(
    "--debug",
    "-d",
    "do_debug",
    is_flag=True,
    help="Enable debug logging to debug.log logfile",
)
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
            if not click.confirm(
                f"Do you want to continue with playlists '{system_name}' ?"
            ):
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
        playlists = [
            p for p in config.get("playlists", []) if p.get("name") == system_name
        ]
    else:
        playlists = config.get("playlists", [])

    systems = {}
    for idx, playlist in enumerate(playlists):
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
            current_task_id = current_system_progress.add_task("Run job %s" % name)
            system_steps_task_id = system_steps_progress.add_task(
                "", total=2, name=name
            )
            system_steps_progress.update(system_steps_task_id, advance=1)
            handler(default, dry_run)
            if dry_run:
                time.sleep(0.2)
            system_steps_progress.update(system_steps_task_id, advance=1)
            system_steps_progress.update(system_steps_task_id, visible=False)
            current_system_progress.stop_task(current_task_id)
            current_system_progress.update(
                current_task_id, description="[bold green]%s synced!" % name
            )
            overall_progress.update(overall_task_id, advance=1)

        overall_progress.update(
            overall_task_id,
            description="[bold green]%s jobs processed, done!" % len(systems),
        )

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
            current_task_id = current_system_progress.add_task(
                "Syncing system %s" % name
            )
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
                        copy_playlist(default, playlist, temp_file, dry_run)

                if do_sync_roms:
                    sync_roms(default, playlist, sync_roms_local, dry_run)

                step_progress.update(step_task_id, advance=1)
                step_progress.stop_task(step_task_id)
                step_progress.update(step_task_id, visible=False)
                system_steps_progress.update(system_steps_task_id, advance=1)

            if dry_run:
                time.sleep(0.2)
            system_steps_progress.update(system_steps_task_id, visible=False)
            current_system_progress.stop_task(current_task_id)
            current_system_progress.update(
                current_task_id, description="[bold green]%s synced!" % name
            )
            overall_progress.update(overall_task_id, advance=1)

        overall_progress.update(
            overall_task_id,
            description="[bold green]%s systems processed, done!" % len(systems),
        )


if __name__ == "__main__":
    main()
