import base64
import json
from pathlib import Path

import pytest
import vdf

from retrosync_core import steam
from retrosync_core.jobs import SteamLibrarySync
from retrosync_core.transports import (
    TransportFileSystemUnix,
    TransportSSHUnix,
    TransportUnixBase,
)

ACCOUNT = "12345"


class DummySteamTransport:
    dry_run = False

    def __init__(self, files=None, dirs=None):
        self.files = {Path(path): data for path, data in (files or {}).items()}
        self.dirs = {Path(path): names for path, names in (dirs or {}).items()}
        self.copies = []
        self.created_dirs = []
        self.captured = []
        self.steam_running = False
        self.steam_rewrites_namespace_after = None

    def read_file(self, path):
        path = Path(path)
        if path in self.files:
            data = self.files[path]
            if (
                self.steam_rewrites_namespace_after is not None
                and path.name.startswith("cloud-storage-namespace-")
                and not path.name.endswith(".bak")
            ):
                if self.steam_rewrites_namespace_after > 0:
                    self.steam_rewrites_namespace_after -= 1
                else:
                    return data + b" "  # simulated Steam re-serialization
            return data
        raise FileNotFoundError(str(path))

    def list_dir(self, path):
        path = Path(path)
        if path in self.dirs:
            return list(self.dirs[path])
        raise FileNotFoundError(str(path))

    def remote_file_exists(self, path):
        return Path(path) in self.files

    def ensure_dir_exists(self, path_directory):
        self.created_dirs.append(Path(path_directory))

    def copy_file(self, src_filename, dest_filename, cancel_check=None):
        _ = cancel_check
        data = Path(src_filename).read_bytes()
        self.copies.append((data, Path(dest_filename)))
        self.files[Path(dest_filename)] = data

    def run_capture(self, cmd, timeout=120):
        _ = timeout
        self.captured.append(cmd)
        if "steam -shutdown" in cmd:
            self.steam_running = False
            return 0, b"", b""
        if "pgrep -x steam" in cmd:
            return (0, b"4242\n", b"") if self.steam_running else (0, b"", b"")
        return 0, b"", b""

    def run_remote(self, cmd, timeout=120):
        return self.run_capture(cmd, timeout=timeout)

    def written_files(self):
        return {dest: data for data, dest in self.copies}


@pytest.fixture(autouse=True)
def _reset_steam_lifecycle():
    from retrosync_core.jobs import reset_steam_lifecycle_state

    reset_steam_lifecycle_state()
    yield
    reset_steam_lifecycle_state()


@pytest.fixture
def steam_env(tmp_path):
    playlists_dir = tmp_path / "playlists"
    playlists_dir.mkdir()
    roms_dir = tmp_path / "roms" / "Nintendo - SNES"
    roms_dir.mkdir(parents=True)
    cores_dir = tmp_path / "cores"
    cores_dir.mkdir()
    thumbnails_dir = tmp_path / "thumbnails" / "Nintendo - SNES" / "Named_Boxarts"
    thumbnails_dir.mkdir(parents=True)
    (thumbnails_dir / "Game A.png").write_bytes(b"BOXART")
    snap_dir = tmp_path / "thumbnails" / "Nintendo - SNES" / "Named_Snaps"
    snap_dir.mkdir()
    (snap_dir / "Game A.png").write_bytes(b"SNAP")

    playlist_doc = {
        "version": "1.5",
        "default_core_path": str(cores_dir / "snes9x_libretro.dylib"),
        "default_core_name": "Snes9x",
        "items": [
            {
                "path": str(roms_dir / "Game A.sfc"),
                "label": "Game A",
                "core_path": "DETECT",
                "core_name": "DETECT",
                "crc32": "00000000|crc",
                "db_name": "Nintendo - SNES.lpl",
            },
            {
                "path": str(roms_dir / "Game B.sfc") + "#submenu",
                "label": "Game B",
                "core_path": "DETECT",
                "core_name": "DETECT",
                "crc32": "00000000|crc",
                "db_name": "Nintendo - SNES.lpl",
            },
        ],
    }
    playlist_path = playlists_dir / "Nintendo - SNES.lpl"
    playlist_path.write_text(json.dumps(playlist_doc))

    default = {
        "transport": "filesystem",
        "src_playlists": str(playlists_dir),
        "src_roms": [str(tmp_path / "roms")],
        "src_cores": str(cores_dir),
        "src_cores_suffix": ".dylib",
        "target_cores": "/deck/cores",
        "target_cores_suffix": ".so",
        "target_roms": "/deck/roms",
        "dest_steam_dir": str(tmp_path / "steam"),
        "steam_launchers_dir": "/deck/launchers",
        "src_thumbnails": str(tmp_path / "thumbnails"),
    }
    playlist = {
        "name": "Nintendo - SNES.lpl",
        "src_folder": "Nintendo - SNES",
        "dest_folder": "snes",
        "src_core_path": "snes9x_libretro",
        "src_core_name": "Nintendo - SNES (Snes9x)",
    }
    return {"tmp_path": tmp_path, "default": default, "playlist": playlist}


