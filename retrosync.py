#!/usr/bin/env python3

__author__ = "Optixx"
__license__ = "MIT"
__version__ = "2.0.0"
__maintainer__ = "David Voswinkel"
__email__ = "david@optixx.org"

import concurrent
import logging
import os
import re
import sys
from dataclasses import dataclass
from functools import wraps
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
    GlobalJob,
    JobBase,
    PlaylistSyncJob,
    PlaylistUpdateJob,
    PlaylistUpdatecJob,
    RomSyncJob,
    ShaderSync,
    SystemJob,
    ThumbnailsUpdateJob,
    ThumbnailsSync,
)
from retrosync_core.paths import (
    expand_user_path,
    expand_user_path_list,
    normalize_webdav_remote_path,
    retroarch_derived_paths,
)
from retrosync_core.runner import (
    ACTION_SYNC_BIOS,
    ACTION_SYNC_PLAYLISTS,
    ACTION_SYNC_ROMS,
    ACTION_SYNC_SHADERS,
    ACTION_SYNC_THUMBNAILS,
    ACTION_UPDATE_PLAYLISTS,
    ACTION_UPDATE_THUMBNAILS,
    ALL_ACTIONS,
    JobRegistry,
    SYNC_ACTIONS,
    SyncAbortError,
    SyncRunner,
    UPDATE_ACTIONS,
)
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
from retrosync_gui import launch_gui
from retrosync_app.services import (
    build_run_config,
    build_runner,
    filter_playlists,
    load_runtime_context,
    validate_run_request,
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
DEFAULT_CONFIG_FILE = "steamdeck.conf"

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
    "ShaderSync",
    "RomSyncJob",
    "PlaylistSyncJob",
    "PlaylistUpdateJob",
    "PlaylistUpdatecJob",
    "ThumbnailsUpdateJob",
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
        return 0, 0, [], []

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
        system_steps_progress.stop_task(task_id)

    def add_step_task(self, *, action, name):
        return step_progress.add_task("", action=action, name=name)

    def finish_step_task(self, task_id):
        task = step_progress.tasks[task_id]
        action = str(task.fields.get("action", "")).strip()
        if action and not action.endswith(" done"):
            action = f"{action} done"
        step_progress.update(task_id, advance=1, action=action or "done")
        step_progress.stop_task(task_id)

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


@dataclass(frozen=True)
class CliRuntimeOptions:
    config_file: str
    transport_override: str | None
    transport_impl: str
    yes: bool
    do_debug: bool


def common_runtime_options(command):
    @click.option(
        "--config-file",
        "-c",
        default=None,
        help="Override config file for this command",
    )
    @click.option(
        "--transport",
        "transport_override",
        type=click.Choice(["filesystem", "ssh", "webdav"], case_sensitive=False),
        default=None,
        help="Override transport mode from config (filesystem, ssh, webdav)",
    )
    @click.option(
        "--transport-impl",
        type=click.Choice(["auto", "unix", "windows"], case_sensitive=False),
        default="auto",
        help="Override transport implementation selection (auto, unix, windows)",
    )
    @click.option("--yes", is_flag=True, help="Skip prompt inputs by saying yes to everything")
    @click.option(
        "--debug",
        "-d",
        "do_debug",
        is_flag=True,
        help="Enable debug logging to debug.log logfile",
    )
    @wraps(command)
    def wrapper(*args, **kwargs):
        return command(*args, **kwargs)

    return wrapper


def selection_options(command):
    @click.option(
        "--system",
        "-n",
        "system_name",
        default=None,
        help="Filter and process only one specific system",
    )
    @click.option("--dry-run", "-D", is_flag=True, help="Dry run, don't sync or create anything")
    @wraps(command)
    def wrapper(*args, **kwargs):
        return command(*args, **kwargs)

    return wrapper


def thumbnail_update_options(command):
    @click.option(
        "--apply",
        "apply_changes",
        is_flag=True,
        help="Download matched assets and rewrite labels during thumbnail updates",
    )
    @click.option(
        "--refresh-thumbnail-cache",
        is_flag=True,
        help="Ignore cached remote thumbnail directory listings and fetch fresh ones",
    )
    @click.option(
        "--no-thumbnail-cache",
        is_flag=True,
        help="Disable reading and writing the local thumbnail directory cache for this run",
    )
    @wraps(command)
    def wrapper(*args, **kwargs):
        return command(*args, **kwargs)

    return wrapper


def configure_logging(do_debug):
    global logger
    if do_debug:
        logging.basicConfig(
            filename="debug.log",
            filemode="a",
            level=logging.DEBUG,
            format="%(asctime)s - %(levelname)s - %(message)s",
        )
        logger = logging.getLogger()
        return
    logging.basicConfig(
        level=logging.WARN,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )
    logger = logging.getLogger()
    logger.disabled = True


def transport_impl_to_force_transport(transport_impl):
    normalized = str(transport_impl).strip().lower()
    return False if normalized in ("", "auto", "false") else normalized


def resolve_config_file(config_file):
    if config_file:
        return config_file
    ctx = click.get_current_context(silent=True)
    if ctx is not None:
        root_config_file = ctx.find_root().params.get("config_file")
        if root_config_file:
            return root_config_file
    return DEFAULT_CONFIG_FILE


def load_cli_runtime(
    *,
    options,
    apply_changes=False,
    refresh_thumbnail_cache=False,
    no_thumbnail_cache=False,
):
    configure_logging(options.do_debug)
    return load_runtime_context(
        config_loader=toml.load,
        config_file=options.config_file,
        transport_override=options.transport_override,
        apply_changes=apply_changes,
        refresh_thumbnail_cache=refresh_thumbnail_cache,
        no_thumbnail_cache=no_thumbnail_cache,
    )


def resolve_system_name(playlists, *, system_name, yes):
    if not system_name:
        return None
    matches = rank_system_matches(system_name, playlists)
    if not matches:
        raise ValueError(f"No playlist match found for '{system_name}'.")
    if yes:
        return matches[0]
    print(f"Select a playlist match for '{system_name}':")
    for idx, match in enumerate(matches, start=1):
        print(f"{idx}. {match}")
    print("0. Cancel")
    selected = click.prompt(
        "Enter selection number",
        type=click.IntRange(0, len(matches)),
    )
    if selected == 0:
        raise ValueError("Operation cancelled.")
    return matches[selected - 1]


def execute_run(
    *,
    actions,
    options,
    system_name=None,
    dry_run=False,
    apply_changes=False,
    refresh_thumbnail_cache=False,
    no_thumbnail_cache=False,
):
    if not actions:
        raise ValueError("At least one action is required.")
    if apply_changes and ACTION_UPDATE_THUMBNAILS not in actions:
        raise ValueError("--apply requires a command that includes thumbnail updates.")
    if (refresh_thumbnail_cache or no_thumbnail_cache) and ACTION_UPDATE_THUMBNAILS not in actions:
        raise ValueError("Thumbnail cache flags require a command that includes thumbnail updates.")

    context = load_cli_runtime(
        options=options,
        apply_changes=apply_changes,
        refresh_thumbnail_cache=refresh_thumbnail_cache,
        no_thumbnail_cache=no_thumbnail_cache,
    )
    default = context.default
    playlists = context.playlists
    selected_system = resolve_system_name(playlists, system_name=system_name, yes=options.yes)
    validation_playlists = filter_playlists(playlists, system_name=selected_system)
    run_cfg = build_run_config(actions=actions, dry_run=dry_run, do_debug=options.do_debug)
    validate_run_request(
        default,
        validation_playlists,
        run_cfg,
        apply_changes=apply_changes,
    )
    runner = build_runner(
        default=default,
        playlists=playlists,
        dry_run=dry_run,
        force_transport=transport_impl_to_force_transport(options.transport_impl),
        reporter=CliRichReporter(),
        job_registry=JobRegistry(
            bios_sync=BiosSync,
            shader_sync=ShaderSync,
            thumbnails_sync=ThumbnailsSync,
            playlist_sync_job=PlaylistSyncJob,
            playlist_update_job=PlaylistUpdateJob,
            thumbnails_update_job=ThumbnailsUpdateJob,
            rom_sync_job=RomSyncJob,
        ),
        transport_factory=TransportFactory,
        runner_factory=SyncRunner,
    )
    runner.run(run_cfg, system_name=selected_system)


def execute_list(*, options):
    context = load_cli_runtime(options=options)
    list_playlists(context.default, context.playlists)


def handle_cli_errors(command):
    @wraps(command)
    def wrapper(*args, **kwargs):
        try:
            return command(*args, **kwargs)
        except ValueError as exc:
            print(str(exc))
            sys.exit(-1)
        except (SyncAbortError, TransportError) as exc:
            print(str(exc))
            sys.exit(-1)

    return wrapper


SYNC_TARGETS = {
    "playlists": {ACTION_SYNC_PLAYLISTS},
    "bios": {ACTION_SYNC_BIOS},
    "thumbnails": {ACTION_SYNC_THUMBNAILS},
    "roms": {ACTION_SYNC_ROMS},
    "shaders": {ACTION_SYNC_SHADERS},
    "all": set(SYNC_ACTIONS),
}
UPDATE_TARGETS = {
    "playlists": {ACTION_UPDATE_PLAYLISTS},
    "thumbnails": {ACTION_UPDATE_THUMBNAILS},
    "all": set(UPDATE_ACTIONS),
}
RUN_PRESETS = {
    "sync": set(SYNC_ACTIONS),
    "update": set(UPDATE_ACTIONS),
    "full": set(ALL_ACTIONS),
}


def resolve_action_targets(targets, target_map, *, command_name):
    if not targets:
        raise ValueError(f"At least one {command_name} target is required.")

    normalized_targets = [str(target).strip().lower() for target in targets]
    if "all" in normalized_targets and len(normalized_targets) > 1:
        raise ValueError(f"'all' cannot be combined with other {command_name} targets.")

    actions = set()
    for target in normalized_targets:
        actions.update(target_map[target])
    return actions


@click.group(name="retrosync.py", invoke_without_command=True)
@click.option(
    "--config-file",
    "-c",
    default=DEFAULT_CONFIG_FILE,
    show_default=True,
    help="Use config file",
)
@click.pass_context
def main(ctx, config_file):  # noqa: ARG001
    """Sync RetroArch content across devices."""
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command("gui")
def gui():
    """Launch the GUI."""
    launch_gui()


@main.group("list")
def list_group():
    """Inspect configured content."""


@list_group.command("playlists")
@common_runtime_options
@handle_cli_errors
def list_playlists_command(config_file, transport_override, transport_impl, yes, do_debug):
    """List configured playlists with source ROM counts."""
    _ = transport_impl, yes
    execute_list(
        options=CliRuntimeOptions(
            config_file=resolve_config_file(config_file),
            transport_override=transport_override,
            transport_impl="auto",
            yes=True,
            do_debug=do_debug,
        )
    )


@list_group.command("systems")
@common_runtime_options
@handle_cli_errors
def list_systems_command(config_file, transport_override, transport_impl, yes, do_debug):
    """Alias for list playlists."""
    _ = transport_impl, yes
    execute_list(
        options=CliRuntimeOptions(
            config_file=resolve_config_file(config_file),
            transport_override=transport_override,
            transport_impl="auto",
            yes=True,
            do_debug=do_debug,
        )
    )


@main.command("sync")
@click.argument(
    "targets",
    type=click.Choice(sorted(SYNC_TARGETS.keys()), case_sensitive=False),
    nargs=-1,
)
@selection_options
@common_runtime_options
@handle_cli_errors
def sync_command(
    targets,
    system_name,
    dry_run,
    config_file,
    transport_override,
    transport_impl,
    yes,
    do_debug,
):
    """Run copy-oriented sync actions."""
    execute_run(
        actions=resolve_action_targets(targets, SYNC_TARGETS, command_name="sync"),
        options=CliRuntimeOptions(
            config_file=resolve_config_file(config_file),
            transport_override=transport_override,
            transport_impl=transport_impl,
            yes=yes,
            do_debug=do_debug,
        ),
        system_name=system_name,
        dry_run=dry_run,
    )


@main.command("update")
@click.argument(
    "target",
    type=click.Choice(sorted(UPDATE_TARGETS.keys()), case_sensitive=False),
)
@thumbnail_update_options
@selection_options
@common_runtime_options
@handle_cli_errors
def update_command(
    target,
    apply_changes,
    refresh_thumbnail_cache,
    no_thumbnail_cache,
    system_name,
    dry_run,
    config_file,
    transport_override,
    transport_impl,
    yes,
    do_debug,
):
    """Run local metadata and asset update actions."""
    execute_run(
        actions=UPDATE_TARGETS[target.lower()],
        options=CliRuntimeOptions(
            config_file=resolve_config_file(config_file),
            transport_override=transport_override,
            transport_impl=transport_impl,
            yes=yes,
            do_debug=do_debug,
        ),
        system_name=system_name,
        dry_run=dry_run,
        apply_changes=apply_changes,
        refresh_thumbnail_cache=refresh_thumbnail_cache,
        no_thumbnail_cache=no_thumbnail_cache,
    )


@main.command("run")
@click.argument(
    "preset",
    type=click.Choice(sorted(RUN_PRESETS.keys()), case_sensitive=False),
)
@thumbnail_update_options
@selection_options
@common_runtime_options
@handle_cli_errors
def run_command(
    preset,
    apply_changes,
    refresh_thumbnail_cache,
    no_thumbnail_cache,
    system_name,
    dry_run,
    config_file,
    transport_override,
    transport_impl,
    yes,
    do_debug,
):
    """Run predefined multi-action workflows."""
    execute_run(
        actions=RUN_PRESETS[preset.lower()],
        options=CliRuntimeOptions(
            config_file=resolve_config_file(config_file),
            transport_override=transport_override,
            transport_impl=transport_impl,
            yes=yes,
            do_debug=do_debug,
        ),
        system_name=system_name,
        dry_run=dry_run,
        apply_changes=apply_changes,
        refresh_thumbnail_cache=refresh_thumbnail_cache,
        no_thumbnail_cache=no_thumbnail_cache,
    )


if __name__ == "__main__":
    main()
