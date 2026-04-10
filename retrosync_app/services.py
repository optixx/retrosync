import copy
from dataclasses import dataclass
import json
import platform
from pathlib import Path

from retrosync_core.jobs import PlaylistUpdateJob, ThumbnailsUpdateJob
from retrosync_core.config import (
    expand_config,
    normalize_playlists,
    normalize_transport_config,
    validate_runtime_config,
)
from retrosync_core.runner import JobRegistry, SyncRunConfig, SyncRunner
from retrosync_core.transports import (
    GLOBAL_EXCLUDE_PATTERNS,
    TransportFactory,
    TransportFileSystemUnix,
    TransportFileSystemWindows,
    TransportSSHUnix,
    TransportSSHWindows,
    TransportWebDAV,
    get_transport_mode,
)


@dataclass(frozen=True)
class RuntimeContext:
    config_file: str
    raw_config: dict
    default: dict
    playlists: list[dict]
    cores: list[dict]


@dataclass(frozen=True)
class PreviewPlanRow:
    action: str
    operation: str
    system: str
    source: str
    destination: str
    size_bytes: int = 0
    details: str = ""


@dataclass(frozen=True)
class PreviewPlan:
    rows: list[PreviewPlanRow]
    planned_copies: int
    planned_skips: int
    planned_overwrites: int
    planned_rewrites: int
    planned_downloads: int
    estimated_transfer_bytes: int


def load_runtime_context(
    *,
    config_loader,
    config_file,
    transport_override=None,
    apply_changes=False,
    refresh_thumbnail_cache=False,
    no_thumbnail_cache=False,
):
    raw_config = config_loader(config_file)
    normalized_override = (
        str(transport_override).strip().lower() if transport_override is not None else None
    )
    default = expand_config(
        normalize_transport_config(
            copy.deepcopy(raw_config),
            transport_override=normalized_override,
        )
    )
    default["_update_thumbnails_apply"] = bool(apply_changes)
    default["_refresh_thumbnail_cache"] = bool(refresh_thumbnail_cache)
    default["_no_thumbnail_cache"] = bool(no_thumbnail_cache)
    playlists = normalize_playlists(copy.deepcopy(raw_config.get("playlists", [])))
    shaders = copy.deepcopy(raw_config.get("shaders", []))
    default["_shaders"] = copy.deepcopy(shaders)
    return RuntimeContext(
        config_file=str(config_file),
        raw_config=copy.deepcopy(raw_config),
        default=default,
        playlists=playlists,
        cores=shaders,
    )


def build_run_config(
    *,
    actions=None,
    do_sync_playlists=False,
    do_sync_bios=False,
    do_sync_thumbnails=False,
    do_sync_roms=False,
    do_sync_shaders=False,
    do_update_playlists=False,
    do_update_thumbnails=False,
    dry_run=False,
    do_debug=False,
):
    return SyncRunConfig(
        actions=actions,
        do_sync_playlists=do_sync_playlists,
        do_sync_bios=do_sync_bios,
        do_sync_thumbnails=do_sync_thumbnails,
        do_sync_roms=do_sync_roms,
        do_sync_shaders=do_sync_shaders,
        do_update_playlists=do_update_playlists,
        do_update_thumbnails=do_update_thumbnails,
        dry_run=dry_run,
        do_debug=do_debug,
    )


def filter_playlists(playlists, *, system_name=None):
    if not system_name:
        return list(playlists)
    return [playlist for playlist in playlists if playlist.get("name") == system_name]


def validate_run_request(default, playlists, run_cfg, *, apply_changes=False):
    validate_runtime_config(
        default,
        playlists,
        default.get("_shaders", []),
        actions=run_cfg.actions,
    )
    if apply_changes and run_cfg.do_update_thumbnails and not default.get("src_thumbnails"):
        raise ValueError("[default] 'src_thumbnails' is required for --update-thumbnails --apply")


def build_runner(
    *,
    default,
    playlists,
    dry_run,
    force_transport,
    reporter,
    job_registry=None,
    transport_factory=TransportFactory,
    runner_factory=SyncRunner,
    event_sink=None,
):
    transport = transport_factory(default, dry_run, force_transport)
    kwargs = {
        "default": default,
        "playlists": playlists,
        "transport": transport,
        "reporter": reporter,
        "job_registry": job_registry or JobRegistry(),
    }
    if event_sink is not None:
        kwargs["event_sink"] = event_sink
    return runner_factory(**kwargs)


