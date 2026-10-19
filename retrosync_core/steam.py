"""Native writer for Steam non-Steam shortcuts, collections and grid artwork.

The formats mirror what Steam ROM Manager writes so both tools can coexist on
the same Steam installation:

- ``shortcuts.vdf`` uses the binary VDF variant whose maps terminate with 0x08
  (the default of the ``vdf`` package, byte-identical on round-trip).
- Shortcut appids are ``crc32(exe + appname) | 0x80000000``, stored signed.
- Collection membership lives in the cloud-storage namespace JSON next to
  ``localconfig.vdf``; entries are merged with Steam's ``union-collections``
  semantics (SRM's category-manager is the reference).

Collections created here use the ``retrosync-`` id prefix: SRM manages the
``srm-`` prefix and marks unknown ``srm-`` collections deleted on its own
writes, while Steam uses opaque ``uc-`` ids. This module only ever modifies
entries it owns, so EmuDeck/SRM and Steam collections are preserved verbatim.
"""

import base64
import json
import zlib
from pathlib import Path

import vdf

DEFAULT_COLLECTION_PREFIX = "RetroSync"
DEFAULT_LAUNCHERS_DIR = "/home/deck/Emulation/tools/launchers"
DEFAULT_EMULATOR = "retroarch"

# Launcher scripts and argument templates mirroring how EmuDeck's Steam
# entries start games (verified against a live Deck): everything routes
# through ~/Emulation/tools/launchers/<script>.sh with the ROM as an argument,
# and the full command lives in the shortcut's Exe field.
EMULATOR_LAUNCHERS = {
    "retroarch": ("retroarch.sh", '-L {core} "{path}"'),
    "dolphin": ("dolphin-emu.sh", '-b -e "{path}"'),
    "duckstation": ("duckstation.sh", '-batch -fullscreen "{path}"'),
    "flycast": ("flycast.sh", '"{path}"'),
    "pcsx2": ("pcsx2-qt.sh", '-batch -fullscreen "{path}"'),
    "ppsspp": ("ppsspp.sh", '-f -g "{path}"'),
    "mupen": ("rosaliesmupengui.sh", '"{path}"'),
    "rpcs3": ("rpcs3.sh", '--no-gui "{path}"'),
    "ryujinx": ("ryujinx.sh", '--fullscreen "{path}"'),
    "supermodel": ("supermodel.sh", '"{path}"'),
}

SHORTCUTS_FILENAME = "shortcuts.vdf"
LOCALCONFIG_FILENAME = "localconfig.vdf"
GRID_DIRNAME = "grid"
CLOUDSTORAGE_DIRNAME = "cloudstorage"
NAMESPACES_FILENAME = "cloud-storage-namespaces.json"
COLLECTION_KEY_PREFIX = "retrosync-"
USER_COLLECTIONS_KEY = "user-collections"
BACKUP_SUFFIX = ".retrosync-bak"

U32_MASK = 0xFFFFFFFF


def compute_appids(exe, appname):
    """Return (unsigned, signed) shortcut appid for an Exe string and title.

    Matches SRM's generate-app-id: crc32 over ``exe + appname`` with the high
    bit forced on. ``exe`` must be the exact Exe field value, quotes included.
    """
    unsigned = zlib.crc32((exe + appname).encode("utf-8")) | 0x80000000
    return unsigned, unsigned - (U32_MASK + 1)


def read_shortcuts_vdf(data):
    if not data:
        return {"shortcuts": {}}
    doc = vdf.binary_loads(data)
    if not isinstance(doc.get("shortcuts"), dict):
        doc["shortcuts"] = {}
    return doc


def write_shortcuts_vdf(doc):
    return vdf.binary_dumps(doc)


def shortcut_field(entry, name, default=None):
    """Case-insensitive field lookup; Steam writes ``AppName``, SRM ``appname``."""
    for key, value in entry.items():
        if key.lower() == name.lower():
            return value
    return default


def entry_tags(entry):
    tags = shortcut_field(entry, "tags", {})
    return [str(value) for value in tags.values()] if isinstance(tags, dict) else []


def is_owned_entry(entry, prefix):
    return any(tag.startswith(prefix) for tag in entry_tags(entry))


def collection_names_for(system_name, prefix, umbrella=True):
    umbrella_name = str(prefix).strip() or DEFAULT_COLLECTION_PREFIX
    system = str(system_name).strip()
    names = []
    if umbrella:
        names.append(umbrella_name)
    if system and system != umbrella_name:
        names.append(f"{umbrella_name} / {system}")
    elif not umbrella and system:
        names.append(system)
    return names


