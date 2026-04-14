from unittest.mock import Mock, patch

from click.testing import CliRunner

from retrosync import (
    ACTION_SYNC_PLAYLISTS,
    ACTION_SYNC_ROMS,
    ACTION_SYNC_SHADERS,
    ACTION_SYNC_THUMBNAILS,
    ACTION_UPDATE_PLAYLISTS,
    ACTION_UPDATE_THUMBNAILS,
    main,
)


def invoke_cli(args, *, input_text=None):
    return CliRunner().invoke(main, args, input=input_text)


def test_help():
    result = invoke_cli(["--help"])
    assert result.exit_code == 0, result.output
    assert "Usage: retrosync.py" in result.output
    assert "sync" in result.output
    assert "update" in result.output
    assert "list" in result.output


def test_no_args_prints_help():
    result = invoke_cli([])
    assert result.exit_code == 0, result.output
    assert "Usage: retrosync.py" in result.output


def test_gui_subcommand_launches_gui():
    with patch("retrosync.launch_gui") as launch_gui:
        result = invoke_cli(["gui"])

    assert result.exit_code == 0, result.output
    launch_gui.assert_called_once_with()


def test_prompt_shows_multiple_matches_and_selection_runs_command():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
            "src_cores": "tests/assets/cores",
            "src_cores_suffix": ".dylib",
        },
        "playlists": [
            {
                "name": "Nintendo - NES.lpl",
                "src_folder": "nes",
                "dest_folder": "nes",
                "src_core_path": "core1",
                "src_core_name": "Core 1",
            },
            {
                "name": "Nintendo - SNES.lpl",
                "src_folder": "snes",
                "dest_folder": "snes",
                "src_core_path": "core2",
                "src_core_name": "Core 2",
            },
        ],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
    ):
        result = invoke_cli(
            [
                "update",
                "playlists",
                "--dry-run",
                "--system",
                "Nintendo",
                "--config-file=ignored.conf",
            ],
            input_text="2\n",
        )

    assert result.exit_code == 0, result.output
    assert "Select a playlist match for 'Nintendo':" in result.output
    assert "1. Nintendo - NES.lpl" in result.output
    assert "2. Nintendo - SNES.lpl" in result.output
    assert fake_runner.run.call_args.kwargs["system_name"] == "Nintendo - SNES.lpl"


def test_yes_skips_prompt_and_uses_first_match():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
            "src_cores": "tests/assets/cores",
            "src_cores_suffix": ".dylib",
        },
        "playlists": [
            {
                "name": "Sony - PlayStation.lpl",
                "src_folder": "psx",
                "dest_folder": "psx",
                "src_core_path": "core1",
                "src_core_name": "Core 1",
            },
        ],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
        patch("retrosync.rank_system_matches", return_value=["Sony - PlayStation.lpl"]),
        patch("retrosync.click.prompt") as prompt_mock,
    ):
        result = invoke_cli(
            [
                "update",
                "playlists",
                "--dry-run",
                "--system=psx",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == 0, result.output
    prompt_mock.assert_not_called()
    assert fake_runner.run.call_args.kwargs["system_name"] == "Sony - PlayStation.lpl"


def _minimal_transport_config(default_transport):
    return {
        "default": {
            "transport": default_transport,
            "src_roms": ["tests/assets/roms"],
            "src_playlists": "tests/assets/playlists",
            "dest_playlists": "tests/assets/playlists",
            "src_cores": "tests/assets/cores",
            "src_cores_suffix": ".dylib",
            "target_roms": "/retroarch/roms",
            "target_cores": "/retroarch/cores",
            "target_cores_suffix": "_libretro.so",
        },
        "ssh": {
            "hostname": "example-host",
            "username": "ssh-user",
            "password": "ssh-pass",
        },
        "webdav": {
            "url": "http://dav.local",
            "username": "dav-user",
            "password": "dav-pass",
        },
        "playlists": [
            {
                "name": "Sony - PlayStation.lpl",
                "src_folder": "psx",
                "dest_folder": "psx",
                "src_core_path": "core1",
                "src_core_name": "Core 1",
            }
        ],
    }


def test_transport_override_cli_sets_webdav_mode_for_factory():
    fake_config = _minimal_transport_config("filesystem")
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)
    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport) as factory_mock,
        patch("retrosync.SyncRunner", fake_runner_ctor),
    ):
        result = invoke_cli(
            [
                "sync",
                "playlists",
                "--dry-run",
                "--yes",
                "--config-file=ignored.conf",
                "--transport=webdav",
            ]
        )

    assert result.exit_code == 0, result.output
    called_default, called_dry_run, called_force_transport = factory_mock.call_args[0]
    assert called_default["transport"] == "webdav"
    assert called_default["url"] == "http://dav.local"
    assert called_dry_run is True
    assert str(called_force_transport).lower() == "false"