def build_preview_plan(
    *,
    default,
    playlists,
    run_cfg,
    apply_changes=False,
    force_transport=False,
    preview_remote_thumbnail_lookup=False,
):
    rows: list[PreviewPlanRow] = []
    capabilities = _resolve_preview_transport_capabilities(
        default,
        force_transport=force_transport,
    )

    if run_cfg.do_sync_bios:
        rows.extend(
            _plan_recursive_copy(
                action="sync_bios",
                system="Global",
                src_root=default.get("src_bios"),
                dest_root=default.get("dest_bios"),
                default=default,
                capabilities=capabilities,
            )
        )

    if run_cfg.do_sync_shaders:
        rows.extend(_plan_shader_sync_rows(default=default, capabilities=capabilities))

    for playlist in playlists:
        system = Path(str(playlist.get("name", ""))).stem

        if run_cfg.do_sync_roms:
            rows.extend(
                _plan_recursive_copy(
                    action="sync_roms",
                    system=system,
                    src_root=Path(default.get("src_roms", [""])[0])
                    / str(playlist.get("src_folder", "")),
                    dest_root=Path(default.get("dest_roms", ""))
                    / str(playlist.get("dest_folder", "")),
                    default=default,
                    capabilities=capabilities,
                )
            )

        if run_cfg.do_sync_thumbnails:
            rows.extend(
                _plan_recursive_copy(
                    action="sync_thumbnails",
                    system=system,
                    src_root=Path(default.get("src_thumbnails", "")) / system,
                    dest_root=Path(default.get("dest_thumbnails", "")) / system,
                    default=default,
                    capabilities=capabilities,
                )
            )

        if run_cfg.do_sync_playlists:
            src = Path(default.get("src_playlists", "")) / str(playlist.get("name", ""))
            dst = Path(default.get("dest_playlists", "")) / str(playlist.get("name", ""))
            rows.append(
                PreviewPlanRow(
                    action="sync_playlists",
                    operation=_classify_sync_operation(
                        src,
                        dst,
                        default=default,
                        capabilities=capabilities,
                    ),
                    system=system,
                    source=str(src),
                    destination=str(dst),
                    size_bytes=_safe_size(src),
                    details="Rewrite playlist core/path references before copy.",
                )
            )

        if run_cfg.do_update_playlists:
            rows.extend(_plan_playlist_update_rows(default=default, playlist=playlist))

        if run_cfg.do_update_thumbnails:
            rows.extend(
                _plan_thumbnail_update_rows(
                    default=default,
                    playlist=playlist,
                    apply_changes=apply_changes,
                    preview_remote_thumbnail_lookup=preview_remote_thumbnail_lookup,
                )
            )

    planned_copies = sum(1 for row in rows if row.operation == "copy")
    planned_skips = sum(1 for row in rows if row.operation == "skip")
    planned_overwrites = sum(1 for row in rows if row.operation == "overwrite")
    planned_rewrites = sum(
        1 for row in rows if "rewrite" in row.operation and row.operation != "rewrite_copy"
    )
    planned_downloads = sum(1 for row in rows if row.operation == "download")
    estimated_transfer_bytes = sum(row.size_bytes for row in rows)
    return PreviewPlan(
        rows=rows,
        planned_copies=planned_copies,
        planned_skips=planned_skips,
        planned_overwrites=planned_overwrites,
        planned_rewrites=planned_rewrites,
        planned_downloads=planned_downloads,
        estimated_transfer_bytes=estimated_transfer_bytes,
    )


def export_preview_plan(plan: PreviewPlan):
    return {
        "planned_copies": plan.planned_copies,
        "planned_skips": plan.planned_skips,
        "planned_overwrites": plan.planned_overwrites,
        "planned_rewrites": plan.planned_rewrites,
        "planned_downloads": plan.planned_downloads,
        "estimated_transfer_bytes": plan.estimated_transfer_bytes,
        "rows": [row.__dict__.copy() for row in plan.rows],
    }


