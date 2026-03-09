import json
import tempfile

from retrosync import PlaylistSyncJob


def test_playlist_migrate_rewrites_paths_from_all_src_rom_roots(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    playlist_name = "Nintendo - Test.lpl"

    playlist_data = {
        "version": "1.5",
        "default_core_path": "/src/cores/fceumm_libretro.dylib",
        "default_core_name": "Nintendo - NES / Famicom (FCEUmm)",
        "label_display_mode": 0,
        "right_thumbnail_mode": 0,
        "left_thumbnail_mode": 0,
        "sort_mode": 0,
        "scan_content_dir": "",
        "scan_file_exts": "",
        "scan_dat_file_path": "",
        "scan_search_recursively": True,
        "scan_search_archives": True,
        "scan_filter_dat_content": False,
        "scan_overwrite_playlist": False,
        "items": [
            {
                "path": "/roms-primary/NES/Super Mario Bros.zip",
                "label": "Super Mario Bros",
                "core_path": "DETECT",
                "core_name": "DETECT",
                "crc32": "|crc",
                "db_name": playlist_name,
            },
            {
                "path": "/roms-alt/NES/Contra.zip",
                "label": "Contra",
                "core_path": "DETECT",
                "core_name": "DETECT",
                "crc32": "|crc",
                "db_name": playlist_name,
            },
        ],
    }

    (src_playlists / playlist_name).write_text(json.dumps(playlist_data), encoding="utf-8")

    default_config = {
        "src_playlists": str(src_playlists),
        "src_roms": ["/roms-primary", "/roms-alt"],
        "target_roms": "/target/roms",
        "src_cores": "/src/cores",
        "target_cores": "/target/cores",
        "src_cores_suffix": ".dylib",
        "target_cores_suffix": ".so",
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "NES",
        "dest_folder": "NES",
    }

    job = PlaylistSyncJob(default_config, transport=None)
    job.setup(playlist)

    with tempfile.NamedTemporaryFile() as temp_file:
        job.migrate_playlist(temp_file)
        temp_file.seek(0)
        migrated = json.loads(temp_file.read().decode("utf-8"))

    assert migrated["default_core_path"] == "/target/cores/fceumm_libretro.so"
    assert migrated["scan_content_dir"] == "/target/roms/NES"
    assert migrated["items"][0]["path"] == "/target/roms/NES/Super Mario Bros.zip"
    assert migrated["items"][1]["path"] == "/target/roms/NES/Contra.zip"
    assert migrated["items"][0]["core_name"] == "DETECT"
    assert migrated["items"][1]["core_path"] == "DETECT"