def test_transport_impl_unix_keeps_force_flag():
    fake_config = _minimal_transport_config("filesystem")
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)
    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport) as factory_mock,
        patch("retrosync.SyncRunner", fake_runner_ctor),
    ):
        result = invoke_cli(
            [
                "sync",
                "playlists",
                "--dry-run",
                "--yes",
                "--config-file=ignored.conf",
                "--transport=ssh",
                "--transport-impl=unix",
            ]
        )

    assert result.exit_code == 0, result.output
    called_default, _, called_force_transport = factory_mock.call_args[0]
    assert called_default["transport"] == "ssh"
    assert called_default["hostname"] == "example-host"
    assert called_force_transport == "unix"


def test_update_thumbnails_sets_action_and_forwards_system_filter():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
        },
        "playlists": [
            {"name": "Sony - PlayStation.lpl", "src_folder": "psx", "dest_folder": "psx"},
        ],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
        patch("retrosync.rank_system_matches", return_value=["Sony - PlayStation.lpl"]),
    ):
        result = invoke_cli(
            [
                "update",
                "thumbnails",
                "--dry-run",
                "--system=psx",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == 0, result.output
    run_cfg = fake_runner.run.call_args.args[0]
    assert ACTION_UPDATE_THUMBNAILS in run_cfg.actions
    assert run_cfg.dry_run is True
    assert fake_runner.run.call_args.kwargs["system_name"] == "Sony - PlayStation.lpl"


def test_sync_shaders_sets_action():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_roms": [],
            "dest_retroarch_base": "/retroarch/config",
        },
        "shaders": [
            {"name": "Snes9x", "shader": "crt/crt-guest-advanced.slangp"},
        ],
        "playlists": [],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
    ):
        result = invoke_cli(
            [
                "sync",
                "shaders",
                "--dry-run",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == 0, result.output
    run_cfg = fake_runner.run.call_args.args[0]
    assert ACTION_SYNC_SHADERS in run_cfg.actions
    assert run_cfg.dry_run is True


def test_update_thumbnails_apply_requires_src_thumbnails():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
        },
        "playlists": [
            {"name": "Sony - PlayStation.lpl", "src_folder": "psx", "dest_folder": "psx"},
        ],
    }

    with patch("retrosync.toml.load", return_value=fake_config):
        result = invoke_cli(
            [
                "update",
                "thumbnails",
                "--apply",
                "--system=psx",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == -1
    assert "[default] 'src_thumbnails' is required for --update-thumbnails --apply" in result.output


def test_update_thumbnails_cache_flags_are_forwarded_to_default_config():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
        },
        "playlists": [
            {"name": "Sony - PlayStation.lpl", "src_folder": "psx", "dest_folder": "psx"},
        ],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
        patch("retrosync.rank_system_matches", return_value=["Sony - PlayStation.lpl"]),
    ):
        result = invoke_cli(
            [
                "update",
                "thumbnails",
                "--dry-run",
                "--refresh-thumbnail-cache",
                "--no-thumbnail-cache",
                "--system=psx",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == 0, result.output
    forwarded_default = fake_runner_ctor.call_args.kwargs["default"]
    assert forwarded_default["_refresh_thumbnail_cache"] is True
    assert forwarded_default["_no_thumbnail_cache"] is True


def test_targeted_update_playlists_ignores_unrelated_invalid_playlist_config():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
            "src_cores": "tests/assets/cores",
            "src_cores_suffix": ".dylib",
        },
        "playlists": [
            {
                "name": "Quake II.lpl",
                "src_folder": "ID - Quake2",
                "dest_folder": "quake2",
                "src_core_path": "vitaquake2_libretro",
                "src_core_name": "Quake II (vitaQuake 2)",
            },
            {
                "name": "Quake III.lpl",
                "src_folder": "ID - Quake3",
                "dest_folder": "quake3",
                "src_core_path": "",
                "src_core_name": "Quake III",
            },
        ],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
        patch("retrosync.rank_system_matches", return_value=["Quake II.lpl", "Quake III.lpl"]),
    ):
        result = invoke_cli(
            [
                "update",
                "playlists",
                "--dry-run",
                "--system=quake",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == 0, result.output
    assert fake_runner.run.call_args.kwargs["system_name"] == "Quake II.lpl"


def test_sync_roms_advances_transport_file_progress_hooks():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_roms": ["tests/assets/roms"],
            "dest_roms": "tests/assets/roms",
        },
        "playlists": [
            {"name": "FBNeo - Arcade Games.lpl", "src_folder": "", "dest_folder": ""},
        ],
    }
    fake_transport = Mock()

    class FakeRomSyncJob:
        name = "Sync ROMs"

        def __init__(self, default, transport):
            self.default = default
            self.transport = transport
            self.size = 0

        def setup(self, _playlist):
            self.size = 2

        def do(self, callback=None, cancel_check=None):
            if cancel_check and cancel_check():
                return
            callback()
            callback()

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.RomSyncJob", FakeRomSyncJob),
        patch("retrosync.begin_transport_file_progress") as begin_mock,
        patch("retrosync.advance_transport_file_progress") as advance_mock,
        patch("retrosync.complete_transport_file_progress") as complete_mock,
    ):
        result = invoke_cli(
            [
                "sync",
                "roms",
                "--dry-run",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == 0, result.output
    begin_mock.assert_called_once_with(2)
    assert advance_mock.call_count == 2
    complete_mock.assert_called_once()


def test_sync_roms_falls_back_to_per_job_progress_when_no_per_file_callbacks():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_roms": ["tests/assets/roms"],
            "dest_roms": "tests/assets/roms",
        },
        "playlists": [
            {"name": "FBNeo - Arcade Games.lpl", "src_folder": "", "dest_folder": ""},
        ],
    }
    fake_transport = Mock()
    fake_transport.capabilities = Mock(per_file_callback=False)

    class FakeRomSyncJob:
        name = "Sync ROMs"

        def __init__(self, default, transport):
            self.default = default
            self.transport = transport
            self.size = 0

        def setup(self, _playlist):
            self.size = 5

        def do(self, callback=None, cancel_check=None):
            if cancel_check and cancel_check():
                return
            if callback:
                callback()
                callback()

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.RomSyncJob", FakeRomSyncJob),
        patch("retrosync.begin_transport_file_progress") as begin_mock,
        patch("retrosync.advance_transport_file_progress") as advance_mock,
        patch("retrosync.complete_transport_file_progress") as complete_mock,
    ):
        result = invoke_cli(
            [
                "sync",
                "roms",
                "--dry-run",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == 0, result.output
    begin_mock.assert_called_once_with(1)
    assert advance_mock.call_count == 1
    complete_mock.assert_called_once()


def test_sync_all_is_copy_only():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "dest_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
            "dest_roms": "tests/assets/roms",
            "src_bios": "tests/assets",
            "dest_bios": "tests/assets",
            "src_config": "tests/assets/config",
            "dest_config": "tests/assets/config",
            "target_roms": "/retroarch/roms",
            "target_cores": "/retroarch/cores",
            "src_cores": "tests/assets/cores",
            "src_cores_suffix": ".dylib",
            "target_cores_suffix": "_libretro.so",
            "src_thumbnails": "tests/assets",
            "dest_thumbnails": "tests/assets",
            "dest_retroarch_base": "/retroarch/config",
        },
        "shaders": [{"name": "Snes9x", "shader": "crt/test.slangp"}],
        "playlists": [
            {
                "name": "Sony - PlayStation.lpl",
                "src_folder": "psx",
                "dest_folder": "psx",
                "src_core_path": "core1",
                "src_core_name": "Core 1",
            }
        ],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
    ):
        result = invoke_cli(["sync", "all", "--dry-run", "--yes", "--config-file=ignored.conf"])

    assert result.exit_code == 0, result.output
    run_cfg = fake_runner.run.call_args.args[0]
    assert ACTION_SYNC_PLAYLISTS in run_cfg.actions
    assert ACTION_SYNC_ROMS in run_cfg.actions
    assert ACTION_SYNC_SHADERS in run_cfg.actions
    assert ACTION_UPDATE_PLAYLISTS not in run_cfg.actions
    assert ACTION_UPDATE_THUMBNAILS not in run_cfg.actions


