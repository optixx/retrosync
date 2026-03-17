import json
import tempfile
import time
from unittest.mock import Mock, patch

from retrosync import PlaylistSyncJob, PlaylistUpdatecJob, ThumbnailsUpdateJob


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


def test_playlist_update_uses_game_labels_for_quake_ii_paks(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_roms = tmp_path / "roms"
    (src_roms / "ID - Quake2" / "baseq2").mkdir(parents=True)
    (src_roms / "ID - Quake2" / "baseq2" / "pak0.pak").write_text("rom", encoding="utf-8")

    playlist_name = "Quake II.lpl"
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
        "src_cores": "/cores",
        "src_cores_suffix": ".so",
        "thumbnail_label_mode": "keep-label",
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "ID - Quake2",
        "src_core_path": "test_core",
        "src_core_name": "Test Core",
    }

    job = PlaylistUpdatecJob(default_config, transport=transport)
    job.setup(playlist)
    job.do()

    updated = json.loads(playlist_file.read_text(encoding="utf-8"))
    assert updated["items"][0]["path"].endswith("baseq2/pak0.pak")
    assert updated["items"][0]["label"] == "Quake II"


def test_playlist_update_uses_game_labels_for_quake_iii_paks(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_roms = tmp_path / "roms"
    (src_roms / "ID - Quake3" / "baseq3").mkdir(parents=True)
    (src_roms / "ID - Quake3" / "missionpack").mkdir(parents=True)
    (src_roms / "ID - Quake3" / "baseoa").mkdir(parents=True)
    (src_roms / "ID - Quake3" / "baseq3" / "pak0.pk3").write_text("rom", encoding="utf-8")
    (src_roms / "ID - Quake3" / "missionpack" / "pak0.pk3").write_text("rom", encoding="utf-8")
    (src_roms / "ID - Quake3" / "baseoa" / "pak0.pk3").write_text("rom", encoding="utf-8")

    playlist_name = "Quake III.lpl"
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
        "src_cores": "/cores",
        "src_cores_suffix": ".so",
        "thumbnail_label_mode": "keep-label",
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "ID - Quake3",
        "src_core_path": "test_core",
        "src_core_name": "Test Core",
    }

    job = PlaylistUpdatecJob(default_config, transport=transport)
    job.setup(playlist)
    job.do()

    updated = json.loads(playlist_file.read_text(encoding="utf-8"))
    labels_by_path = {item["path"]: item["label"] for item in updated["items"]}
    assert (
        labels_by_path[str(src_roms / "ID - Quake3" / "baseq3" / "pak0.pk3")] == "Quake III Arena"
    )
    assert (
        labels_by_path[str(src_roms / "ID - Quake3" / "missionpack" / "pak0.pk3")]
        == "Quake III: Team Arena"
    )
    assert labels_by_path[str(src_roms / "ID - Quake3" / "baseoa" / "pak0.pk3")] == "OpenArena"


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


