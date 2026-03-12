import time
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock
from typing import Protocol

from .jobs import (
    BiosSync,
    FavoritesSync,
    PlaylistSyncJob,
    PlaylistUpdateJob,
    RomSyncJob,
    ThumbnailsSync,
)
from .transports import TransportError


def format_transfer_size(num_bytes):
    gib = 1024**3
    mib = 1024**2
    if num_bytes >= gib:
        return f"{num_bytes / gib:.2f} GB"
    return f"{num_bytes / mib:.2f} MB"


@dataclass(frozen=True)
class SyncRunConfig:
    do_sync_playlists: bool
    do_sync_bios: bool
    do_sync_favorites: bool
    do_sync_thumbnails: bool
    do_sync_roms: bool
    do_update_playlists: bool
    dry_run: bool = False
    do_debug: bool = False


@dataclass(frozen=True)
class JobRegistry:
    bios_sync: type = BiosSync
    favorites_sync: type = FavoritesSync
    thumbnails_sync: type = ThumbnailsSync
    playlist_sync_job: type = PlaylistSyncJob
    playlist_update_job: type = PlaylistUpdateJob
    rom_sync_job: type = RomSyncJob


class SyncReporter(Protocol):
    def start(self, *, overall_total: int, supports_per_file_progress: bool) -> None: ...

    def finish(self) -> None: ...

    def update_overall(self, *, description: str | None = None, advance: int = 0) -> None: ...

    def add_current_task(self, description: str) -> int: ...

    def stop_current_task(self, task_id: int, *, description: str) -> None: ...

    def add_system_steps(self, *, name: str, total: int) -> int: ...

    def advance_system_steps(self, task_id: int, *, advance: int = 1) -> None: ...

    def hide_system_steps(self, task_id: int) -> None: ...

    def add_step_task(self, *, action: str, name: str) -> int: ...

    def finish_step_task(self, task_id: int) -> None: ...

    def begin_transport_file_progress(self, total: int) -> None: ...

    def advance_transport_file_progress(self, *, step: int = 1) -> None: ...

    def complete_transport_file_progress(self) -> None: ...

    def end_transport_file_progress(self) -> None: ...

    def set_transport_status(self, message: str) -> None: ...

    def hide_transport_tasks(self) -> None: ...

    def emit_summary(self, message: str) -> None: ...


class SyncAbortError(Exception):
    pass


class CancelToken:
    def __init__(self):
        self._event = Event()
        self._lock = Lock()
        self._reason = "Sync cancelled."

    def cancel(self, reason=None):
        with self._lock:
            if reason:
                self._reason = str(reason)
            self._event.set()

    def is_cancelled(self):
        return self._event.is_set()

    def reason(self):
        with self._lock:
            return self._reason