def test_sync_accepts_multiple_targets():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "dest_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
            "dest_roms": "tests/assets/roms",
            "src_thumbnails": "tests/assets",
            "dest_thumbnails": "tests/assets",
            "dest_retroarch_base": "/retroarch/config",
        },
        "shaders": [{"name": "Snes9x", "shader": "crt/test.slangp"}],
        "playlists": [
            {
                "name": "Sony - PlayStation.lpl",
                "src_folder": "psx",
                "dest_folder": "psx",
                "src_core_path": "core1",
                "src_core_name": "Core 1",
            }
        ],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
    ):
        result = invoke_cli(
            [
                "sync",
                "roms",
                "shaders",
                "thumbnails",
                "--dry-run",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    assert result.exit_code == 0, result.output
    run_cfg = fake_runner.run.call_args.args[0]
    assert ACTION_SYNC_ROMS in run_cfg.actions
    assert ACTION_SYNC_SHADERS in run_cfg.actions
    assert ACTION_SYNC_THUMBNAILS in run_cfg.actions
    assert ACTION_SYNC_PLAYLISTS not in run_cfg.actions


def test_sync_rejects_all_combined_with_other_targets():
    result = invoke_cli(["sync", "all", "roms", "--dry-run", "--yes", "--config-file=ignored.conf"])

    assert result.exit_code == -1
    assert "'all' cannot be combined with other sync targets." in result.output


def test_run_full_includes_sync_and_update_actions():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_playlists": "tests/assets/playlists",
            "dest_playlists": "tests/assets/playlists",
            "src_roms": ["tests/assets/roms"],
            "dest_roms": "tests/assets/roms",
            "src_bios": "tests/assets",
            "dest_bios": "tests/assets",
            "src_config": "tests/assets/config",
            "dest_config": "tests/assets/config",
            "target_roms": "/retroarch/roms",
            "target_cores": "/retroarch/cores",
            "src_cores": "tests/assets/cores",
            "src_cores_suffix": ".dylib",
            "target_cores_suffix": "_libretro.so",
            "src_thumbnails": "tests/assets",
            "dest_thumbnails": "tests/assets",
            "dest_retroarch_base": "/retroarch/config",
        },
        "shaders": [{"name": "Snes9x", "shader": "crt/test.slangp"}],
        "playlists": [
            {
                "name": "Sony - PlayStation.lpl",
                "src_folder": "psx",
                "dest_folder": "psx",
                "src_core_path": "core1",
                "src_core_name": "Core 1",
            }
        ],
    }
    fake_transport = Mock()
    fake_runner = Mock()
    fake_runner_ctor = Mock(return_value=fake_runner)

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport),
        patch("retrosync.SyncRunner", fake_runner_ctor),
    ):
        result = invoke_cli(["run", "full", "--dry-run", "--yes", "--config-file=ignored.conf"])

    assert result.exit_code == 0, result.output
    run_cfg = fake_runner.run.call_args.args[0]
    assert ACTION_SYNC_PLAYLISTS in run_cfg.actions
    assert ACTION_UPDATE_PLAYLISTS in run_cfg.actions
    assert ACTION_UPDATE_THUMBNAILS in run_cfg.actions


