"""Review scope validation and conservative Git diff sizing."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


class ScopeError(ValueError):
    """Raised when a review scope cannot be validated or inspected."""


SCOPE_MANIFEST_VERSION = 1
SCOPE_MANIFEST_KINDS = {"uncommitted", "base", "commit", "range"}


@dataclass(frozen=True)
class ReviewScope:
    kind: str
    value: Optional[str] = None

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {"kind": self.kind, "value": self.value}

    @property
    def label(self) -> str:
        if self.kind == "uncommitted":
            return "uncommitted changes"
        if self.kind == "base":
            return f"changes against base {self.value}"
        if self.kind == "commit":
            return f"commit {self.value}"
        if self.kind == "range":
            return f"commit range {self.value}"
        if self.kind == "custom":
            return "custom review instructions"
        return self.kind

    def native_args(self) -> List[str]:
        if self.kind == "base" and self.value:
            return ["--base", self.value]
        if self.kind == "commit" and self.value:
            return ["--commit", self.value]
        if self.kind == "uncommitted":
            return ["--uncommitted"]
        return []

    def prompt_instruction(self) -> str:
        if self.kind == "base":
            return f"Review the changes on the current branch against merge-base with {self.value}."
        if self.kind == "commit":
            return f"Review only the changes introduced by commit {self.value}."
        if self.kind == "uncommitted":
            return (
                "Review all staged, unstaged, and untracked changes in this repository."
            )
        if self.kind == "range":
            return f"Review only the changes in git range {self.value}."
        return ""


@dataclass(frozen=True)
class ManifestScope:
    repo: str
    scope: ReviewScope

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "repo": self.repo,
            "kind": self.scope.kind,
            "value": self.scope.value,
        }


def load_scope_manifest(path_value: str) -> List[ManifestScope]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise ScopeError(f"Scope manifest does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScopeError(f"Invalid scope manifest {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise ScopeError("Scope manifest must be a JSON object")
    if payload.get("version") != SCOPE_MANIFEST_VERSION:
        raise ScopeError(f"Scope manifest version must be {SCOPE_MANIFEST_VERSION}")
    raw_scopes = payload.get("scopes")
    if not isinstance(raw_scopes, list) or not raw_scopes:
        raise ScopeError("Scope manifest scopes must be a non-empty array")

    entries: List[ManifestScope] = []
    seen = set()
    for index, item in enumerate(raw_scopes):
        label = f"Scope manifest entry {index}"
        if not isinstance(item, Mapping):
            raise ScopeError(f"{label} must be an object")
        extras = sorted(set(item) - {"repo", "kind", "value"})
        if extras:
            raise ScopeError(f"{label} has unsupported fields: {', '.join(extras)}")
        repo_value = item.get("repo")
        kind = item.get("kind")
        value = item.get("value")
        if not isinstance(repo_value, str) or not repo_value.strip():
            raise ScopeError(f"{label} repo must be a non-empty path")
        repo_path = Path(repo_value).expanduser()
        if not repo_path.is_absolute():
            repo_path = path.parent / repo_path
        repo = str(repo_path.resolve())
        if not isinstance(kind, str) or kind not in SCOPE_MANIFEST_KINDS:
            supported = ", ".join(sorted(SCOPE_MANIFEST_KINDS))
            raise ScopeError(f"{label} kind must be one of: {supported}")
        if kind == "uncommitted":
            if value is not None:
                raise ScopeError(f"{label} uncommitted scope cannot set value")
            scope = ReviewScope(kind)
        else:
            if not isinstance(value, str) or not value.strip():
                raise ScopeError(f"{label} {kind} scope requires value")
            scope = ReviewScope(kind, value)
        if repo in seen:
            raise ScopeError(f"{label} duplicates an earlier repository")
        seen.add(repo)
        entries.append(ManifestScope(repo, scope))
    return entries


def combine_metrics(items: Sequence[Tuple[str, "DiffMetrics"]]) -> "DiffMetrics":
    paths: List[str] = []
    warnings: List[str] = []
    for repo, metrics in items:
        paths.extend(f"{repo}:{path}" for path in metrics.paths)
        warnings.extend(metrics.warnings)
    return DiffMetrics(
        changed_files=sum(metrics.changed_files for _, metrics in items),
        changed_lines=sum(metrics.changed_lines for _, metrics in items),
        untracked_bytes=sum(metrics.untracked_bytes for _, metrics in items),
        paths=paths,
        warnings=list(dict.fromkeys(warnings)),
    )


@dataclass
class DiffMetrics:
    changed_files: int = 0
    changed_lines: int = 0
    untracked_bytes: int = 0
    paths: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, object]:
        return {
            "changed_files": self.changed_files,
            "changed_lines": self.changed_lines,
            "untracked_bytes": self.untracked_bytes,
            "paths": list(self.paths),
        }


def _developer_dir_for_git(git_path: str) -> Optional[str]:
    marker = "/Contents/Developer/usr/bin/git"
    if marker in git_path:
        return git_path.split(marker, 1)[0] + "/Contents/Developer"
    marker = "/CommandLineTools/usr/bin/git"
    if marker in git_path:
        return git_path.split(marker, 1)[0] + "/CommandLineTools"
    return None


def resolve_developer_git_details(
    timeout: int = 5,
) -> Tuple[Optional[str], Optional[str]]:
    """Resolve the real developer Git binary without relying on Apple's shim in sandboxed turns."""
    configured = os.environ.get("CODEX_REVIEWER_GIT_BIN")
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve()), None

    developer_dir = os.environ.get("DEVELOPER_DIR")
    if developer_dir:
        candidate = Path(developer_dir) / "usr/bin/git"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve()), None

    xcrun_warning = None
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["xcrun", "--find", "git"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            candidate = Path(result.stdout.strip())
            if (
                result.returncode == 0
                and candidate.is_file()
                and os.access(candidate, os.X_OK)
            ):
                resolved = str(candidate.resolve())
                if resolved != "/usr/bin/git":
                    return resolved, None
                xcrun_warning = "xcrun resolved only the /usr/bin/git shim; sandboxed Git may require temporary writes"
        except (OSError, subprocess.SubprocessError):
            xcrun_warning = "xcrun could not resolve the developer Git binary"

    for candidate in (
        Path("/Applications/Xcode.app/Contents/Developer/usr/bin/git"),
        Path("/Library/Developer/CommandLineTools/usr/bin/git"),
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve()), xcrun_warning
    fallback = shutil.which("git")
    if fallback:
        resolved = str(Path(fallback).resolve())
        warning = xcrun_warning
        if resolved == "/usr/bin/git":
            warning = (
                warning
                or "Only the /usr/bin/git shim is available; read-only sandbox Git may fail"
            )
        return resolved, warning
    return None, xcrun_warning or "Git executable not found"


def resolve_developer_git(timeout: int = 5) -> Optional[str]:
    return resolve_developer_git_details(timeout)[0]


def developer_git_environment(
    git_path: Optional[str], base: Optional[Dict[str, str]] = None
) -> Dict[str, str]:
    env = dict(base or os.environ)
    if not git_path:
        return env
    git_dir = str(Path(git_path).parent)
    path_parts = [
        part
        for part in env.get("PATH", "").split(os.pathsep)
        if part and part != git_dir
    ]
    env["PATH"] = os.pathsep.join([git_dir] + path_parts)
    developer_dir = _developer_dir_for_git(git_path)
    if developer_dir:
        env["DEVELOPER_DIR"] = developer_dir
    return env


class GitInspector:
    """Inspect Git scopes outside the Codex sandbox for deterministic preflight sizing."""

    def __init__(self, cwd: str, git_path: Optional[str] = None, timeout: int = 15):
        self.cwd = str(Path(cwd).expanduser().resolve())
        if git_path:
            self.git_path = git_path
            self.environment_warnings: List[str] = []
        else:
            self.git_path, warning = resolve_developer_git_details()
            self.environment_warnings = [warning] if warning else []
        self.timeout = timeout

    def validate_repository(self) -> Optional[str]:
        if not Path(self.cwd).is_dir():
            return f"Working directory does not exist: {self.cwd}"
        if not self.git_path:
            return "Git executable not found"
        result = self._run(["rev-parse", "--show-toplevel"])
        if result.returncode != 0:
            return self._detail(result, "Working directory is not a Git repository")
        return None

    def metrics(self, scope: ReviewScope) -> Optional[DiffMetrics]:
        if scope.kind == "custom":
            return None
        repository_error = self.validate_repository()
        if repository_error:
            raise ScopeError(repository_error)
        if scope.kind == "uncommitted":
            return self._uncommitted_metrics()
        if scope.kind == "base" and scope.value:
            self._verify_ref(scope.value)
            return self._diff_metrics([f"{scope.value}...HEAD"])
        if scope.kind == "range" and scope.value:
            self._validate_range(scope.value)
            return self._diff_metrics([scope.value])
        if scope.kind == "commit" and scope.value:
            self._verify_ref(scope.value)
            return self._commit_metrics(scope.value)
        raise ScopeError(f"Unsupported or incomplete Git scope: {scope.kind}")

    def _verify_ref(self, ref: str) -> None:
        if not ref or "\x00" in ref or "\n" in ref:
            raise ScopeError("Git ref must be a non-empty single-line value")
        result = self._run(
            [
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{ref}^{{commit}}",
            ]
        )
        if result.returncode != 0:
            raise ScopeError(f"Git ref does not resolve to a commit: {ref}")

    def _validate_range(self, value: str) -> None:
        delimiter = "..." if "..." in value else ".." if ".." in value else None
        if not delimiter:
            raise ScopeError(f"Review range must use '..' or '...': {value}")
        left, right = value.split(delimiter, 1)
        self._verify_ref(left)
        self._verify_ref(right)

    def _diff_metrics(self, revisions: Sequence[str]) -> DiffMetrics:
        paths = self._z_paths(
            ["diff", "--name-only", "-z", "--end-of-options", *revisions]
        )
        line_count, warnings = self._numstat(
            ["diff", "--numstat", "-z", "--end-of-options", *revisions]
        )
        return DiffMetrics(
            changed_files=len(paths),
            changed_lines=line_count,
            paths=sorted(paths),
            warnings=[*self.environment_warnings, *warnings],
        )

    def _commit_metrics(self, commit: str) -> DiffMetrics:
        base_args = ["diff-tree", "--root", "--no-commit-id", "-r"]
        paths = self._z_paths(
            [*base_args, "--name-only", "-z", "--end-of-options", commit]
        )
        line_count, warnings = self._numstat(
            [*base_args, "--numstat", "-z", "--end-of-options", commit]
        )
        return DiffMetrics(
            changed_files=len(paths),
            changed_lines=line_count,
            paths=sorted(paths),
            warnings=[*self.environment_warnings, *warnings],
        )

    def _uncommitted_metrics(self) -> DiffMetrics:
        unstaged = self._z_paths(["diff", "--name-only", "-z"])
        staged = self._z_paths(["diff", "--cached", "--name-only", "-z"])
        untracked = self._z_paths(["ls-files", "--others", "--exclude-standard", "-z"])
        paths = sorted(set(unstaged) | set(staged) | set(untracked))
        changed_lines, unstaged_warnings = self._numstat(["diff", "--numstat", "-z"])
        staged_lines, staged_warnings = self._numstat(
            ["diff", "--cached", "--numstat", "-z"]
        )
        changed_lines += staged_lines
        untracked_bytes = 0
        warnings: List[str] = [
            *self.environment_warnings,
            *unstaged_warnings,
            *staged_warnings,
        ]
        for relative in untracked:
            path = Path(self.cwd) / relative
            try:
                size = path.lstat().st_size
                untracked_bytes += size
                if path.is_symlink():
                    warnings.append(
                        f"Untracked symbolic link {relative!r} counted by file size only"
                    )
                    continue
                equivalent, binary = self._untracked_line_equivalent(path, size)
                changed_lines += equivalent
                if binary:
                    warnings.append(
                        f"Untracked binary file {relative!r} counted by file size only"
                    )
            except OSError as exc:
                warnings.append(f"Could not size untracked path {relative!r}: {exc}")
        return DiffMetrics(
            changed_files=len(paths),
            changed_lines=changed_lines,
            untracked_bytes=untracked_bytes,
            paths=paths,
            warnings=warnings,
        )

    @staticmethod
    def _untracked_line_equivalent(path: Path, size: int) -> Tuple[int, bool]:
        if size == 0:
            return 0, False
        with path.open("rb") as handle:
            first = handle.read(8192)
            if b"\x00" in first:
                return 0, True
            count = first.count(b"\n")
            last = first[-1:]
            while chunk := handle.read(1024 * 1024):
                count += chunk.count(b"\n")
                last = chunk[-1:]
            return (count if last == b"\n" else count + 1), False

    def _z_paths(self, args: Sequence[str]) -> List[str]:
        result = self._run(args, text=False)
        if result.returncode != 0:
            raise ScopeError(
                self._detail(result, f"Git command failed: {' '.join(args)}")
            )
        output = (
            result.stdout
            if isinstance(result.stdout, bytes)
            else result.stdout.encode()
        )
        return [
            item.decode("utf-8", errors="surrogateescape")
            for item in output.split(b"\x00")
            if item
        ]

    def _numstat(self, args: Sequence[str]) -> Tuple[int, List[str]]:
        result = self._run(args, text=False)
        if result.returncode != 0:
            raise ScopeError(
                self._detail(result, f"Git command failed: {' '.join(args)}")
            )
        output = (
            result.stdout
            if isinstance(result.stdout, bytes)
            else result.stdout.encode()
        )
        total = 0
        warnings: List[str] = []
        for record in output.split(b"\x00"):
            parts = record.split(b"\t", 2)
            if len(parts) < 2:
                continue
            try:
                total += int(parts[0]) + int(parts[1])
            except ValueError:
                path = (
                    parts[2].decode("utf-8", errors="surrogateescape")
                    if len(parts) > 2
                    else "<renamed binary>"
                )
                warnings.append(
                    f"Tracked binary file {path!r} is covered by the changed-file guard, not changed lines"
                )
        return total, warnings

    def _run(
        self, args: Sequence[str], text: bool = True
    ) -> subprocess.CompletedProcess:
        if not self.git_path:
            raise ScopeError("Git executable not found")
        return subprocess.run(
            [self.git_path, "-C", self.cwd, *args],
            capture_output=True,
            text=text,
            timeout=self.timeout,
            check=False,
            env=developer_git_environment(self.git_path),
        )

    @staticmethod
    def _detail(result: subprocess.CompletedProcess, fallback: str) -> str:
        stderr = (
            result.stderr.decode(errors="replace")
            if isinstance(result.stderr, bytes)
            else result.stderr
        )
        stdout = (
            result.stdout.decode(errors="replace")
            if isinstance(result.stdout, bytes)
            else result.stdout
        )
        return (stderr or stdout or fallback).strip()


def large_diff_error(
    metrics: DiffMetrics, scope: ReviewScope, max_files: int, max_lines: int
) -> Optional[str]:
    if metrics.changed_files <= max_files and metrics.changed_lines <= max_lines:
        return None
    return (
        f"Review scope {scope.label} is large ({metrics.changed_files} files, "
        f"{metrics.changed_lines} changed-line equivalents). Default guard is "
        f"{max_files} files / {max_lines} lines. Split the review, use --quick, "
        "or pass --allow-large-diff when the broad scope is intentional."
    )
