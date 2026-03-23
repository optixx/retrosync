from pathlib import Path

import os
import time

from retrosync_app.services import build_preview_plan, build_run_config
from retrosync_core.jobs import ThumbnailsUpdateJob


def test_build_preview_plan_emits_copy_rewrite_and_download_rows(tmp_path, monkeypatch):
    src_roms = tmp_path / "roms"
    (src_roms / "psx").mkdir(parents=True)
    rom_file = src_roms / "psx" / "game1.chd"
    rom_file.write_text("rom")

    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    playlist_path = src_playlists / "Sony - PlayStation.lpl"
    playlist_path.write_text('{"items":[{"label":"A"},{"label":"B"}]}', encoding="utf-8")

    default = {
        "src_roms": [str(src_roms)],
        "dest_roms": str(tmp_path / "dest_roms"),
        "src_playlists": str(src_playlists),
        "dest_playlists": str(tmp_path / "dest_playlists"),
        "src_thumbnails": str(tmp_path / "thumbnails"),
    }
    playlists = [
        {
            "name": "Sony - PlayStation.lpl",
            "src_folder": "psx",
            "dest_folder": "psx",
        }
    ]
    run_cfg = build_run_config(
        do_sync_playlists=True,
        do_sync_bios=False,
        do_sync_favorites=False,
        do_sync_thumbnails=False,
        do_sync_roms=True,
        do_sync_shaders=False,
        do_update_playlists=True,
        do_update_thumbnails=True,
        dry_run=True,
        do_debug=False,
    )

    def fake_build_report_rows(_self, _callback=None, _cancel_check=None):
        return [
            {
                "item_index": 0,
                "system": "Sony - PlayStation",
                "remote_system": "Sony - PlayStation",
                "rom": "game1.chd",
                "label": "A",
                "thumbnail": "Game One",
                "match_type": "normalized",
                "score": 1.0,
                "local_present": False,
                "asset_urls": {
                    "boxart": {"url": "https://example.com/box.png", "ext": ".png"},
                    "title": None,
                    "snap": None,
                },
                "url": "https://example.com/box.png",
            }
        ]

    monkeypatch.setattr(ThumbnailsUpdateJob, "build_report_rows", fake_build_report_rows)

    plan = build_preview_plan(
        default=default,
        playlists=playlists,
        run_cfg=run_cfg,
        apply_changes=True,
        preview_remote_thumbnail_lookup=True,
    )

    operations = {(row.action, row.operation) for row in plan.rows}
    assert ("sync_roms", "copy") in operations
    assert ("sync_playlists", "copy") in operations
    assert ("update_playlists", "rewrite") in operations
    assert ("update_thumbnails", "download") in operations
    assert ("update_thumbnails", "rewrite") in operations
    assert plan.planned_copies >= 2
    assert plan.planned_rewrites >= 2
    assert plan.planned_downloads == 1
    assert any(Path(row.source) == rom_file for row in plan.rows if row.action == "sync_roms")


def test_build_preview_plan_uses_item_level_rows_for_playlist_and_thumbnail_updates(
    tmp_path, monkeypatch
):
    src_roms = tmp_path / "roms"
    (src_roms / "psx").mkdir(parents=True)
    (src_roms / "psx" / "game1.cue").write_text("cue")
    (src_roms / "psx" / "game2.cue").write_text("cue")

    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    playlist_path = src_playlists / "Sony - PlayStation.lpl"
    playlist_path.write_text(
        '{"items":[{"path":"game1.cue","label":"Game 1"},{"path":"game2.cue","label":"Game 2"}]}',
        encoding="utf-8",
    )

    def fake_build_report_rows(_self, _callback=None, _cancel_check=None):
        return [
            {
                "item_index": 0,
                "system": "Sony - PlayStation",
                "remote_system": "Sony - PlayStation",
                "rom": "game1.cue",
                "label": "Game 1",
                "thumbnail": "Game One",
                "match_type": "normalized",
                "score": 1.0,
                "local_present": False,
                "asset_urls": {
                    "boxart": {"url": "https://example.com/box.png", "ext": ".png"},
                    "title": None,
                    "snap": None,
                },
                "url": "https://example.com/box.png",
            },
            {
                "item_index": 1,
                "system": "Sony - PlayStation",
                "remote_system": "Sony - PlayStation",
                "rom": "game2.cue",
                "label": "Game 2",
                "thumbnail": "",
                "match_type": "none",
                "score": "",
                "local_present": False,
                "asset_urls": {"boxart": None, "title": None, "snap": None},
                "url": "",
            },
        ]

    monkeypatch.setattr(ThumbnailsUpdateJob, "build_report_rows", fake_build_report_rows)

    default = {
        "src_roms": [str(src_roms)],
        "src_playlists": str(src_playlists),
        "src_thumbnails": str(tmp_path / "thumbs"),
    }
    playlists = [
        {
            "name": "Sony - PlayStation.lpl",
            "src_folder": "psx",
            "dest_folder": "psx",
        }
    ]
    run_cfg = build_run_config(
        do_sync_playlists=False,
        do_sync_bios=False,
        do_sync_favorites=False,
        do_sync_thumbnails=False,
        do_sync_roms=False,
        do_sync_shaders=False,
        do_update_playlists=True,
        do_update_thumbnails=True,
        dry_run=True,
        do_debug=False,
    )

    plan = build_preview_plan(
        default=default,
        playlists=playlists,
        run_cfg=run_cfg,
        apply_changes=True,
        preview_remote_thumbnail_lookup=True,
    )

    playlist_rows = [row for row in plan.rows if row.action == "update_playlists"]
    thumbnail_rows = [row for row in plan.rows if row.action == "update_thumbnails"]
    assert len(playlist_rows) == 2
    assert len([row for row in thumbnail_rows if row.operation == "inspect"]) == 2
    assert len([row for row in thumbnail_rows if row.operation == "download"]) == 1
    assert any("ROM game1.cue" in row.details for row in playlist_rows)
    assert any("Rewrite playlist label" in row.details for row in thumbnail_rows)