class SyncRunner:
    def __init__(
        self,
        *,
        default,
        playlists,
        transport,
        reporter: SyncReporter,
        job_registry: JobRegistry | None = None,
    ):
        self.default = default
        self.playlists = playlists
        self.transport = transport
        self.reporter = reporter
        self.job_registry = job_registry or JobRegistry()

    def _raise_if_cancelled(self, cancel_token):
        if cancel_token.is_cancelled():
            raise SyncAbortError(cancel_token.reason())

    def run(self, cfg: SyncRunConfig, *, system_name=None, cancel_token=None):
        cancel_token = cancel_token or CancelToken()
        jobs = []
        if cfg.do_sync_bios:
            jobs.append(self.job_registry.bios_sync(self.default, self.playlists, self.transport))

        if cfg.do_sync_favorites:
            jobs.append(
                self.job_registry.favorites_sync(self.default, self.playlists, self.transport)
            )

        if cfg.do_sync_thumbnails:
            jobs.append(
                self.job_registry.thumbnails_sync(self.default, self.playlists, self.transport)
            )

        system_jobs = []
        if cfg.do_update_playlists:
            system_jobs.append(self.job_registry.playlist_update_job(self.default, self.transport))

        if cfg.do_sync_playlists:
            system_jobs.append(self.job_registry.playlist_sync_job(self.default, self.transport))

        if cfg.do_sync_roms:
            system_jobs.append(self.job_registry.rom_sync_job(self.default, self.transport))

        playlists = self.playlists
        if system_name:
            playlists = [p for p in playlists if p.get("name") == system_name]

        systems = {}
        for _, playlist in enumerate(playlists):
            name = Path(playlist.get("name")).stem
            if not playlist.get("disabled", False):
                system_transfer_bytes = 0
                for job in system_jobs:
                    job.setup(playlist)
                    system_transfer_bytes += getattr(job, "transfer_bytes", 0)
                systems[name] = {
                    "name": name,
                    "playlist": playlist,
                    "transfer_bytes": system_transfer_bytes,
                }

        total_transfer_bytes = sum(getattr(job, "transfer_bytes", 0) for job in jobs) + sum(
            system["transfer_bytes"] for system in systems.values()
        )

        overall_total = len(jobs) + (len(systems) if system_jobs else 0)
        supports_per_file_progress = getattr(
            getattr(self.transport, "capabilities", None), "per_file_callback", True
        )

        self.reporter.start(
            overall_total=overall_total, supports_per_file_progress=supports_per_file_progress
        )
        try:
            self._raise_if_cancelled(cancel_token)
            for idx, job in enumerate(jobs):
                self._raise_if_cancelled(cancel_token)
                top_descr = f"[bold #AAAAAA]({idx} out of {len(jobs)} jobs done)"
                self.reporter.update_overall(description=top_descr)
                job_size = format_transfer_size(getattr(job, "transfer_bytes", 0))
                current_task_id = self.reporter.add_current_task(f"Run job {job.name} ({job_size})")
                system_steps_task_id = self.reporter.add_system_steps(name=job.name, total=2)
                self.reporter.advance_system_steps(system_steps_task_id, advance=1)
                self.reporter.begin_transport_file_progress(
                    job.size if supports_per_file_progress else 1
                )
                try:
                    cancel_check = cancel_token.is_cancelled
                    if supports_per_file_progress:

                        def callback():
                            self._raise_if_cancelled(cancel_token)
                            self.reporter.advance_transport_file_progress(step=1)
                    else:
                        callback = None
                    job.do(callback=callback, cancel_check=cancel_check)
                    self._raise_if_cancelled(cancel_token)
                    if not supports_per_file_progress:
                        self.reporter.advance_transport_file_progress(step=1)
                    self.reporter.complete_transport_file_progress()
                except TransportError as exc:
                    interrupted = isinstance(exc.__cause__, KeyboardInterrupt) or (
                        "interrupted by user" in str(exc).lower()
                    )
                    if interrupted:
                        raise SyncAbortError("Stopping workers...") from exc
                    raise SyncAbortError(f"Transfer aborted: {exc}") from exc
                finally:
                    self.reporter.end_transport_file_progress()
                if cfg.dry_run:
                    time.sleep(0.2)
                self.reporter.advance_system_steps(system_steps_task_id, advance=1)
                self.reporter.hide_system_steps(system_steps_task_id)
                self.reporter.stop_current_task(
                    current_task_id, description=f"[bold green]{job.name} synced ({job_size})"
                )
                self.reporter.update_overall(advance=1)

            self.reporter.update_overall(
                description=(
                    f"[bold green]{len(jobs)} jobs processed "
                    f"({format_transfer_size(total_transfer_bytes)}), done!"
                )
            )

            if cfg.do_update_playlists or cfg.do_sync_playlists or cfg.do_sync_roms:
                for idx, key in enumerate(systems.keys()):
                    self._raise_if_cancelled(cancel_token)
                    name = systems[key]["name"]
                    playlist = systems[key]["playlist"]
                    top_descr = f"[bold #AAAAAA]({idx} out of {len(systems)} systems synced)"
                    self.reporter.update_overall(description=top_descr)
                    system_transfer_size = format_transfer_size(systems[key]["transfer_bytes"])
                    current_task_id = self.reporter.add_current_task(
                        f"Syncing system {name} ({system_transfer_size})"
                    )

                    system_jobs_size = 0
                    for job in system_jobs:
                        job.setup(playlist)
                        system_jobs_size += job.size

                    if supports_per_file_progress:
                        system_steps_total = system_jobs_size
                    else:
                        system_steps_total = len(system_jobs)
                    system_steps_task_id = self.reporter.add_system_steps(
                        name=name, total=system_steps_total
                    )
                    for job in system_jobs:
                        self._raise_if_cancelled(cancel_token)
                        step_size = format_transfer_size(getattr(job, "transfer_bytes", 0))
                        step_task_id = self.reporter.add_step_task(
                            action=f"{job.name} ({step_size})", name=name
                        )
                        if cfg.do_debug:
                            time.sleep(1)

                        job.setup(playlist)
                        self.reporter.begin_transport_file_progress(
                            job.size if supports_per_file_progress else 1
                        )
                        try:
                            cancel_check = cancel_token.is_cancelled
                            if supports_per_file_progress:

                                def callback(system_steps_task_id=system_steps_task_id):
                                    self._raise_if_cancelled(cancel_token)
                                    self.reporter.advance_system_steps(
                                        system_steps_task_id, advance=1
                                    )
                                    self.reporter.advance_transport_file_progress(step=1)
                            else:
                                callback = None
                            job.do(callback=callback, cancel_check=cancel_check)
                            self._raise_if_cancelled(cancel_token)
                            if not supports_per_file_progress:
                                self.reporter.advance_system_steps(system_steps_task_id, advance=1)
                                self.reporter.advance_transport_file_progress(step=1)
                            self.reporter.complete_transport_file_progress()
                        except TransportError as exc:
                            interrupted = isinstance(exc.__cause__, KeyboardInterrupt) or (
                                "interrupted by user" in str(exc).lower()
                            )
                            if interrupted:
                                raise SyncAbortError("Stopping workers...") from exc
                            raise SyncAbortError(f"Transfer aborted: {exc}") from exc
                        finally:
                            self.reporter.end_transport_file_progress()

                        self.reporter.finish_step_task(step_task_id)

                    if cfg.dry_run:
                        time.sleep(0.2)
                    self.reporter.hide_system_steps(system_steps_task_id)
                    self.reporter.stop_current_task(
                        current_task_id,
                        description=f"[bold green]{name} synced ({system_transfer_size})",
                    )
                    self.reporter.update_overall(advance=1)

                self.reporter.update_overall(
                    description=(
                        f"[bold green]{len(systems)} systems processed "
                        f"({format_transfer_size(total_transfer_bytes)}), done!"
                    )
                )
            self.reporter.hide_transport_tasks()
        except KeyboardInterrupt as exc:
            raise SyncAbortError("Stopping workers...") from exc
        finally:
            self.reporter.finish()

        if cfg.dry_run:
            self.reporter.emit_summary(
                f"Dry-run estimate: {format_transfer_size(total_transfer_bytes)} would be copied."
            )
        else:
            self.reporter.emit_summary(
                f"Estimated transfer volume: {format_transfer_size(total_transfer_bytes)}."
            )

        return total_transfer_bytes
