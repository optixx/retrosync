from pathlib import Path


def expand_user_path(value):
    if value is None:
        return None
    return str(Path(value).expanduser())


def expand_user_path_list(value):
    if isinstance(value, list):
        items = value
    else:
        items = [value]
    return [expand_user_path(item) for item in items]


def normalize_webdav_remote_path(path_value, *, home=None):
    if isinstance(path_value, Path):
        path = path_value.as_posix()
    else:
        path = str(path_value).replace("\\", "/")

    home_path = (home or Path.home()).as_posix()

    if path.startswith(f"{home_path}/"):
        path = path[len(home_path) + 1 :]
    elif path == home_path:
        path = ""

    path = path.lstrip("/")
    return f"/{path}" if path else "/"


def retroarch_derived_paths(base, *, prefix):
    base_path = Path(base)
    return {
        f"{prefix}_playlists": base_path / "playlists",
        f"{prefix}_bios": base_path / "system",
        f"{prefix}_config": base_path / "config",
        f"{prefix}_cores": base_path / "cores",
        f"{prefix}_thumbnails": base_path / "thumbnails",
    }
