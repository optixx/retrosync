import pytest
from pathlib import Path
from unittest.mock import patch, Mock

from retrosync import (
    TransportFactory,
    TransportFileSystemUnix,
    TransportSSHUnix,
    TransportFileSystemWindows,
    TransportSSHWindows,
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
