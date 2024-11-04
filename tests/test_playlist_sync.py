# FILEPATH: test_playlist_sync_job.py

import pytest
import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from retrosync import PlaylistSyncJob, TransportLocalUnix as Transport


@pytest.fixture
def default_config():
    return {
        "hostname": "example.com",
        "username": "user",
        "password": "password",
        "src_playlists": "tests/assets/playlists",
        "src_roms": "tests/assets/src",
        "src_config": "tests/assets/config",
        "src_bios": "tests/assets/bios",
        "src_cores": "tests/assets/cores",
        "src_thumbnails": "",
        "src_cores_suffix": ".dylib",
        "dest_playlists": "tests/assets/playlists",
        "dest_roms": "tests/assets/dst",
        "dest_config": "tests/assets/config",
        "dest_bios": "",
        "dest_cores": "tests/assets/cores",
        "dest_thumbnails": "",
        "target_cores_suffix": ".so",
        "target_cores": "test/assets/cores",
        "target_roms": "",
    }


@pytest.fixture
def playlist():
    return {
        "name": "test_playlist.lpl",
        "src_folder": "psx",
        "dest_folder": "psx",
    }


@pytest.fixture
def transport():
    transport = MagicMock(spec=Transport)
    transport.dry_run = False
    return transport


def test_playlist_sync_job_setup(default_config, playlist, transport):
    job = PlaylistSyncJob(default_config, transport)
    job.setup(playlist)
    assert job.playlist == playlist
    assert job.size == 1


@patch("retrosync.tempfile.NamedTemporaryFile")
@patch("retrosync.open")
def test_playlist_sync_job_do(
    mock_open, mock_tempfile, default_config, playlist, transport, mocker
):
    job = PlaylistSyncJob(default_config, transport)
    job.setup(playlist)

    # Mock the temporary file
    mock_temp_file = MagicMock()
    mock_tempfile.return_value.__enter__.return_value = mock_temp_file

    # Mock the playlist file content
    playlist_content = {
        "default_core_path": "test/assets/cores",
        "items": [
            {
                "path": "tests/assets/psx/rom1.zip",
            },
        ],
    }
    mock_open.return_value.__enter__.return_value.read.return_value = json.dumps(playlist_content)

    mocker.patch.object(transport, "copy_file")
    job.do(None)

    # Check if the temporary file was written with the correct content
    expected_content = {
        "default_core_path": "test/assets/cores",
        "items": [
            {
                "path": "tests/assets/psx/rom1.zip",
                "core_name": "DETECT",
                "core_path": "DETECT",
            },
        ],
        "scan_content_dir": "psx",
        "scan_dat_file_path": "",
    }
    mock_temp_file.write.assert_called_once_with(json.dumps(expected_content).encode("utf-8"))

    # Check if the transport copied the file
    transport.copy_file.assert_called_once_with(
        Path(mock_temp_file.name), Path(default_config["dest_playlists"]) / playlist["name"]
    )