def _config_dir(env):
    return Path(env["default"]["dest_steam_dir"]) / "userdata" / ACCOUNT / "config"


def _seeded_transport(env, shortcuts_doc=None, namespace_data=None):
    steam_root = Path(env["default"]["dest_steam_dir"])
    config_dir = _config_dir(env)
    cloud_dir = config_dir / "cloudstorage"
    files = {
        Path(env["default"].get("steam_launchers_dir", "/deck/launchers"))
        / "retroarch.sh": b"#!/bin/sh",
        config_dir / "localconfig.vdf": vdf.dumps(
            {
                "UserLocalConfigStore": {
                    "Software": {
                        "Valve": {
                            "Steam": {
                                "user-collections": json.dumps(
                                    {"favorite": {"id": "favorite", "added": [7], "removed": []}},
                                    separators=(",", ":"),
                                )
                            }
                        }
                    }
                }
            }
        ).encode("utf-8"),
    }
    if shortcuts_doc is not None:
        files[config_dir / "shortcuts.vdf"] = steam.write_shortcuts_vdf(shortcuts_doc)
    if namespace_data is not None:
        files[cloud_dir / "cloud-storage-namespaces.json"] = steam.dumps_json([[1, "798"]])
        files[cloud_dir / "cloud-storage-namespace-1.json"] = steam.dumps_json(namespace_data)
    dirs = {steam_root / "userdata": [ACCOUNT]}
    return DummySteamTransport(files=files, dirs=dirs)


def _expected_shortcut(label, rom_name):
    core = "/deck/cores/snes9x_libretro.so"
    rom = f"/deck/roms/snes/{rom_name}"
    unsigned, shortcut = steam.build_shortcut(
        label,
        f'"/deck/launchers/retroarch.sh" -L {core} "{rom}"',
        "",
        "/deck/launchers",
        ["RetroSync / Nintendo - SNES"],
    )
    return unsigned, shortcut


