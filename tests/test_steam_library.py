import json
import struct
import zlib
from pathlib import Path

import pytest

from retrosync_core import steam


def _string(value):
    return value.encode("utf-8") + b"\x00"


def _int32(value):
    return struct.pack("<i", value)


def test_compute_appids_matches_srm_reference_vector():
    # Worked example from SRM's generate-app-id source.
    exe = (
        '"/home/deck/emulation/tools/launchers/retroarch.sh" '
        "-L /home/deck/.var/app/org.libretro.RetroArch/config/retroarch/cores/"
        'snes9x_libretro.so "/home/deck/Emulation/roms/snes/Super Mario World.sfc"'
    )
    unsigned, signed = steam.compute_appids(exe, "Super Mario World")
    assert unsigned == 2507011446
    assert signed == -1787955850


def test_compute_appids_is_crc32_with_high_bit():
    unsigned, signed = steam.compute_appids('"/usr/bin/flatpak"', "Game")
    expected = zlib.crc32(b'"/usr/bin/flatpak"Game') | 0x80000000
    assert unsigned == expected
    assert signed == expected - 2**32


def test_shortcuts_vdf_round_trip_preserves_foreign_fields():
    appid = (0x80000000 | 12345) - 2**32
    blob = (
        b"\x00"
        + _string("shortcuts")
        + b"\x00"
        + _string("0")
        + b"\x02"
        + _string("appid")
        + _int32(appid)
        + b"\x01"
        + _string("AppName")
        + _string("Foreign Game")
        + b"\x01"
        + _string("Exe")
        + _string('"/opt/emulator.sh"')
        + b"\x00"
        + _string("tags")
        + b"\x01"
        + _string("0")
        + _string("Arcade FBNeo")
        + b"\x08"
        + b"\x02"
        + _string("LastPlayTime")
        + _int32(0)
        + b"\x08"
        + b"\x08"
        + b"\x08"
    )
    doc = steam.read_shortcuts_vdf(blob)
    assert steam.write_shortcuts_vdf(doc) == blob


def test_read_shortcuts_vdf_handles_empty_input():
    assert steam.read_shortcuts_vdf(b"") == {"shortcuts": {}}
    assert steam.read_shortcuts_vdf(None) == {"shortcuts": {}}


def test_collection_names_for_builds_umbrella_and_system():
    assert steam.collection_names_for("Arcade - FBNeo", "RetroSync") == [
        "RetroSync",
        "RetroSync / Arcade - FBNeo",
    ]
    # A system named exactly like the umbrella collapses to one collection.
    assert steam.collection_names_for("RetroSync", "RetroSync") == ["RetroSync"]


def test_collection_names_for_system_only_without_umbrella():
    assert steam.collection_names_for("Arcade - FBNeo", "RetroSync", umbrella=False) == [
        "RetroSync / Arcade - FBNeo"
    ]
    assert steam.collection_names_for("RetroSync", "RetroSync", umbrella=False) == ["RetroSync"]


def test_build_launch_command_uses_emulator_table_for_every_entry():
    for emulator, (script, _) in steam.EMULATOR_LAUNCHERS.items():
        exe_field, start_dir = steam.build_launch_command(
            emulator, "/deck/launchers", "/deck/cores/x.so", "/deck/roms/g/Game One.zip"
        )
        assert exe_field.startswith(f'"/deck/launchers/{script}"'), emulator
        assert start_dir == "/deck/launchers"
        assert '"{path}"' not in exe_field


def test_build_launch_command_retroarch_includes_core():
    exe_field, _ = steam.build_launch_command(
        "retroarch",
        "/deck/launchers",
        "/deck/cores/stella_libretro.so",
        "/deck/roms/a2600/Pitfall!.zip",
    )
    assert exe_field == (
        '"/deck/launchers/retroarch.sh" -L /deck/cores/stella_libretro.so '
        '"/deck/roms/a2600/Pitfall!.zip"'
    )


def test_build_launch_command_rejects_unknown_emulator():
    with pytest.raises(ValueError, match="Unknown steam_emulator 'nosuch'"):
        steam.build_launch_command("nosuch", "/deck/launchers", "c", "r")


def test_build_launch_command_custom_command_first_token_is_script():
    exe_field, start_dir = steam.build_launch_command(
        "retroarch",
        "/deck/launchers",
        "/deck/cores/x.so",
        "/deck/roms/g/Game One.zip",
        custom_command='my-wrapper.sh --fullscreen -L {core} "{path}"',
    )
    assert exe_field == (
        '"/deck/launchers/my-wrapper.sh" --fullscreen '
        '-L /deck/cores/x.so "/deck/roms/g/Game One.zip"'
    )
    assert start_dir == "/deck/launchers"


