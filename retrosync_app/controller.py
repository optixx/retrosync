import copy
import queue
import threading
import time
from dataclasses import replace
from pathlib import Path

import toml

from .persistence import (
    DEFAULT_APP_STATE_PATH,
    PERSISTED_RUN_SETUP_FIELDS,
    load_app_state,
    save_app_state,
)
from retrosync_core.events import EventType
from retrosync_core.runner import CancelToken, JobRegistry, SyncAbortError, SyncRunner
from retrosync_core.transports import TransportError, TransportFactory

from .services import (
    build_preview_plan,
    build_run_config,
    build_runner,
    load_runtime_context,
    validate_run_request,
)
from .state import (
    AppState,
    LogEntryView,
    PreviewPlanRowView,
    PreviewReportView,
    PreviewState,
    RunState,
    SystemRowState,
    TransportStatusView,
)


class QueueEventSink:
    def __init__(self, message_queue):
        self._message_queue = message_queue

    def emit(self, event):
        self._message_queue.put(("sync_event", event))


class QueueReporter:
    def __init__(self, message_queue):
        self._message_queue = message_queue

    def start(self, *, overall_total, supports_per_file_progress):
        if not supports_per_file_progress:
            self._message_queue.put(
                (
                    "transport_status",
                    {
                        "message": "Per-file progress unavailable; using per-job progress.",
                        "severity": "warning",
                    },
                )
            )

    def finish(self):
        return None

    def update_overall(self, *, description=None, advance=0):
        _ = (description, advance)

    def add_current_task(self, description):
        _ = description
        return 0

    def stop_current_task(self, task_id, *, description):
        _ = (task_id, description)

    def add_system_steps(self, *, name, total):
        _ = (name, total)
        return 0

    def advance_system_steps(self, task_id, *, advance=1):
        _ = (task_id, advance)

    def hide_system_steps(self, task_id):
        _ = task_id

    def add_step_task(self, *, action, name):
        _ = (action, name)
        return 0

    def finish_step_task(self, task_id):
        _ = task_id

    def begin_transport_file_progress(self, total):
        _ = total

    def advance_transport_file_progress(self, *, step=1):
        _ = step

    def complete_transport_file_progress(self):
        return None

    def end_transport_file_progress(self):
        return None

    def set_transport_status(self, message):
        self._message_queue.put(
            (
                "transport_status",
                {
                    "message": str(message),
                    "severity": "info",
                },
            )
        )

    def hide_transport_tasks(self):
        return None

    def emit_summary(self, message):
        self._message_queue.put(("summary", str(message)))