def test_steam_sync_reconciles_shortcuts_collections_and_artwork(steam_env):
    env = steam_env
    stale_unsigned, stale = _expected_shortcut("Old Game", "old.sfc")
    foreign = {
        "appid": 111,
        "AppName": "EmuDeck Game",
        "Exe": '"/home/deck/Emulation/tools/launchers/retroarch.sh"',
        "tags": {"0": "Arcade FBNeo"},
    }
    existing = {"shortcuts": {"0": foreign, "1": dict(stale)}}
    namespace = [
        [
            "user-collections.srm-SUPER",
            {
                "key": "user-collections.srm-SUPER",
                "timestamp": 100,
                "value": json.dumps(
                    {"id": "srm-SUPER", "name": "Arcade FBNeo", "added": [777], "removed": []}
                ),
                "version": "100",
                "conflictResolutionMethod": "custom",
                "strMethodId": "union-collections",
            },
        ],
        [
            "user-collections.retrosync-RetroSync",
            {
                "key": "user-collections.retrosync-RetroSync",
                "timestamp": 100,
                "value": json.dumps(
                    {
                        "id": "retrosync-RetroSync",
                        "name": "RetroSync",
                        "added": [stale_unsigned],
                        "removed": [],
                    }
                ),
                "version": "100",
                "conflictResolutionMethod": "custom",
                "strMethodId": "union-collections",
            },
        ],
    ]
    transport = _seeded_transport(env, shortcuts_doc=existing, namespace_data=namespace)
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])

    unsigned_a, expected_a = _expected_shortcut("Game A", "Game A.sfc")
    unsigned_b, expected_b = _expected_shortcut("Game B", "Game B.sfc")
    assert job.size == 3  # two shortcuts + write step
    job.do()

    written = transport.written_files()
    config_dir = _config_dir(env)
    cloud_dir = config_dir / "cloudstorage"
    grid_dir = config_dir / "grid"

    # Shortcuts: foreign preserved, stale pruned, both games present.
    new_doc = steam.read_shortcuts_vdf(written[config_dir / "shortcuts.vdf"])
    entries = list(new_doc["shortcuts"].values())
    assert foreign in entries
    assert expected_a in entries
    assert expected_b in entries
    assert len(entries) == 3
    # Backup of the original file was written before the update.
    assert written[config_dir / "shortcuts.vdf.retrosync-bak"] == steam.write_shortcuts_vdf(
        existing
    )

    # Collections: per-system collection created; the umbrella (disabled by
    # default) was emptied by the stale removal and deleted; SRM untouched.
    merged = json.loads(written[cloud_dir / "cloud-storage-namespace-1.json"])
    by_name = {}
    for item in merged:
        if str(item[0]).startswith("user-collections."):
            value = json.loads(item[1]["value"])
            by_name[value["name"]] = value
    assert "RetroSync" not in by_name
    assert set(by_name["RetroSync / Nintendo - SNES"]["added"]) == {unsigned_a, unsigned_b}
    srm_entry = [item for item in merged if item[0] == "user-collections.srm-SUPER"][0]
    assert srm_entry[1]["timestamp"] == 100
    assert written[cloud_dir / "cloud-storage-namespace-1.json.retrosync-bak"] == steam.dumps_json(
        namespace
    )

    # Artwork: boxart -> tall cover, snap -> hero; missing art tolerated.
    assert written[grid_dir / f"{unsigned_a}p.png"] == b"BOXART"
    assert written[grid_dir / f"{unsigned_a}_hero.png"] == b"SNAP"
    assert not [dest for dest in written if dest.name.startswith(f"{unsigned_b}")]

    messages = job.consume_deferred_messages()
    assert len(messages) == 1
    assert "2 added" in messages[0]
    assert "1 removed" in messages[0]
    assert job.consume_final_deferred_messages() == []


def test_steam_sync_is_idempotent_on_second_run(steam_env):
    env = steam_env
    transport = _seeded_transport(env, shortcuts_doc=None, namespace_data=[])
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    assert transport.copies

    second = DummySteamTransport(
        dirs={Path(env["default"]["dest_steam_dir"]) / "userdata": [ACCOUNT]}
    )
    second.files[Path(env["default"]["steam_launchers_dir"]) / "retroarch.sh"] = b"#!/bin/sh"
    for data, dest in transport.copies:
        if dest.name != "cloud-storage-namespaces.json":
            second.files[dest] = data

    job2 = SteamLibrarySync(env["default"], second)
    job2.setup(env["playlist"])
    job2.do()
    assert second.copies == []
    message = job2.consume_deferred_messages()[0]
    assert "0 added" in message and "0 removed" in message


def test_steam_sync_dry_run_writes_nothing(steam_env):
    env = steam_env
    transport = _seeded_transport(env, shortcuts_doc=None, namespace_data=[])
    transport.dry_run = True
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    assert transport.copies == []
    assert transport.created_dirs == []
    message = job.consume_deferred_messages()[0]
    assert "Would update" in message
    assert "2 added" in message


