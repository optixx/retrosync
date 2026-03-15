#!/usr/bin/env python3

__author__ = "Optixx"
__license__ = "MIT"
__version__ = "0.1.0"
__maintainer__ = "David Voswinkel"
__email__ = "david@optixx.org"

import concurrent
import logging
import os
import re
import sys
from pathlib import Path

import click
import toml
from rich.console import Console
from rich.live import Live
from rich.table import Table

from retrosync_core.config import (
    PlaylistConfigModel,
    RuntimeConfigModel,
    expand_config,
    normalize_playlists,
    normalize_transport_config,
    rank_system_matches,
    validate_runtime_config,
)
from retrosync_core.jobs import (
    BiosSync,
    FavoritesSync,
    GlobalJob,
    JobBase,
    PlaylistSyncJob,
    PlaylistUpdateJob,
    PlaylistUpdatecJob,
    RomSyncJob,
    SystemJob,
    ThumbnailsSync,
)
from retrosync_core.paths import (
    expand_user_path,
    expand_user_path_list,
    normalize_webdav_remote_path,
    retroarch_derived_paths,
)
from retrosync_core.runner import JobRegistry, SyncAbortError, SyncRunConfig, SyncRunner
from retrosync_core.transports import (
    GLOBAL_EXCLUDE_PATTERNS,
    TransportBase,
    TransportCapabilities,
    TransportError,
    TransportFactory,
    TransportFileSystemUnix,
    TransportFileSystemWindows,
    TransportSSHUnix,
    TransportSSHWindows,
    TransportUnixBase,
    TransportWebDAV,
    TransportWindowsBase,
    get_transport_mode,
)
from retrosync_core.ui import (
    advance_transport_file_progress,
    begin_transport_file_progress,
    complete_transport_file_progress,
    current_system_progress,
    end_transport_file_progress,
    hide_transport_tasks,
    init_live_tasks,
    overall_progress,
    progress_group,
    set_transport_status,
    step_progress,
    system_steps_progress,
)

logger = logging.getLogger()

__all__ = [
    "GLOBAL_EXCLUDE_PATTERNS",
    "TransportBase",
    "TransportCapabilities",
    "TransportError",
    "TransportFactory",
    "TransportFileSystemUnix",
    "TransportFileSystemWindows",
    "TransportSSHUnix",
    "TransportSSHWindows",
    "TransportUnixBase",
    "TransportWebDAV",
    "TransportWindowsBase",
    "get_transport_mode",
    "JobBase",
    "GlobalJob",
    "SystemJob",
    "BiosSync",
    "ThumbnailsSync",
    "FavoritesSync",
    "RomSyncJob",
    "PlaylistSyncJob",
    "PlaylistUpdateJob",
    "PlaylistUpdatecJob",
    "RuntimeConfigModel",
    "PlaylistConfigModel",
    "expand_config",
    "normalize_playlists",
    "normalize_transport_config",
    "rank_system_matches",
    "validate_runtime_config",
    "expand_user_path",
    "expand_user_path_list",
    "normalize_webdav_remote_path",
    "retroarch_derived_paths",
    "begin_transport_file_progress",
    "advance_transport_file_progress",
    "complete_transport_file_progress",
    "end_transport_file_progress",
    "set_transport_status",
    "concurrent",
    "count_playlist_roms",
    "list_playlists",
    "main",
]


def count_playlist_roms(default, playlist):
    src_folder = playlist.get("src_folder")
    if src_folder is None:
        raise ValueError(f"[playlists] '{playlist.get('name')}' is missing 'src_folder'")

    whitelist = playlist.get("src_whitelist")
    blacklist = playlist.get("src_blacklist")
    whitelist_pattern = re.compile(whitelist) if whitelist else None
    blacklist_pattern = re.compile(blacklist) if blacklist else None

    src_roots = default.get("src_roms", [])
    if not src_roots:
        return 0, [], []

    count = 0
    total_size = 0
    unreadable_paths = []
    src_rom_dir = Path(src_roots[0]) / src_folder
    resolved_paths = [str(src_rom_dir)]
    if not src_rom_dir.exists() or not src_rom_dir.is_dir():
        return count, total_size, resolved_paths, unreadable_paths
    if not os.access(src_rom_dir, os.R_OK):
        unreadable_paths.append(str(src_rom_dir))
        return count, total_size, resolved_paths, unreadable_paths

    try:
        for file in sorted(src_rom_dir.rglob("*")):
            if not file.is_file():
                continue
            file_str = str(file)
            if blacklist_pattern and blacklist_pattern.search(file_str):
                continue
            if whitelist_pattern and not whitelist_pattern.search(file_str):
                continue
            count += 1
            total_size += file.stat().st_size
    except PermissionError:
        unreadable_paths.append(str(src_rom_dir))

    return count, total_size, resolved_paths, unreadable_paths


def list_playlists(default, playlists):
    if not default.get("src_roms"):
        raise ValueError("[default] 'src_roms' is required for --playlist-list")

    table = Table(title="Configured Playlists")
    table.add_column("System", style="bold")
    table.add_column("ROM Count", justify="right")
    table.add_column("ROM Size", justify="right")

    for playlist in playlists:
        count, total_size, resolved_paths, unreadable_paths = count_playlist_roms(default, playlist)
        system_name = Path(playlist.get("name", "")).stem
        if playlist.get("disabled", False):
            system_name = f"{system_name} 🛑"
        table.add_row(
            system_name,
            "n/a" if unreadable_paths else str(count),
            "n/a" if unreadable_paths else f"{total_size / (1024**3):.2f} GB",
        )

    Console(width=240).print(table)