def test_list_playlists_outputs_system_status_counts_and_paths(tmp_path):
    roms_a = tmp_path / "roms-a"
    roms_b = tmp_path / "roms-b"
    (roms_a / "NES").mkdir(parents=True)
    (roms_b / "NES").mkdir(parents=True)
    (roms_a / "PSX").mkdir(parents=True)
    (roms_a / "NES" / "Mario.zip").write_bytes(b"a" * 1048576)
    (roms_b / "NES" / "Zelda.zip").write_bytes(b"b" * 524288)
    (roms_a / "PSX" / "Metal Gear Solid.cue").write_bytes(b"c" * 2097152)
    (roms_a / "PSX" / "Metal Gear Solid.bin").write_bytes(b"d" * 1048576)

    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_roms": [str(roms_a), str(roms_b)],
        },
        "playlists": [
            {"name": "Nintendo - NES.lpl", "src_folder": "NES", "dest_folder": "NES"},
            {
                "name": "Sony - PlayStation.lpl",
                "src_folder": "PSX",
                "dest_folder": "PSX",
                "src_whitelist": r"\.(m3u|cue)$",
                "src_blacklist": r"\.bin$",
                "disabled": True,
            },
        ],
    }

    with patch("retrosync.toml.load", return_value=fake_config):
        result = invoke_cli(["list", "playlists", "--config-file=ignored.conf"])

    assert result.exit_code == 0, result.output
    assert "Configured Playlists" in result.output
    assert "Nintendo - NES" in result.output
    assert "Sony - PlayStation" in result.output
    assert "🛑" in result.output


def test_list_playlists_does_not_create_transport():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_roms": ["tests/assets/roms"],
        },
        "playlists": [
            {"name": "Nintendo - NES.lpl", "src_folder": "nes", "dest_folder": "nes"},
        ],
    }

    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory") as factory_mock,
    ):
        result = invoke_cli(["list", "systems", "--config-file=ignored.conf"])

    assert result.exit_code == 0, result.output
    factory_mock.assert_not_called()
    assert "Nintendo - NES" in result.output
