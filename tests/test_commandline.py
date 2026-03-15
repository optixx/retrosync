import io
from unittest.mock import patch, Mock
from retrosync import main


def run_cli_tool(args):
    with patch("sys.argv", args), patch("sys.stdout", new=io.StringIO()) as mock_stdout:
        try:
            main()
        except SystemExit:
            pass
        return mock_stdout.getvalue().strip()


def test_help():
    output = run_cli_tool(["retrosync.py", "--help"])
    assert "Usage: retrosync.py" in output


def test_no_args_prints_help():
    output = run_cli_tool(["retrosync.py"])
    assert "Usage: retrosync.py" in output


def test_prompt():
    try:
        run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--update-playlists",
                "--name=psx",
                "--config-file=test.conf",
            ]
        )
        raise AssertionError()
    except OSError:
        assert True


def test_prompt_yes():
    try:
        run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--update-playlists",
                "--name=psx",
                "--yes",
                "--config-file=test.conf",
            ]
        )
        assert True
    except OSError:
        raise AssertionError()


def test_prompt_shows_multiple_matches():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_roms": ["tests/assets/roms"],
            "src_playlists": "tests/assets/playlists",
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
            {
                "name": "FBNeo - Arcade Games.lpl",
                "src_folder": "fbneo",
                "dest_folder": "fbneo",
                "src_core_path": "core3",
                "src_core_name": "Core 3",
            },
        ],
    }
    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.click.prompt", return_value=0),
    ):
        output = run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--update-playlists",
                "--name=Nintendo",
                "--config-file=ignored.conf",
            ]
        )
    assert "Select a playlist match for 'Nintendo':" in output
    assert "1. Nintendo - NES.lpl" in output
    assert "2. Nintendo - SNES.lpl" in output
    assert "0. Cancel" in output


def _minimal_transport_config(default_transport):
    return {
        "default": {
            "transport": default_transport,
            "src_roms": ["tests/assets/roms"],
        },
        "ssh": {
            "hostname": "example-host",
            "username": "ssh-user",
            "password": "ssh-pass",
        },
        "webdav": {
            "host": "http://dav.local",
            "username": "dav-user",
            "password": "dav-pass",
        },
        "playlists": [],
    }


def test_transport_override_cli_sets_webdav_mode_for_factory():
    fake_config = _minimal_transport_config("filesystem")
    fake_transport = Mock()
    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport) as factory_mock,
    ):
        run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--sync-playlists",
                "--yes",
                "--config-file=ignored.conf",
                "--transport=webdav",
            ]
        )

    called_default, called_dry_run, called_force_transport = factory_mock.call_args[0]
    assert called_default["transport"] == "webdav"
    assert called_default["host"] == "http://dav.local"
    assert called_dry_run is True
    assert str(called_force_transport).lower() == "false"


def test_transport_override_with_transport_unix_keeps_force_flag():
    fake_config = _minimal_transport_config("filesystem")
    fake_transport = Mock()
    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.TransportFactory", return_value=fake_transport) as factory_mock,
    ):
        run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--sync-playlists",
                "--yes",
                "--config-file=ignored.conf",
                "--transport=ssh",
                "--transport-unix",
            ]
        )

    called_default, _, called_force_transport = factory_mock.call_args[0]
    assert called_default["transport"] == "ssh"
    assert called_default["hostname"] == "example-host"
    assert called_force_transport == "unix"


def test_rom_sync_advances_transport_file_progress_hooks():
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
        run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--sync-roms",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    begin_mock.assert_called_once_with(2)
    assert advance_mock.call_count == 2
    complete_mock.assert_called_once()


def test_rom_sync_falls_back_to_per_job_progress_when_no_per_file_callbacks():
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
        run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--sync-roms",
                "--yes",
                "--config-file=ignored.conf",
            ]
        )

    begin_mock.assert_called_once_with(1)
    assert advance_mock.call_count == 1
    complete_mock.assert_called_once()


def test_playlist_list_outputs_system_status_counts_and_paths(tmp_path):
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
        output = run_cli_tool(
            [
                "retrosync.py",
                "--playlist-list",
                "--config-file=ignored.conf",
            ]
        )

    assert "Configured Playlists" in output
    assert "Nintendo - NES" in output
    assert "1" in output
    assert "0.00 GB" in output
    assert "Sony - PlayStation" in output
    assert "🛑" in output
    assert "0.00 GB" in output


def test_playlist_list_does_not_create_transport():
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
        output = run_cli_tool(
            [
                "retrosync.py",
                "--playlist-list",
                "--config-file=ignored.conf",
            ]
        )

    factory_mock.assert_not_called()
    assert "Nintendo - NES" in output
