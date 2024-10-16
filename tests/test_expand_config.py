from pathlib import Path
from retrosync import expand_config


def test_expand_config():
    default = {
        "src_playlists": "~/playlists",
        "src_bios": "~/bios",
        "src_config": "~/config",
        "src_roms": "~/roms",
        "src_cores": "~/cores",
        "src_thumbnails": "~/thumbnails",
        "dest_playlists": "~/dest_playlists",
        "dest_bios": "~/dest_bios",
        "dest_config": "~/dest_config",
        "dest_roms": "~/dest_roms",
        "dest_thumbnails": "~/dest_thumbnails",
    }

    expanded = expand_config(default)

    for key, value in default.items():
        assert expanded[key] == str(Path(value).expanduser())
