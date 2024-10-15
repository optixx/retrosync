import pytest
from pathlib import Path
from retrosync import ThumbnailsSync, TransportRemoteUnix


@pytest.fixture
def default_config():
    return {
        "hostname": "example.com",
        "username": "user",
        "password": "password",
        "target": "remote",
        "src_thumbnails": "/local/thumbnails",
        "dest_thumbnails": "/remote/thumbnails",
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
    return True


def test_thumbnails_sync_setup(default_config, playlists, dry_run):
    transport = TransportRemoteUnix(default_config, dry_run)
    thumbnails_sync = ThumbnailsSync(default_config, playlists, transport)
    thumbnails_sync.setup()
    assert thumbnails_sync.src == Path("/local/thumbnails")
    assert thumbnails_sync.dst == Path("/remote/thumbnails")
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
