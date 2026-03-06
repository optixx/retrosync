import pytest
from pathlib import Path
from retrosync import TransportSSHUnix


@pytest.fixture
def default_config():
    return {
        "hostname": "example.com",
        "username": "user",
        "password": "password",
        "transport": "ssh",
    }


@pytest.fixture
def dry_run():
    return True


def test_build_dest_unix(default_config, dry_run):
    transport = TransportSSHUnix(default_config, dry_run)
    path = Path("/some/path")
    expected = '"user@example.com:/some/path"'
    assert transport.build_dest(path) == expected
