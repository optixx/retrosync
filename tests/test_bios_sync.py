import pytest
from pathlib import Path
from retrosync import BiosSync, TransportRemoteUnix


@pytest.fixture
def default_config():
    return {
        "hostname": "example.com",
        "username": "user",
        "password": "password",
        "src_playlists": "tests/assets/playlists",
        "src_bios": "tests/assets/bios",
        "src_config": "tests/assets/config",
        "src_roms": "tests/assets/roms",
        "src_cores": "",
        "src_thumbnails": "",
        "src_cores_suffix": ".dylib",
        "dest_playlists": "",
        "dest_bios": "tests/assets/bios",
        "dest_config": "tests/assets/config",
        "dest_roms": "",
        "dest_cores": "",
        "dest_thumbnails": "",
        "dest_cores_suffix": ".so",
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
    assert bios_sync.src == Path("tests/assets/bios")
    assert bios_sync.dst == Path("tests/assets/bios")
    assert bios_sync.size == 1


def test_bios_sync_do(default_config, playlists, transport, mocker):
    bios_sync = BiosSync(default_config, playlists, transport)
    bios_sync.setup()
    mock_copy_files = mocker.patch.object(transport, "copy_files")
    bios_sync.do()
    mock_copy_files.assert_called_once_with(
        Path("tests/assets/bios"),
        Path("tests/assets/bios"),
        whitelist=[],
        recursive=True,
    )