class RetrosyncAppController:
    def __init__(
        self,
        *,
        toml_loader=toml.load,
        transport_factory=TransportFactory,
        runner_factory=SyncRunner,
        job_registry=None,
        app_state_path=DEFAULT_APP_STATE_PATH,
    ):
        self._toml_loader = toml_loader
        self._transport_factory = transport_factory
        self._runner_factory = runner_factory
        self._job_registry = job_registry or JobRegistry()
        self._app_state_path = Path(app_state_path)
        self._state = AppState()
        self._message_queue = queue.Queue()
        self._raw_config = None
        self._context = None
        self._default = None
        self._playlists = []
        self._worker = None
        self._cancel_token = None
        self._load_persisted_state()

    def snapshot(self):
        return copy.deepcopy(self._state)

    def set_config_path(self, path):
        self._state.config_path = str(path).strip()
        self._persist_state()

    def set_transport_override(self, value):
        normalized = None if value in (None, "", "default") else str(value).strip().lower()
        self._state.run_setup.transport_override = normalized
        if self._raw_config is not None:
            self._rebuild_runtime_config()
            self._refresh_preview_plan()

    def set_system_filter(self, value):
        self._state.system_filter = str(value)

    def set_system_selected(self, playlist_name, selected):
        for row in self._state.systems:
            if row.playlist_name == playlist_name:
                row.selected = bool(selected)
                self._refresh_preview_plan()
                return

    def set_all_systems_selected(self, selected):
        changed = False
        for row in self._state.systems:
            if row.disabled:
                continue
            if row.selected != bool(selected):
                row.selected = bool(selected)
                changed = True
        if changed:
            self._refresh_preview_plan()

    def set_filtered_systems_selected(self, selected):
        filter_text = self._state.system_filter.strip().lower()
        if not filter_text:
            return
        changed = False
        for row in self._state.systems:
            if row.disabled:
                continue
            should_select = bool(selected) and filter_text in row.name.lower()
            if row.selected != should_select:
                row.selected = should_select
                changed = True
        if changed:
            self._refresh_preview_plan()

    def set_action(self, action_name, value):
        if hasattr(self._state.run_setup, action_name):
            current = getattr(self._state.run_setup, action_name)
            setattr(
                self._state.run_setup,
                action_name,
                bool(value) if isinstance(current, bool) else value,
            )
            if self._raw_config is not None and action_name in {
                "apply_changes",
                "refresh_thumbnail_cache",
                "no_thumbnail_cache",
            }:
                self._rebuild_runtime_config()
            self._refresh_preview_plan()
            self._persist_state()

    def load_config(self, path=None):
        if path is not None:
            self.set_config_path(path)
        config_path = self._state.config_path.strip()
        if not config_path:
            self._set_config_error("Config path is required.")
            return False

        self._state.run_state = RunState()

        try:
            self._context = load_runtime_context(
                config_loader=self._toml_loader,
                config_file=config_path,
                transport_override=self._state.run_setup.transport_override,
                apply_changes=self._state.run_setup.apply_changes,
                refresh_thumbnail_cache=self._state.run_setup.refresh_thumbnail_cache,
                no_thumbnail_cache=self._state.run_setup.no_thumbnail_cache,
            )
            self._raw_config = self._context.raw_config
            self._rebuild_runtime_config()
        except Exception as exc:
            self._context = None
            self._raw_config = None
            self._default = None
            self._playlists = []
            self._state.systems = []
            self._set_config_error(str(exc))
            return False

        self._state.config_loaded = True
        self._refresh_preview_plan()
        if self._state.config_error:
            self._state.status_message = f"Loaded config from {config_path} with validation errors."
            self._append_unique_log("warning", self._state.status_message)
            return False
        self._state.status_message = f"Loaded config from {config_path}"
        self._append_unique_log("info", self._state.status_message)
        return True

    def reload_config(self):
        return self.load_config(self._state.config_path)

    def start_run(self, *, dry_run=None):
        if self._worker is not None and self._worker.is_alive():
            self._append_log("warning", "A run is already in progress.")
            return False

        if self._raw_config is None and not self.load_config():
            return False

        selected_playlists = self._selected_playlists()
        if not selected_playlists:
            self._set_run_error("Select at least one enabled system.")
            return False

        self._rebuild_runtime_config()
        default = copy.deepcopy(self._default)
        playlists = [dict(playlist) for playlist in selected_playlists]
        run_setup = self._state.run_setup
        effective_dry_run = run_setup.dry_run if dry_run is None else bool(dry_run)
        run_cfg = self._build_run_config(effective_dry_run)

        try:
            validate_run_request(
                default,
                playlists,
                run_cfg,
                apply_changes=run_setup.apply_changes,
            )
        except ValueError as exc:
            self._set_run_error(str(exc))
            return False

        try:
            runner = build_runner(
                default=default,
                playlists=playlists,
                dry_run=effective_dry_run,
                force_transport=run_setup.force_transport,
                reporter=QueueReporter(self._message_queue),
                job_registry=self._job_registry,
                transport_factory=self._transport_factory,
                runner_factory=self._runner_factory,
                event_sink=QueueEventSink(self._message_queue),
            )
        except (TransportError, OSError, ValueError) as exc:
            self._set_run_error(str(exc))
            return False

        self._cancel_token = CancelToken()
        self._state.run_state = RunState(
            status="validating",
            is_dry_run=effective_dry_run,
            started_at=time.time(),
            can_cancel=True,
            active_detail="Preparing run",
            progress_current=0,
            progress_total=0,
        )
        self._append_log(
            "info",
            f"Starting {'dry run' if effective_dry_run else 'run'} for {len(playlists)} system(s).",
        )
        self._state.preview = PreviewState()
        self._state.transport_status = TransportStatusView()
        self._worker = threading.Thread(
            target=self._run_worker,
            args=(runner, run_cfg, self._cancel_token),
            daemon=True,
        )
        self._worker.start()
        return True

    def cancel_run(self):
        if self._cancel_token is None or self._state.run_state.can_cancel is False:
            return False
        self._cancel_token.cancel("Cancelled by user.")
        self._append_log("warning", "Cancellation requested.")
        return True

    def drain_events(self):
        drained = False
        while True:
            try:
                kind, payload = self._message_queue.get_nowait()
            except queue.Empty:
                break
            drained = True
            if kind == "sync_event":
                self._apply_sync_event(payload)
            elif kind == "summary":
                self._consume_summary(payload)
            elif kind == "transport_status":
                self._consume_transport_status(payload)
            elif kind == "worker_error":
                self._set_run_error(payload)

        if (
            self._worker is not None
            and not self._worker.is_alive()
            and not self._state.run_state.can_cancel
        ):
            self._worker = None
            self._cancel_token = None
        return drained

    def _run_worker(self, runner, run_cfg, cancel_token):
        try:
            runner.run(run_cfg, cancel_token=cancel_token)
        except SyncAbortError:
            return
        except Exception as exc:
            self._message_queue.put(("worker_error", str(exc)))

    def _rebuild_runtime_config(self):
        if self._context is None:
            return
        self._context = load_runtime_context(
            config_loader=lambda _: self._context.raw_config,
            config_file=self._context.config_file,
            transport_override=self._state.run_setup.transport_override,
            apply_changes=self._state.run_setup.apply_changes,
            refresh_thumbnail_cache=self._state.run_setup.refresh_thumbnail_cache,
            no_thumbnail_cache=self._state.run_setup.no_thumbnail_cache,
        )
        self._raw_config = self._context.raw_config
        self._default = self._context.default
        self._playlists = self._context.playlists
        selected_by_name = {row.playlist_name: row.selected for row in self._state.systems}
        self._state.systems = [
            SystemRowState(
                name=str(playlist.get("name", "")).removesuffix(".lpl"),
                playlist_name=str(playlist.get("name", "")),
                source_folder=playlist.get("src_folder"),
                destination_folder=playlist.get("dest_folder"),
                selected=selected_by_name.get(
                    str(playlist.get("name", "")), not playlist.get("disabled", False)
                ),
                disabled=bool(playlist.get("disabled", False)),
            )
            for playlist in self._playlists
        ]
        self._state.transport = str(self._default.get("transport", ""))

    def _refresh_preview_plan(self):
        if self._default is None:
            self._state.preview = PreviewState()
            return
        selected_playlists = self._selected_playlists()
        run_cfg = self._build_run_config(self._state.run_setup.dry_run)
        validation_error = self._validate_current_selection(
            playlists=selected_playlists,
            run_cfg=run_cfg,
        )
        if validation_error:
            self._state.preview = PreviewState()
            return
        try:
            plan = build_preview_plan(
                default=self._default,
                playlists=selected_playlists,
                run_cfg=run_cfg,
                apply_changes=self._state.run_setup.apply_changes,
                force_transport=self._state.run_setup.force_transport,
            )
        except Exception as exc:
            self._state.preview = PreviewState()
            self._set_config_error(str(exc), keep_loaded=True)
            return
        reports = list(self._state.preview.reports)
        self._state.preview = PreviewState(
            estimated_transfer_bytes=plan.estimated_transfer_bytes,
            planned_steps_total=len(plan.rows),
            planned_copies=plan.planned_copies,
            planned_skips=plan.planned_skips,
            planned_overwrites=plan.planned_overwrites,
            planned_rewrites=plan.planned_rewrites,
            planned_downloads=plan.planned_downloads,
            plan_rows=[
                PreviewPlanRowView(
                    action=row.action,
                    operation=row.operation,
                    system=row.system,
                    source=row.source,
                    destination=row.destination,
                    size_bytes=row.size_bytes,
                    details=row.details,
                )
                for row in plan.rows
            ],
            reports=reports,
        )

    def _validate_current_selection(self, *, playlists, run_cfg):
        if self._default is None:
            return None
        try:
            validate_run_request(
                copy.deepcopy(self._default),
                [dict(playlist) for playlist in playlists],
                run_cfg,
                apply_changes=self._state.run_setup.apply_changes,
            )
        except ValueError as exc:
            self._set_config_error(str(exc), keep_loaded=True)
            return str(exc)

        self._state.config_error = None
        return None

    def _selected_playlists(self):
        selected_names = {
            row.playlist_name for row in self._state.systems if row.selected and not row.disabled
        }
        return [playlist for playlist in self._playlists if playlist.get("name") in selected_names]

    def _build_run_config(self, dry_run):
        run_setup = self._state.run_setup
        return build_run_config(
            do_sync_playlists=run_setup.do_sync_playlists,
            do_sync_bios=run_setup.do_sync_bios,
            do_sync_thumbnails=run_setup.do_sync_thumbnails,
            do_sync_roms=run_setup.do_sync_roms,
            do_sync_shaders=run_setup.do_sync_shaders,
            do_update_playlists=run_setup.do_update_playlists,
            do_update_thumbnails=run_setup.do_update_thumbnails,
            dry_run=bool(dry_run),
            do_debug=run_setup.do_debug,
        )

    def _apply_sync_event(self, event):
        self._state.run_state.run_id = event.run_id
        message = self._event_message(event)
        if message:
            level = "error" if event.event_type == EventType.RUN_FAILED else "info"
            if event.event_type == EventType.RUN_CANCELLED:
                level = "warning"
            self._append_log(level, message, ts=event.ts)

        if event.event_type == EventType.RUN_STARTED:
            self._state.run_state.status = "running"
            self._state.run_state.is_dry_run = bool(event.data.get("dry_run", False))
            self._state.run_state.progress_total = int(event.total or 0)
            self._state.run_state.progress_current = 0
            self._state.run_state.started_at = event.ts
            self._state.run_state.finished_at = None
            self._state.run_state.last_error = None
            self._state.run_state.can_cancel = True
            self._state.run_state.active_detail = "Run started"
            self._state.preview.estimated_transfer_bytes = int(event.bytes_estimated or 0)
            self._state.preview.planned_steps_total = int(event.total or 0)
        elif event.event_type == EventType.OVERALL_UPDATED:
            if event.advance:
                self._state.run_state.progress_current += int(event.advance)
            if event.message:
                self._state.run_state.active_detail = event.message
        elif event.event_type == EventType.SYSTEM_STARTED and event.system:
            self._update_system_row(event.system, status="running", last_message="Running")
            self._state.run_state.active_detail = f"System: {event.system}"
        elif event.event_type == EventType.SYSTEM_FINISHED and event.system:
            self._update_system_row(event.system, status="done", last_message="Completed")
        elif event.event_type == EventType.JOB_STARTED and event.job:
            self._state.run_state.active_detail = f"Job: {event.job}"
        elif event.event_type == EventType.STEP_STARTED and event.job and event.system:
            self._update_system_row(event.system, status="running", last_message=event.job)
            self._state.run_state.active_detail = f"{event.system}: {event.job}"
        elif event.event_type == EventType.RUN_FINISHED:
            self._state.run_state.status = "done"
            self._state.run_state.finished_at = event.ts
            self._state.run_state.can_cancel = False
            self._state.run_state.active_detail = "Run finished"
            if self._state.run_state.progress_total:
                self._state.run_state.progress_current = self._state.run_state.progress_total
            self._state.status_message = "Run finished."
        elif event.event_type == EventType.RUN_CANCELLED:
            self._state.run_state.status = "cancelled"
            self._state.run_state.finished_at = event.ts
            self._state.run_state.last_error = event.error or event.message
            self._state.run_state.can_cancel = False
            self._state.run_state.active_detail = event.message or "Run cancelled"
            self._state.status_message = event.message or "Run cancelled."
        elif event.event_type == EventType.RUN_FAILED:
            self._state.run_state.status = "failed"
            self._state.run_state.finished_at = event.ts
            self._state.run_state.last_error = event.error or event.message
            self._state.run_state.can_cancel = False
            self._state.run_state.active_detail = event.message or "Run failed"
            self._state.status_message = event.message or "Run failed."
        elif event.event_type == EventType.SUMMARY_EMITTED:
            self._state.preview.estimated_transfer_bytes = int(event.bytes_estimated or 0)

    def _event_message(self, event):
        if event.event_type == EventType.RUN_STARTED:
            return "Run started."
        if event.event_type == EventType.RUN_FINISHED:
            return "Run finished."
        if event.event_type == EventType.RUN_CANCELLED:
            return event.message or "Run cancelled."
        if event.event_type == EventType.RUN_FAILED:
            return event.message or event.error or "Run failed."
        if event.event_type == EventType.SYSTEM_STARTED and event.system:
            return f"Processing {event.system}"
        if event.event_type == EventType.SYSTEM_FINISHED and event.system:
            return f"Completed {event.system}"
        if event.event_type == EventType.JOB_STARTED and event.job:
            return f"Starting {event.job}"
        if event.event_type == EventType.JOB_FINISHED and event.job:
            return f"Finished {event.job}"
        if event.event_type == EventType.STEP_STARTED and event.system and event.job:
            return f"{event.system}: {event.job}"
        if event.event_type == EventType.TRANSFER_FINISHED and event.system and event.job:
            return f"{event.system}: transfer finished for {event.job}"
        if event.event_type == EventType.SUMMARY_EMITTED and event.message:
            return event.message
        return event.message

    def _consume_summary(self, message):
        text = str(message)
        self._state.run_state.result_summary = text
        report = PreviewReportView(
            kind=self._summary_kind(text),
            title=self._summary_title(text),
            message=text,
            severity="info",
            ts=time.time(),
        )
        self._state.preview.reports.append(report)
        self._state.preview.reports = self._state.preview.reports[-50:]
        self._append_log("info", text, source="summary")

    def _consume_transport_status(self, payload):
        message = str(payload.get("message", ""))
        severity = str(payload.get("severity", "info"))
        self._state.transport_status = TransportStatusView(
            message=message,
            severity=severity,
            visible=bool(message),
        )
        if message:
            self._append_log(severity, message, source="transport")

    def _summary_kind(self, text):
        if "Thumbnail Coverage Summary" in text:
            return "thumbnail_coverage"
        if "would be copied" in text or "Estimated transfer volume" in text:
            return "transfer_estimate"
        return "report"

    def _summary_title(self, text):
        if "Thumbnail Coverage Summary" in text:
            return "Thumbnail Coverage"
        if "would be copied" in text or "Estimated transfer volume" in text:
            return "Transfer Estimate"
        return "Report"

    def _update_system_row(self, system_name, *, status=None, last_message=None):
        for idx, row in enumerate(self._state.systems):
            if row.name != system_name:
                continue
            updated_row = row
            if status is not None:
                updated_row = replace(updated_row, status=status)
            if last_message is not None:
                updated_row = replace(updated_row, last_message=last_message)
            self._state.systems[idx] = updated_row
            return

    def _append_log(self, level, message, *, ts=None, source="app", event_type=None):
        self._state.logs.append(
            LogEntryView(
                level=level,
                message=str(message),
                ts=time.time() if ts is None else ts,
                source=source,
                event_type=event_type,
            )
        )
        self._state.logs = self._state.logs[-500:]

    def _append_unique_log(self, level, message, *, source="app"):
        if self._state.logs:
            last = self._state.logs[-1]
            if last.level == level and last.message == str(message) and last.source == source:
                return
        self._append_log(level, message, source=source)

    def _load_persisted_state(self):
        payload = load_app_state(self._app_state_path)
        config_path = payload.get("config_path")
        if isinstance(config_path, str) and config_path.strip():
            self._state.config_path = config_path.strip()
        run_setup = payload.get("run_setup", {})
        if isinstance(run_setup, dict):
            for field_name in PERSISTED_RUN_SETUP_FIELDS:
                if field_name not in run_setup or not hasattr(self._state.run_setup, field_name):
                    continue
                setattr(self._state.run_setup, field_name, run_setup[field_name])

    def _persist_state(self):
        payload = {
            "config_path": self._state.config_path,
            "run_setup": {
                field_name: getattr(self._state.run_setup, field_name)
                for field_name in PERSISTED_RUN_SETUP_FIELDS
            },
        }
        try:
            save_app_state(self._app_state_path, payload)
        except OSError:
            self._append_log("warning", f"Failed to save app state to {self._app_state_path}")

    def _set_config_error(self, message, *, keep_loaded=False):
        self._state.config_loaded = bool(keep_loaded and self._default is not None)
        self._state.config_error = str(message)
        self._state.status_message = str(message)
        self._append_unique_log("error", message)

    def _set_run_error(self, message):
        self._state.run_state = replace(
            self._state.run_state,
            status="failed",
            finished_at=time.time(),
            can_cancel=False,
            active_detail=str(message),
            last_error=str(message),
        )
        self._state.status_message = str(message)
        self._append_log("error", message)