def test_finalize_reports_visibility_without_restarting_steam(steam_env):
    from retrosync_core.jobs import reset_steam_lifecycle_state

    env = steam_env
    transport = _seeded_transport(env)
    transport.steam_running = True
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    reset_steam_lifecycle_state()
    job.finalize()
    final = job.consume_final_deferred_messages()
    assert any("cloud-managed" in message for message in final)
    assert not [cmd for cmd in transport.captured if "steam -shutdown" in cmd]
    assert not [cmd for cmd in transport.captured if "systemctl reboot" in cmd]
    # Second finalize is a no-op (once per run).
    job.finalize()
    assert job.consume_final_deferred_messages() == []


def test_finalize_reboots_deck_when_requested(steam_env):
    from retrosync_core.jobs import reset_steam_lifecycle_state

    env = steam_env
    env["default"]["steam_reboot_deck"] = True
    transport = _seeded_transport(env)
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    reset_steam_lifecycle_state()
    job.finalize()
    assert [cmd for cmd in transport.captured if "systemctl reboot" in cmd]
    final = job.consume_final_deferred_messages()
    assert any("reboot requested" in message for message in final)


def test_finalize_skipped_on_dry_run(steam_env):
    from retrosync_core.jobs import reset_steam_lifecycle_state

    env = steam_env
    transport = _seeded_transport(env)
    transport.dry_run = True
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    reset_steam_lifecycle_state()
    job.finalize()
    assert job.consume_final_deferred_messages() == []


def test_steam_sync_requires_explicit_account_when_ambiguous(steam_env):
    env = steam_env
    steam_root = Path(env["default"]["dest_steam_dir"])
    transport = DummySteamTransport(dirs={steam_root / "userdata": ["111", "222"]})
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    with pytest.raises(ValueError, match="Multiple Steam accounts"):
        job.do()


def test_steam_sync_uses_configured_steam_user(steam_env):
    env = steam_env
    env["default"]["steam_user"] = "999"
    transport = _seeded_transport(env)
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    config_dir = Path(env["default"]["dest_steam_dir"]) / "userdata" / "999" / "config"
    assert config_dir / "shortcuts.vdf" in transport.written_files()


def test_steam_sync_honors_category_override_and_prefix(steam_env):
    env = steam_env
    env["playlist"]["steam_category"] = "SNES Games"
    env["default"]["steam_collection_prefix"] = "RS"
    transport = _seeded_transport(env)
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    new_doc = steam.read_shortcuts_vdf(
        transport.written_files()[_config_dir(env) / "shortcuts.vdf"]
    )
    tags = list(new_doc["shortcuts"].values())[0]["tags"]
    assert sorted(tags.values()) == ["RS / SNES Games"]


def test_steam_sync_opt_in_umbrella_collection(steam_env):
    env = steam_env
    env["default"]["steam_umbrella_collection"] = True
    transport = _seeded_transport(env, namespace_data=[])
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    new_doc = steam.read_shortcuts_vdf(
        transport.written_files()[_config_dir(env) / "shortcuts.vdf"]
    )
    tags = list(new_doc["shortcuts"].values())[0]["tags"]
    assert sorted(tags.values()) == ["RetroSync", "RetroSync / Nintendo - SNES"]
    merged = json.loads(
        transport.written_files()[
            _config_dir(env) / "cloudstorage" / "cloud-storage-namespace-1.json"
        ]
    )
    names = {
        json.loads(item[1]["value"])["name"]
        for item in merged
        if str(item[0]).startswith("user-collections.")
    }
    assert names == {"RetroSync", "RetroSync / Nintendo - SNES"}


def test_steam_sync_fails_when_launcher_script_is_missing(steam_env):
    env = steam_env
    transport = _seeded_transport(env)
    transport.files.pop(Path(env["default"]["steam_launchers_dir"]) / "retroarch.sh")
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    with pytest.raises(ValueError, match="retroarch.sh' not found on the target"):
        job.do()


def _written_entry(transport, config_dir, label):
    doc = steam.read_shortcuts_vdf(transport.written_files()[config_dir / "shortcuts.vdf"])
    for entry in doc["shortcuts"].values():
        if entry.get("appname") == label:
            return entry
    raise AssertionError(f"entry {label!r} not written")


