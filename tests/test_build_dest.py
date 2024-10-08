import pytest
from pathlib import Path
from retrosync import TransportRemoteUnix


@pytest.fixture
def default_config():
    return {
        "hostname": "example.com",
        "username": "user",
        "password": "password",
        "target": "remote",
    }


@pytest.fixture
def dry_run():
    return True


def test_build_dest_unix(default_config, dry_run):
    transport = TransportRemoteUnix(default_config, dry_run)
    path = Path("/some/path")
    expected = '"user@example.com:/some/path"'
    assert transport.build_dest(path) == expected
