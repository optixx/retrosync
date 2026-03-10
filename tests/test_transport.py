import pytest
from pathlib import Path
from unittest.mock import patch, Mock
import urllib.error

from retrosync import (
    TransportFactory,
    TransportFileSystemUnix,
    TransportError,
    TransportWebDAV,
    TransportSSHUnix,
    TransportFileSystemWindows,
    TransportSSHWindows,
    normalize_transport_config,
)


@pytest.fixture
def default_config():
    return {
        "transport": "filesystem",
        "hostname": "localhost",
        "username": "user",
        "password": "password",
        "src_bios": "tests/assets/bios",
        "dest_bios": "tests/assets/bios",
    }


@pytest.fixture
def dry_run():
    return False


def test_transport_unix_local(default_config, dry_run):
    transport = TransportFactory(default_config, dry_run, force_transport="unix")
    assert isinstance(transport, TransportFileSystemUnix)


def test_transport_unix_remote(default_config, dry_run):
    default_config["transport"] = "ssh"
    transport = TransportFactory(default_config, dry_run, force_transport="unix")
    assert isinstance(transport, TransportSSHUnix)


def test_transport_windows_local(default_config, dry_run):
    with patch("platform.system", return_value="Windows"):
        transport = TransportFactory(default_config, dry_run, force_transport="windows")
        assert isinstance(transport, TransportFileSystemWindows)


def test_transport_windows_remote(default_config, dry_run):
    with patch("platform.system", return_value="Windows"):
        default_config["transport"] = "ssh"
        transport = TransportFactory(default_config, dry_run, force_transport="windows")
        assert isinstance(transport, TransportSSHWindows)


def test_transport_unix_local_copy_file(default_config, dry_run):
    transport = TransportFileSystemUnix(default_config, dry_run)
    src = Path("tests/assets/bios")
    dest = Path("tests/assets/bios")
    with patch("shutil.copy") as mock_copy:
        transport.copy_file(src, dest)
        mock_copy.assert_called_once_with(src, dest)


def test_transport_unix_remote_copy_file(default_config, dry_run):
    default_config["transport"] = "ssh"
    transport = TransportSSHUnix(default_config, dry_run)
    src = Path("tests/assets/bios")
    dest = Path("tests/assets/bios")
    with patch.object(transport, "execute") as mock_execute:
        transport.copy_file(src, dest)
        mock_execute.assert_called_once()


def test_transport_windows_local_copy_file(default_config, dry_run):
    transport = TransportFileSystemWindows(default_config, dry_run)
    src = Path("tests/assets/bios")
    dest = Path("tests/assets/bios")
    with patch("shutil.copy") as mock_copy:
        transport.copy_file(src, dest)
        mock_copy.assert_called_once_with(src, dest)


def test_transport_windows_remote_connect(default_config, dry_run):
    transport = TransportSSHWindows(default_config, dry_run)
    with (
        patch.object(transport, "connect") as mock_connect,
    ):
        transport.connect()
        mock_connect.assert_called_once()


def test_transport_windows_remote_copy_file(default_config, dry_run):
    transport = TransportSSHWindows(default_config, dry_run)
    src = Path("tests/assets/bios")
    dest = Path("tests/assets/bios")
    transport.connected = True
    transport.sftp = Mock()
    transport.sftp.put = True

    with patch.object(transport.sftp, "put") as mock_put, patch.object(transport.sftp, "stat"):
        transport.copy_file(src, dest)
        mock_put.assert_called_once_with(str(src), str(dest))


def test_transport_base_excludes_known_junk_paths(default_config):
    transport = TransportFileSystemWindows(default_config, dry_run=True)
    assert transport.is_excluded_path(Path(".DS_Store"))
    assert transport.is_excluded_path(Path("__MACOSX/file.bin"))
    assert transport.is_excluded_path(Path("foo/._bar"))
    assert transport.is_excluded_path(Path(".zip"))
    assert not transport.is_excluded_path(Path("NES/Super Mario Bros.zip"))


def test_guess_file_count_skips_globally_excluded_paths(tmp_path, default_config):
    src = tmp_path / "src"
    src.mkdir()
    (src / "good.rom").write_text("ok", encoding="utf-8")
    (src / ".DS_Store").write_text("junk", encoding="utf-8")
    (src / ".zip").write_text("broken", encoding="utf-8")
    (src / "__MACOSX").mkdir()
    (src / "__MACOSX" / "meta.bin").write_text("junk", encoding="utf-8")

    transport = TransportFileSystemWindows(default_config, dry_run=True)
    assert transport.guess_file_count(src, whitelist=[], recursive=True) == 1