def build_shortcut(label, exe, launch_options, start_dir, tags):
    unsigned, signed = compute_appids(exe, label)
    tag_map = {str(idx): name for idx, name in enumerate(dict.fromkeys(tags))}
    shortcut = {
        "appid": signed,
        "appname": str(label),
        "exe": str(exe),
        "StartDir": str(start_dir),
        "LaunchOptions": str(launch_options),
        "icon": "",
        "tags": tag_map,
    }
    return unsigned, shortcut


def build_launch_command(emulator, launchers_dir, core_path, rom_path, custom_command=None):
    """Return (exe field, StartDir) for a shortcut.

    With ``custom_command`` the template's first token names the launcher
    script (e.g. ``retroarch.sh -L {core} "{path}"``); otherwise the
    emulator's built-in template from ``EMULATOR_LAUNCHERS`` is used.
    """
    directory = str(launchers_dir).strip().rstrip("/") or DEFAULT_LAUNCHERS_DIR
    if custom_command:
        rendered = (
            str(custom_command)
            .replace("{core}", str(core_path))
            .replace("{path}", str(rom_path))
            .strip()
        )
        parts = rendered.split(None, 1)
        if not parts:
            raise ValueError("steam_launch_command must not be empty.")
        script, args = parts[0], parts[1] if len(parts) > 1 else ""
    else:
        key = str(emulator or DEFAULT_EMULATOR).strip().lower()
        if key not in EMULATOR_LAUNCHERS:
            raise ValueError(
                f"Unknown steam_emulator '{key}'. "
                f"Use one of: {', '.join(sorted(EMULATOR_LAUNCHERS))}."
            )
        script, args_template = EMULATOR_LAUNCHERS[key]
        args = args_template.replace("{core}", str(core_path)).replace("{path}", str(rom_path))
    exe_field = f'"{directory}/{script}"' + (f" {args}" if args else "")
    return exe_field, directory


def launcher_script_for(emulator, launchers_dir):
    key = str(emulator or DEFAULT_EMULATOR).strip().lower()
    if key not in EMULATOR_LAUNCHERS:
        raise ValueError(
            f"Unknown steam_emulator '{key}'. Use one of: {', '.join(sorted(EMULATOR_LAUNCHERS))}."
        )
    directory = str(launchers_dir).strip().rstrip("/") or DEFAULT_LAUNCHERS_DIR
    return Path(directory) / EMULATOR_LAUNCHERS[key][0]


class ReconcileReport:
    def __init__(self):
        self.added = []
        self.updated = []
        self.unchanged = []
        self.removed = []
        self.removed_appids = set()
        self.skipped_foreign = []

    def has_changes(self):
        return bool(self.added or self.updated or self.removed or self.skipped_foreign)

    def summary(self):
        parts = [f"{len(self.added)} added", f"{len(self.updated)} updated"]
        parts.append(f"{len(self.removed)} removed")
        if self.skipped_foreign:
            parts.append(f"{len(self.skipped_foreign)} skipped (foreign appid)")
        return ", ".join(parts)


