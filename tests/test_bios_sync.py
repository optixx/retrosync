import pytest
from pathlib import Path
from retrosync import BiosSync, TransportRemoteUnix


@pytest.fixture
def default_config():
    return {
        "hostname": "example.com",
        "username": "user",
        "password": "password",
        "target": "remote",
        "src_bios": "/local/bios",
        "dest_bios": "/remote/bios",
    }


@pytest.fixture
def playlists():
    return []


@pytest.fixture
def transport(default_config):
    return TransportRemoteUnix(default_config, dry_run=True)


def test_bios_sync_setup(default_config, playlists, transport):
    bios_sync = BiosSync(default_config, playlists, transport)
    bios_sync.setup()
    assert bios_sync.src == Path("/local/bios")
    assert bios_sync.dst == Path("/remote/bios")
    assert bios_sync.size == 0  # Assuming guess_file_count returns 0 for the test


def test_bios_sync_do(default_config, playlists, transport, mocker):
    bios_sync = BiosSync(default_config, playlists, transport)
    bios_sync.setup()
    mock_copy_files = mocker.patch.object(transport, "copy_files")
    bios_sync.do()
    mock_copy_files.assert_called_once_with(
        Path("/local/bios"),
        Path("/remote/bios"),
        whitelist=[],
        recursive=True,
    )
