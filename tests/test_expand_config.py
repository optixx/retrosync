from pathlib import Path
from retrosync import expand_config


def test_expand_config():
    default = {
        "src_playlists": "~/playlists",
        "src_bios": "~/bios",
        "src_config": "~/config",
        "src_roms": ["~/roms"],
        "src_cores": "~/cores",
        "src_thumbnails": "~/thumbnails",
        "dest_playlists": "~/dest_playlists",
        "dest_bios": "~/dest_bios",
        "dest_config": "~/dest_config",
        "dest_roms": "~/dest_roms",
        "dest_thumbnails": "~/dest_thumbnails",
    }

    expanded = expand_config(default)

    assert expanded["src_roms"] == [str(Path("~/roms").expanduser())]
    for key, value in default.items():
        if key == "src_roms":
            continue
        assert expanded[key] == str(Path(value).expanduser())


def test_expand_config_derives_paths_from_retroarch_bases():
    default = {
        "src_retroarch_base": "~/Library/Application Support/RetroArch",
        "dest_retroarch_base": "/target/retroarch",
        "src_roms": ["~/roms"],
    }

    expanded = expand_config(default)

    src_base = Path("~/Library/Application Support/RetroArch").expanduser()
    dest_base = Path("/target/retroarch")
    assert expanded["src_playlists"] == str(src_base / "playlists")
    assert expanded["src_bios"] == str(src_base / "system")
    assert expanded["src_config"] == str(src_base / "config")
    assert expanded["src_cores"] == str(src_base / "cores")
    assert expanded["src_thumbnails"] == str(src_base / "thumbnails")
    assert expanded["dest_playlists"] == str(dest_base / "playlists")
    assert expanded["dest_bios"] == str(dest_base / "system")
    assert expanded["dest_config"] == str(dest_base / "config")
    assert expanded["dest_cores"] == str(dest_base / "cores")
    assert expanded["dest_thumbnails"] == str(dest_base / "thumbnails")


def test_expand_config_keeps_explicit_overrides_with_retroarch_bases():
    default = {
        "src_retroarch_base": "~/retroarch-src",
        "dest_retroarch_base": "/retroarch-dest",
        "src_roms": ["~/roms"],
        "dest_bios": "/custom/bios",
        "src_config": "~/custom-config",
    }

    expanded = expand_config(default)

    assert expanded["dest_bios"] == "/custom/bios"
    assert expanded["src_config"] == str(Path("~/custom-config").expanduser())
    assert expanded["dest_playlists"] == str(Path("/retroarch-dest/playlists"))


def test_expand_config_sets_core_suffix_from_flavor():
    default = {
        "src_roms": ["~/roms"],
        "src_flavor": "apple",
        "target_flavor": "linux",
    }

    expanded = expand_config(default)

    assert expanded["src_cores_suffix"] == ".dylib"
    assert expanded["target_cores_suffix"] == ".so"