def test_update_thumbnails_matches_remote_boxart_listing_with_alias(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_thumbnails = tmp_path / "thumbnails"
    (src_thumbnails / "Panasonic - 3DO" / "Named_Boxarts").mkdir(parents=True)
    (src_thumbnails / "Panasonic - 3DO" / "Named_Boxarts" / "Gex.png").write_text(
        "img", encoding="utf-8"
    )
    playlist_name = "Panasonic - 3DO.lpl"
    (src_playlists / playlist_name).write_text(
        json.dumps(
            {
                "items": [
                    {
                        "path": "/roms/3do/Alone in the Dark (USA).chd",
                        "label": "Alone in the Dark",
                    },
                    {
                        "path": "/roms/3do/Gex (Europe).chd",
                        "label": "Gex",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    default_config = {
        "src_playlists": str(src_playlists),
        "src_thumbnails": str(src_thumbnails),
        "thumbnail_url": "https://thumbnails.libretro.com/",
        "_thumbnail_cache_dir": str(tmp_path / ".cache" / "retrosync" / "thumbnail-index"),
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "3DO",
        "dest_folder": "3DO",
    }

    class DummyResponse:
        def __init__(self, text):
            self.text = text

        def read(self):
            return self.text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    root_html = """
    <html><body>
      <a href="../">Parent Directory</a>
      <a href="The%203DO%20Company%20-%203DO/">The 3DO Company - 3DO/</a>
    </body></html>
    """
    boxart_html = """
    <html><body>
      <a href="../">Parent Directory</a>
      <a href="Alone%20in%20the%20Dark.png">Alone in the Dark.png</a>
      <a href="Gex.png">Gex.png</a>
    </body></html>
    """
    empty_html = """
    <html><body>
      <a href="../">Parent Directory</a>
    </body></html>
    """

    def fake_urlopen(url, timeout=30):
        _ = timeout
        if url == "https://thumbnails.libretro.com/":
            return DummyResponse(root_html)
        if url == "https://thumbnails.libretro.com/The%203DO%20Company%20-%203DO/Named_Boxarts/":
            return DummyResponse(boxart_html)
        if url == "https://thumbnails.libretro.com/The%203DO%20Company%20-%203DO/Named_Titles/":
            return DummyResponse(empty_html)
        if url == "https://thumbnails.libretro.com/The%203DO%20Company%20-%203DO/Named_Snaps/":
            return DummyResponse(empty_html)
        raise AssertionError(url)

    job = ThumbnailsUpdateJob(default_config, transport=Mock())
    job.setup(playlist)
    job._directory_cache = {}
    job._boxart_index_cache = {}

    with patch("retrosync_core.jobs.urlopen", side_effect=fake_urlopen):
        rows = job.build_report_rows()

    assert rows[0]["remote_system"] == "The 3DO Company - 3DO"
    assert rows[0]["thumbnail"] == "Alone in the Dark"
    assert rows[0]["match_type"] == "exact"
    assert rows[0]["local_present"] is False
    assert rows[1]["thumbnail"] == "Gex"
    assert rows[1]["local_present"] is True
    assert rows[1]["url"].endswith("/Gex.png")


def test_update_thumbnails_prefers_matching_region_for_ambiguous_titles(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    playlist_name = "Atari - 7800.lpl"
    (src_playlists / playlist_name).write_text(
        json.dumps(
            {
                "items": [
                    {
                        "path": "/roms/7800/Alien Brigade (1990) (Atari) (PAL).a78",
                        "label": "Alien Brigade (1990) (Atari) (PAL)",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    job = ThumbnailsUpdateJob({"src_playlists": str(src_playlists)}, transport=Mock())
    job.setup({"name": playlist_name, "src_folder": "7800", "dest_folder": "7800"})
    job.thumbnail_index = job.build_thumbnail_index_from_names(
        ["Alien Brigade (Europe)", "Alien Brigade (USA)"]
    )

    match = job.match_thumbnail_candidate(
        "Alien Brigade (1990) (Atari) (PAL)",
        "Alien Brigade (1990) (Atari) (PAL)",
        allow_fuzzy=True,
    )

    assert match["matched"] == "Alien Brigade (Europe)"
    assert match["match_type"] == "relaxed-region"


def test_update_thumbnails_canonicalizes_brothers_and_junior_titles():
    job = ThumbnailsUpdateJob({"src_playlists": "/tmp"}, transport=Mock())
    job.thumbnail_index = job.build_thumbnail_index_from_names(
        ["Mario Bros. (Europe)", "Donkey Kong Junior (Europe)"]
    )

    mario_match = job.match_thumbnail_candidate(
        "Mario Brothers (1988) (PAL)",
        "Mario Brothers (1988) (PAL)",
        allow_fuzzy=True,
    )
    dk_match = job.match_thumbnail_candidate(
        "Donkey Kong Jr (1988) (PAL)",
        "Donkey Kong Jr (1988) (PAL)",
        allow_fuzzy=True,
    )

    assert mario_match["matched"] == "Mario Bros. (Europe)"
    assert mario_match["match_type"] == "canonical-region"
    assert dk_match["matched"] == "Donkey Kong Junior (Europe)"
    assert dk_match["match_type"] == "canonical-region"


def test_update_thumbnails_apply_downloads_assets_and_rewrites_label(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_thumbnails = tmp_path / "thumbnails"
    playlist_name = "Nintendo - Test.lpl"
    playlist_file = src_playlists / playlist_name
    playlist_file.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "path": "/roms/Nintendo Test/Super Mario Bros (USA).zip",
                        "label": "Super Mario Bros (USA)",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    default_config = {
        "src_playlists": str(src_playlists),
        "src_thumbnails": str(src_thumbnails),
        "thumbnail_url": "https://thumbnails.libretro.com/",
        "_update_thumbnails_apply": True,
        "_thumbnail_cache_dir": str(tmp_path / ".cache" / "retrosync" / "thumbnail-index"),
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "Nintendo Test",
        "dest_folder": "Nintendo Test",
    }

    class DummyResponse:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    root_html = b"""
    <html><body>
      <a href="Nintendo%20-%20Test/">Nintendo - Test/</a>
    </body></html>
    """
    boxart_html = b"""
    <html><body>
      <a href="Super%20Mario%20Bros.png">Super Mario Bros.png</a>
    </body></html>
    """
    titles_html = b"""
    <html><body>
      <a href="Super%20Mario%20Bros.png">Super Mario Bros.png</a>
    </body></html>
    """
    snaps_html = b"""
    <html><body>
      <a href="Super%20Mario%20Bros.png">Super Mario Bros.png</a>
    </body></html>
    """

    binary_payloads = {
        "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Boxarts/Super%20Mario%20Bros.png": b"box",
        "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Titles/Super%20Mario%20Bros.png": b"title",
        "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Snaps/Super%20Mario%20Bros.png": b"snap",
    }

    def fake_urlopen(url, timeout=30):
        _ = timeout
        html_payloads = {
            "https://thumbnails.libretro.com/": root_html,
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Boxarts/": boxart_html,
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Titles/": titles_html,
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Snaps/": snaps_html,
        }
        if url in html_payloads:
            return DummyResponse(html_payloads[url])
        if url in binary_payloads:
            return DummyResponse(binary_payloads[url])
        raise AssertionError(url)

    transport = Mock()
    transport.dry_run = False
    job = ThumbnailsUpdateJob(default_config, transport=transport)
    job.setup(playlist)
    job._directory_cache = {}
    job._boxart_index_cache = {}
    job._asset_folder_cache = {}

    with patch("retrosync_core.jobs.urlopen", side_effect=fake_urlopen):
        job.do()

    updated = json.loads(playlist_file.read_text(encoding="utf-8"))
    assert updated["items"][0]["label"] == "Super Mario Bros"
    assert (
        src_thumbnails / "Nintendo - Test" / "Named_Boxarts" / "Super Mario Bros.png"
    ).read_bytes() == b"box"
    assert (
        src_thumbnails / "Nintendo - Test" / "Named_Titles" / "Super Mario Bros.png"
    ).read_bytes() == b"title"
    assert (
        src_thumbnails / "Nintendo - Test" / "Named_Snaps" / "Super Mario Bros.png"
    ).read_bytes() == b"snap"


def test_update_thumbnails_reports_progress_for_search_and_apply(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    src_thumbnails = tmp_path / "thumbnails"
    playlist_name = "Nintendo - Test.lpl"
    playlist_file = src_playlists / playlist_name
    playlist_file.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "path": "/roms/Nintendo Test/Super Mario Bros (USA).zip",
                        "label": "Super Mario Bros (USA)",
                    },
                    {
                        "path": "/roms/Nintendo Test/Contra (USA).zip",
                        "label": "Contra (USA)",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    default_config = {
        "src_playlists": str(src_playlists),
        "src_thumbnails": str(src_thumbnails),
        "thumbnail_url": "https://thumbnails.libretro.com/",
        "_update_thumbnails_apply": True,
        "_thumbnail_cache_dir": str(tmp_path / ".cache" / "retrosync" / "thumbnail-index"),
    }
    playlist = {
        "name": playlist_name,
        "src_folder": "Nintendo Test",
        "dest_folder": "Nintendo Test",
    }

    class DummyResponse:
        def __init__(self, payload):
            self.payload = payload

        def read(self):
            return self.payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    html_payload = b"""
    <html><body>
      <a href="Nintendo%20-%20Test/">Nintendo - Test/</a>
      <a href="Super%20Mario%20Bros.png">Super Mario Bros.png</a>
      <a href="Contra.png">Contra.png</a>
    </body></html>
    """

    def fake_urlopen(url, timeout=30):
        _ = timeout
        directory_payloads = {
            "https://thumbnails.libretro.com/": b'<html><body><a href="Nintendo%20-%20Test/">Nintendo - Test/</a></body></html>',
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Boxarts/": html_payload,
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Titles/": html_payload,
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Snaps/": html_payload,
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Boxarts/Super%20Mario%20Bros.png": b"box1",
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Titles/Super%20Mario%20Bros.png": b"title1",
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Snaps/Super%20Mario%20Bros.png": b"snap1",
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Boxarts/Contra.png": b"box2",
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Titles/Contra.png": b"title2",
            "https://thumbnails.libretro.com/Nintendo%20-%20Test/Named_Snaps/Contra.png": b"snap2",
        }
        if url in directory_payloads:
            return DummyResponse(directory_payloads[url])
        raise AssertionError(url)

    transport = Mock()
    transport.dry_run = False
    callback = Mock()
    job = ThumbnailsUpdateJob(default_config, transport=transport)
    job.setup(playlist)
    job._directory_cache = {}
    job._boxart_index_cache = {}
    job._asset_folder_cache = {}

    assert job.size == 8

    with patch("retrosync_core.jobs.urlopen", side_effect=fake_urlopen):
        job.do(callback=callback)

    assert callback.call_count == 8


def test_update_thumbnails_mame_prefers_specific_numeric_variant_over_duplicate_separators():
    job = ThumbnailsUpdateJob({"src_playlists": "/tmp"}, transport=Mock())
    job.thumbnail_index = job.build_thumbnail_index_from_names(
        [
            "1941 - Counter Attack (World)",
            "1941_ Counter Attack (World)",
            "1941_ Counter Attack (World 900227)",
            "1941 - Counter Attack (Japan)",
        ]
    )

    match = job.match_thumbnail_candidate(
        "1941: Counter Attack (World 900227)",
        "1941: Counter Attack (World 900227)",
        allow_fuzzy=True,
    )

    assert match["matched"] == "1941_ Counter Attack (World 900227)"


def test_update_thumbnails_prefers_plain_retail_and_region_priority_when_source_has_no_region():
    job = ThumbnailsUpdateJob({"src_playlists": "/tmp"}, transport=Mock())
    job.thumbnail_index = job.build_thumbnail_index_from_names(
        [
            "Rayman (Europe) (En,Fr,De) (1S)",
            "Rayman (USA) (R2)",
            "Rayman (USA)",
        ]
    )

    match = job.match_thumbnail_candidate("Rayman", "Rayman", allow_fuzzy=True)

    assert match["matched"] == "Rayman (USA)"
    assert match["match_type"] == "normalized"


def test_update_thumbnails_uses_local_directory_cache_across_job_instances(tmp_path):
    cache_dir = tmp_path / ".cache" / "retrosync" / "thumbnail-index"
    default_config = {
        "src_playlists": str(tmp_path),
        "_thumbnail_cache_dir": str(cache_dir),
    }
    playlist = {"name": "Nintendo - Test.lpl", "src_folder": "test", "dest_folder": "test"}

    class DummyResponse:
        def __init__(self, text):
            self.text = text

        def read(self):
            return self.text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    html_payload = """
    <html><body>
      <a href="../">Parent Directory</a>
      <a href="Nintendo%20-%20Test/">Nintendo - Test/</a>
    </body></html>
    """

    first_job = ThumbnailsUpdateJob(default_config, transport=Mock())
    first_job.setup(playlist)
    with patch(
        "retrosync_core.jobs.urlopen", return_value=DummyResponse(html_payload)
    ) as open_mock:
        entries = first_job._parse_directory_listing("https://thumbnails.libretro.com/")
    assert open_mock.call_count == 1
    assert entries == [
        {
            "name": "Nintendo - Test",
            "url": "https://thumbnails.libretro.com/Nintendo%20-%20Test/",
            "is_dir": True,
        }
    ]
    assert any(cache_dir.iterdir())

    second_job = ThumbnailsUpdateJob(default_config, transport=Mock())
    second_job.setup(playlist)
    with patch(
        "retrosync_core.jobs.urlopen", side_effect=AssertionError("network should not be used")
    ):
        cached_entries = second_job._parse_directory_listing("https://thumbnails.libretro.com/")
    assert cached_entries == entries


def test_update_thumbnails_ignores_stale_directory_cache(tmp_path):
    cache_dir = tmp_path / ".cache" / "retrosync" / "thumbnail-index"
    default_config = {
        "src_playlists": str(tmp_path),
        "_thumbnail_cache_dir": str(cache_dir),
    }
    playlist = {"name": "Nintendo - Test.lpl", "src_folder": "test", "dest_folder": "test"}
    job = ThumbnailsUpdateJob(default_config, transport=Mock())
    job.setup(playlist)
    job._directory_cache = {}

    stale_entries = [
        {
            "name": "Stale System",
            "url": "https://thumbnails.libretro.com/Stale%20System/",
            "is_dir": True,
        }
    ]
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = job._cache_file_for_url("https://thumbnails.libretro.com/")
    cache_file.write_text(
        json.dumps(
            {
                "version": job.DIRECTORY_CACHE_VERSION,
                "url": "https://thumbnails.libretro.com/",
                "created_at": time.time() - (job.DIRECTORY_CACHE_TTL_SECONDS + 10),
                "entries": stale_entries,
            }
        ),
        encoding="utf-8",
    )

    class DummyResponse:
        def __init__(self, text):
            self.text = text

        def read(self):
            return self.text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    fresh_html = """
    <html><body>
      <a href="Nintendo%20-%20Test/">Nintendo - Test/</a>
    </body></html>
    """

    with patch("retrosync_core.jobs.urlopen", return_value=DummyResponse(fresh_html)) as open_mock:
        entries = job._parse_directory_listing("https://thumbnails.libretro.com/")

    assert open_mock.call_count == 1
    assert entries[0]["name"] == "Nintendo - Test"


def test_update_thumbnails_no_cache_skips_read_and_write(tmp_path):
    cache_dir = tmp_path / ".cache" / "retrosync" / "thumbnail-index"
    default_config = {
        "src_playlists": str(tmp_path),
        "_thumbnail_cache_dir": str(cache_dir),
        "_no_thumbnail_cache": True,
    }
    playlist = {"name": "Nintendo - Test.lpl", "src_folder": "test", "dest_folder": "test"}
    job = ThumbnailsUpdateJob(default_config, transport=Mock())
    job.setup(playlist)
    job._directory_cache = {}

    class DummyResponse:
        def __init__(self, text):
            self.text = text

        def read(self):
            return self.text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    html_payload = """
    <html><body>
      <a href="Nintendo%20-%20Test/">Nintendo - Test/</a>
    </body></html>
    """

    with patch("retrosync_core.jobs.urlopen", return_value=DummyResponse(html_payload)):
        entries = job._parse_directory_listing("https://thumbnails.libretro.com/")

    assert entries[0]["name"] == "Nintendo - Test"
    assert not cache_dir.exists()
    assert job._cache_status_summary() == "cache: disabled | memory 0 | disk 0 | network 1"


def test_update_thumbnails_reports_cache_summary_for_disk_hits(tmp_path):
    cache_dir = tmp_path / ".cache" / "retrosync" / "thumbnail-index"
    default_config = {
        "src_playlists": str(tmp_path),
        "_thumbnail_cache_dir": str(cache_dir),
    }
    playlist = {"name": "Nintendo - Test.lpl", "src_folder": "test", "dest_folder": "test"}
    job = ThumbnailsUpdateJob(default_config, transport=Mock())
    job.setup(playlist)
    job._directory_cache = {}

    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = job._cache_file_for_url("https://thumbnails.libretro.com/")
    cache_file.write_text(
        json.dumps(
            {
                "version": job.DIRECTORY_CACHE_VERSION,
                "url": "https://thumbnails.libretro.com/",
                "created_at": time.time(),
                "entries": [
                    {
                        "name": "Nintendo - Test",
                        "url": "https://thumbnails.libretro.com/Nintendo%20-%20Test/",
                        "is_dir": True,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    entries = job._parse_directory_listing("https://thumbnails.libretro.com/")

    assert entries[0]["name"] == "Nintendo - Test"
    assert job._cache_status_summary() == "cache: normal | memory 0 | disk 1 | network 0"


def test_update_thumbnails_reports_cache_summary_for_refresh_mode(tmp_path):
    default_config = {
        "src_playlists": str(tmp_path),
        "_thumbnail_cache_dir": str(tmp_path / ".cache" / "retrosync" / "thumbnail-index"),
        "_refresh_thumbnail_cache": True,
    }
    playlist = {"name": "Nintendo - Test.lpl", "src_folder": "test", "dest_folder": "test"}
    job = ThumbnailsUpdateJob(default_config, transport=Mock())
    job.setup(playlist)
    job._directory_cache = {}

    class DummyResponse:
        def __init__(self, text):
            self.text = text

        def read(self):
            return self.text.encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    html_payload = """
    <html><body>
      <a href="Nintendo%20-%20Test/">Nintendo - Test/</a>
    </body></html>
    """

    with patch("retrosync_core.jobs.urlopen", return_value=DummyResponse(html_payload)):
        entries = job._parse_directory_listing("https://thumbnails.libretro.com/")

    assert entries[0]["name"] == "Nintendo - Test"
    assert job._cache_status_summary() == "cache: refresh | memory 0 | disk 0 | network 1"


def test_update_thumbnails_formats_report_text_with_fixed_width(tmp_path):
    default_config = {
        "src_playlists": str(tmp_path),
    }
    playlist = {"name": "Nintendo - Test.lpl", "src_folder": "test", "dest_folder": "test"}
    job = ThumbnailsUpdateJob(default_config, transport=Mock())
    job.setup(playlist)

    assert job._format_report_text("short") == "short"
    assert (
        job._format_report_text("1234567890123456789012345678901", 30)
        == "12345678901234567890123456789…"
    )
    assert job._report_text_column_width(120) == 29
    assert job._report_text_column_width(240) == 69
    assert job._report_text_column_width(60) == 18


def test_update_thumbnails_emits_final_coverage_summary_table(tmp_path):
    default_config = {
        "src_playlists": str(tmp_path),
    }
    transport = Mock()
    job = ThumbnailsUpdateJob(default_config, transport=transport)

    job._summary_rows = [
        {"system": "Atari - 7800", "rom_count": 10, "match_count": 8, "coverage": 80.0},
        {"system": "Atari - Jaguar", "rom_count": 5, "match_count": 5, "coverage": 100.0},
    ]

    messages = job.consume_final_deferred_messages()

    assert len(messages) == 1
    assert "Thumbnail Coverage Summary" in messages[0]
    assert "Atari - 7800" in messages[0]
    assert "80.0%" in messages[0]
    assert "Atari - Jaguar" in messages[0]
    assert "100.0%" in messages[0]
