import json

from retrosync import (
    BiosSync,
    FavoritesSync,
    PlaylistSyncJob,
    RomSyncJob,
    TransportFileSystemUnix,
)


def test_sync_jobs_create_missing_target_directories(tmp_path, mocker):
    src_root = tmp_path / "src"
    dest_root = tmp_path / "dest"

    src_bios = src_root / "system"
    src_config = src_root / "config"
    src_roms = src_root / "roms"
    src_playlists = src_root / "playlists"

    src_bios.mkdir(parents=True)
    src_config.mkdir(parents=True)
    (src_roms / "NES").mkdir(parents=True)
    src_playlists.mkdir(parents=True)

    (src_bios / "bios.bin").write_text("bios", encoding="utf-8")
    (src_config / "content_favorites.lpl").write_text('{"items": []}', encoding="utf-8")
    (src_roms / "NES" / "Super Mario Bros.zip").write_text("rom", encoding="utf-8")

    playlist_name = "Nintendo - NES.lpl"
    playlist_data = {
        "version": "1.5",
        "default_core_path": "/src/cores/fceumm_libretro.dylib",
        "default_core_name": "DETECT",
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
                "path": f"{src_roms}/NES/Super Mario Bros.zip",
                "label": "Super Mario Bros",
                "core_path": "DETECT",
                "core_name": "DETECT",
                "crc32": "DETECT",
                "db_name": playlist_name,
            }
        ],
    }
    (src_playlists / playlist_name).write_text(json.dumps(playlist_data), encoding="utf-8")

    default_config = {
        "transport": "filesystem",
        "src_bios": str(src_bios),
        "src_config": str(src_config),
        "src_roms": [str(src_roms)],
        "src_playlists": str(src_playlists),
        "dest_bios": str(dest_root / "system"),
        "dest_config": str(dest_root / "config"),
        "dest_roms": str(dest_root / "roms"),
        "dest_playlists": str(dest_root / "playlists"),
        "src_cores": "/src/cores",
        "target_cores": "/target/cores",
        "src_cores_suffix": ".dylib",
        "target_cores_suffix": ".so",
        "target_roms": "/target/roms",
    }
    playlist = {"name": playlist_name, "src_folder": "NES", "dest_folder": "NES"}

    mocker.patch.object(TransportFileSystemUnix, "check", return_value=None)
    transport = TransportFileSystemUnix(default_config, dry_run=False)
    mocker.patch.object(transport, "execute", return_value=None)

    bios_sync = BiosSync(default_config, [], transport)
    bios_sync.setup()
    bios_sync.do()
    assert (dest_root / "system").is_dir()

    favorites_sync = FavoritesSync(default_config, [], transport)
    favorites_sync.setup()
    favorites_sync.do()
    assert (dest_root / "config").is_dir()
    assert (dest_root / "config" / "content_favorites.lpl").exists()

    playlist_sync = PlaylistSyncJob(default_config, transport)
    playlist_sync.setup(playlist)
    playlist_sync.do(None)
    assert (dest_root / "playlists").is_dir()
    assert (dest_root / "playlists" / playlist_name).exists()

    rom_sync = RomSyncJob(default_config, transport)
    rom_sync.setup(playlist)
    rom_sync.do(callback=None)
    assert (dest_root / "roms" / "NES").is_dir()
