from retrosync import rank_system_matches


def test_rank_system_matches_exact_match():
    playlists = [
        {"name": "NES", "dest_folder": "nes"},
        {"name": "SNES", "dest_folder": "snes"},
    ]
    assert rank_system_matches("NES", playlists, limit=1) == ["NES"]
    assert rank_system_matches("SNES", playlists, limit=1) == ["SNES"]


def test_rank_system_matches_partial_match():
    playlists = [
        {"name": "NES", "dest_folder": "nes"},
        {"name": "SNES", "dest_folder": "snes"},
    ]
    assert rank_system_matches("NE", playlists, limit=1) == ["NES"]
    assert rank_system_matches("SNE", playlists, limit=1) == ["SNES"]


def test_rank_system_matches_disabled_playlist():
    playlists = [
        {"name": "ATARI", "dest_folder": "2600"},
        {"name": "SNES", "dest_folder": "snes", "disabled": True},
    ]
    assert "SNES" not in rank_system_matches("SNES", playlists, limit=5)
    assert rank_system_matches("2600", playlists, limit=1) == ["ATARI"]


def test_rank_system_matches_distance():
    playlists = [
        {"name": "NES", "dest_folder": "nes"},
        {"name": "SNES", "dest_folder": "snes"},
        {"name": "Mega Drive", "dest_folder": "megadrive"},
    ]
    assert rank_system_matches("Mega", playlists, limit=1) == ["Mega Drive"]
    assert rank_system_matches("Drive", playlists, limit=1) == ["Mega Drive"]
