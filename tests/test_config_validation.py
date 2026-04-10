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


def test_validate_runtime_config_requires_webdav_url():
    default = _base_default()
    default["transport"] = "webdav"
    default["url"] = ""

    with pytest.raises(ValueError, match="'url' is required for WebDAV transport"):
        validate_runtime_config(
            default,
            _base_playlists(),
            [],
            do_sync_playlists=False,
            do_sync_bios=False,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_sync_shaders=False,
            do_update_playlists=False,
            do_update_thumbnails=False,
        )


def test_validate_runtime_config_requires_sync_bios_paths():
    default = _base_default()
    default["dest_bios"] = ""

    with pytest.raises(ValueError, match="'dest_bios' is required for --sync-bios"):
        validate_runtime_config(
            default,
            _base_playlists(),
            [],
            do_sync_playlists=False,
            do_sync_bios=True,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_sync_shaders=False,
            do_update_playlists=False,
            do_update_thumbnails=False,
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
            [],
            do_sync_playlists=False,
            do_sync_bios=False,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_sync_shaders=False,
            do_update_playlists=True,
            do_update_thumbnails=False,
        )


def test_validate_runtime_config_allows_sync_roms_with_empty_folder_names():
    default = _base_default()
    playlists = _base_playlists()

    validate_runtime_config(
        default,
        playlists,
        [],
        do_sync_playlists=False,
        do_sync_bios=False,
        do_sync_thumbnails=False,
        do_sync_roms=True,
        do_sync_shaders=False,
        do_update_playlists=False,
        do_update_thumbnails=False,
    )


def test_validate_runtime_config_requires_playlist_source_for_update_thumbnails():
    default = _base_default()
    default["src_playlists"] = ""

    with pytest.raises(ValueError, match="'src_playlists' is required for --update-thumbnails"):
        validate_runtime_config(
            default,
            _base_playlists(),
            [],
            do_sync_playlists=False,
            do_sync_bios=False,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_sync_shaders=False,
            do_update_playlists=False,
            do_update_thumbnails=True,
        )


def test_validate_runtime_config_requires_shaders_for_sync_shaders():
    default = _base_default()
    default["dest_retroarch_base"] = "/dest/retroarch"

    with pytest.raises(
        ValueError, match=r"\[shaders\] at least one shader is required for --sync-shaders"
    ):
        validate_runtime_config(
            default,
            _base_playlists(),
            [],
            do_sync_playlists=False,
            do_sync_bios=False,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_sync_shaders=True,
            do_update_playlists=False,
            do_update_thumbnails=False,
        )


def test_validate_runtime_config_requires_shader_field_for_sync_shaders():
    default = _base_default()
    default["dest_retroarch_base"] = "/dest/retroarch"

    with pytest.raises(ValueError, match=r"\[shaders\]\[1\] Field required"):
        validate_runtime_config(
            default,
            _base_playlists(),
            [{"name": "Snes9x"}],
            do_sync_playlists=False,
            do_sync_bios=False,
            do_sync_thumbnails=False,
            do_sync_roms=False,
            do_sync_shaders=True,
            do_update_playlists=False,
            do_update_thumbnails=False,
        )
