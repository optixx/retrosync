import re
from pathlib import Path

import Levenshtein
from pydantic import BaseModel, ConfigDict, Field, ValidationError


class RuntimeConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    transport: str = "filesystem"
    hostname: str | None = None
    username: str | None = None
    password: str | None = None
    host: str | None = None
    src_roms: list[str] = Field(default_factory=list)
    src_playlists: str | None = None
    src_bios: str | None = None
    src_config: str | None = None
    src_thumbnails: str | None = None
    src_cores: str | None = None
    src_cores_suffix: str | None = None
    dest_playlists: str | None = None
    dest_roms: str | None = None
    dest_bios: str | None = None
    dest_config: str | None = None
    dest_thumbnails: str | None = None
    target_roms: str | None = None
    target_cores: str | None = None
    target_cores_suffix: str | None = None


class PlaylistConfigModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str
    src_folder: str | None = None
    dest_folder: str | None = None
    src_core_path: str | None = None
    src_core_name: str | None = None
    disabled: bool = False


def rank_system_matches(system_name, playlists, limit=8):
    if not system_name:
        return []

    def normalize(value):
        if value is None:
            return ""
        value = value.lower().replace(".lpl", "")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    def acronym(value):
        tokens = normalize(value).split()
        parts = []
        for token in tokens:
            if token.isdigit():
                parts.append(token)
            else:
                parts.append(token[0])
        return "".join(parts)

    needle = normalize(system_name)
    candidates = []
    for playlist in playlists:
        if playlist.get("disabled", False):
            continue
        playlist_name = playlist.get("name")
        n1 = normalize(playlist_name)
        n2 = normalize(playlist.get("dest_folder"))

        d1 = Levenshtein.distance(n1, needle, weights=(1, 1, 2))
        d2 = Levenshtein.distance(n2, needle, weights=(1, 1, 2))
        best_dist = min(d1, d2)
        best_len = min(max(len(n1), len(needle)), max(len(n2), len(needle)))
        ratio = best_dist / max(best_len, 1)

        rank = 99
        if needle == n1 or needle == n2:
            rank = 0
        elif needle in n1 or needle in n2:
            rank = 1
        else:
            needle_parts = needle.split()
            if needle_parts and all(part in n1 or part in n2 for part in needle_parts):
                rank = 2
            else:
                a1 = acronym(n1)
                a2 = acronym(n2)
                if needle in a1 or needle in a2:
                    rank = 3
                elif ratio <= 0.45:
                    rank = 4

        if rank < 99:
            candidates.append((rank, ratio, best_dist, playlist_name))
        else:
            candidates.append((5, ratio, best_dist, playlist_name))

    candidates.sort(key=lambda x: (x[0], x[1], x[2], x[3]))

    selected = []
    seen = set()
    for item in candidates:
        name = item[3]
        if name not in seen:
            selected.append(name)
            seen.add(name)
        if len(selected) >= limit:
            break
    return selected


def expand_config(default):
    default = dict(default)

    def apply_core_flavor_defaults():
        src_flavor = str(default.get("src_flavor", "")).strip().lower()
        target_flavor = str(default.get("target_flavor", "")).strip().lower()

        src_suffix_by_flavor = {
            "apple": ".dylib",
            "linux": ".so",
        }
        target_suffix_by_flavor = {
            "apple": ".framework",
            "linux": ".so",
        }

        if default.get("src_cores_suffix") is None and src_flavor in src_suffix_by_flavor:
            default["src_cores_suffix"] = src_suffix_by_flavor[src_flavor]

        if default.get("target_cores_suffix") is None and target_flavor in target_suffix_by_flavor:
            default["target_cores_suffix"] = target_suffix_by_flavor[target_flavor]

    def ensure_retroarch_paths(prefix):
        base_key = f"{prefix}_retroarch_base"
        base = default.get(base_key)
        if not base:
            return

        base_path = Path(base)
        derived = {
            f"{prefix}_playlists": base_path / "playlists",
            f"{prefix}_bios": base_path / "system",
            f"{prefix}_config": base_path / "config",
            f"{prefix}_cores": base_path / "cores",
            f"{prefix}_thumbnails": base_path / "thumbnails",
        }

        for key, value in derived.items():
            if default.get(key) is None:
                default[key] = str(value)

    ensure_retroarch_paths("src")
    ensure_retroarch_paths("dest")
    apply_core_flavor_defaults()

    src_roms = default.get("src_roms")
    if isinstance(src_roms, list):
        expanded_src_roms = [str(Path(item).expanduser()) for item in src_roms]
    else:
        expanded_src_roms = [str(Path(src_roms).expanduser())]
    default["src_roms"] = expanded_src_roms

    for item in [
        "src_playlists",
        "src_bios",
        "src_config",
        "src_cores",
        "src_thumbnails",
        "dest_playlists",
        "dest_bios",
        "dest_config",
        "dest_roms",
        "dest_thumbnails",
    ]:
        if default.get(item) is not None:
            default[item] = str(Path(default.get(item)).expanduser())
    return default


