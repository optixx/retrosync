import time

from retrosync_app.controller import RetrosyncAppController
from retrosync_core.events import EventType, SyncEvent


class DummyTransport:
    class capabilities:
        per_file_callback = False


class DummyRunner:
    def __init__(self, *, default, playlists, transport, reporter, job_registry, event_sink):
        self.reporter = reporter
        self.event_sink = event_sink
        self.playlists = playlists

    def run(self, cfg, *, system_name=None, cancel_token=None):
        _ = (cfg, system_name, cancel_token)
        self.reporter.start(overall_total=len(self.playlists), supports_per_file_progress=False)
        self.event_sink.emit(
            SyncEvent(event_type=EventType.RUN_STARTED, run_id="run-1", total=len(self.playlists))
        )
        if self.playlists:
            system_name = self.playlists[0]["name"].removesuffix(".lpl")
            self.event_sink.emit(
                SyncEvent(
                    event_type=EventType.SYSTEM_STARTED,
                    run_id="run-1",
                    system=system_name,
                )
            )
            self.event_sink.emit(
                SyncEvent(
                    event_type=EventType.SYSTEM_FINISHED,
                    run_id="run-1",
                    system=system_name,
                )
            )
        self.event_sink.emit(SyncEvent(event_type=EventType.RUN_FINISHED, run_id="run-1"))
        self.reporter.emit_summary("Dry-run estimate: 0.00 MB would be copied.")
        self.reporter.finish()


class FailingRunner:
    def __init__(self, *, default, playlists, transport, reporter, job_registry, event_sink):
        _ = (default, playlists, transport, reporter, job_registry, event_sink)

    def run(self, cfg, *, system_name=None, cancel_token=None):
        _ = (cfg, system_name, cancel_token)
        raise RuntimeError("boom")


def _config():
    return {
        "default": {
            "transport": "filesystem",
            "src_roms": ["tests/assets/roms"],
            "src_playlists": "tests/assets/playlists",
            "dest_playlists": "tests/assets/playlists",
            "target_roms": "roms",
            "src_cores": "tests/assets/cores",
            "target_cores": "cores",
            "src_cores_suffix": ".dylib",
            "target_cores_suffix": ".so",
        },
        "playlists": [
            {"name": "Sony - PlayStation.lpl", "src_folder": "psx", "dest_folder": "psx"},
            {"name": "Nintendo - NES.lpl", "src_folder": "nes", "dest_folder": "nes"},
        ],
    }


def _broken_config():
    return {
        "default": {
            "transport": "filesystem",
            "src_roms": ["tests/assets/roms"],
            "src_playlists": "tests/assets/playlists",
            "dest_playlists": "tests/assets/playlists",
            "src_cores": "",
        },
        "playlists": [
            {"name": "Sony - PlayStation.lpl", "src_folder": "psx", "dest_folder": "psx"},
        ],
    }


def test_controller_load_config_populates_state(tmp_path):
    controller = RetrosyncAppController(
        toml_loader=lambda _: _config(),
        app_state_path=tmp_path / "gui-state.json",
    )

    loaded = controller.load_config("test.conf")
    state = controller.snapshot()

    assert loaded is True
    assert state.config_loaded is True
    assert state.transport == "filesystem"
    assert [row.name for row in state.systems] == ["Sony - PlayStation", "Nintendo - NES"]
    assert len(state.preview.plan_rows) >= 1
    assert state.preview.planned_copies >= 1


def test_controller_load_config_validates_selected_actions(tmp_path):
    controller = RetrosyncAppController(
        toml_loader=lambda _: _broken_config(),
        app_state_path=tmp_path / "gui-state.json",
    )

    loaded = controller.load_config("broken.conf")
    state = controller.snapshot()

    assert loaded is False
    assert state.config_loaded is True
    assert state.config_error is not None
    assert "'target_roms' is required for --sync-playlists" in state.config_error
    assert state.preview.plan_rows == []


def test_controller_run_updates_state_from_bridge_events(tmp_path):
    controller = RetrosyncAppController(
        toml_loader=lambda _: _config(),
        transport_factory=lambda default, dry_run, force_transport: DummyTransport(),
        runner_factory=DummyRunner,
        app_state_path=tmp_path / "gui-state.json",
    )
    controller.load_config("test.conf")
    controller.set_action("do_sync_playlists", True)

    started = controller.start_run(dry_run=True)
    assert started is True

    for _ in range(50):
        controller.drain_events()
        state = controller.snapshot()
        if state.run_state.status == "done":
            break
        time.sleep(0.01)

    state = controller.snapshot()
    assert state.run_state.status == "done"
    assert state.run_state.result_summary == "Dry-run estimate: 0.00 MB would be copied."
    assert state.systems[0].status == "done"
    assert state.transport_status.severity == "warning"
    assert "Per-file progress unavailable" in state.transport_status.message
    assert state.preview.estimated_transfer_bytes == 0
    assert len(state.preview.reports) == 1
    assert state.preview.reports[0].kind == "transfer_estimate"
    assert any("Run started." in entry.message for entry in state.logs)


def test_controller_worker_failure_marks_run_failed_without_sticking(tmp_path):
    controller = RetrosyncAppController(
        toml_loader=lambda _: _config(),
        transport_factory=lambda default, dry_run, force_transport: DummyTransport(),
        runner_factory=FailingRunner,
        app_state_path=tmp_path / "gui-state.json",
    )
    controller.load_config("test.conf")
    controller.set_action("do_sync_playlists", True)

    started = controller.start_run(dry_run=True)
    assert started is True

    for _ in range(50):
        controller.drain_events()
        state = controller.snapshot()
        if state.run_state.status == "failed":
            break
        time.sleep(0.01)

    state = controller.snapshot()
    assert state.run_state.status == "failed"
    assert state.run_state.can_cancel is False
    assert state.run_state.last_error == "boom"


def test_controller_persists_last_config_and_selected_actions(tmp_path):
    app_state_path = tmp_path / "gui-state.json"

    controller = RetrosyncAppController(
        toml_loader=lambda _: _config(),
        app_state_path=app_state_path,
    )
    controller.set_config_path("saved.conf")
    controller.set_action("do_sync_roms", True)
    controller.set_action("do_sync_shaders", True)
    controller.set_action("do_update_thumbnails", True)
    controller.set_action("do_debug", True)

    restored = RetrosyncAppController(
        toml_loader=lambda _: _config(),
        app_state_path=app_state_path,
    )
    state = restored.snapshot()

    assert state.config_path == "saved.conf"
    assert state.run_setup.do_sync_roms is True
    assert state.run_setup.do_sync_shaders is True
    assert state.run_setup.do_update_thumbnails is True
    assert state.run_setup.do_debug is True