def reconcile_shortcuts(existing_doc, desired, *, rom_dir_prefix, prefix, roms_base=None):
    """Merge desired shortcuts into an existing shortcuts document.

    ``desired`` maps unsigned appid to a shortcut dict built by
    ``build_shortcut``. Entries are "ours" when tagged with a collection name
    starting with ``prefix``; foreign entries are preserved byte-for-byte
    (their dict is passed through untouched). Our entries whose command
    references a ROM below ``rom_dir_prefix`` are pruned when no longer
    desired; with ``roms_base`` (the target's ROM root) our entries that
    reference no target ROM at all — e.g. leftovers written with source
    machine paths — are pruned too, while other playlists' entries (whose ROMs
    live somewhere under ``roms_base``) are kept. Desired appids that collide
    with a foreign entry are skipped.
    """
    report = ReconcileReport()
    shortcuts = existing_doc.get("shortcuts", {})
    rom_prefix = str(rom_dir_prefix)
    roms_base = str(roms_base) if roms_base else None

    foreign = []
    owned = []
    for key in shortcuts:
        entry = shortcuts[key]
        if is_owned_entry(entry, prefix):
            owned.append(entry)
        else:
            foreign.append(entry)

    foreign_appids = set()
    for entry in foreign:
        appid = shortcut_field(entry, "appid")
        if isinstance(appid, int):
            foreign_appids.add(appid & U32_MASK)

    merged = []
    for entry in owned:
        appid = shortcut_field(entry, "appid")
        unsigned = appid & U32_MASK if isinstance(appid, int) else None
        command = "{exe} {options}".format(
            exe=str(shortcut_field(entry, "exe", "")),
            options=str(shortcut_field(entry, "LaunchOptions", "")),
        )
        in_this_system = bool(rom_prefix) and rom_prefix in command
        orphaned = bool(roms_base) and roms_base not in command
        if unsigned in desired:
            if in_this_system:
                merged.append(desired[unsigned])
                if _managed_fields_equal(entry, desired[unsigned]):
                    report.unchanged.append(desired[unsigned]["appname"])
                else:
                    report.updated.append(desired[unsigned]["appname"])
            else:
                # Same appid managed by another playlist; keep desired version.
                merged.append(desired[unsigned])
                report.updated.append(desired[unsigned]["appname"])
        elif (in_this_system or orphaned) and unsigned is not None:
            report.removed.append(str(shortcut_field(entry, "appname", "")))
            report.removed_appids.add(unsigned)
        else:
            # Owned by another system (different rom tree); keep untouched.
            merged.append(entry)

    merged_appids = set()
    for entry in merged:
        appid = shortcut_field(entry, "appid")
        if isinstance(appid, int):
            merged_appids.add(appid & U32_MASK)

    for unsigned in sorted(desired):
        shortcut = desired[unsigned]
        if unsigned in foreign_appids:
            report.skipped_foreign.append(shortcut["appname"])
            continue
        if unsigned not in merged_appids:
            merged.append(shortcut)
            merged_appids.add(unsigned)
            report.added.append(shortcut["appname"])

    new_doc = {"shortcuts": {str(idx): entry for idx, entry in enumerate(foreign + merged)}}
    return new_doc, report


def _managed_fields_equal(entry, desired):
    for field in ("appname", "exe", "StartDir", "LaunchOptions"):
        if str(shortcut_field(entry, field, "")) != str(desired.get(field, "")):
            return False
    return True


def collection_key_for(name):
    encoded = base64.urlsafe_b64encode(str(name).encode("utf-8")).decode("ascii")
    return COLLECTION_KEY_PREFIX + encoded.rstrip("=")


def select_namespace(namespaces_data):
    """Pick the active cloud-storage namespace like SRM: highest version wins."""
    if not namespaces_data:
        return 1
    try:
        ranked = sorted(
            namespaces_data,
            key=lambda item: int(item[1]),
            reverse=True,
        )
    except (TypeError, ValueError, IndexError):
        return 1
    if not ranked or str(ranked[0][1]) == "0":
        return 1
    try:
        return int(ranked[0][0])
    except (TypeError, ValueError):
        return 1


