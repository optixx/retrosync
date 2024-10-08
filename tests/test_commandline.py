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
