import pytest
from unittest.mock import Mock
from retrosync import ThumbnailsSync, TransportSSHUnix


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


def test_thumbnails_sync_setup(tmp_path, default_config, dry_run):
    system_dir = tmp_path / "Nintendo - Entertainment System" / "Named_Boxarts"
    system_dir.mkdir(parents=True)
    (system_dir / "Mario.png").write_bytes(b"img")
    default_config["src_thumbnails"] = str(tmp_path)
    default_config["dest_thumbnails"] = str(tmp_path / "dest")
    transport = TransportSSHUnix(default_config, dry_run)
    thumbnails_sync = ThumbnailsSync(default_config, transport)
    thumbnails_sync.setup({"name": "Nintendo - Entertainment System.lpl"})
    assert thumbnails_sync.src == tmp_path / "Nintendo - Entertainment System"
    assert thumbnails_sync.dst == tmp_path / "dest" / "Nintendo - Entertainment System"
    assert thumbnails_sync.size == transport.guess_file_count(thumbnails_sync.src, [], True)


def test_thumbnails_sync_do(tmp_path, default_config, dry_run, mocker):
    system_dir = tmp_path / "Nintendo - Entertainment System" / "Named_Boxarts"
    system_dir.mkdir(parents=True)
    (system_dir / "Mario.png").write_bytes(b"img")
    default_config["src_thumbnails"] = str(tmp_path)
    default_config["dest_thumbnails"] = str(tmp_path / "dest")
    transport = TransportSSHUnix(default_config, dry_run)
    thumbnails_sync = ThumbnailsSync(default_config, transport)
    thumbnails_sync.setup({"name": "Nintendo - Entertainment System.lpl"})

    mock_copy_files = mocker.patch.object(transport, "copy_files")
    thumbnails_sync.do()

    mock_copy_files.assert_called_once_with(
        thumbnails_sync.src,
        thumbnails_sync.dst,
        whitelist=[],
        recursive=True,
        callback=None,
    )


def test_thumbnails_sync_do_forwards_callback(tmp_path, default_config, dry_run, mocker):
    system_dir = tmp_path / "Nintendo - Entertainment System" / "Named_Boxarts"
    system_dir.mkdir(parents=True)
    (system_dir / "Mario.png").write_bytes(b"img")
    default_config["src_thumbnails"] = str(tmp_path)
    default_config["dest_thumbnails"] = str(tmp_path / "dest")
    transport = TransportSSHUnix(default_config, dry_run)
    thumbnails_sync = ThumbnailsSync(default_config, transport)
    thumbnails_sync.setup({"name": "Nintendo - Entertainment System.lpl"})
    callback = Mock()

    mock_copy_files = mocker.patch.object(transport, "copy_files")
    thumbnails_sync.do(callback=callback)

    mock_copy_files.assert_called_once_with(
        thumbnails_sync.src,
        thumbnails_sync.dst,
        whitelist=[],
        recursive=True,
        callback=callback,
    )


def test_thumbnails_sync_missing_system_is_noop(tmp_path, default_config, dry_run, mocker):
    default_config["src_thumbnails"] = str(tmp_path)
    default_config["dest_thumbnails"] = str(tmp_path / "dest")
    transport = TransportSSHUnix(default_config, dry_run)
    thumbnails_sync = ThumbnailsSync(default_config, transport)
    thumbnails_sync.setup({"name": "Missing System.lpl"})

    mock_copy_files = mocker.patch.object(transport, "copy_files")
    thumbnails_sync.do()

    assert thumbnails_sync.size == 0
    assert thumbnails_sync.transfer_bytes == 0
    mock_copy_files.assert_not_called()