def test_launcher_script_for_resolves_path():
    path = steam.launcher_script_for("duckstation", "/deck/launchers")
    assert path == Path("/deck/launchers/duckstation.sh")
    with pytest.raises(ValueError, match="Unknown steam_emulator"):
        steam.launcher_script_for("nosuch", "/deck/launchers")


def _entry(label, exe, tags, launch_options=""):
    unsigned, signed = steam.compute_appids(exe, label)
    return unsigned, {
        "appid": signed,
        "appname": label,
        "exe": exe,
        "StartDir": "/usr/bin",
        "LaunchOptions": launch_options,
        "icon": "",
        "tags": {str(idx): tag for idx, tag in enumerate(tags)},
    }


def _desired(entries):
    return dict(entries)


PREFIX = "RetroSync"
SYSTEM = "RetroSync / Nintendo - SNES"
ROM_PREFIX = "/deck/roms/snes"


def test_reconcile_adds_desired_entries_to_empty_library():
    unsigned_a, entry_a = _entry("Game A", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM])
    unsigned_b, entry_b = _entry("Game B", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM])
    new_doc, report = steam.reconcile_shortcuts(
        {"shortcuts": {}},
        _desired([(unsigned_a, entry_a), (unsigned_b, entry_b)]),
        rom_dir_prefix=ROM_PREFIX,
        prefix=PREFIX,
    )
    assert sorted(new_doc["shortcuts"]) == ["0", "1"]
    assert report.added == ["Game A", "Game B"]
    assert report.removed == []
    assert new_doc["shortcuts"]["0"] is entry_a


def test_reconcile_is_idempotent_on_second_run():
    unsigned, entry = _entry(
        "Game A", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM], f"-L x {ROM_PREFIX}/a.sfc"
    )
    doc = {"shortcuts": {"0": dict(entry)}}
    new_doc, report = steam.reconcile_shortcuts(
        doc, _desired([(unsigned, entry)]), rom_dir_prefix=ROM_PREFIX, prefix=PREFIX
    )
    assert new_doc == doc
    assert report.unchanged == ["Game A"]
    assert not report.has_changes()


def test_reconcile_updates_changed_launch_options():
    unsigned, entry = _entry("Game A", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM])
    changed = dict(entry)
    changed["LaunchOptions"] = "new options"
    new_doc, report = steam.reconcile_shortcuts(
        {"shortcuts": {"0": dict(entry)}},
        _desired([(unsigned, changed)]),
        rom_dir_prefix=ROM_PREFIX,
        prefix=PREFIX,
    )
    assert report.updated == ["Game A"]
    assert new_doc["shortcuts"]["0"]["LaunchOptions"] == "new options"


def test_reconcile_prunes_stale_ours_and_preserves_foreign():
    stale_unsigned, stale = _entry(
        "Old Game", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM], f"{ROM_PREFIX}/old.sfc"
    )
    foreign_unsigned, foreign = _entry("EmuDeck Game", '"/opt/launcher.sh"', ["Arcade FBNeo"])
    foreign_original = dict(foreign)
    keep_unsigned, keep = _entry(
        "Game A", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM], f"{ROM_PREFIX}/a.sfc"
    )
    existing = {"shortcuts": {"0": dict(foreign), "1": dict(stale)}}
    new_doc, report = steam.reconcile_shortcuts(
        existing, _desired([(keep_unsigned, keep)]), rom_dir_prefix=ROM_PREFIX, prefix=PREFIX
    )
    assert report.removed == ["Old Game"]
    assert report.removed_appids == {stale_unsigned}
    assert len(new_doc["shortcuts"]) == 2
    assert foreign_original in [new_doc["shortcuts"][key] for key in new_doc["shortcuts"]]
    assert keep in [new_doc["shortcuts"][key] for key in new_doc["shortcuts"]]


def test_reconcile_keeps_owned_entries_from_other_rom_trees():
    other_unsigned, other = _entry(
        "Other System Game", '"/usr/bin/flatpak"', [SYSTEM], "/deck/roms/megadrive/x.md"
    )
    desired_unsigned, desired = _entry("Game A", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM])
    new_doc, report = steam.reconcile_shortcuts(
        {"shortcuts": {"0": dict(other)}},
        _desired([(desired_unsigned, desired)]),
        rom_dir_prefix=ROM_PREFIX,
        prefix=PREFIX,
    )
    assert report.removed == []
    entries = list(new_doc["shortcuts"].values())
    assert other in entries
    assert desired in entries


