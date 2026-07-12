"""Install-source-aware Codex CLI bootstrap and update checks."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterator, List, Optional, Sequence, Tuple

from .catalog import (
    CODEX_BIN_ENV,
    INSTALL_NPM,
    INSTALL_STANDALONE,
    CodexBinary,
)


AUTO_UPDATE_ENV = "CODEX_REVIEWER_AUTO_UPDATE"
UPDATE_TTL_ENV = "CODEX_REVIEWER_UPDATE_TTL_SECONDS"
UPDATE_CACHE_ENV = "CODEX_REVIEWER_UPDATE_CACHE"
DEFAULT_UPDATE_TTL_SECONDS = 24 * 60 * 60
UPDATE_FAILURE_TTL_SECONDS = 15 * 60
DEFAULT_UPDATE_TIMEOUT = 180
LOCK_STALE_SECONDS = 10 * 60


@dataclass
class UpdateOutcome:
    enabled: bool
    install_method: str
    attempted: bool = False
    checked: bool = False
    updated: bool = False
    bootstrapped: bool = False
    cache_hit: bool = False
    command: Optional[str] = None
    before_version: Optional[str] = None
    after_version: Optional[str] = None
    skipped_reason: Optional[str] = None
    error: Optional[str] = None
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _env_enabled() -> bool:
    value = os.environ.get(AUTO_UPDATE_ENV, "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _update_ttl() -> int:
    raw = os.environ.get(UPDATE_TTL_ENV)
    if not raw:
        return DEFAULT_UPDATE_TTL_SECONDS
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_UPDATE_TTL_SECONDS


def update_cache_path() -> Path:
    explicit = os.environ.get(UPDATE_CACHE_ENV)
    if explicit:
        return Path(explicit).expanduser()
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        root = Path(os.environ["LOCALAPPDATA"]) / "CodexReviewer" / "cache"
    else:
        root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
        root = root / "codex-reviewer"
    return root / "update-check.json"


def _cache_state(
    path: Path, binary: CodexBinary, ttl: int
) -> Optional[Tuple[str, Optional[str]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    checked_at = payload.get("checked_at")
    status = str(payload.get("status") or "ok")
    effective_ttl = UPDATE_FAILURE_TTL_SECONDS if status == "failed" else ttl
    if effective_ttl <= 0:
        return None
    age = (
        time.time() - float(checked_at) if isinstance(checked_at, (int, float)) else -1
    )
    if not (
        0 <= age < effective_ttl
        and payload.get("install_method") == binary.install_method
        and payload.get("version") == binary.version_string
        and payload.get("binary") == binary.path
    ):
        return None
    error = payload.get("error")
    return status, str(error) if error else None


def _write_cache(
    path: Path,
    binary: CodexBinary,
    *,
    status: str = "ok",
    error: Optional[str] = None,
) -> Optional[str]:
    payload = {
        "schema_version": 1,
        "checked_at": time.time(),
        "install_method": binary.install_method,
        "binary": binary.path,
        "version": binary.version_string,
        "status": status,
        "error": error,
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            prefix=".update-check.", suffix=".tmp", dir=path.parent
        )
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass
    except OSError as exc:
        return f"Could not persist Codex update cache: {exc}"
    return None


@contextmanager
def _update_lock(cache_path: Path) -> Iterator[bool]:
    lock = cache_path.parent / "update.lock"
    owned = False
    try:
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # A read-only cache root should not make review unavailable.
            yield True
            return
        try:
            lock.mkdir()
            owned = True
        except FileExistsError:
            try:
                stale = time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS
            except OSError:
                stale = False
            if stale:
                try:
                    lock.rmdir()
                    lock.mkdir()
                    owned = True
                except OSError:
                    yield False
                    return
            else:
                yield False
                return
        yield True
    finally:
        if owned:
            try:
                lock.rmdir()
            except OSError:
                pass


def _installer_command() -> Tuple[Sequence[str], str]:
    if os.name == "nt":
        command = [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-c",
            "$env:CODEX_NON_INTERACTIVE=1; irm https://chatgpt.com/codex/install.ps1 | iex",
        ]
        return command, "official standalone PowerShell installer"
    command = [
        "sh",
        "-c",
        "curl -fsSL https://chatgpt.com/codex/install.sh | CODEX_NON_INTERACTIVE=1 sh",
    ]
    return command, "official standalone installer"


def _npm_repair_command() -> Optional[Tuple[Sequence[str], str]]:
    npm = shutil.which("npm")
    if not npm:
        return None
    return [npm, "install", "-g", "@openai/codex"], "npm install -g @openai/codex"


def _run_update(
    command: Sequence[str], *, timeout: int
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _failure_detail(result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout).strip()
    if len(detail) > 1000:
        detail = detail[-1000:]
    return detail or f"exit status {result.returncode}"


def _should_repair(binary: CodexBinary, detail: str) -> bool:
    lowered = detail.lower()
    return (
        not binary.supported
        or "unknown command" in lowered
        or "unrecognized subcommand" in lowered
        or "unexpected argument 'update'" in lowered
    )


def prepare_codex_binary(
    requested: Optional[str] = None,
    *,
    check_updates: bool = True,
    force_update: bool = False,
    timeout: int = DEFAULT_UPDATE_TIMEOUT,
) -> tuple[CodexBinary, UpdateOutcome]:
    """Select npm first, otherwise standalone, and periodically update it.

    An explicit ``--codex-bin`` or ``CODEX_REVIEWER_CODEX_BIN`` is a lifecycle
    pin: it is honored exactly and never modified automatically.
    """

    binary = CodexBinary.discover(requested)
    explicit = requested is not None or bool(os.environ.get(CODEX_BIN_ENV))
    enabled = check_updates and _env_enabled()
    outcome = UpdateOutcome(
        enabled=enabled,
        install_method=binary.install_method,
        before_version=binary.version_string,
    )
    if explicit:
        outcome.skipped_reason = "explicit binary override"
        if force_update:
            outcome.warnings.append(
                "Automatic update is skipped for an explicit --codex-bin or CODEX_REVIEWER_CODEX_BIN"
            )
        return binary, outcome
    if not enabled:
        outcome.skipped_reason = "automatic update disabled"
        return binary, outcome

    cache_path = update_cache_path()
    cache_state = (
        _cache_state(cache_path, binary, _update_ttl())
        if binary.path and not force_update
        else None
    )
    if (
        binary.path
        and binary.install_method in {INSTALL_NPM, INSTALL_STANDALONE}
        and cache_state
    ):
        outcome.cache_hit = True
        outcome.after_version = binary.version_string
        if cache_state[0] == "failed":
            outcome.skipped_reason = "recent update failure backoff"
            outcome.error = cache_state[1] or "Previous Codex update check failed"
            outcome.warnings.append(
                outcome.error + "; retrying automatically after the failure backoff"
            )
        else:
            outcome.skipped_reason = "fresh update cache"
        return binary, outcome

    with _update_lock(cache_path) as acquired:
        if not acquired:
            outcome.skipped_reason = "another update check is in progress"
            outcome.warnings.append(
                "Skipped Codex update because another reviewer process holds the update lock"
            )
            return CodexBinary.discover(), outcome

        outcome.attempted = True
        if binary.path and binary.install_method in {INSTALL_NPM, INSTALL_STANDALONE}:
            command: Sequence[str] = [binary.path, "update"]
            command_label = f"{binary.path} update"
        elif binary.install_method == INSTALL_NPM:
            repair = _npm_repair_command()
            if repair is None:
                outcome.error = "npm-managed Codex was found but npm is unavailable"
                return binary, outcome
            command, command_label = repair
        else:
            command, command_label = _installer_command()
            outcome.bootstrapped = True
        outcome.command = command_label

        try:
            result = _run_update(command, timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            result = None
            first_error = str(exc)
        else:
            first_error = _failure_detail(result) if result.returncode != 0 else ""

        if result is None or result.returncode != 0:
            # Older CLIs may not have `codex update`. Repair through the same
            # package manager, never by silently switching npm users to standalone.
            fallback = (
                _npm_repair_command()
                if binary.install_method == INSTALL_NPM
                else _installer_command()
                if binary.install_method == INSTALL_STANDALONE
                else None
            )
            if (
                fallback
                and list(fallback[0]) != list(command)
                and _should_repair(binary, first_error)
            ):
                outcome.command = fallback[1]
                try:
                    result = _run_update(fallback[0], timeout=timeout)
                except (OSError, subprocess.SubprocessError) as exc:
                    result = None
                    first_error = f"{first_error}; fallback failed: {exc}"
                else:
                    if result.returncode != 0:
                        first_error = (
                            f"{first_error}; fallback failed: {_failure_detail(result)}"
                        )

        if result is None or result.returncode != 0:
            outcome.error = f"Codex update check failed: {first_error}"
            outcome.warnings.append(
                outcome.error + "; continuing with the currently selected CLI"
            )
            cache_error = _write_cache(
                cache_path, binary, status="failed", error=outcome.error
            )
            if cache_error:
                outcome.warnings.append(cache_error)
            return binary, outcome

        refreshed = CodexBinary.discover()
        outcome.checked = True
        outcome.install_method = refreshed.install_method
        outcome.after_version = refreshed.version_string
        outcome.updated = (
            outcome.before_version is not None
            and outcome.after_version is not None
            and outcome.before_version != outcome.after_version
        )
        cache_error = _write_cache(cache_path, refreshed)
        if cache_error:
            outcome.warnings.append(cache_error)
        return refreshed, outcome
