import pytest
from retrosync import TransportUnixBase, TransportSSHUnix, TransportFileSystemUnix


def test_transport_base_unix_check_executable_exists():
    transport = TransportUnixBase({}, dry_run=True)
    with pytest.raises(SystemExit):
        transport.check_executable_exists("nonexistent_executable")


def test_transport_remote_unix_check():
    transport = TransportSSHUnix({"transport": "ssh"}, dry_run=True)
    with pytest.raises(SystemExit):
        transport.check_executable_exists("nonexistent_executable")


def test_transport_local_unix_check():
    transport = TransportFileSystemUnix({"transport": "filesystem"}, dry_run=True)
    with pytest.raises(SystemExit):
        transport.check_executable_exists("nonexistent_executable")