def test_reconcile_matches_rom_prefix_in_exe_field():
    # Launcher-script style: the ROM path lives in the Exe field.
    old_unsigned, old = _entry(
        "Old Game",
        f'"/deck/launchers/retroarch.sh" -L /core.so "{ROM_PREFIX}/old.sfc"',
        [SYSTEM],
    )
    keep_unsigned, keep = _entry(
        "Game A",
        f'"/deck/launchers/retroarch.sh" -L /core.so "{ROM_PREFIX}/a.sfc"',
        [SYSTEM],
    )
    new_doc, report = steam.reconcile_shortcuts(
        {"shortcuts": {"0": dict(old)}},
        _desired([(keep_unsigned, keep)]),
        rom_dir_prefix=ROM_PREFIX,
        prefix=PREFIX,
    )
    assert report.removed == ["Old Game"]
    assert list(new_doc["shortcuts"].values()) == [keep]


def test_reconcile_skips_desired_appids_owned_by_foreign_entries():
    unsigned, entry = _entry("Game A", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM])
    foreign = {
        "appid": entry["appid"],
        "AppName": "Game A",
        "Exe": '"/usr/bin/flatpak"',
        "tags": {"0": "Something Else"},
    }
    new_doc, report = steam.reconcile_shortcuts(
        {"shortcuts": {"0": foreign}},
        _desired([(unsigned, entry)]),
        rom_dir_prefix=ROM_PREFIX,
        prefix=PREFIX,
    )
    assert report.skipped_foreign == ["Game A"]
    assert list(new_doc["shortcuts"].values()) == [foreign]


def test_reconcile_matches_fields_regardless_of_casing():
    unsigned, entry = _entry(
        "Game A", '"/usr/bin/flatpak"', ["RetroSync", SYSTEM], f"-L c {ROM_PREFIX}/a.sfc"
    )
    foreign_style = {
        "appid": entry["appid"],
        "AppName": "Game A",
        "Exe": '"/usr/bin/flatpak"',
        "StartDir": "/usr/bin",
        "LaunchOptions": f"-L c {ROM_PREFIX}/a.sfc",
        "tags": {"0": "RetroSync"},
    }
    new_doc, report = steam.reconcile_shortcuts(
        {"shortcuts": {"0": foreign_style}},
        _desired([(unsigned, entry)]),
        rom_dir_prefix=ROM_PREFIX,
        prefix=PREFIX,
    )
    assert report.unchanged == ["Game A"]
    assert new_doc["shortcuts"]["0"] is entry


def test_collection_key_for_is_urlsafe_base64():
    assert steam.collection_key_for("RetroSync / SNES") == (
        "retrosync-"
        + __import__("base64").urlsafe_b64encode(b"RetroSync / SNES").decode().rstrip("=")
    )


def test_select_namespace_picks_highest_version():
    assert steam.select_namespace([[1, "798"], [3, "0"]]) == 1
    assert steam.select_namespace([[1, "42"], [3, "798"]]) == 3
    assert steam.select_namespace([[1, "0"]]) == 1
    assert steam.select_namespace(None) == 1
    assert steam.select_namespace([["bad"]]) == 1


def _namespace_entry(key, name, added, removed=None, is_deleted=False):
    return [
        key,
        {
            "key": key,
            "timestamp": 100,
            "value": json.dumps(
                {
                    "id": key.removeprefix("user-collections."),
                    "name": name,
                    "added": added,
                    "removed": removed or [],
                }
            ),
            "version": "100",
            "conflictResolutionMethod": "custom",
            "strMethodId": "union-collections",
            **({"is_deleted": True} if is_deleted else {}),
        },
    ]


def test_find_collection_slots_matches_retrosync_keys_and_name_prefix():
    data = [
        _namespace_entry("user-collections.srm-SUPER", "Arcade FBNeo", [1]),
        _namespace_entry("user-collections.retrosync-UmV0cm9TeW5j", "RetroSync", [1, 2]),
        _namespace_entry("user-collections.uc-abc", "Favorites", [3]),
        _namespace_entry("user-collections.uc-def", "RetroSync / Shared", [4]),
    ]
    slots = steam.find_collection_slots(data, prefix="RetroSync")
    assert set(slots) == {"RetroSync", "RetroSync / Shared"}


