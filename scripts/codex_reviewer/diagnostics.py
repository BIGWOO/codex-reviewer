"""Non-inference CLI readiness checks and opt-in full environment diagnostics."""

from __future__ import annotations

import json
import subprocess
from typing import Dict, List, Mapping, Optional, Sequence

from .catalog import CodexBinary


DEFAULT_DIAGNOSTIC_TIMEOUT = 90


def _probe(command: Sequence[str], timeout: int) -> Dict[str, object]:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "incomplete", "reason": "timeout", "timeout_seconds": timeout}
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        return {"status": "fail", "error": type(exc).__name__}
    # Do not put authentication output (including API key previews) in receipts.
    return {
        "status": "pass" if result.returncode == 0 else "fail",
        "exit_code": result.returncode,
    }


def cli_health_checks(
    binary: CodexBinary,
    *,
    profile: Optional[str] = None,
    strict_config: bool = False,
    full_diagnostics: bool = False,
    diagnostic_timeout: int = DEFAULT_DIAGNOSTIC_TIMEOUT,
) -> List[Dict[str, object]]:
    assert binary.path is not None
    prefix = [binary.path]
    if profile:
        prefix.extend(["--profile", profile])
    if strict_config:
        prefix.append("--strict-config")
    config = _probe([*prefix, "features", "list"], 10)
    credentials = _probe([binary.path, "login", "status"], 10)
    statuses = {config["status"], credentials["status"]}
    health_status = (
        "fail" if "fail" in statuses
        else "incomplete" if "incomplete" in statuses
        else "pass"
    )
    checks: List[Dict[str, object]] = [
        {
            "name": "auth_config",
            "status": health_status,
            "detail": {
                "config_load": config,
                "credentials": credentials,
                "authentication_scope": "stored credentials only; provider access is not verified",
            },
        }
    ]
    if not full_diagnostics:
        checks.append({
            "name": "full_diagnostics",
            "status": "skip",
            "detail": "Use --full-diagnostics for complete Codex environment diagnostics",
        })
        return checks

    try:
        result = subprocess.run(
            [*prefix, "doctor", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=diagnostic_timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        checks.append({
            "name": "full_diagnostics",
            "status": "incomplete",
            "detail": {"reason": "timeout", "timeout_seconds": diagnostic_timeout},
        })
        return checks
    except (OSError, UnicodeError, subprocess.SubprocessError) as exc:
        checks.append({
            "name": "full_diagnostics",
            "status": "fail",
            "detail": type(exc).__name__,
        })
        return checks

    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = None
    report = payload.get("checks") if isinstance(payload, Mapping) else None
    overall = payload.get("overallStatus") if isinstance(payload, Mapping) else None
    valid_statuses = {"ok", "warning", "fail"}
    if (
        not isinstance(report, Mapping)
        or not report
        or not isinstance(overall, str)
        or overall not in valid_statuses
        or any(
            not isinstance(check, Mapping)
            or not isinstance(check.get("status"), str)
            or check.get("status") not in valid_statuses
            for check in report.values()
        )
    ):
        checks.append({
            "name": "full_diagnostics",
            "status": "fail",
            "detail": "Unparseable codex doctor output",
        })
        return checks
    source_statuses = {check["status"] for check in report.values()}
    status = (
        "fail" if result.returncode != 0 or overall == "fail" or "fail" in source_statuses
        else "warn" if overall == "warning" or "warning" in source_statuses
        else "pass"
    )
    checks.append({
        "name": "full_diagnostics",
        "status": status,
        "detail": {
            "source_overall_status": overall,
            "checks": dict(report),
            "exit_code": result.returncode,
        },
    })
    return checks
