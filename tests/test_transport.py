import pytest
from retrosync import TransportBaseUnix, TransportRemoteUnix, TransportLocalUnix


def test_transport_base_unix_check_executable_exists():
    transport = TransportBaseUnix({}, dry_run=True)
    with pytest.raises(SystemExit):
        transport.check_executable_exists("nonexistent_executable")


def test_transport_remote_unix_check():
    transport = TransportRemoteUnix({}, dry_run=True)
    with pytest.raises(SystemExit):
        transport.check_executable_exists("nonexistent_executable")


def test_transport_local_unix_check():
    transport = TransportLocalUnix({}, dry_run=True)
    with pytest.raises(SystemExit):
        transport.check_executable_exists("nonexistent_executable")
