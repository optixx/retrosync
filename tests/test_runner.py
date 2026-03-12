import pytest

from retrosync_core.events import EventType, MemoryEventSink
from retrosync_core.runner import (
    CancelToken,
    JobRegistry,
    SyncAbortError,
    SyncRunConfig,
    SyncRunner,
)
from retrosync_core.transports import TransportError


class DummyTransport:
    class capabilities:
        per_file_callback = True


class DummyGlobalJob:
    name = "Dummy"

    def __init__(self, default, playlists, transport):
        self.size = 1
        self.transfer_bytes = 1024

    def do(self, callback=None, cancel_check=None):
        if cancel_check and cancel_check():
            raise RuntimeError("cancelled")
        if callback:
            callback()


class DummyFailJob:
    name = "DummyFail"

    def __init__(self, default, playlists, transport):
        self.size = 1
        self.transfer_bytes = 1024

    def do(self, callback=None, cancel_check=None):
        raise TransportError("kaboom")


class DummyReporter:
    def __init__(self, cancel_token=None, cancel_on_first_advance=False):
        self.cancel_token = cancel_token
        self.cancel_on_first_advance = cancel_on_first_advance
        self.advance_calls = 0
        self.started = False
        self.finished = False

    def start(self, *, overall_total, supports_per_file_progress):
        self.started = True

    def finish(self):
        self.finished = True

    def update_overall(self, *, description=None, advance=0):
        pass

    def add_current_task(self, description):
        return 1

    def stop_current_task(self, task_id, *, description):
        pass

    def add_system_steps(self, *, name, total):
        return 1

    def advance_system_steps(self, task_id, *, advance=1):
        pass

    def hide_system_steps(self, task_id):
        pass

    def add_step_task(self, *, action, name):
        return 1

    def finish_step_task(self, task_id):
        pass

    def begin_transport_file_progress(self, total):
        pass

    def advance_transport_file_progress(self, *, step=1):
        self.advance_calls += 1
        if self.cancel_on_first_advance and self.advance_calls == 1 and self.cancel_token:
            self.cancel_token.cancel("Cancelled by user.")

    def complete_transport_file_progress(self):
        pass

    def end_transport_file_progress(self):
        pass

    def set_transport_status(self, message):
        pass

    def hide_transport_tasks(self):
        pass

    def emit_summary(self, message):
        pass


def _cfg():
    return SyncRunConfig(
        do_sync_playlists=False,
        do_sync_bios=True,
        do_sync_favorites=False,
        do_sync_thumbnails=False,
        do_sync_roms=False,
        do_update_playlists=False,
        dry_run=True,
        do_debug=False,
    )


def test_runner_respects_pre_cancelled_token():
    token = CancelToken()
    token.cancel("Cancelled before run.")
    reporter = DummyReporter()
    sink = MemoryEventSink()
    runner = SyncRunner(
        default={},
        playlists=[],
        transport=DummyTransport(),
        reporter=reporter,
        job_registry=JobRegistry(bios_sync=DummyGlobalJob),
        event_sink=sink,
    )

    with pytest.raises(SyncAbortError, match="Cancelled before run."):
        runner.run(_cfg(), cancel_token=token)

    assert reporter.started is True
    assert reporter.finished is True
    assert sink.events[0].event_type == EventType.RUN_STARTED
    assert sink.events[-1].event_type == EventType.RUN_CANCELLED


def test_runner_cancels_after_callback_progress():
    token = CancelToken()
    reporter = DummyReporter(cancel_token=token, cancel_on_first_advance=True)
    sink = MemoryEventSink()
    runner = SyncRunner(
        default={},
        playlists=[],
        transport=DummyTransport(),
        reporter=reporter,
        job_registry=JobRegistry(bios_sync=DummyGlobalJob),
        event_sink=sink,
    )

    with pytest.raises(SyncAbortError, match="Cancelled by user."):
        runner.run(_cfg(), cancel_token=token)

    assert reporter.advance_calls == 1
    assert reporter.finished is True
    assert any(e.event_type == EventType.TRANSFER_ADVANCED for e in sink.events)
    assert sink.events[-1].event_type == EventType.RUN_CANCELLED


def test_runner_emits_success_event_sequence():
    reporter = DummyReporter()
    sink = MemoryEventSink()
    runner = SyncRunner(
        default={},
        playlists=[],
        transport=DummyTransport(),
        reporter=reporter,
        job_registry=JobRegistry(bios_sync=DummyGlobalJob),
        event_sink=sink,
    )

    runner.run(_cfg())

    event_types = [e.event_type for e in sink.events]
    assert event_types[0] == EventType.RUN_STARTED
    assert EventType.JOB_STARTED in event_types
    assert EventType.JOB_FINISHED in event_types
    assert EventType.RUN_FINISHED in event_types
    assert event_types[-1] == EventType.SUMMARY_EMITTED


def test_runner_emits_failed_event():
    reporter = DummyReporter()
    sink = MemoryEventSink()
    runner = SyncRunner(
        default={},
        playlists=[],
        transport=DummyTransport(),
        reporter=reporter,
        job_registry=JobRegistry(bios_sync=DummyFailJob),
        event_sink=sink,
    )

    with pytest.raises(SyncAbortError, match="Transfer aborted:"):
        runner.run(_cfg())

    assert sink.events[0].event_type == EventType.RUN_STARTED
    assert sink.events[-1].event_type == EventType.RUN_FAILED
