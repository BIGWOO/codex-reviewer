"""Strict bounded-scope parsing and deterministic review packet construction."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import subprocess
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .scope import developer_git_environment, resolve_developer_git_details
from .prompts import DEFAULT_REVIEW_CRITERIA


BOUNDED_SCOPE_VERSION = 1
BOUNDED_PACKET_VERSION = 1
BOUNDED_KINDS = {"commit_snapshot", "commit_diff", "uncommitted_diff"}
EVIDENCE_STATUSES = {"passed", "failed", "not_run"}


class BoundedScopeError(ValueError):
    """Raised when a bounded scope or packet input is invalid."""


@dataclass(frozen=True)
class BoundedPacket:
    prompt: str
    repository: str
    scope: Mapping[str, object]
    metrics: Mapping[str, int]
    scope_fingerprint: str
    packet_sha256: str
    prompt_sha256: str


def load_json_object(path_value: str, label: str) -> Mapping[str, object]:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise BoundedScopeError(f"{label} does not exist: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BoundedScopeError(f"Invalid {label} {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise BoundedScopeError(f"{label} must be a JSON object")
    return payload


class _GitPacketBuilder:
    def __init__(self, cwd: str):
        requested = Path(cwd).expanduser().resolve()
        git_path, warning = resolve_developer_git_details()
        if not git_path:
            raise BoundedScopeError(warning or "Git executable not found")
        self.git_path = git_path
        self.env = developer_git_environment(git_path)
        self.requested_cwd = str(requested)
        root = self._run_text(["rev-parse", "--show-toplevel"], cwd=requested).strip()
        if not root:
            raise BoundedScopeError("Working directory is not a Git repository")
        self.root = Path(root).resolve()

    def build(
        self,
        raw_scope: Mapping[str, object],
        evidence: Optional[Mapping[str, object]],
    ) -> BoundedPacket:
        kind = self._validate_scope_header(raw_scope)
        normalized_evidence = self._validate_evidence(evidence)
        if kind == "commit_snapshot":
            normalized_scope, files, metrics = self._commit_snapshot(raw_scope)
        elif kind == "commit_diff":
            normalized_scope, files, metrics = self._commit_diff(raw_scope)
        else:
            normalized_scope, files, metrics = self._uncommitted_diff(raw_scope)

        fingerprint_payload = {
            "version": BOUNDED_SCOPE_VERSION,
            "repository": str(self.root),
            "scope": normalized_scope,
        }
        scope_fingerprint = _sha256_json(fingerprint_payload)
        scope_payload = dict(normalized_scope)
        scope_payload["metrics"] = dict(metrics)
        packet_payload = {
            "packet_version": BOUNDED_PACKET_VERSION,
            "contract": {
                "role": "read_only_code_reviewer",
                "scope_rule": "Review only the files, ranges, and diff layers in this packet.",
                "tool_policy": "none",
                "test_policy": "Use caller_evidence only; do not run tests or inspect the repository.",
                "finding_policy": "Report only discrete actionable defects introduced by this scope.",
                "review_criteria": DEFAULT_REVIEW_CRITERIA,
                "output_policy": "Return only JSON matching the supplied schema.",
            },
            "repository": {"absolute_path": str(self.root)},
            "scope": scope_payload,
            "scope_fingerprint": scope_fingerprint,
            "caller_evidence": normalized_evidence,
            "files": files,
        }
        prompt = _canonical_json(packet_payload) + "\n"
        packet_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        return BoundedPacket(
            prompt=prompt,
            repository=str(self.root),
            scope=scope_payload,
            metrics=metrics,
            scope_fingerprint=scope_fingerprint,
            packet_sha256=packet_sha256,
            prompt_sha256=packet_sha256,
        )

    @staticmethod
    def _validate_scope_header(raw_scope: Mapping[str, object]) -> str:
        version = raw_scope.get("version")
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != BOUNDED_SCOPE_VERSION
        ):
            raise BoundedScopeError(
                f"Bounded scope version must be {BOUNDED_SCOPE_VERSION}"
            )
        kind = raw_scope.get("kind")
        if not isinstance(kind, str) or kind not in BOUNDED_KINDS:
            supported = ", ".join(sorted(BOUNDED_KINDS))
            raise BoundedScopeError(f"Bounded scope kind must be one of: {supported}")
        allowed = (
            {"version", "kind", "files"}
            if kind == "uncommitted_diff"
            else {"version", "kind", "commit", "files"}
        )
        extras = sorted(set(raw_scope) - allowed)
        if extras:
            raise BoundedScopeError(
                f"Bounded scope has unsupported fields: {', '.join(extras)}"
            )
        missing = sorted(allowed - set(raw_scope))
        if missing:
            raise BoundedScopeError(
                f"Bounded scope is missing required fields: {', '.join(missing)}"
            )
        return kind

    def _commit_snapshot(
        self, raw_scope: Mapping[str, object]
    ) -> Tuple[Dict[str, object], List[Dict[str, object]], Dict[str, int]]:
        requested_ref, commit = self._resolve_commit(raw_scope.get("commit"))
        raw_files = raw_scope.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise BoundedScopeError("commit_snapshot files must be a non-empty array")
        files: List[Dict[str, object]] = []
        normalized_files: List[Dict[str, object]] = []
        seen = set()
        total_lines = 0
        total_bytes = 0
        for index, item in enumerate(raw_files):
            label = f"commit_snapshot file {index}"
            if not isinstance(item, Mapping):
                raise BoundedScopeError(f"{label} must be an object")
            extras = sorted(set(item) - {"path", "ranges"})
            if extras:
                raise BoundedScopeError(
                    f"{label} has unsupported fields: {', '.join(extras)}"
                )
            path = self._validate_path(item.get("path"), label)
            if path in seen:
                raise BoundedScopeError(f"{label} duplicates path {path!r}")
            seen.add(path)
            raw_ranges = item.get("ranges")
            ranges = self._validate_ranges(raw_ranges, label)
            content = self._git_blob(commit, path)
            text = self._decode_text(content, path)
            source_lines = text.splitlines()
            source_lines_with_endings = text.splitlines(keepends=True)
            rendered_ranges = []
            for start, end in ranges:
                if end > len(source_lines):
                    raise BoundedScopeError(
                        f"{label} range {start}-{end} exceeds {len(source_lines)} lines"
                    )
                selected = [
                    {"number": number, "text": source_lines[number - 1]}
                    for number in range(start, end + 1)
                ]
                rendered_ranges.append(
                    {"start": start, "end": end, "lines": selected}
                )
                total_lines += len(selected)
                total_bytes += sum(
                    len(source_lines_with_endings[number - 1].encode("utf-8"))
                    for number in range(start, end + 1)
                )
            normalized_files.append(
                {
                    "path": path,
                    "ranges": [
                        {"start": start, "end": end} for start, end in ranges
                    ],
                }
            )
            files.append(
                {
                    "path": path,
                    "absolute_path": str(self.root / path),
                    "source_ref": commit,
                    "ranges": rendered_ranges,
                }
            )
        scope = {
            "kind": "commit_snapshot",
            "requested_ref": requested_ref,
            "resolved_commit": commit,
            "files": normalized_files,
        }
        return scope, files, self._metrics(files, total_lines, total_bytes)

    def _commit_diff(
        self, raw_scope: Mapping[str, object]
    ) -> Tuple[Dict[str, object], List[Dict[str, object]], Dict[str, int]]:
        requested_ref, commit = self._resolve_commit(raw_scope.get("commit"))
        paths = self._validate_path_list(raw_scope.get("files"), "commit_diff files")
        changed = set(
            self._z_paths(
                [
                    "diff-tree",
                    "--root",
                    "--no-commit-id",
                    "-r",
                    "--no-renames",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--name-only",
                    "-z",
                    commit,
                ]
            )
        )
        missing = [path for path in paths if path not in changed]
        if missing:
            raise BoundedScopeError(
                "commit_diff files are not changed by the commit: " + ", ".join(missing)
            )
        binary = self._binary_numstat_paths(
            [
                "diff-tree",
                "--root",
                "--no-commit-id",
                "-r",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--numstat",
                "-z",
                commit,
            ]
        )
        selected_binary = sorted(set(paths) & binary)
        if selected_binary:
            raise BoundedScopeError(
                "commit_diff does not support binary files: "
                + ", ".join(selected_binary)
            )
        files = []
        total_lines = 0
        total_bytes = 0
        for path in paths:
            raw_patch = self._run(
                [
                    "diff-tree",
                    "--root",
                    "--no-commit-id",
                    "-r",
                    "--patch",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--no-renames",
                    "--unified=80",
                    commit,
                    "--",
                    path,
                ]
            )
            patch = self._decode_text(raw_patch, path)
            patch_lines = len(patch.splitlines())
            total_lines += patch_lines
            total_bytes += len(raw_patch)
            files.append(
                {
                    "path": path,
                    "absolute_path": str(self.root / path),
                    "source_ref": commit,
                    "patch": patch,
                }
            )
        scope = {
            "kind": "commit_diff",
            "requested_ref": requested_ref,
            "resolved_commit": commit,
            "files": paths,
        }
        return scope, files, self._metrics(files, total_lines, total_bytes)

    def _uncommitted_diff(
        self, raw_scope: Mapping[str, object]
    ) -> Tuple[Dict[str, object], List[Dict[str, object]], Dict[str, int]]:
        paths = self._validate_path_list(
            raw_scope.get("files"), "uncommitted_diff files"
        )
        staged_paths = set(
            self._z_paths(
                [
                    "diff",
                    "--cached",
                    "--no-renames",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--name-only",
                    "-z",
                ]
            )
        )
        unstaged_paths = set(
            self._z_paths(
                [
                    "diff",
                    "--no-renames",
                    "--no-ext-diff",
                    "--no-textconv",
                    "--name-only",
                    "-z",
                ]
            )
        )
        untracked_paths = set(
            self._z_paths(["ls-files", "--others", "--exclude-standard", "-z"])
        )
        changed = staged_paths | unstaged_paths | untracked_paths
        missing = [path for path in paths if path not in changed]
        if missing:
            raise BoundedScopeError(
                "uncommitted_diff files have no selected changes: "
                + ", ".join(missing)
            )
        binary = self._binary_numstat_paths(
            [
                "diff",
                "--cached",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--numstat",
                "-z",
            ]
        ) | self._binary_numstat_paths(
            [
                "diff",
                "--no-renames",
                "--no-ext-diff",
                "--no-textconv",
                "--numstat",
                "-z",
            ]
        )
        selected_binary = sorted(set(paths) & binary)
        if selected_binary:
            raise BoundedScopeError(
                "uncommitted_diff does not support binary files: "
                + ", ".join(selected_binary)
            )

        files = []
        total_lines = 0
        total_bytes = 0
        for path in paths:
            record: Dict[str, object] = {
                "path": path,
                "absolute_path": str(self.root / path),
                "source_ref": "HEAD/index/worktree",
            }
            layers = []
            if path in staged_paths:
                raw_patch = self._run(
                    [
                        "diff",
                        "--cached",
                        "--patch",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--no-renames",
                        "--unified=80",
                        "--",
                        path,
                    ]
                )
                patch = self._decode_text(raw_patch, path)
                record["staged_patch"] = patch
                layers.append("staged")
                total_lines += len(patch.splitlines())
                total_bytes += len(raw_patch)
            if path in unstaged_paths:
                raw_patch = self._run(
                    [
                        "diff",
                        "--patch",
                        "--no-ext-diff",
                        "--no-textconv",
                        "--no-renames",
                        "--unified=80",
                        "--",
                        path,
                    ]
                )
                patch = self._decode_text(raw_patch, path)
                record["unstaged_patch"] = patch
                layers.append("unstaged")
                total_lines += len(patch.splitlines())
                total_bytes += len(raw_patch)
            if path in untracked_paths:
                absolute = self.root / path
                try:
                    metadata = absolute.lstat()
                except OSError as exc:
                    raise BoundedScopeError(
                        f"Could not read untracked file {path!r}: {exc}"
                    ) from exc
                if absolute.is_symlink() or not absolute.is_file():
                    raise BoundedScopeError(
                        f"uncommitted_diff supports only regular untracked files: {path}"
                    )
                try:
                    raw_content = absolute.read_bytes()
                except OSError as exc:
                    raise BoundedScopeError(
                        f"Could not read untracked file {path!r}: {exc}"
                    ) from exc
                text = self._decode_text(raw_content, path)
                source_lines = text.splitlines()
                record["lines"] = [
                    {"number": number, "text": line}
                    for number, line in enumerate(source_lines, start=1)
                ]
                layers.append("untracked")
                total_lines += len(source_lines)
                total_bytes += metadata.st_size
            record["layers"] = layers
            files.append(record)
        scope = {"kind": "uncommitted_diff", "base_ref": "HEAD", "files": paths}
        return scope, files, self._metrics(files, total_lines, total_bytes)

    @staticmethod
    def _validate_evidence(
        evidence: Optional[Mapping[str, object]],
    ) -> Dict[str, object]:
        if evidence is None:
            return {"version": 1, "checks": []}
        extras = sorted(set(evidence) - {"version", "checks"})
        if extras:
            raise BoundedScopeError(
                f"Evidence has unsupported fields: {', '.join(extras)}"
            )
        version = evidence.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise BoundedScopeError("Evidence version must be 1")
        checks = evidence.get("checks")
        if not isinstance(checks, list) or not checks:
            raise BoundedScopeError("Evidence checks must be a non-empty array")
        normalized = []
        seen = set()
        for index, check in enumerate(checks):
            label = f"Evidence check {index}"
            if not isinstance(check, Mapping):
                raise BoundedScopeError(f"{label} must be an object")
            extras = sorted(set(check) - {"name", "status", "detail"})
            if extras:
                raise BoundedScopeError(
                    f"{label} has unsupported fields: {', '.join(extras)}"
                )
            name = check.get("name")
            if (
                not isinstance(name, str)
                or not name.strip()
                or len(name) > 200
                or any(character in name for character in "\r\n\x00")
            ):
                raise BoundedScopeError(f"{label} name must be a short single-line string")
            if name in seen:
                raise BoundedScopeError(f"{label} duplicates check {name!r}")
            seen.add(name)
            status = check.get("status")
            if not isinstance(status, str) or status not in EVIDENCE_STATUSES:
                supported = ", ".join(sorted(EVIDENCE_STATUSES))
                raise BoundedScopeError(f"{label} status must be one of: {supported}")
            item: Dict[str, object] = {"name": name, "status": status}
            if "detail" in check:
                detail = check.get("detail")
                if not isinstance(detail, str) or len(detail) > 2000:
                    raise BoundedScopeError(
                        f"{label} detail must be a string up to 2000 characters"
                    )
                item["detail"] = detail
            normalized.append(item)
        return {"version": 1, "checks": normalized}

    def _resolve_commit(self, value: object) -> Tuple[str, str]:
        if (
            not isinstance(value, str)
            or not value.strip()
            or any(character in value for character in "\r\n\x00")
        ):
            raise BoundedScopeError("commit must be a non-empty single-line ref")
        result = self._run(
            [
                "rev-parse",
                "--verify",
                "--quiet",
                "--end-of-options",
                f"{value}^{{commit}}",
            ],
            allowed_exit_codes=(0, 1),
        )
        commit = result.decode("ascii", errors="ignore").strip()
        if not commit:
            raise BoundedScopeError(f"Git ref does not resolve to a commit: {value}")
        return value, commit

    @staticmethod
    def _validate_path(value: object, label: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or any(ord(character) < 32 for character in value)
            or "\\" in value
        ):
            raise BoundedScopeError(f"{label} path must be a safe repo-relative path")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
            raise BoundedScopeError(f"{label} path must be a normalized repo-relative path")
        if value in {".", ""}:
            raise BoundedScopeError(f"{label} path must identify a file")
        return value

    def _validate_path_list(self, value: object, label: str) -> List[str]:
        if not isinstance(value, list) or not value:
            raise BoundedScopeError(f"{label} must be a non-empty array")
        paths = []
        seen = set()
        for index, item in enumerate(value):
            path = self._validate_path(item, f"{label} entry {index}")
            if path in seen:
                raise BoundedScopeError(f"{label} duplicates path {path!r}")
            seen.add(path)
            paths.append(path)
        return paths

    @staticmethod
    def _validate_ranges(value: object, label: str) -> List[Tuple[int, int]]:
        if not isinstance(value, list) or not value:
            raise BoundedScopeError(f"{label} ranges must be a non-empty array")
        ranges = []
        for index, item in enumerate(value):
            range_label = f"{label} range {index}"
            if not isinstance(item, Mapping):
                raise BoundedScopeError(f"{range_label} must be an object")
            extras = sorted(set(item) - {"start", "end"})
            if extras or set(item) != {"start", "end"}:
                raise BoundedScopeError(
                    f"{range_label} must contain only start and end"
                )
            start = item.get("start")
            end = item.get("end")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 1
                or end < start
            ):
                raise BoundedScopeError(
                    f"{range_label} must use positive 1-based inclusive lines"
                )
            ranges.append((start, end))
        ranges.sort()
        for previous, current in zip(ranges, ranges[1:]):
            if current[0] <= previous[1]:
                raise BoundedScopeError(f"{label} ranges overlap or duplicate")
        return ranges

    def _git_blob(self, commit: str, path: str) -> bytes:
        result = self._run(
            ["show", f"{commit}:{path}"], allowed_exit_codes=(0, 128)
        )
        if not result and not self._object_exists(commit, path):
            raise BoundedScopeError(f"File does not exist at {commit}: {path}")
        return result

    def _object_exists(self, commit: str, path: str) -> bool:
        result = subprocess.run(
            [self.git_path, "-C", str(self.root), "cat-file", "-e", f"{commit}:{path}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=self.env,
            check=False,
        )
        return result.returncode == 0

    @staticmethod
    def _decode_text(content: bytes, path: str) -> str:
        if b"\x00" in content:
            raise BoundedScopeError(f"Bounded scope does not support binary file: {path}")
        try:
            return content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise BoundedScopeError(
                f"Bounded scope file is not valid UTF-8 and may be binary: {path}"
            ) from exc

    def _z_paths(self, args: Sequence[str]) -> List[str]:
        raw = self._run(args)
        try:
            return [
                item.decode("utf-8", errors="strict")
                for item in raw.split(b"\x00")
                if item
            ]
        except UnicodeDecodeError as exc:
            raise BoundedScopeError("Git scope contains a non-UTF-8 path") from exc

    def _binary_numstat_paths(self, args: Sequence[str]) -> set[str]:
        raw = self._run(args)
        binary = set()
        for record in raw.split(b"\x00"):
            if not record:
                continue
            fields = record.split(b"\t", 2)
            if len(fields) == 3 and fields[0] == b"-" and fields[1] == b"-":
                try:
                    binary.add(fields[2].decode("utf-8", errors="strict"))
                except UnicodeDecodeError as exc:
                    raise BoundedScopeError(
                        "Git scope contains a non-UTF-8 path"
                    ) from exc
        return binary

    def _run_text(self, args: Sequence[str], *, cwd: Path) -> str:
        return self._run(args, cwd=cwd).decode("utf-8", errors="strict")

    def _run(
        self,
        args: Sequence[str],
        *,
        cwd: Optional[Path] = None,
        allowed_exit_codes: Sequence[int] = (0,),
    ) -> bytes:
        try:
            result = subprocess.run(
                [self.git_path, "--literal-pathspecs", "-C", str(cwd or self.root), *args],
                capture_output=True,
                timeout=30,
                check=False,
                env=self.env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise BoundedScopeError(f"Git inspection failed: {exc}") from exc
        if result.returncode not in allowed_exit_codes:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            raise BoundedScopeError(detail or f"Git command failed with {result.returncode}")
        return result.stdout

    @staticmethod
    def _metrics(
        files: Sequence[Mapping[str, object]], lines: int, content_bytes: int
    ) -> Dict[str, int]:
        return {"files": len(files), "lines": lines, "bytes": content_bytes}


def build_bounded_packet(
    cwd: str,
    scope: Mapping[str, object],
    *,
    evidence: Optional[Mapping[str, object]] = None,
) -> BoundedPacket:
    return _GitPacketBuilder(cwd).build(scope, evidence)


def _canonical_json(payload: object) -> str:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256_json(payload: object) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