class CliRichReporter:
    def __init__(self):
        self.live = None
        self.overall_task_id = None

    def start(self, *, overall_total, supports_per_file_progress):
        self.overall_task_id = overall_progress.add_task("", total=overall_total)
        self.live = Live(progress_group)
        self.live.__enter__()
        init_live_tasks()
        if not supports_per_file_progress:
            set_transport_status("Per-file progress unavailable; using per-job progress.")

    def finish(self):
        if self.live is not None:
            self.live.__exit__(None, None, None)
            self.live = None

    def update_overall(self, *, description=None, advance=0):
        if self.overall_task_id is None:
            return
        kwargs = {}
        if description is not None:
            kwargs["description"] = description
        if advance:
            kwargs["advance"] = advance
        if kwargs:
            overall_progress.update(self.overall_task_id, **kwargs)

    def add_current_task(self, description):
        return current_system_progress.add_task(description)

    def stop_current_task(self, task_id, *, description):
        current_system_progress.stop_task(task_id)
        current_system_progress.update(task_id, description=description)

    def add_system_steps(self, *, name, total):
        return system_steps_progress.add_task("", total=total, name=name)

    def advance_system_steps(self, task_id, *, advance=1):
        system_steps_progress.update(task_id, advance=advance)

    def hide_system_steps(self, task_id):
        system_steps_progress.update(task_id, visible=False)

    def add_step_task(self, *, action, name):
        return step_progress.add_task("", action=action, name=name)

    def finish_step_task(self, task_id):
        step_progress.update(task_id, advance=1)
        step_progress.stop_task(task_id)
        step_progress.update(task_id, visible=False)

    def begin_transport_file_progress(self, total):
        begin_transport_file_progress(total)

    def advance_transport_file_progress(self, *, step=1):
        advance_transport_file_progress(step)

    def complete_transport_file_progress(self):
        complete_transport_file_progress()

    def end_transport_file_progress(self):
        end_transport_file_progress()

    def set_transport_status(self, message):
        set_transport_status(message)

    def hide_transport_tasks(self):
        hide_transport_tasks()

    def emit_summary(self, message):
        print(message)


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
    "--playlist-list",
    "do_playlist_list",
    is_flag=True,
    help="List configured playlists with source ROM counts",
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
    "--transport",
    "transport_override",
    type=click.Choice(["filesystem", "ssh", "webdav"], case_sensitive=False),
    default=None,
    help="Override transport mode from config (filesystem, ssh, webdav)",
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
    do_playlist_list,
    system_name,
    config_file,
    transport_override,
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

    if not any(
        [
            do_sync_playlists,
            do_sync_roms,
            do_sync_bios,
            do_sync_favorites,
            do_sync_thumbails,
            do_update_playlists,
            do_playlist_list,
        ]
    ):
        click.echo(click.get_current_context().get_help())
        sys.exit(0)

    try:
        config = toml.load(config_file)
        normalized_transport_override = (
            str(transport_override).strip().lower() if transport_override is not None else None
        )
        default = expand_config(
            normalize_transport_config(config, transport_override=normalized_transport_override)
        )
        playlists = normalize_playlists(config.get("playlists", []))
        validate_runtime_config(
            default,
            playlists,
            do_sync_playlists=do_sync_playlists,
            do_sync_bios=do_sync_bios,
            do_sync_favorites=do_sync_favorites,
            do_sync_thumbnails=do_sync_thumbails,
            do_sync_roms=do_sync_roms,
            do_update_playlists=do_update_playlists,
        )
    except ValueError as exc:
        print(str(exc))
        sys.exit(-1)

    if do_playlist_list:
        try:
            list_playlists(default, playlists)
        except ValueError as exc:
            print(str(exc))
            sys.exit(-1)
        sys.exit(0)

    if system_name:
        matches = rank_system_matches(system_name, playlists)
        if not matches:
            print(f"No playlist match found for '{system_name}'.")
            sys.exit(-1)
        if yes:
            system_name = matches[0]
        else:
            print(f"Select a playlist match for '{system_name}':")
            for idx, match in enumerate(matches, start=1):
                print(f"{idx}. {match}")
            print("0. Cancel")
            selected = click.prompt(
                "Enter selection number",
                type=click.IntRange(0, len(matches)),
            )
            if selected == 0:
                sys.exit(-1)
            system_name = matches[selected - 1]

    try:
        transport = TransportFactory(default, dry_run, force_transport)
        runner = SyncRunner(
            default=default,
            playlists=playlists,
            transport=transport,
            reporter=CliRichReporter(),
            job_registry=JobRegistry(
                bios_sync=BiosSync,
                favorites_sync=FavoritesSync,
                thumbnails_sync=ThumbnailsSync,
                playlist_sync_job=PlaylistSyncJob,
                playlist_update_job=PlaylistUpdateJob,
                rom_sync_job=RomSyncJob,
            ),
        )
        run_cfg = SyncRunConfig(
            do_sync_playlists=do_sync_playlists,
            do_sync_bios=do_sync_bios,
            do_sync_favorites=do_sync_favorites,
            do_sync_thumbnails=do_sync_thumbails,
            do_sync_roms=do_sync_roms,
            do_update_playlists=do_update_playlists,
            dry_run=dry_run,
            do_debug=do_debug,
        )
        runner.run(run_cfg, system_name=system_name)
    except (SyncAbortError, TransportError) as exc:
        print(str(exc))
        sys.exit(-1)


if __name__ == "__main__":
    main()
