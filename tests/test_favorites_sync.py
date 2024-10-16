import pytest
from pathlib import Path
from retrosync import FavoritesSync, TransportLocalUnix


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
        "dest_bios": "",
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
    return TransportLocalUnix(default_config, dry_run=True)


def test_favorites_sync_setup(default_config, playlists, transport):
    favorites_sync = FavoritesSync(default_config, playlists, transport)
    favorites_sync.setup()
    assert favorites_sync.src == Path("tests/assets/config/content_favorites.lpl")
    assert favorites_sync.dst == Path("tests/assets/config/content_favorites.lpl")


def test_favorites_sync_do(default_config, playlists, transport, mocker):
    favorites_sync = FavoritesSync(default_config, playlists, transport)
    favorites_sync.setup()
    mock_copy_file = mocker.patch.object(transport, "copy_file")
    favorites_sync.do()
    mock_copy_file.assert_called_once()


def test_favorites_sync_migrate(default_config, playlists, transport, mocker):
    favorites_sync = FavoritesSync(default_config, playlists, transport)
    mock_open = mocker.mock_open(read_data='{"items": []}')
    mocker.patch("builtins.open", mock_open)
    mock_tempfile = mocker.patch("tempfile.NamedTemporaryFile", mocker.mock_open())
    favorites_sync.migrate(Path("tests/assets/config/content_favorites.lpl"), mock_tempfile())
    mock_open.assert_called_once_with(Path("tests/assets/config/content_favorites.lpl"))
    mock_tempfile().write.assert_called_once_with(b'{"items": []}')
    mock_tempfile().flush.assert_called_once()
    mock_tempfile().seek.assert_called_once_with(0)