def test_transport_windows_local_copy_files_skips_global_excludes(tmp_path, default_config):
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "good.rom").write_text("ok", encoding="utf-8")
    (src / ".DS_Store").write_text("junk", encoding="utf-8")
    (src / ".zip").write_text("broken", encoding="utf-8")
    (src / "__MACOSX").mkdir()
    (src / "__MACOSX" / "meta.bin").write_text("junk", encoding="utf-8")

    transport = TransportFileSystemWindows(default_config, dry_run=False)
    transport.copy_files(src, dest, whitelist=[], recursive=True, callback=None)

    assert (dest / "good.rom").exists()
    assert not (dest / ".DS_Store").exists()
    assert not (dest / ".zip").exists()
    assert not (dest / "__MACOSX").exists()


def test_normalize_transport_config_includes_webdav_settings():
    config = {
        "default": {"transport": "webdav"},
        "webdav": {
            "host": "http://127.0.0.1:8080",
            "username": "alice",
            "password": "secret",
        },
    }
    default = normalize_transport_config(config)
    assert default["host"] == "http://127.0.0.1:8080"
    assert default["username"] == "alice"
    assert default["password"] == "secret"


def test_normalize_transport_config_reads_ssh_and_webdav_sections():
    config = {
        "default": {"transport": "ssh"},
        "ssh": {
            "hostname": "steamdeck",
            "username": "deck",
            "password": "secret",
        },
        "webdav": {
            "host": "https://dav.local",
            "username": "dav-user",
            "password": "dav-pass",
        },
    }
    default = normalize_transport_config(config)
    assert default["hostname"] == "steamdeck"
    assert "host" not in default
    assert default["username"] == "deck"
    assert default["password"] == "secret"


def test_normalize_transport_config_keeps_remote_fallback():
    config = {
        "default": {"transport": "webdav"},
        "remote": {
            "hostname": "legacy-host",
            "username": "legacy-user",
            "password": "legacy-pass",
        },
    }
    default = normalize_transport_config(config)
    assert default["hostname"] == "legacy-host"
    assert default["username"] == ""
    assert default["password"] == ""
    assert default["host"] == ""


def test_transport_webdav_reads_config():
    default = {
        "transport": "webdav",
        "host": "dav.local:8080",
        "username": "user",
        "password": "pass",
    }
    transport = TransportWebDAV(default, dry_run=True)
    assert transport.base_url == "http://dav.local:8080"
    assert transport.username == "user"
    assert transport.password == "pass"


def test_transport_webdav_requires_host():
    default = {
        "transport": "webdav",
        "host": "",
        "username": "",
        "password": "",
    }
    with pytest.raises(SystemExit):
        TransportWebDAV(default, dry_run=True)


def test_transport_webdav_request_401_retries_with_preemptive_basic_auth():
    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
    }
    transport = TransportWebDAV(default, dry_run=False)
    calls = []

    def fake_request_once(method, path, headers=None, **_kwargs):
        calls.append({"method": method, "path": path, "headers": dict(headers or {})})
        if len(calls) == 1:
            raise urllib.error.HTTPError(
                url=f"{transport.base_url}{path}", code=401, msg="Unauthorized", hdrs=None, fp=None
            )
        return None

    with patch.object(transport, "_request_once", side_effect=fake_request_once):
        transport._request("MKCOL", "/Sync")

    assert len(calls) == 2
    assert "Authorization" not in calls[0]["headers"]
    assert calls[1]["headers"]["Authorization"] == transport._auth_header


def test_transport_webdav_request_401_raises_clear_message_after_retry():
    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
    }
    transport = TransportWebDAV(default, dry_run=False)

    with patch.object(
        transport,
        "_request_once",
        side_effect=[
            urllib.error.HTTPError(
                url=f"{transport.base_url}/Sync", code=401, msg="Unauthorized", hdrs=None, fp=None
            ),
            urllib.error.HTTPError(
                url=f"{transport.base_url}/Sync", code=401, msg="Unauthorized", hdrs=None, fp=None
            ),
        ],
    ):
        with pytest.raises(RuntimeError, match="HTTP 401 \\(Unauthorized\\)"):
            transport._request("MKCOL", "/Sync")


def test_transport_webdav_request_401_without_credentials_raises_clear_message():
    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "",
        "password": "",
    }
    transport = TransportWebDAV(default, dry_run=False)

    with patch.object(
        transport,
        "_request_once",
        side_effect=urllib.error.HTTPError(
            url=f"{transport.base_url}/Sync", code=401, msg="Unauthorized", hdrs=None, fp=None
        ),
    ):
        with pytest.raises(RuntimeError, match="HTTP 401 \\(Unauthorized\\)"):
            transport._request("MKCOL", "/Sync")


