from retrosync import match_system


def test_match_system_exact_match():
    playlists = [
        {"name": "NES", "dest_folder": "nes"},
        {"name": "SNES", "dest_folder": "snes"},
    ]
    assert match_system("NES", playlists) == "NES"
    assert match_system("SNES", playlists) == "SNES"


def test_match_system_partial_match():
    playlists = [
        {"name": "NES", "dest_folder": "nes"},
        {"name": "SNES", "dest_folder": "snes"},
    ]
    assert match_system("NE", playlists) == "NES"
    assert match_system("SNE", playlists) == "SNES"


def test_match_system_disabled_playlist():
    playlists = [
        {"name": "ATARI", "dest_folder": "2600"},
        {"name": "SNES", "dest_folder": "snes", "disabled": True},
    ]
    assert match_system("SNES", playlists) is None
    assert match_system("2600", playlists) == "ATARI"


def test_match_system_levenshtein_distance():
    playlists = [
        {"name": "NES", "dest_folder": "nes"},
        {"name": "SNES", "dest_folder": "snes"},
        {"name": "Mega Drive", "dest_folder": "megadrive"},
    ]
    assert match_system("Mega", playlists) == "Mega Drive"
    assert match_system("Drive", playlists) == "Mega Drive"
