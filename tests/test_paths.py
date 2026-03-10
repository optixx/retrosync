from pathlib import Path

from retrosync import (
    expand_user_path,
    expand_user_path_list,
    normalize_webdav_remote_path,
    retroarch_derived_paths,
)


def test_expand_user_path_list_accepts_scalar_and_list():
    single = expand_user_path_list("~/roms")
    many = expand_user_path_list(["~/roms-a", "~/roms-b"])

    assert len(single) == 1
    assert all(item.startswith(str(Path.home())) for item in single)
    assert len(many) == 2
    assert all(item.startswith(str(Path.home())) for item in many)


def test_normalize_webdav_remote_path_maps_home_relative_paths():
    home = Path("/Users/david")
    assert (
        normalize_webdav_remote_path("/Users/david/Sync/RetroArch", home=home) == "/Sync/RetroArch"
    )
    assert normalize_webdav_remote_path("/Sync/RetroArch", home=home) == "/Sync/RetroArch"
    assert normalize_webdav_remote_path("/Users/david", home=home) == "/"


def test_retroarch_derived_paths_returns_expected_keys():
    derived = retroarch_derived_paths("/base/retroarch", prefix="src")
    assert derived["src_playlists"] == Path("/base/retroarch/playlists")
    assert derived["src_bios"] == Path("/base/retroarch/system")
    assert derived["src_config"] == Path("/base/retroarch/config")
    assert derived["src_cores"] == Path("/base/retroarch/cores")
    assert derived["src_thumbnails"] == Path("/base/retroarch/thumbnails")


def test_expand_user_path_returns_none_for_none():
    assert expand_user_path(None) is None