def _parse_collection_value(meta):
    try:
        value = json.loads(meta.get("value", "{}"))
    except (TypeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    value.setdefault("added", [])
    value.setdefault("removed", [])
    return value


def find_collection_slots(namespace_data, *, prefix):
    """Map collection name -> slot index for entries this tool owns.

    A slot is ours when its key uses the ``retrosync-`` prefix or its parsed
    collection name starts with the configured prefix.
    """
    slots = {}
    for index, item in enumerate(namespace_data):
        if not isinstance(item, list) or len(item) < 2:
            continue
        key = str(item[0])
        meta = item[1] if isinstance(item[1], dict) else {}
        if key.startswith("user-collections.retrosync-"):
            owned = True
        else:
            value = _parse_collection_value(meta)
            owned = value is not None and str(value.get("name", "")).startswith(prefix)
        if not owned:
            continue
        value = _parse_collection_value(meta)
        if value is not None and value.get("name"):
            slots[str(value["name"])] = index
    return slots


def collection_membership(namespace_data, name):
    """Return the current ``added`` appid set of a collection, if present."""
    for item in namespace_data or []:
        if not isinstance(item, list) or len(item) < 2:
            continue
        meta = item[1] if isinstance(item[1], dict) else {}
        value = _parse_collection_value(meta)
        if value is not None and str(value.get("name", "")) == str(name):
            return {int(appid) for appid in value.get("added", []) if isinstance(appid, int)}
    return set()


def merge_collection_namespace(namespace_data, collections, *, prefix, timestamp):
    """Write ``collections`` (name -> member unsigned appids) into the namespace.

    Only entries owned by this tool are modified (see ``find_collection_slots``);
    every other entry — Steam's own, SRM's, or unknown — is preserved as-is.
    ``timestamp`` is unix seconds used for the entry version fields.
    """
    data = list(namespace_data) if namespace_data else []
    slots = find_collection_slots(data, prefix=prefix)

    for name in sorted(collections):
        members = sorted({int(appid) for appid in collections[name]})
        key = None
        collection_id = None
        previous_removed = []
        index = slots.get(str(name))
        if index is not None:
            item = data[index]
            key = str(item[0])
            value = _parse_collection_value(item[1]) or {}
            collection_id = str(value.get("id") or key.removeprefix("user-collections."))
            previous_removed = [int(a) for a in value.get("removed", []) if isinstance(a, int)]
        if key is None:
            key = f"user-collections.{collection_key_for(name)}"
            collection_id = key.removeprefix("user-collections.")
        value = {
            "id": collection_id,
            "name": str(name),
            "added": members,
            "removed": sorted(set(previous_removed) - set(members)),
        }
        entry = [
            key,
            {
                "key": key,
                "timestamp": int(timestamp),
                "value": json.dumps(value),
                "version": str(int(timestamp)),
                "conflictResolutionMethod": "custom",
                "strMethodId": "union-collections",
            },
        ]
        if index is not None:
            data[index] = entry
        else:
            data.append(entry)
    return data


def localconfig_has_user_collection(localconfig_data, name):
    """True when localconfig.vdf's user-collections cache contains the collection."""
    if not localconfig_data:
        return False
    try:
        doc = vdf.loads(localconfig_data.decode("utf-8", "replace"))
    except ValueError:
        return False
    key = collection_key_for(name)

    def walk(node):
        if isinstance(node, dict):
            raw = node.get(USER_COLLECTIONS_KEY)
            if isinstance(raw, str):
                try:
                    return key in json.loads(raw)
                except ValueError:
                    return False
            return any(walk(value) for value in node.values())
        return False

    return walk(doc)


def merge_localconfig_user_collections(localconfig_data, collections):
    """Merge collections into localconfig.vdf's ``user-collections`` cache.

    The library UI materializes collections from this JSON blob (keyed by
    collection id like the srm- entries EmuDeck's SRM produces), not from the
    cloud-storage namespace file — writing the namespace alone can leave the
    collection invisible. Returns the new localconfig bytes, or None when
    nothing changed or the blob was not found.
    """
    if not localconfig_data:
        return None
    try:
        doc = vdf.loads(localconfig_data.decode("utf-8", "replace"))
    except ValueError:
        return None

    def merge_blob(value):
        try:
            blob = json.loads(value)
        except ValueError:
            return None
        if not isinstance(blob, dict):
            return None
        updated = False
        for name in sorted(collections):
            key = collection_key_for(name)
            members = sorted({int(appid) for appid in collections[name]})
            existing = blob.get(key)
            previous_removed = []
            if isinstance(existing, dict):
                previous_removed = [
                    int(a) for a in existing.get("removed", []) if isinstance(a, int)
                ]
                if sorted(existing.get("added", [])) == members:
                    continue
            blob[key] = {
                "id": key,
                "added": members,
                "removed": sorted(set(previous_removed) - set(members)),
            }
            updated = True
        if not updated:
            return None
        return json.dumps(blob, separators=(",", ":"))

    def walk(node):
        if isinstance(node, dict):
            raw = node.get(USER_COLLECTIONS_KEY)
            if isinstance(raw, str):
                merged = merge_blob(raw)
                if merged is not None:
                    node[USER_COLLECTIONS_KEY] = merged
                    return True
            for value in node.values():
                if walk(value):
                    return True
        return False

    if not walk(doc):
        return None
    return vdf.dumps(doc).encode("utf-8")


def remove_collection_members(namespace_data, name, appids):
    """Remove ``appids`` from a collection we own; drop it when left empty.

    Returns (new_data, changed). Unknown or foreign collections are returned
    untouched.
    """
    data = list(namespace_data) if namespace_data else []
    remove = {int(appid) for appid in appids}
    for index, item in enumerate(data):
        if not isinstance(item, list) or len(item) < 2:
            continue
        meta = item[1] if isinstance(item[1], dict) else {}
        value = _parse_collection_value(meta)
        if value is None or str(value.get("name", "")) != str(name):
            continue
        members = {int(a) for a in value.get("added", []) if isinstance(a, int)}
        if not (members & remove):
            return data, False
        remaining = sorted(members - remove)
        if not remaining:
            data.pop(index)
            return data, True
        value["added"] = remaining
        meta["value"] = json.dumps(value)
        data[index] = [item[0], meta]
        return data, True
    return data, False


def dumps_json(data):
    return json.dumps(data, separators=(",", ":")).encode("utf-8")
