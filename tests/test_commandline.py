import io
from unittest.mock import patch
from retrosync import main


def run_cli_tool(args):
    with patch("sys.argv", args), patch("sys.stdout", new=io.StringIO()) as mock_stdout:
        try:
            main()
        except SystemExit:
            pass
        return mock_stdout.getvalue().strip()


def test_help():
    output = run_cli_tool(["retrosync.py", "--help"])
    assert "Usage: retrosync.py" in output


def test_no_args_prints_help():
    output = run_cli_tool(["retrosync.py"])
    assert "Usage: retrosync.py" in output


def test_prompt():
    try:
        run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--update-playlists",
                "--name=psx",
                "--config-file=test.conf",
            ]
        )
        raise AssertionError()
    except OSError:
        assert True


def test_prompt_yes():
    try:
        run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--update-playlists",
                "--name=psx",
                "--yes",
                "--config-file=test.conf",
            ]
        )
        assert True
    except OSError:
        raise AssertionError()


def test_prompt_shows_multiple_matches():
    fake_config = {
        "default": {
            "transport": "filesystem",
            "src_roms": ["tests/assets/roms"],
        },
        "playlists": [
            {"name": "Nintendo - NES.lpl", "src_folder": "nes", "dest_folder": "nes"},
            {"name": "Nintendo - SNES.lpl", "src_folder": "snes", "dest_folder": "snes"},
            {
                "name": "FBNeo - Arcade Games.lpl",
                "src_folder": "fbneo",
                "dest_folder": "fbneo",
            },
        ],
    }
    with (
        patch("retrosync.toml.load", return_value=fake_config),
        patch("retrosync.click.prompt", return_value=0),
    ):
        output = run_cli_tool(
            [
                "retrosync.py",
                "--dry-run",
                "--update-playlists",
                "--name=Nintendo",
                "--config-file=ignored.conf",
            ]
        )
    assert "Select a playlist match for 'Nintendo':" in output
    assert "1. Nintendo - NES.lpl" in output
    assert "2. Nintendo - SNES.lpl" in output
    assert "0. Cancel" in output