def test_build_preview_plan_classifies_local_sync_rows_as_copy_skip_overwrite(tmp_path):
    src_roms = tmp_path / "roms"
    dest_roms = tmp_path / "dest_roms"
    (src_roms / "psx").mkdir(parents=True)
    (dest_roms / "psx").mkdir(parents=True)

    copy_src = src_roms / "psx" / "copy.bin"
    skip_src = src_roms / "psx" / "skip.bin"
    overwrite_src = src_roms / "psx" / "overwrite.bin"
    copy_src.write_text("copy", encoding="utf-8")
    skip_src.write_text("same", encoding="utf-8")
    overwrite_src.write_text("newer", encoding="utf-8")

    skip_dest = dest_roms / "psx" / "skip.bin"
    overwrite_dest = dest_roms / "psx" / "overwrite.bin"
    skip_dest.write_text("same", encoding="utf-8")
    overwrite_dest.write_text("old", encoding="utf-8")

    older = int(time.time()) - 60
    newer = int(time.time()) + 60
    os.utime(skip_src, (older, older))
    os.utime(skip_dest, (newer, newer))
    os.utime(overwrite_src, (newer, newer))
    os.utime(overwrite_dest, (older, older))

    default = {
        "transport": "filesystem",
        "src_roms": [str(src_roms)],
        "dest_roms": str(dest_roms),
    }
    playlists = [{"name": "Sony - PlayStation.lpl", "src_folder": "psx", "dest_folder": "psx"}]
    run_cfg = build_run_config(
        do_sync_playlists=False,
        do_sync_bios=False,
        do_sync_favorites=False,
        do_sync_thumbnails=False,
        do_sync_roms=True,
        do_sync_shaders=False,
        do_update_playlists=False,
        do_update_thumbnails=False,
        dry_run=True,
        do_debug=False,
    )

    plan = build_preview_plan(default=default, playlists=playlists, run_cfg=run_cfg)

    operations = {Path(row.source).name: row.operation for row in plan.rows}
    assert operations["copy.bin"] == "copy"
    assert operations["skip.bin"] == "skip"
    assert operations["overwrite.bin"] == "overwrite"
    assert plan.planned_copies == 1
    assert plan.planned_skips == 1
    assert plan.planned_overwrites == 1


def test_build_preview_plan_uses_local_only_thumbnail_preview_by_default(tmp_path):
    src_playlists = tmp_path / "playlists"
    src_playlists.mkdir()
    playlist_path = src_playlists / "Sony - PlayStation.lpl"
    playlist_path.write_text(
        '{"items":[{"path":"roms/game1.cue","label":"Game 1"},{"path":"roms/game2.cue","label":"Game 2"}]}',
        encoding="utf-8",
    )

    default = {
        "src_playlists": str(src_playlists),
        "src_thumbnails": str(tmp_path / "thumbs"),
    }
    playlists = [
        {
            "name": "Sony - PlayStation.lpl",
            "src_folder": "psx",
            "dest_folder": "psx",
        }
    ]
    run_cfg = build_run_config(
        do_sync_playlists=False,
        do_sync_bios=False,
        do_sync_favorites=False,
        do_sync_thumbnails=False,
        do_sync_roms=False,
        do_sync_shaders=False,
        do_update_playlists=False,
        do_update_thumbnails=True,
        dry_run=True,
        do_debug=False,
    )

    plan = build_preview_plan(default=default, playlists=playlists, run_cfg=run_cfg)

    assert all(row.operation == "inspect" for row in plan.rows)
    assert all("deferred until run" in row.details for row in plan.rows)
