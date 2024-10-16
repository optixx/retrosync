import pytest
from pathlib import Path
from retrosync import ThumbnailsSync, TransportRemoteUnix


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
        "src_thumbnails": "tests/assets/thumbnails",
        "src_cores_suffix": ".dylib",
        "dest_playlists": "",
        "dest_bios": "tests/assets/bios",
        "dest_config": "tests/assets/config",
        "dest_roms": "",
        "dest_cores": "",
        "dest_thumbnails": "tests/assets/thumbnails",
        "dest_cores_suffix": ".so",
    }


@pytest.fixture
def playlists():
    return [
        {
            "name": "Playlist 1",
            "src_folder": "folder1",
            "dest_folder": "folder1",
        },
        {
            "name": "Playlist 2",
            "src_folder": "folder2",
            "dest_folder": "folder2",
        },
    ]


@pytest.fixture
def dry_run():
    return False


def test_thumbnails_sync_setup(default_config, playlists, dry_run):
    transport = TransportRemoteUnix(default_config, dry_run)
    thumbnails_sync = ThumbnailsSync(default_config, playlists, transport)
    thumbnails_sync.setup()
    assert thumbnails_sync.src == Path("tests/assets/thumbnails")
    assert thumbnails_sync.dst == Path("tests/assets/thumbnails")
    assert thumbnails_sync.size == transport.guess_file_count(thumbnails_sync.src, [], True)


def test_thumbnails_sync_do(default_config, playlists, dry_run, mocker):
    transport = TransportRemoteUnix(default_config, dry_run)
    thumbnails_sync = ThumbnailsSync(default_config, playlists, transport)
    thumbnails_sync.setup()

    mock_copy_files = mocker.patch.object(transport, "copy_files")
    thumbnails_sync.do()

    mock_copy_files.assert_called_once_with(
        thumbnails_sync.src,
        thumbnails_sync.dst,
        whitelist=[],
        recursive=True,
    )
