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
import Levenshtein
from pathlib import Path
from collections import defaultdict
from lxml import etree

logging.basicConfig(
    level=logging.DEBUG, format="%(asctime)s - %(levelname)s - %(message)s"
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
    name = playlist.get("name")
    logger.debug(f"migrate_playlist: name={name}")
    local = Path(default.get("local_playlists")) / name
    if not dry_run:
        backup_file(local)

    with open(local, "r") as file:
        data = json.load(file)

    local_rom_dir = Path(default.get("local_roms_alt")) / playlist.get("local_folder")
    assert os.path.isdir(local_rom_dir)
    prefer_metadata_files = find_metadata(local_rom_dir)
    name_map = find_dat(local_rom_dir)
    items = []
    files = glob.glob(str(local_rom_dir / "*"))
    files.sort()
    files_len = len(files)
    for idx, file in enumerate(files):
        if Path(file).is_dir():
            subs = glob.glob(str(Path(file) / "*.cue"))
            if len(subs) == 1:
                file = subs.pop()

        if prefer_metadata_files:
            if Path(file).suffix not in metadata_suffixes:
                continue

        stem = str(Path(file).stem)
        new_item = copy.copy(item_tpl)
        new_item["path"] = file
        new_item["label"] = name_map.get(stem, stem)
        new_item["db_name"] = local.name
        logger.debug(f"update_playlist: Update [{idx+1}/{files_len}] path={file}")
        items.append(new_item)

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
    local_rom_dir_alt = Path(default.get("local_roms_alt")) / playlist.get(
        "local_folder"
    )
    assert os.path.isdir(local_rom_dir)
    assert os.path.isdir(local_rom_dir_alt)
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
        new_path = new_path.replace(str(local_rom_dir_alt), str(remote_rom_dir))
        new_item["path"] = new_path
        items.append(new_item)

    data["items"] = items
    doc = json.dumps(data)
    logger.debug(json.dumps(data, indent=2))
    assert str(local_rom_dir) not in doc
    assert str(local_rom_dir_alt) not in doc
    temp_file.write(doc.encode("utf-8"))
    temp_file.flush()
    temp_file.seek(0)


def copy_playlist(default, playlist, temp_file, dry_run):
    name = playlist.get("name")
    logger.debug("copy_playlist: name={name}")
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


@click.command()
@click.option("--all", "-a", "do_all", is_flag=True, help="Sync all file")
@click.option(
    "--sync-playlists", "-p", "do_sync_playlists", is_flag=True, help="Sync playlist"
)
@click.option("--sync-bios", "-b", "do_sync_bios", is_flag=True, help="Sync bios files")
@click.option(
    "--sync-thumbnails",
    "-t",
    "do_sync_thumbails",
    is_flag=True,
    help="Sync thumbnails files",
)
@click.option("--sync-roms", "-r", "do_sync_roms", is_flag=True, help="Sync roms files")
@click.option(
    "--update-playlists",
    "-u",
    "do_update_playlists",
    is_flag=True,
    help="Update local playlist",
)
@click.option(
    "--sync-roms-local",
    "-l",
    default=None,
    help="Sync roms files to local path mounted path (Sdcard)",
)
@click.option(
    "--name", "-n", "system_name", default=None, help="Process only one specific system"
)
@click.option("--dry-run", "-D", is_flag=True, help="Dry run")
@click.option("--debug", "-d", "do_debug", is_flag=True, help="Enable debug logging")
@click.option("--yes", is_flag=True)
def main(
    do_all,
    do_sync_playlists,
    do_sync_bios,
    do_sync_thumbails,
    do_sync_roms,
    do_update_playlists,
    sync_roms_local,
    system_name,
    dry_run,
    do_debug,
    yes,
):
    if do_debug:
        logger.setLevel(logging.DEBUG)

    for command in ["ssh", "scp", "rsync"]:
        check_executable_exists(command)

    if do_all:
        do_sync_playlists = do_sync_roms = do_sync_bios = True

    config = toml.load("config.toml")
    default = config.get("default")
    playlists = config.get("playlists", [])
    if system_name:
        system_name = match_system(system_name, playlists)
        if not yes:
            if not click.confirm(
                f"Do you want to continue with playlists '{system_name}' ?"
            ):
                sys.exit(-1)

    if do_sync_bios:
        sync_bios(default, dry_run)

    if do_sync_thumbails:
        sync_thumbnails(default, dry_run)

    if do_sync_playlists or do_sync_roms or system_name:
        for playlist in config.get("playlists", []):
            if system_name and system_name != playlist.get("name"):
                logger.debug(
                    "main: Skip %s looking for config %s",
                    playlist.get("name"),
                    system_name,
                )
                continue

            logger.info("main: Process %s", playlist.get("name"))

            if do_update_playlists:
                update_playlist(default, playlist, dry_run)

            if do_sync_playlists:
                with tempfile.NamedTemporaryFile() as temp_file:
                    migrate_playlist(default, playlist, temp_file, dry_run)
                    copy_playlist(default, playlist, temp_file, dry_run)

            if do_sync_roms:
                sync_roms(default, playlist, sync_roms_local, dry_run)


if __name__ == "__main__":
    main()
