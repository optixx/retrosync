import pytest
from pathlib import Path
from retrosync import RomSyncJob, TransportRemoteUnix


@pytest.fixture
def default_config():
    return {
        "hostname": "example.com",
        "username": "user",
        "password": "password",
        "src_playlists": "tests/assets/playlists",
        "src_roms": "tests/assets/roms",
        "src_config": "tests/assets/config",
        "src_bios": "tests/assets/bios",
        "src_cores": "",
        "src_thumbnails": "",
        "src_cores_suffix": ".dylib",
        "dest_playlists": "",
        "dest_roms": "tests/assets/roms",
        "dest_config": "tests/assets/config",
        "dest_bios": "",
        "dest_cores": "",
        "dest_thumbnails": "",
        "dest_cores_suffix": ".so",
    }


@pytest.fixture
def playlist():
    return {"src_folder": "", "dest_folder": ""}


@pytest.fixture
def transport(default_config):
    return TransportRemoteUnix(default_config, dry_run=True)


def test_roms_sync_setup(default_config, playlist, transport):
    roms_sync = RomSyncJob(default_config, transport)
    roms_sync.setup(playlist)
    assert roms_sync.src == Path("tests/assets/roms")
    assert roms_sync.dst == Path("tests/assets/roms")
    assert roms_sync.size > 1


def test_roms_sync_do(default_config, playlist, transport, mocker):
    roms_sync = RomSyncJob(default_config, transport)
    roms_sync.setup(playlist)
    mock_copy_files = mocker.patch.object(transport, "copy_files")
    roms_sync.do(callback=None)
    mock_copy_files.assert_called_once_with(
        Path("tests/assets/roms"),
        Path("tests/assets/roms"),
        whitelist=[],
        recursive=True,
        callback=None,
    )