def test_steam_sync_uses_standalone_emulator_launcher(steam_env):
    env = steam_env
    env["playlist"]["steam_emulator"] = "duckstation"
    transport = _seeded_transport(env)
    launcher = Path(env["default"]["steam_launchers_dir"]) / "duckstation.sh"
    transport.files[launcher] = b"#!/bin/sh"
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    entry = _written_entry(transport, _config_dir(env), "Game A")
    assert entry["exe"] == (
        '"/deck/launchers/duckstation.sh" -batch -fullscreen "/deck/roms/snes/Game A.sfc"'
    )
    assert entry["StartDir"] == "/deck/launchers"
    assert entry["LaunchOptions"] == ""


def test_steam_sync_uses_custom_launch_command(steam_env):
    env = steam_env
    env["playlist"]["steam_launch_command"] = 'my-wrapper.sh --core {core} "{path}"'
    transport = _seeded_transport(env)
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    entry = _written_entry(transport, _config_dir(env), "Game A")
    assert entry["exe"] == (
        '"/deck/launchers/my-wrapper.sh" --core /deck/cores/snes9x_libretro.so '
        '"/deck/roms/snes/Game A.sfc"'
    )


def test_filesystem_transport_reads_files_and_lists_dirs(tmp_path):
    target = tmp_path / "data.txt"
    target.write_bytes(b"payload")
    transport = TransportFileSystemUnix({"transport": "filesystem"}, dry_run=False)
    assert transport.read_file(target) == b"payload"
    assert transport.list_dir(tmp_path) == ["data.txt"]
    with pytest.raises(FileNotFoundError):
        transport.read_file(tmp_path / "missing.txt")


def test_ssh_transport_read_file_list_dir_and_exists(mocker):
    transport = TransportSSHUnix(
        {"transport": "ssh", "hostname": "deck", "username": "deck", "password": "deck"},
        dry_run=False,
    )

    def fake_run_capture(cmd, timeout=120):
        _ = timeout
        if "base64" in cmd:
            return 0, base64.b64encode(b"payload"), b""
        if "ls -1" in cmd:
            return 0, b"one\ntwo\n", b""
        if "test -e" in cmd:
            return 1, b"", b""
        raise AssertionError(f"unexpected cmd: {cmd}")

    mocker.patch.object(transport, "run_capture", side_effect=fake_run_capture)
    assert transport.read_file(Path("/remote/file")) == b"payload"
    assert transport.list_dir(Path("/remote/dir")) == ["one", "two"]
    assert transport.remote_file_exists(Path("/remote/missing")) is False


def test_transport_unix_run_capture_returns_output():
    transport = TransportUnixBase.__new__(TransportFileSystemUnix)
    returncode, stdout, _ = transport.run_capture("echo hello")
    assert returncode == 0
    assert stdout.strip() == b"hello"
    returncode, _, stderr = transport.run_capture("ls /definitely-missing-dir-42")
    assert returncode != 0
    assert stderr


def test_migrated_playlist_rewrites_symlinked_source_roots(tmp_path):
    from retrosync_core.jobs import build_migrated_playlist

    real_root = tmp_path / "Library" / "CloudStorage" / "Roms"
    (real_root / "MySystem").mkdir(parents=True)
    (real_root / "MySystem" / "Game.zip").write_bytes(b"ROM")
    cloud_root = tmp_path / "Dropbox" / "Roms"
    cloud_root.parent.mkdir(parents=True)
    cloud_root.symlink_to(real_root, target_is_directory=True)
    cores = tmp_path / "cores"
    cores.mkdir()
    playlists = tmp_path / "playlists"
    playlists.mkdir()
    # Playlist references the symlink path, like RetroArch does on macOS.
    (playlists / "MySystem.lpl").write_text(
        json.dumps(
            {
                "default_core_path": str(cloud_root.parent / "core.dylib"),
                "items": [{"path": str(cloud_root / "MySystem" / "Game.zip"), "label": "Game"}],
            }
        )
    )
    (cloud_root.parent / "core.dylib").write_bytes(b"")
    default = {
        "src_playlists": str(playlists),
        "src_cores": str(cloud_root.parent),
        "src_cores_suffix": ".dylib",
        "target_cores": "/deck/cores",
        "target_cores_suffix": ".so",
        "target_roms": "/deck/roms",
    }
    data = build_migrated_playlist(
        default,
        {"name": "MySystem.lpl", "src_folder": "MySystem", "dest_folder": "mysystem"},
        "MySystem.lpl",
        [cloud_root],
    )
    assert data["items"][0]["path"] == "/deck/roms/mysystem/Game.zip"
    assert data["default_core_path"] == "/deck/cores/core.so"