def _plan_recursive_copy(*, action, system, src_root, dest_root, default, capabilities):
    if not src_root or not dest_root:
        return []
    src_root = Path(src_root)
    dest_root = Path(dest_root)
    if not src_root.exists() or not src_root.is_dir():
        return []
    rows = []
    for filename in sorted(src_root.rglob("*")):
        if not filename.is_file():
            continue
        relative = filename.relative_to(src_root)
        if any(_matches_exclude(part) for part in relative.parts):
            continue
        rows.append(
            PreviewPlanRow(
                action=action,
                operation=_classify_sync_operation(
                    filename,
                    dest_root / relative,
                    default=default,
                    capabilities=capabilities,
                ),
                system=system,
                source=str(filename),
                destination=str(dest_root / relative),
                size_bytes=_safe_size(filename),
            )
        )
    return rows


def _plan_shader_sync_rows(*, default, capabilities):
    def shader_candidates(shader_value):
        if isinstance(shader_value, list):
            return [str(candidate).strip() for candidate in shader_value if str(candidate).strip()]
        candidate = str(shader_value).strip()
        return [candidate] if candidate else []

    def shader_storage(shader_file):
        suffix = Path(shader_file).suffix.lower()
        if suffix == ".glslp":
            return "shaders_glsl", ".glslp"
        if suffix == ".cgp":
            return "shaders_cg", ".cgp"
        return "shaders_slang", ".slangp"

    dst_base = default.get("dest_retroarch_base")
    if not dst_base:
        return []
    rows = []
    for shader in default.get("_shaders", []):
        core_name = str(shader.get("name", "")).strip()
        candidates = shader_candidates(shader.get("shader", ""))
        if not core_name or not candidates:
            continue
        planned_extensions = set()
        for shader_file in candidates:
            shader_dir_name, preset_ext = shader_storage(shader_file)
            if preset_ext in planned_extensions:
                continue
            content = f'#reference "../../shaders/{shader_dir_name}/{shader_file}"\n'
            destination = Path(dst_base) / "config" / core_name / f"{core_name}{preset_ext}"
            rows.append(
                PreviewPlanRow(
                    action="sync_shaders",
                    operation=_classify_generated_sync_operation(
                        destination,
                        content,
                        default=default,
                        capabilities=capabilities,
                    ),
                    system="Global",
                    source=shader_file,
                    destination=str(destination),
                    size_bytes=len(content.encode("utf-8")),
                    details=f"Generate shader preset for core '{core_name}'.",
                )
            )
            planned_extensions.add(preset_ext)
    return rows


def _matches_exclude(part):
    from fnmatch import fnmatch

    return any(fnmatch(part, pattern) for pattern in GLOBAL_EXCLUDE_PATTERNS)


def _safe_size(path):
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def _playlist_item_count(path):
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    items = data.get("items", [])
    return len(items) if isinstance(items, list) else 0


def _resolve_preview_transport_capabilities(default, *, force_transport=False):
    mode = get_transport_mode(default)
    normalized_force = str(force_transport).strip().lower() if force_transport else ""
    current_platform = platform.system()
    if mode == "webdav":
        return TransportWebDAV.capabilities
    if normalized_force == "windows":
        return (
            TransportSSHWindows.capabilities
            if mode == "ssh"
            else TransportFileSystemWindows.capabilities
        )
    if normalized_force == "unix":
        return (
            TransportSSHUnix.capabilities if mode == "ssh" else TransportFileSystemUnix.capabilities
        )
    if current_platform == "Windows":
        return (
            TransportSSHWindows.capabilities
            if mode == "ssh"
            else TransportFileSystemWindows.capabilities
        )
    return TransportSSHUnix.capabilities if mode == "ssh" else TransportFileSystemUnix.capabilities


def _classify_sync_operation(src, dest, *, default, capabilities):
    mode = get_transport_mode(default)
    src_path = Path(src)
    dest_path = Path(dest)
    if mode != "filesystem":
        return "copy"
    if not dest_path.exists():
        return "copy"
    try:
        src_stat = src_path.stat()
        dest_stat = dest_path.stat()
    except OSError:
        return "copy"
    if capabilities.size_aware_skip and src_stat.st_size == dest_stat.st_size:
        if not capabilities.preserves_mtime or int(src_stat.st_mtime) <= int(dest_stat.st_mtime):
            return "skip"
    if capabilities.preserves_mtime and src_stat.st_size == dest_stat.st_size:
        if int(src_stat.st_mtime) <= int(dest_stat.st_mtime):
            return "skip"
    return "overwrite"


