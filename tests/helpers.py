"""Shared test helpers for CLI and Git fixture tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "scripts" / "codex_review.py"
FAKE_CODEX_SOURCE = ROOT / "tests" / "fake_codex.py"


def make_fake_codex(
    parent: Path,
    version: str = "0.144.1",
    catalog: dict[str, Any] | None = None,
) -> Path:
    directory = parent / f"codex-{version}"
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "codex"
    shutil.copyfile(FAKE_CODEX_SOURCE, binary)
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    (directory / ".fake_version").write_text(version, encoding="utf-8")
    if catalog is not None:
        (directory / ".fake_catalog.json").write_text(
            json.dumps(catalog), encoding="utf-8"
        )
    return binary


def run_cli(
    *args: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 15,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(ENTRYPOINT), *map(str, args)],
        cwd=cwd or ROOT,
        env=merged_env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=check,
    )


def init_git_fixture(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Codex Reviewer Test")
    git(repo, "config", "user.email", "codex-reviewer@example.invalid")
    (repo / "app.py").write_text(
        "def total(values):\n    return sum(values)\n", encoding="utf-8"
    )
    git(repo, "add", "app.py")
    git(repo, "commit", "-m", "initial")
    return repo


def read_fake_log(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))
