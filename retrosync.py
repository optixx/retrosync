#!/usr/bin/env python3

__author__ = "Optixx"
__license__ = "MIT"
__version__ = "0.1.0"
__maintainer__ = "David Voswinkel"
__email__ = "david@optixx.org"

import logging
import sys
import time
import concurrent
from pathlib import Path

import click
import toml
from rich.live import Live

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
from retrosync_core.transports import (
    GLOBAL_EXCLUDE_PATTERNS,
    TransportBase,
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
    "begin_transport_file_progress",
    "advance_transport_file_progress",
    "complete_transport_file_progress",
    "end_transport_file_progress",
    "set_transport_status",
    "concurrent",
    "main",
]


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

    jobs = []
    transport = TransportFactory(default, dry_run, force_transport)
    if do_sync_bios:
        jobs.append(BiosSync(default, playlists, transport))

    if do_sync_favorites:
        jobs.append(FavoritesSync(default, playlists, transport))

    if do_sync_thumbails:
        jobs.append(ThumbnailsSync(default, playlists, transport))

    system_jobs = []
    if do_update_playlists:
        system_jobs.append(PlaylistUpdateJob(default, transport))

    if do_sync_playlists:
        system_jobs.append(PlaylistSyncJob(default, transport))

    if do_sync_roms:
        system_jobs.append(RomSyncJob(default, transport))

    if system_name:
        playlists = [p for p in playlists if p.get("name") == system_name]

    systems = {}
    for _, playlist in enumerate(playlists):
        name = Path(playlist.get("name")).stem
        if not playlist.get("disabled", False):
            systems[name] = {"name": name, "playlist": playlist}

    overall_total = len(jobs) + (len(systems) if system_jobs else 0)
    overall_task_id = overall_progress.add_task("", total=overall_total)
    with Live(progress_group):
        init_live_tasks()
        for (
            idx,
            job,
        ) in enumerate(jobs):
            top_descr = f"[bold #AAAAAA]({idx} out of {len(jobs)} jobs done)"
            overall_progress.update(overall_task_id, description=top_descr)
            current_task_id = current_system_progress.add_task(f"Run job {job.name}")
            system_steps_task_id = system_steps_progress.add_task("", total=2, name=job.name)
            system_steps_progress.update(system_steps_task_id, advance=1)
            begin_transport_file_progress(job.size)
            try:
                job.do(callback=lambda: advance_transport_file_progress(1))
                complete_transport_file_progress()
            except TransportError as exc:
                hide_transport_tasks()
                print(f"Transfer aborted: {exc}")
                sys.exit(-1)
            finally:
                end_transport_file_progress()
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
                top_descr = f"[bold #AAAAAA]({idx} out of {len(systems)} systems synced)"
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
                    begin_transport_file_progress(job.size)
                    try:
                        job.do(
                            lambda system_steps_task_id=system_steps_task_id: (
                                system_steps_progress.update(system_steps_task_id, advance=1),
                                advance_transport_file_progress(1),
                            )
                        )
                        complete_transport_file_progress()
                    except TransportError as exc:
                        hide_transport_tasks()
                        print(f"Transfer aborted: {exc}")
                        sys.exit(-1)
                    finally:
                        end_transport_file_progress()

                    step_progress.update(step_task_id, advance=1)
                    step_progress.stop_task(step_task_id)
                    step_progress.update(step_task_id, visible=False)

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
        hide_transport_tasks()


if __name__ == "__main__":
    main()
