from time import strftime
import pytest
from pathlib import Path
from unittest.mock import patch, Mock

from retrosync import (
    Transport,
    TransportLocalUnix,
    TransportRemoteUnix,
    TransportLocalWindows,
    TransportRemoteWindows,
)


@pytest.fixture
def default_config():
    return {
        "target": "local",
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
    transport = Transport(default_config, dry_run, force_transport="unix")
    assert isinstance(transport, TransportLocalUnix)


def test_transport_unix_remote(default_config, dry_run):
    default_config["target"] = "remote"
    transport = Transport(default_config, dry_run, force_transport="unix")
    assert isinstance(transport, TransportRemoteUnix)


def test_transport_windows_local(default_config, dry_run):
    with patch("platform.system", return_value="Windows"):
        transport = Transport(default_config, dry_run, force_transport="windows")
        assert isinstance(transport, TransportLocalWindows)


def test_transport_windows_remote(default_config, dry_run):
    with patch("platform.system", return_value="Windows"):
        default_config["target"] = "remote"
        transport = Transport(default_config, dry_run, force_transport="windows")
        assert isinstance(transport, TransportRemoteWindows)


def test_transport_unix_local_copy_file(default_config, dry_run):
    transport = TransportLocalUnix(default_config, dry_run)
    src = Path("tests/assets/bios")
    dest = Path("tests/assets/bios")
    with patch("shutil.copy") as mock_copy:
        transport.copy_file(src, dest)
        mock_copy.assert_called_once_with(src, dest)


def test_transport_unix_remote_copy_file(default_config, dry_run):
    default_config["target"] = "remote"
    transport = TransportRemoteUnix(default_config, dry_run)
    src = Path("tests/assets/bios")
    dest = Path("tests/assets/bios")
    with patch.object(transport, "execute") as mock_execute:
        transport.copy_file(src, dest)
        mock_execute.assert_called_once()


def test_transport_windows_local_copy_file(default_config, dry_run):
    transport = TransportLocalWindows(default_config, dry_run)
    src = Path("tests/assets/bios")
    dest = Path("tests/assets/bios")
    with patch("shutil.copy") as mock_copy:
        transport.copy_file(src, dest)
        mock_copy.assert_called_once_with(src, dest)


def test_transport_windows_remote_connect(default_config, dry_run):
    transport = TransportRemoteWindows(default_config, dry_run)
    with (
        patch.object(transport, "connect") as mock_connect,
    ):
        transport.connect()
        mock_connect.assert_called_once()


def test_transport_windows_remote_copy_file(default_config, dry_run):
    transport = TransportRemoteWindows(default_config, dry_run)
    src = Path("tests/assets/bios")
    dest = Path("tests/assets/bios")
    transport.connected = True
    transport.sftp = Mock()
    transport.sftp.put = True

    with patch.object(transport.sftp, "put") as mock_put, patch.object(transport.sftp, "stat"):
        transport.copy_file(src, dest)
        mock_put.assert_called_once_with(str(src), str(dest))
