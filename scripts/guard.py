"""Stop harmful changes before they're committed, pushed, or merged.

Checks the files a change touches for:
  - secrets: private keys, cloud and API tokens, and passwords or keys written into code
  - files that should never be committed: .env files, key files, databases, the synced
    player data (backend/data/), virtualenvs, and compiled Python
  - files over 1 MB
  - unresolved merge-conflict markers
  - debugging leftovers in app code: breakpoint(), pdb, and JavaScript `debugger;`
and the commit messages for AI attribution (Co-Authored-By: Claude, "Generated with ...").

Usage (from the repository root; needs only Python and git):
    python scripts/guard.py --staged           # what's about to be committed (pre-commit hook)
    python scripts/guard.py --range A..B       # commits A..B and the files they change (pre-push, CI)

A line that trips a content check on purpose can end with `guard: allow`.
Exits 1 and lists every problem if anything is found.
"""

import argparse
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass

MAX_BYTES = 1024 * 1024
ALLOW_MARKER = "guard: allow"

FORBIDDEN_PATHS = [
    (".env", "environment files hold secrets"),
    (".env.*", "environment files hold secrets"),
    ("*.pem", "key and certificate files"),
    ("*.key", "key files"),
    ("*.p12", "key stores"),
    ("*.pfx", "key stores"),
    ("id_rsa*", "SSH keys"),
    ("id_ed25519*", "SSH keys"),
    ("*.db", "databases (collected data stays local)"),
    ("*.db-wal", "databases (collected data stays local)"),
    ("*.db-shm", "databases (collected data stays local)"),
    ("*.sqlite", "databases (collected data stays local)"),
    ("*.sqlite3", "databases (collected data stays local)"),
    ("*.pyc", "compiled Python"),
]
FORBIDDEN_DIRS = [
    ("backend/data/", "synced and collected data is regenerated locally, never committed"),
    (".venv/", "virtualenvs"),
    ("venv/", "virtualenvs"),
    ("__pycache__/", "compiled Python"),
    ("node_modules/", "installed packages"),
]
ALLOWED_NAMES = {".env.example", ".env.sample"}

SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"), "a GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{50,}\b"), "a GitHub token"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_-]{20,}"), "an Anthropic API key"),
    (re.compile(r"\bsk-(?:proj-)?[A-Za-z0-9_-]{32,}"), "an OpenAI-style API key"),
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), "a Slack token"),
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), "a Google API key"),
    (re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}"), "a Stripe live key"),
    (re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]{6,}@"), "a password in a URL"),
    (re.compile(
        r"""(?i)\b(?:password|passwd|secret|api[_-]?key|access[_-]?key|auth[_-]?token|client[_-]?secret)"""
        r"""\s*[:=]\s*["'][^"'\s]{12,}["']"""), "a hardcoded secret"),
]
CONFLICT = re.compile(r"^(?:<{7} |>{7} |={7}$)")
DEBUG_PYTHON = re.compile(r"^\s*(?:breakpoint\(\)|import pdb\b|import ipdb\b|.*\bpdb\.set_trace\(\))")
DEBUG_JS = re.compile(r"^\s*debugger\s*;?\s*$")
ATTRIBUTION = [
    re.compile(r"(?im)^co-authored-by:.*\b(?:claude|anthropic|copilot|chatgpt|openai|cursor)\b"),
    re.compile(r"(?i)generated (?:with|by) (?:\[?claude|copilot|chatgpt|an ai)"),
]


@dataclass
class Problem:
    where: str
    what: str

    def __str__(self) -> str:
        return f"{self.where}: {self.what}"


def git(*args: str, binary: bool = False):
    result = subprocess.run(["git", *args], capture_output=True, check=True)  # noqa: S603, S607 - git, fixed args
    return result.stdout if binary else result.stdout.decode("utf-8", "replace")


def check_path(path: str) -> list[Problem]:
    name = path.rsplit("/", 1)[-1]
    problems = []
    if name not in ALLOWED_NAMES:
        for pattern, why in FORBIDDEN_PATHS:
            if fnmatch.fnmatch(name, pattern):
                problems.append(Problem(path, f"shouldn't be committed ({why})"))
                break
    for prefix, why in FORBIDDEN_DIRS:
        if path.startswith(prefix) or f"/{prefix}" in f"/{path}":
            problems.append(Problem(path, f"shouldn't be committed ({why})"))
            break
    return problems


def check_content(path: str, data: bytes) -> list[Problem]:
    problems = []
    if len(data) > MAX_BYTES:
        problems.append(Problem(path, f"is {len(data) / 1024 / 1024:.1f} MB; files over 1 MB don't belong in git"))
    if b"\0" in data[:8192]:
        return problems  # binary: only the size check applies
    text = data.decode("utf-8", "replace")
    is_test = "/tests/" in f"/{path}" or path.rsplit("/", 1)[-1].startswith("test_")
    is_python, is_js = path.endswith(".py"), path.endswith(".js")
    for number, line in enumerate(text.splitlines(), 1):
        if ALLOW_MARKER in line:
            continue
        where = f"{path}:{number}"
        for pattern, what in SECRET_PATTERNS:
            if pattern.search(line):
                problems.append(Problem(where, f"looks like {what}"))
                break
        if CONFLICT.match(line):
            problems.append(Problem(where, "unresolved merge-conflict marker"))
        if not is_test and is_python and DEBUG_PYTHON.match(line):
            problems.append(Problem(where, "debugger left in"))
        if not is_test and is_js and DEBUG_JS.match(line):
            problems.append(Problem(where, "`debugger` left in"))
    return problems


def check_message(sha: str, message: str) -> list[Problem]:
    subject = message.splitlines()[0] if message.strip() else ""
    return [Problem(f"commit {sha[:8]} ({subject[:50]})", "commit message has AI attribution; remove it")
            for pattern in ATTRIBUTION if pattern.search(message)]


def staged() -> list[Problem]:
    problems = []
    for path in git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0"):
        if path:
            problems += check_path(path) + check_content(path, git("show", f":{path}", binary=True))
    return problems


def commit_range(spec: str) -> list[Problem]:
    problems = []
    end = spec.split("..", 1)[1] if ".." in spec else spec
    for path in git("diff", "--name-only", "--diff-filter=ACMR", "-z", spec).split("\0"):
        if path:
            problems += check_path(path) + check_content(path, git("show", f"{end}:{path}", binary=True))
    for sha in git("rev-list", "--no-merges", spec).split():
        problems += check_message(sha, git("log", "-1", "--format=%B", sha))
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--staged", action="store_true", help="check the staged changes")
    mode.add_argument("--range", metavar="A..B", help="check the commits in A..B and the files they change")
    args = parser.parse_args(argv)
    try:
        problems = staged() if args.staged else commit_range(args.range)
    except subprocess.CalledProcessError as error:
        print(f"guard: git failed: {error.stderr.decode('utf-8', 'replace').strip()}", file=sys.stderr)
        return 2
    if not problems:
        print("guard: no problems found")
        return 0
    print(f"guard: {len(problems)} problem{'s' if len(problems) != 1 else ''} found:", file=sys.stderr)
    for problem in problems:
        print(f"  {problem}", file=sys.stderr)
    print(f"Fix them, or end a line with `{ALLOW_MARKER}` if it's intentional.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
