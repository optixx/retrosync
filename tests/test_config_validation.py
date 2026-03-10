import pytest

from retrosync import validate_runtime_config


def _base_default():
    return {
        "transport": "filesystem",
        "src_roms": ["/roms"],
        "dest_roms": "/dest/roms",
        "src_playlists": "/playlists",
        "dest_playlists": "/dest/playlists",
        "src_bios": "/bios",
        "dest_bios": "/dest/bios",
        "src_config": "/config",
        "dest_config": "/dest/config",
        "src_thumbnails": "/thumbs",
        "dest_thumbnails": "/dest/thumbs",
        "src_cores": "/cores",
        "target_cores": "/target/cores",
        "src_cores_suffix": ".dylib",
        "target_cores_suffix": ".so",
        "target_roms": "/target/roms",
    }


def _base_playlists():
    return [
        {
            "name": "System.lpl",
            "src_folder": "",
            "dest_folder": "",
            "src_core_path": "core_libretro",
            "src_core_name": "Core Name",
        }
    ]


def test_validate_runtime_config_requires_webdav_host():
    default = _base_default()
    default["transport"] = "webdav"
    default["host"] = ""

    with pytest.raises(ValueError, match="'host' is required for WebDAV transport"):
        validate_runtime_config(
            default,
            _base_playlists(),
            do_sync_playlists=False,
            do_sync_bios=False,
            do_sync_favorites=False,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_update_playlists=False,
        )


def test_validate_runtime_config_requires_sync_bios_paths():
    default = _base_default()
    default["dest_bios"] = ""

    with pytest.raises(ValueError, match="'dest_bios' is required for --sync-bios"):
        validate_runtime_config(
            default,
            _base_playlists(),
            do_sync_playlists=False,
            do_sync_bios=True,
            do_sync_favorites=False,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_update_playlists=False,
        )


def test_validate_runtime_config_requires_playlist_core_fields_for_update():
    default = _base_default()
    playlists = _base_playlists()
    playlists[0]["src_core_name"] = ""

    with pytest.raises(
        ValueError, match="'src_core_name' must not be empty for --update-playlists"
    ):
        validate_runtime_config(
            default,
            playlists,
            do_sync_playlists=False,
            do_sync_bios=False,
            do_sync_favorites=False,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_update_playlists=True,
        )


def test_validate_runtime_config_allows_sync_roms_with_empty_folder_names():
    default = _base_default()
    playlists = _base_playlists()

    validate_runtime_config(
        default,
        playlists,
        do_sync_playlists=False,
        do_sync_bios=False,
        do_sync_favorites=False,
        do_sync_thumbnails=False,
        do_sync_roms=True,
        do_update_playlists=False,
    )
