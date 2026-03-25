import json
from pathlib import Path


DEFAULT_APP_STATE_PATH = Path(".cache") / "retrosync" / "gui-state.json"

PERSISTED_RUN_SETUP_FIELDS = [
    "do_sync_playlists",
    "do_sync_bios",
    "do_sync_favorites",
    "do_sync_thumbnails",
    "do_sync_roms",
    "do_sync_shaders",
    "do_update_playlists",
    "do_update_thumbnails",
    "apply_changes",
    "refresh_thumbnail_cache",
    "no_thumbnail_cache",
    "do_debug",
    "force_transport",
]


def load_app_state(path):
    state_path = Path(path)
    if not state_path.is_file():
        return {}
    try:
        return json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def save_app_state(path, payload):
    state_path = Path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