def normalize_transport_config(config, transport_override=None):
    def normalize_mode(value):
        mode = str(value).strip().lower()
        if mode not in {"filesystem", "ssh", "webdav"}:
            raise ValueError(
                f"Unsupported transport mode '{value}'. Use filesystem, ssh, or webdav."
            )
        return mode

    default = config.get("default", {})
    selected_mode = transport_override or default.get("transport", "filesystem")
    default["transport"] = normalize_mode(selected_mode)

    ssh = config.get("ssh", {})
    webdav = config.get("webdav", {})
    remote = config.get("remote", {})

    section_map = {
        "hostname": ssh,
        "username": ssh,
        "password": ssh,
    }
    for item, section in section_map.items():
        if item in section:
            default[item] = section.get(item)
        elif item in remote:
            default[item] = remote.get(item)
        elif item not in default:
            default[item] = ""

    if default["transport"] == "webdav":
        default["host"] = webdav.get("host", "")
        default["username"] = webdav.get("username", "")
        default["password"] = webdav.get("password", "")

    return default


def normalize_playlists(playlists):
    for playlist in playlists:
        if playlist.get("dest_folder") is None:
            playlist["dest_folder"] = playlist.get("src_folder")
    return playlists


def validate_runtime_config(
    default,
    playlists,
    *,
    do_sync_playlists: bool,
    do_sync_bios: bool,
    do_sync_favorites: bool,
    do_sync_thumbnails: bool,
    do_sync_roms: bool,
    do_update_playlists: bool,
):
    errors = []
    try:
        runtime = RuntimeConfigModel.model_validate(default)
    except ValidationError as exc:
        raise ValueError(str(exc)) from exc

    parsed_playlists = []
    for idx, playlist in enumerate(playlists, start=1):
        try:
            parsed_playlists.append(PlaylistConfigModel.model_validate(playlist))
        except ValidationError as exc:
            errors.append(f"[playlists][{idx}] {exc.errors()[0].get('msg', 'invalid entry')}")

    def require_default(key, reason):
        value = default.get(key)
        missing = value is None
        if isinstance(value, str):
            missing = value.strip() == ""
        elif isinstance(value, list):
            missing = len(value) == 0
        if missing:
            errors.append(f"[default] '{key}' is required for {reason}")

    def require_playlist_attr(attr, reason, allow_empty=False):
        for idx, playlist in enumerate(parsed_playlists, start=1):
            if playlist.disabled:
                continue
            value = getattr(playlist, attr)
            if value is None:
                errors.append(f"[playlists][{idx}] '{attr}' is required for {reason}")
            elif isinstance(value, str) and not allow_empty and value.strip() == "":
                errors.append(f"[playlists][{idx}] '{attr}' must not be empty for {reason}")

    if runtime.transport == "ssh":
        require_default("hostname", "SSH transport")
        require_default("username", "SSH transport")
        require_default("password", "SSH transport")
    elif runtime.transport == "webdav":
        require_default("host", "WebDAV transport")

    if do_sync_bios:
        require_default("src_bios", "--sync-bios")
        require_default("dest_bios", "--sync-bios")

    if do_sync_favorites:
        require_default("src_config", "--sync-favorites")
        require_default("dest_config", "--sync-favorites")
        require_default("target_roms", "--sync-favorites")
        require_default("target_cores", "--sync-favorites")
        require_default("src_cores", "--sync-favorites")
        require_default("src_cores_suffix", "--sync-favorites")
        require_default("target_cores_suffix", "--sync-favorites")
        require_playlist_attr("src_core_name", "--sync-favorites")
        require_playlist_attr("dest_folder", "--sync-favorites", allow_empty=True)

    needs_system_jobs = do_sync_playlists or do_sync_roms or do_update_playlists

    if do_sync_thumbnails:
        require_default("src_thumbnails", "--sync-thumbnails")
        require_default("dest_thumbnails", "--sync-thumbnails")

    if needs_system_jobs and parsed_playlists:
        require_playlist_attr("name", "system jobs")
        require_playlist_attr("src_folder", "system jobs", allow_empty=True)
        require_playlist_attr("dest_folder", "system jobs", allow_empty=True)

    if do_sync_roms and parsed_playlists:
        require_default("src_roms", "--sync-roms")
        require_default("dest_roms", "--sync-roms")

    if do_sync_playlists and parsed_playlists:
        require_default("src_playlists", "--sync-playlists")
        require_default("dest_playlists", "--sync-playlists")
        require_default("src_roms", "--sync-playlists")
        require_default("target_roms", "--sync-playlists")
        require_default("src_cores", "--sync-playlists")
        require_default("target_cores", "--sync-playlists")
        require_default("src_cores_suffix", "--sync-playlists")
        require_default("target_cores_suffix", "--sync-playlists")

    if do_update_playlists and parsed_playlists:
        require_default("src_playlists", "--update-playlists")
        require_default("src_roms", "--update-playlists")
        require_default("src_cores", "--update-playlists")
        require_default("src_cores_suffix", "--update-playlists")
        require_playlist_attr("src_core_path", "--update-playlists")
        require_playlist_attr("src_core_name", "--update-playlists")

    if errors:
        raise ValueError("Invalid configuration:\n- " + "\n- ".join(errors))