def _classify_generated_sync_operation(dest, content, *, default, capabilities):
    _ = capabilities
    if get_transport_mode(default) != "filesystem":
        return "copy"
    dest_path = Path(dest)
    if not dest_path.exists():
        return "copy"
    try:
        if dest_path.read_text(encoding="utf-8") == content:
            return "skip"
    except OSError:
        return "copy"
    return "overwrite"


class _PreviewTransport:
    dry_run = True


def _plan_playlist_update_rows(*, default, playlist):
    job = PlaylistUpdateJob(default, _PreviewTransport())
    job.setup(playlist)
    try:
        item_rows = job.build_preview_rows()
    except Exception as exc:
        playlist_path = Path(default.get("src_playlists", "")) / str(playlist.get("name", ""))
        return [
            PreviewPlanRow(
                action="update_playlists",
                operation="rewrite",
                system=Path(str(playlist.get("name", ""))).stem,
                source=str(playlist_path),
                destination=str(playlist_path),
                size_bytes=_safe_size(playlist_path),
                details=f"Preview unavailable: {exc}",
            )
        ]

    playlist_path = Path(default.get("src_playlists", "")) / str(playlist.get("name", ""))
    system = Path(str(playlist.get("name", ""))).stem
    rows = []
    for item in item_rows:
        rows.append(
            PreviewPlanRow(
                action="update_playlists",
                operation="rewrite",
                system=system,
                source=item["path"],
                destination=str(playlist_path),
                details=(
                    f"ROM {item['rom']} -> playlist label '{item['label']}'"
                    + (" using thumbnail-aligned label." if item["thumbnail_match"] else "")
                ),
            )
        )
    return rows


def _plan_thumbnail_update_rows(
    *,
    default,
    playlist,
    apply_changes,
    preview_remote_thumbnail_lookup,
):
    job = ThumbnailsUpdateJob(default, _PreviewTransport())
    job.setup(playlist)
    system = Path(str(playlist.get("name", ""))).stem
    playlist_path = Path(default.get("src_playlists", "")) / str(playlist.get("name", ""))

    if not preview_remote_thumbnail_lookup:
        rows = []
        for item in job._load_playlist_items():
            rom_path = item.get("path", "")
            rom_name = Path(rom_path.split("#")[0]).name if rom_path else ""
            label = item.get("label", Path(rom_name).stem if rom_name else "")
            rows.append(
                PreviewPlanRow(
                    action="update_thumbnails",
                    operation="inspect",
                    system=system,
                    source=str(playlist_path),
                    destination=str(Path(default.get("src_thumbnails", "")) / system),
                    details=(
                        f"ROM {rom_name or '(unknown)'} label '{label}' -> "
                        "thumbnail lookup deferred until run."
                    ),
                )
            )
        if apply_changes and rows:
            rows.append(
                PreviewPlanRow(
                    action="update_thumbnails",
                    operation="inspect",
                    system=system,
                    source=str(playlist_path),
                    destination=str(Path(default.get("src_thumbnails", "")) / system),
                    details="Apply preview requires remote thumbnail lookup and is deferred until run.",
                )
            )
        return rows

    try:
        report_rows = job.build_report_rows()
    except Exception as exc:
        return [
            PreviewPlanRow(
                action="update_thumbnails",
                operation="download",
                system=system,
                source="thumbnails.libretro.com",
                destination=str(Path(default.get("src_thumbnails", "")) / system),
                details=f"Preview unavailable: {exc}",
            )
        ]

    rows = []
    for row in report_rows:
        details = (
            f"ROM {row['rom']} label '{row['label']}' -> thumbnail '{row['thumbnail'] or 'none'}' "
            f"match={row['match_type']} score={row['score']}"
        )
        rows.append(
            PreviewPlanRow(
                action="update_thumbnails",
                operation="inspect",
                system=system,
                source=row.get("url") or "thumbnail-match",
                destination=str(Path(default.get("src_thumbnails", "")) / system),
                details=details,
            )
        )

    if apply_changes:
        for row in job.build_apply_preview_rows(report_rows):
            rows.append(
                PreviewPlanRow(
                    action="update_thumbnails",
                    operation=row["operation"],
                    system=system,
                    source=row["source"],
                    destination=row["destination"],
                    details=(
                        f"ROM {row['rom']} label '{row['label']}' -> thumbnail '{row['thumbnail']}'. "
                        f"{row['details']}"
                    ),
                    size_bytes=_safe_size(playlist_path) if row["operation"] == "rewrite" else 0,
                )
            )
    return rows
