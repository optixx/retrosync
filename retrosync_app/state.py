from dataclasses import dataclass, field


@dataclass
class RunSetupState:
    do_sync_playlists: bool = True
    do_sync_bios: bool = False
    do_sync_favorites: bool = False
    do_sync_thumbnails: bool = False
    do_sync_roms: bool = False
    do_update_playlists: bool = False
    do_update_thumbnails: bool = False
    apply_changes: bool = False
    refresh_thumbnail_cache: bool = False
    no_thumbnail_cache: bool = False
    dry_run: bool = True
    do_debug: bool = False
    yes: bool = True
    transport_override: str | None = None
    force_transport: str | bool = False


@dataclass
class RunState:
    status: str = "idle"
    is_dry_run: bool = False
    started_at: float | None = None
    finished_at: float | None = None
    active_detail: str = ""
    progress_current: int = 0
    progress_total: int = 0
    can_cancel: bool = False
    last_error: str | None = None
    result_summary: str | None = None
    run_id: str | None = None


@dataclass
class SystemRowState:
    name: str
    playlist_name: str
    source_folder: str | None
    destination_folder: str | None
    selected: bool = True
    disabled: bool = False
    status: str = "idle"
    last_message: str = ""


@dataclass
class LogEntryView:
    level: str
    message: str
    ts: float
    source: str = "app"
    event_type: str | None = None


@dataclass
class TransportStatusView:
    message: str = ""
    severity: str = "info"
    visible: bool = False


@dataclass
class PreviewReportView:
    kind: str
    title: str
    message: str
    severity: str
    ts: float


@dataclass
class PreviewPlanRowView:
    action: str
    operation: str
    system: str
    source: str
    destination: str
    size_bytes: int = 0
    details: str = ""


@dataclass
class PreviewState:
    estimated_transfer_bytes: int = 0
    planned_steps_total: int = 0
    planned_copies: int | None = 0
    planned_skips: int | None = 0
    planned_overwrites: int | None = 0
    planned_rewrites: int | None = 0
    planned_downloads: int | None = 0
    unmatched_items: int | None = None
    plan_rows: list[PreviewPlanRowView] = field(default_factory=list)
    reports: list[PreviewReportView] = field(default_factory=list)


@dataclass
class AppState:
    config_path: str = "steamdeck.conf"
    config_loaded: bool = False
    status_message: str = "Load a config to begin."
    config_error: str | None = None
    transport: str = ""
    system_filter: str = ""
    run_setup: RunSetupState = field(default_factory=RunSetupState)
    run_state: RunState = field(default_factory=RunState)
    transport_status: TransportStatusView = field(default_factory=TransportStatusView)
    preview: PreviewState = field(default_factory=PreviewState)
    systems: list[SystemRowState] = field(default_factory=list)
    logs: list[LogEntryView] = field(default_factory=list)