def test_merge_collection_namespace_updates_membership_and_preserves_foreign():
    data = [
        _namespace_entry("user-collections.srm-SUPER", "Arcade FBNeo", [1, 2]),
        _namespace_entry("user-collections.retrosync-RetroSync", "RetroSync", [9, 10]),
    ]
    merged = steam.merge_collection_namespace(
        data,
        {"RetroSync": [10, 11], "RetroSync / SNES": [10, 11]},
        prefix="RetroSync",
        timestamp=12345,
    )
    by_key = {item[0]: item[1] for item in merged}
    # Foreign srm- entry untouched.
    assert by_key["user-collections.srm-SUPER"]["timestamp"] == 100
    umbrella = json.loads(by_key["user-collections.retrosync-RetroSync"]["value"])
    assert umbrella["added"] == [10, 11]
    assert by_key["user-collections.retrosync-RetroSync"]["version"] == "12345"
    system_key = f"user-collections.{steam.collection_key_for('RetroSync / SNES')}"
    system = json.loads(by_key[system_key]["value"])
    assert system["name"] == "RetroSync / SNES"
    assert system["added"] == [10, 11]


def test_merge_collection_namespace_reuses_existing_ids_and_clears_removed():
    key = f"user-collections.{steam.collection_key_for('RetroSync')}"
    data = [_namespace_entry(key, "RetroSync", [9], removed=[7, 8])]
    merged = steam.merge_collection_namespace(
        data, {"RetroSync": [7, 9]}, prefix="RetroSync", timestamp=1
    )
    value = json.loads(merged[0][1]["value"])
    assert value["id"] == key.removeprefix("user-collections.")
    assert value["added"] == [7, 9]
    assert value["removed"] == [8]


def test_collection_membership_reads_added_ids():
    data = [
        _namespace_entry(
            f"user-collections.{steam.collection_key_for('RetroSync')}", "RetroSync", [5, 6]
        ),
    ]
    assert steam.collection_membership(data, "RetroSync") == {5, 6}
    assert steam.collection_membership(data, "Missing") == set()
    assert steam.collection_membership(None, "RetroSync") == set()


def test_remove_collection_members_prunes_and_deletes_when_empty():
    key = f"user-collections.{steam.collection_key_for('RetroSync')}"
    foreign = _namespace_entry("user-collections.srm-X", "Arcade FBNeo", [7])
    data = [_namespace_entry(key, "RetroSync", [5, 6]), foreign]
    new_data, changed = steam.remove_collection_members(data, "RetroSync", [5])
    assert changed is True
    assert steam.collection_membership(new_data, "RetroSync") == {6}
    assert new_data[1] == foreign

    new_data, changed = steam.remove_collection_members(new_data, "RetroSync", [6])
    assert changed is True
    assert steam.collection_membership(new_data, "RetroSync") == set()
    assert len(new_data) == 1

    same, changed = steam.remove_collection_members(new_data, "RetroSync", [6])
    assert changed is False
    assert same == new_data


def _localconfig_with_blob(blob_json):
    import vdf

    return vdf.dumps(
        {
            "UserLocalConfigStore": {
                "Software": {"Valve": {"Steam": {steam.USER_COLLECTIONS_KEY: blob_json}}}
            }
        }
    ).encode("utf-8")


def test_merge_localconfig_user_collections_adds_and_preserves():
    blob = json.dumps(
        {
            "favorite": {"id": "favorite", "added": [1], "removed": []},
            "srm-X": {"id": "srm-X", "added": [2], "removed": []},
        },
        separators=(",", ":"),
    )
    data = _localconfig_with_blob(blob)
    out = steam.merge_localconfig_user_collections(data, {"RetroSync / SNES": {10, 11}})
    assert out is not None
    assert b"retrosync-" in out

    import vdf

    doc = vdf.loads(out.decode("utf-8"))

    def find(node):
        if isinstance(node, dict):
            if steam.USER_COLLECTIONS_KEY in node:
                return node[steam.USER_COLLECTIONS_KEY]
            for value in node.values():
                found = find(value)
                if found:
                    return found
        return None

    merged_blob = json.loads(find(doc))
    assert set(merged_blob) == {"favorite", "srm-X", steam.collection_key_for("RetroSync / SNES")}
    assert merged_blob["favorite"]["added"] == [1]
    entry = merged_blob[steam.collection_key_for("RetroSync / SNES")]
    assert entry["id"] == steam.collection_key_for("RetroSync / SNES")
    assert entry["added"] == [10, 11]
    # Idempotent: merging the same members again changes nothing.
    assert steam.merge_localconfig_user_collections(out, {"RetroSync / SNES": {10, 11}}) is None


def test_merge_localconfig_user_collections_without_blob_returns_none():
    import vdf

    data = vdf.dumps({"UserLocalConfigStore": {"Software": {}}}).encode("utf-8")
    assert steam.merge_localconfig_user_collections(data, {"RetroSync": {1}}) is None
    assert steam.merge_localconfig_user_collections(b"", {"RetroSync": {1}}) is None