def test_transport_webdav_request_network_errors_map_to_transport_error():
    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
    }
    transport = TransportWebDAV(default, dry_run=False)

    with patch.object(transport, "_request_once", side_effect=urllib.error.URLError("offline")):
        with pytest.raises(TransportError, match="target may be offline or unreachable"):
            transport._request("PUT", "/Sync/file.bin", body=b"test")


def test_transport_webdav_ensure_dir_exists_uses_cache_after_first_create():
    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
    }
    transport = TransportWebDAV(default, dry_run=False)

    with (
        patch.object(transport, "_path_exists", return_value=False),
        patch.object(transport, "_mkcol") as mock_mkcol,
    ):
        transport.ensure_dir_exists(Path("/Sync/RetroArch/system"))
        transport.ensure_dir_exists(Path("/Sync/RetroArch/system"))

    # First call creates each segment once, second call is cache-only.
    assert mock_mkcol.call_count == 3


def test_transport_webdav_ensure_dir_exists_skips_mkcol_when_server_path_exists():
    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
    }
    transport = TransportWebDAV(default, dry_run=False)

    with (
        patch.object(transport, "_path_exists", return_value=True),
        patch.object(transport, "_mkcol") as mock_mkcol,
    ):
        transport.ensure_dir_exists(Path("/Sync/RetroArch"))

    mock_mkcol.assert_not_called()
    assert "/Sync" in transport._known_dirs
    assert "/Sync/RetroArch" in transport._known_dirs


def test_transport_webdav_mkcol_ignores_failure_if_path_already_exists():
    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
    }
    transport = TransportWebDAV(default, dry_run=False)

    with (
        patch.object(transport, "_request", side_effect=RuntimeError("failed mkcol")),
        patch.object(transport, "_path_exists", return_value=True),
    ):
        transport._mkcol("/Sync/RetroArch")


def test_transport_webdav_mkcol_reraises_failure_when_path_missing():
    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
    }
    transport = TransportWebDAV(default, dry_run=False)

    with (
        patch.object(transport, "_request", side_effect=RuntimeError("failed mkcol")),
        patch.object(transport, "_path_exists", return_value=False),
    ):
        with pytest.raises(RuntimeError, match="failed mkcol"):
            transport._mkcol("/Sync/RetroArch")


def test_transport_webdav_parallel_copy_calls_callback_for_each_file(tmp_path):
    src = tmp_path / "src"
    dst = Path("/Sync")
    src.mkdir()
    (src / "a.bin").write_bytes(b"a")
    (src / "b.bin").write_bytes(b"b")

    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
        "webdav_max_workers": "2",
    }
    transport = TransportWebDAV(default, dry_run=False)
    callback = Mock()

    with (
        patch.object(transport, "ensure_dir_exists"),
        patch.object(transport, "copy_file") as mock_copy_file,
    ):
        transport.copy_files(src, dst, whitelist=[], recursive=False, callback=callback)

    assert mock_copy_file.call_count == 2
    assert callback.call_count == 2


def test_transport_webdav_parallel_copy_keyboard_interrupt_maps_to_transport_error(tmp_path):
    src = tmp_path / "src"
    dst = Path("/Sync")
    src.mkdir()
    (src / "a.bin").write_bytes(b"a")
    (src / "b.bin").write_bytes(b"b")

    default = {
        "transport": "webdav",
        "host": "http://dav.local",
        "username": "user",
        "password": "pass",
        "webdav_max_workers": "2",
    }
    transport = TransportWebDAV(default, dry_run=False)

    class DummyFuture:
        def __init__(self, should_interrupt=False):
            self.cancelled = False
            self._should_interrupt = should_interrupt

        def cancel(self):
            self.cancelled = True
            return True

        def result(self):
            if self._should_interrupt:
                raise KeyboardInterrupt()
            return None

    class DummyExecutor:
        def __init__(self):
            self.futures = []
            self.shutdown_calls = []

        def submit(self, fn, src_filename, dest_filename):
            should_interrupt = len(self.futures) == 0
            future = DummyFuture(should_interrupt=should_interrupt)
            self.futures.append(future)
            return future

        def shutdown(self, wait, cancel_futures):
            self.shutdown_calls.append((wait, cancel_futures))

    dummy_executor = DummyExecutor()

    with (
        patch.object(transport, "ensure_dir_exists"),
        patch("retrosync.concurrent.futures.ThreadPoolExecutor", return_value=dummy_executor),
        patch("retrosync.concurrent.futures.as_completed", side_effect=lambda d: list(d.keys())),
    ):
        with pytest.raises(TransportError, match="Transfer interrupted by user"):
            transport.copy_files(src, dst, whitelist=[], recursive=False, callback=None)

    assert all(f.cancelled for f in dummy_executor.futures)
    assert dummy_executor.shutdown_calls == [(False, True)]
