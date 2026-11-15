"""The pre-commit / CI guard (scripts/guard.py). Fake secrets are built by joining pieces, so
this file never contains one itself."""

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "guard.py"
spec = importlib.util.spec_from_file_location("guard", SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)

FAKE = {
    "private key": "-----BEGIN RSA " + "PRIVATE KEY-----",
    "aws": "AKIA" + "ABCDEFGHIJKLMNOP",
    "github": "ghp_" + "a" * 36,
    "anthropic": "sk-ant-" + "api03-" + "x" * 30,
    "slack": "xoxb-" + "1234567890-abc",
    "url password": "postgres://admin:" + "hunter2hunter2@db.example.com/x",
    "assignment": 'API_KEY = "' + "abc123def456ghi789" + '"',
}


def problems(path, text):
    return guard.check_content(path, text.encode())


@pytest.mark.parametrize("name", FAKE)
def test_finds_secrets(name):
    found = problems("app/config.py", f"x = 1\n{FAKE[name]}\n")
    assert len(found) == 1 and found[0].where == "app/config.py:2", name


def test_allow_marker_and_ordinary_code_pass():
    assert problems("app/x.py", FAKE["aws"] + "  # guard: allow\n") == []
    ordinary = 'password = request.form["password"]\nkey = secrets.token_urlsafe(32)\nheaders = {"X-Profile-Key": key}\n'
    assert problems("app/x.py", ordinary) == []


@pytest.mark.parametrize("path", [".env", "backend/.env.production", "certs/server.pem", "deploy/id_rsa",
                                  "backend/data/players.json", "backend/data/fantasy.db", "x/leagues.db-wal",
                                  "backend/.venv/lib/site.py", "app/__pycache__/x.cpython-314.pyc"])
def test_blocks_files_that_should_never_be_committed(path):
    assert guard.check_path(path), path


@pytest.mark.parametrize("path", [".env.example", "backend/app/data/db.py", "frontend/app.js", "README.md"])
def test_allows_normal_files(path):
    assert guard.check_path(path) == [], path


def test_size_limit_and_binary_files():
    assert "over 1 MB" in str(guard.check_content("big.json", b"x" * (guard.MAX_BYTES + 1))[0])
    assert guard.check_content("logo.png", b"\x89PNG\0" + FAKE["aws"].encode()) == []  # binary: size only


def test_conflict_markers_and_debuggers():
    text = "<" * 7 + " HEAD\na = 1\n" + "=" * 7 + "\na = 2\n" + ">" * 7 + " main\n"
    assert len(problems("app/x.py", text)) == 3
    assert problems("app/x.py", "def f():\n    breakpoint()\n")[0].what == "debugger left in"
    assert problems("app/x.py", "import pdb; pdb.set_trace()\n")
    assert problems("frontend/x.js", "  debugger;\n")
    assert problems("backend/tests/test_x.py", "breakpoint()\n") == []  # fine while debugging tests
    assert problems("frontend/x.js", "// the debugger; keyword\n") == []


def test_commit_messages_with_ai_attribution():
    trailer = "Co-Authored-" + "By: Claude <noreply@anthropic.com>"
    assert guard.check_message("abc12345", f"Fix it\n\n{trailer}\n")
    assert guard.check_message("abc12345", "Add thing\n\nGenerated " + "with Claude Code\n")
    assert guard.check_message("abc12345", "Fix it\n\nCo-authored-by: Sam <sam@example.com>\n") == []
    assert guard.check_message("abc12345", "Explain how Claude-style prompts are parsed\n") == []


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def run(*args):
        subprocess.run(["git", *args], check=True, capture_output=True)

    run("init", "-q", "-b", "main")
    run("config", "user.email", "test@example.com")
    run("config", "user.name", "Test")
    run("config", "commit.gpgsign", "false")
    (tmp_path / "README.md").write_text("hello\n")
    run("add", ".")
    run("commit", "-qm", "Start")
    return tmp_path, run


def test_staged_mode(repo, capsys):
    path, run = repo
    (path / "app.py").write_text("print('ok')\n")
    run("add", "app.py")
    assert guard.main(["--staged"]) == 0
    (path / "app.py").write_text(f"print('ok')\n{FAKE['github']}\n")
    assert guard.main(["--staged"]) == 0  # only what's staged counts
    run("add", "app.py")
    assert guard.main(["--staged"]) == 1
    assert "app.py:2: looks like a GitHub token" in capsys.readouterr().err


def test_range_mode_checks_files_and_messages(repo, capsys):
    path, run = repo
    start = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    (path / ".env").write_text("X=1\n")
    run("add", ".env")
    run("commit", "-qm", "Add settings\n\nCo-Authored-" + "By: Claude <noreply@anthropic.com>")
    assert guard.main(["--range", f"{start}..HEAD"]) == 1
    err = capsys.readouterr().err
    assert ".env: shouldn't be committed" in err and "AI attribution" in err
    run("rm", "-q", ".env")
    run("commit", "-qm", "Remove settings")
    # A file removed later in the range isn't in the final tree, but the message still counts.
    assert guard.main(["--range", "HEAD~1..HEAD"]) == 0
