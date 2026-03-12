import json
import tempfile
from unittest.mock import Mock

from retrosync import PlaylistSyncJob, PlaylistUpdatecJob


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


def test_playlist_sync_do_calls_callback_once(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    playlist_name = "Nintendo - Test.lpl"
    (src_playlists / playlist_name).write_text(
        json.dumps({"default_core_path": "", "items": []}),
        encoding="utf-8",
    )

    transport = Mock()
    default_config = {
        "src_playlists": str(src_playlists),
        "dest_playlists": "/dest/playlists",
    }
    playlist = {"name": playlist_name}

    job = PlaylistSyncJob(default_config, transport=transport)
    job.setup(playlist)
    callback = Mock()

    # Avoid depending on migration internals here; this test asserts callback behavior.
    job.migrate_playlist = Mock()
    job.do(callback=callback)

    callback.assert_called_once()


def test_playlist_update_do_calls_callback_once(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_roms = tmp_path / "roms"
    src_roms.mkdir()
    (src_roms / "game.zip").write_text("rom", encoding="utf-8")

    playlist_name = "Nintendo - Test.lpl"
    (src_playlists / playlist_name).write_text(
        json.dumps(
            {
                "default_core_path": "",
                "default_core_name": "",
                "scan_content_dir": "",
                "scan_dat_file_path": "",
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    transport = Mock()
    transport.dry_run = True
    default_config = {
        "src_playlists": str(src_playlists),
        "src_roms": [str(src_roms)],
        "src_cores": "/cores",
        "src_cores_suffix": ".so",
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "",
        "src_core_path": "test_core",
        "src_core_name": "Test Core",
    }

    job = PlaylistUpdatecJob(default_config, transport=transport)
    job.setup(playlist)
    callback = Mock()
    job.do(callback=callback)

    callback.assert_called_once()


def test_playlist_update_prefers_thumbnail_label_match(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_roms = tmp_path / "roms"
    src_roms.mkdir()
    (src_roms / "Super Mario Bros (USA).zip").write_text("rom", encoding="utf-8")

    src_thumbnails = tmp_path / "thumbnails"
    (src_thumbnails / "Nintendo - Test" / "Named_Boxarts").mkdir(parents=True)
    (src_thumbnails / "Nintendo - Test" / "Named_Boxarts" / "Super Mario Bros.png").write_text(
        "img", encoding="utf-8"
    )

    playlist_name = "Nintendo - Test.lpl"
    playlist_file = src_playlists / playlist_name
    playlist_file.write_text(
        json.dumps(
            {
                "default_core_path": "",
                "default_core_name": "",
                "scan_content_dir": "",
                "scan_dat_file_path": "",
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    transport = Mock()
    transport.dry_run = False
    default_config = {
        "src_playlists": str(src_playlists),
        "src_roms": [str(src_roms)],
        "src_thumbnails": str(src_thumbnails),
        "src_cores": "/cores",
        "src_cores_suffix": ".so",
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "",
        "src_core_path": "test_core",
        "src_core_name": "Test Core",
    }

    job = PlaylistUpdatecJob(default_config, transport=transport)
    job.setup(playlist)
    job.do()

    updated = json.loads(playlist_file.read_text(encoding="utf-8"))
    assert updated["items"][0]["path"].endswith("Super Mario Bros (USA).zip")
    assert updated["items"][0]["label"] == "Super Mario Bros"


def test_playlist_update_can_keep_default_label(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_roms = tmp_path / "roms"
    src_roms.mkdir()
    (src_roms / "Contra (USA).zip").write_text("rom", encoding="utf-8")

    src_thumbnails = tmp_path / "thumbnails"
    (src_thumbnails / "Nintendo - Test" / "Named_Boxarts").mkdir(parents=True)
    (src_thumbnails / "Nintendo - Test" / "Named_Boxarts" / "Contra.png").write_text(
        "img", encoding="utf-8"
    )

    playlist_name = "Nintendo - Test.lpl"
    playlist_file = src_playlists / playlist_name
    playlist_file.write_text(
        json.dumps(
            {
                "default_core_path": "",
                "default_core_name": "",
                "scan_content_dir": "",
                "scan_dat_file_path": "",
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    transport = Mock()
    transport.dry_run = False
    default_config = {
        "src_playlists": str(src_playlists),
        "src_roms": [str(src_roms)],
        "src_thumbnails": str(src_thumbnails),
        "thumbnail_label_mode": "keep-label",
        "src_cores": "/cores",
        "src_cores_suffix": ".so",
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "",
        "src_core_path": "test_core",
        "src_core_name": "Test Core",
    }

    job = PlaylistUpdatecJob(default_config, transport=transport)
    job.setup(playlist)
    job.do()

    updated = json.loads(playlist_file.read_text(encoding="utf-8"))
    assert updated["items"][0]["label"] == "Contra (USA)"


def test_playlist_update_relaxed_thumbnail_match_for_variant_date_and_proto(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_roms = tmp_path / "roms"
    src_roms.mkdir()
    (src_roms / "Alien Vs Predator (Prototype) (1993) [!].lnx").write_text("rom", encoding="utf-8")

    src_thumbnails = tmp_path / "thumbnails"
    (src_thumbnails / "Atari - Lynx" / "Named_Boxarts").mkdir(parents=True)
    (
        src_thumbnails
        / "Atari - Lynx"
        / "Named_Boxarts"
        / "Alien vs Predator (USA) (Proto) (1993-12-17).png"
    ).write_text("img", encoding="utf-8")

    playlist_name = "Atari - Lynx.lpl"
    playlist_file = src_playlists / playlist_name
    playlist_file.write_text(
        json.dumps(
            {
                "default_core_path": "",
                "default_core_name": "",
                "scan_content_dir": "",
                "scan_dat_file_path": "",
                "items": [],
            }
        ),
        encoding="utf-8",
    )

    transport = Mock()
    transport.dry_run = False
    default_config = {
        "src_playlists": str(src_playlists),
        "src_roms": [str(src_roms)],
        "src_thumbnails": str(src_thumbnails),
        "src_cores": "/cores",
        "src_cores_suffix": ".so",
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "",
        "src_core_path": "test_core",
        "src_core_name": "Test Core",
    }

    job = PlaylistUpdatecJob(default_config, transport=transport)
    job.setup(playlist)
    job.do()

    updated = json.loads(playlist_file.read_text(encoding="utf-8"))
    assert updated["items"][0]["label"] == "Alien vs Predator (USA) (Proto) (1993-12-17)"