def _shortcut_entry(label, exe, tags, launch_options=""):
    unsigned, signed = steam.compute_appids(exe, label)
    return unsigned, {
        "appid": signed,
        "appname": label,
        "exe": exe,
        "StartDir": "/deck/launchers",
        "LaunchOptions": launch_options,
        "icon": "",
        "tags": {str(idx): tag for idx, tag in enumerate(tags)},
    }


def test_reconcile_prunes_entries_with_source_machine_paths():
    zombie_unsigned, zombie = _shortcut_entry(
        "Mac Path Game",
        '"/usr/bin/flatpak"',
        ["RetroSync / Nintendo - SNES"],
        '-L c "/Users/david/Dropbox/roms/snes/game.sfc"',
    )
    keep_unsigned, keep = _shortcut_entry(
        "Other System Game",
        '"/usr/bin/flatpak"',
        ["RetroSync / Mega Drive"],
        '-L c "/deck/roms/megadrive/game.md"',
    )
    desired_unsigned, desired = _shortcut_entry(
        "Game A", '"/deck/launchers/retroarch.sh"', [SYSTEM]
    )
    new_doc, report = steam.reconcile_shortcuts(
        {"shortcuts": {"0": dict(zombie), "1": dict(keep)}},
        {desired_unsigned: desired},
        rom_dir_prefix=ROM_PREFIX,
        prefix=PREFIX,
        roms_base="/deck/roms",
    )
    assert report.removed == ["Mac Path Game"]
    assert report.removed_appids == {zombie_unsigned}
    entries = list(new_doc["shortcuts"].values())
    assert keep in entries
    assert desired in entries
    assert zombie not in entries


ROM_PREFIX = "/deck/roms/snes"
PREFIX = "RetroSync"
SYSTEM = "RetroSync / Nintendo - SNES"


def test_steam_sync_drops_ghost_collection_members(steam_env):
    env = steam_env
    ghost_id = 987654321
    stale_unsigned, stale = _expected_shortcut("Old Game", "old.sfc")
    existing = {"shortcuts": {"0": dict(stale)}}
    system_key = f"user-collections.{steam.collection_key_for('RetroSync / Nintendo - SNES')}"
    namespace = [
        [
            system_key,
            {
                "key": system_key,
                "timestamp": 100,
                "value": json.dumps(
                    {
                        "id": system_key.removeprefix("user-collections."),
                        "name": "RetroSync / Nintendo - SNES",
                        "added": [stale_unsigned, ghost_id],
                        "removed": [],
                    }
                ),
                "version": "100",
                "conflictResolutionMethod": "custom",
                "strMethodId": "union-collections",
            },
        ],
    ]
    transport = _seeded_transport(env, shortcuts_doc=existing, namespace_data=namespace)
    job = SteamLibrarySync(env["default"], transport)
    job.setup(env["playlist"])
    job.do()
    merged = json.loads(
        transport.written_files()[
            _config_dir(env) / "cloudstorage" / "cloud-storage-namespace-1.json"
        ]
    )
    value = [json.loads(item[1]["value"]) for item in merged if item[0] == system_key][0]
    unsigned_a, _ = _expected_shortcut("Game A", "Game A.sfc")
    unsigned_b, _ = _expected_shortcut("Game B", "Game B.sfc")
    assert set(value["added"]) == {unsigned_a, unsigned_b}
