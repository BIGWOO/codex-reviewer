#!/usr/bin/env python3
"""Configurable Codex CLI stand-in used by the stdlib test suite."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time


DEFAULT_CATALOG = {
    "models": [
        {
            "slug": "gpt-5.6-sol",
            "supported_reasoning_levels": [
                {"effort": effort}
                for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
            ],
            "context_window": 372000,
            "max_context_window": 372000,
            "additional_speed_tiers": ["fast"],
        },
        {
            "slug": "gpt-5.6-terra",
            "supported_reasoning_levels": [
                {"effort": effort}
                for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
            ],
            "context_window": 372000,
            "max_context_window": 372000,
            "additional_speed_tiers": [],
        },
        {
            "slug": "gpt-5.5",
            "supported_reasoning_levels": [
                {"effort": effort} for effort in ("low", "medium", "high", "xhigh")
            ],
            "context_window": 272000,
            "max_context_window": 272000,
            "additional_speed_tiers": [],
        },
    ]
}


def _sidecar(name: str) -> Path:
    return Path(sys.argv[0]).resolve().parent / name


def _read_text(name: str, default: str) -> str:
    path = _sidecar(name)
    return path.read_text(encoding="utf-8").strip() if path.exists() else default


def _catalog(bundled: bool) -> dict:
    name = ".fake_bundled_catalog.json" if bundled else ".fake_catalog.json"
    path = _sidecar(name)
    if not path.exists() and bundled:
        path = _sidecar(".fake_catalog.json")
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return DEFAULT_CATALOG


def _append_log(stdin_payload: str | None) -> None:
    raw_path = os.environ.get("FAKE_CODEX_LOG")
    if not raw_path:
        return
    path = Path(raw_path)
    records = []
    if path.exists():
        records = json.loads(path.read_text(encoding="utf-8"))
    records.append(
        {
            "argv": sys.argv[1:],
            "stdin": stdin_payload,
            "cwd": os.getcwd(),
            "path": os.environ.get("PATH"),
        }
    )
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")


def _spawn_child() -> None:
    pid_path = os.environ.get("FAKE_CODEX_CHILD_PID")
    if not pid_path:
        return
    child_code = "import time; time.sleep(120)"
    if os.environ.get("FAKE_CODEX_CHILD_IGNORE_TERM"):
        child_code = (
            "import signal,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(120)"
        )
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    Path(pid_path).write_text(str(child.pid), encoding="utf-8")


def main() -> int:
    args = sys.argv[1:]
    if "--version" in args or args == ["-V"]:
        _append_log(None)
        print(f"codex-cli {_read_text('.fake_version', '0.144.1')}")
        return 0

    if args == ["update"]:
        _append_log(None)
        target = os.environ.get("FAKE_CODEX_UPDATE_VERSION")
        if target:
            _sidecar(".fake_version").write_text(target, encoding="utf-8")
        exit_code = int(os.environ.get("FAKE_CODEX_UPDATE_EXIT", "0"))
        if exit_code:
            print("fake update failed", file=sys.stderr)
            return exit_code
        print("fake update completed")
        return 0

    if (
        "debug" in args
        and args.index("debug") + 1 < len(args)
        and args[args.index("debug") + 1] == "models"
    ):
        _append_log(None)
        bundled = "--bundled" in args
        if not bundled and _sidecar(".fake_refresh_error").exists():
            print("catalog refresh failed", file=sys.stderr)
            return 3
        if _sidecar(".fake_invalid_catalog").exists():
            print("not-json")
            return 0
        print(json.dumps(_catalog(bundled)))
        return 0

    if "doctor" in args:
        _append_log(None)
        print(
            os.environ.get(
                "FAKE_CODEX_DOCTOR_OUTPUT",
                json.dumps({"status": "healthy", "auth": "ok", "config": "ok"}),
            )
        )
        return 0

    stdin_payload = (
        None
        if os.environ.get("FAKE_CODEX_SKIP_STDIN")
        else sys.stdin.read()
        if not sys.stdin.isatty()
        else None
    )
    _append_log(stdin_payload)
    _spawn_child()
    if os.environ.get("FAKE_CODEX_STDERR"):
        print(os.environ["FAKE_CODEX_STDERR"], file=sys.stderr, flush=True)

    sleep_seconds = float(os.environ.get("FAKE_CODEX_SLEEP", "0"))
    if sleep_seconds:
        if "--json" in args:
            print(
                json.dumps({"type": "thread.started", "thread_id": "partial-thread"}),
                flush=True,
            )
        time.sleep(sleep_seconds)

    exit_code = int(os.environ.get("FAKE_CODEX_EXIT", "0"))
    final_message = os.environ.get(
        "FAKE_CODEX_FINAL",
        '{"findings":[],"overall_correctness":"patch is correct",'
        '"overall_explanation":"seeded bug found","overall_confidence_score":0.99}',
    )
    if "--output-last-message" in args and not os.environ.get(
        "FAKE_CODEX_SKIP_LAST_MESSAGE"
    ):
        output_index = args.index("--output-last-message") + 1
        if output_index < len(args):
            Path(args[output_index]).write_text(final_message, encoding="utf-8")
    if "--json" in args:
        events = [
            {"type": "thread.started", "thread_id": "fake-thread"},
            {"type": "turn.started"},
        ]
        if exit_code:
            events.append(
                {"type": "turn.failed", "error": {"message": "fake inference failure"}}
            )
        elif not os.environ.get("FAKE_CODEX_NO_FINAL"):
            events.extend(
                [
                    {
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": final_message},
                    },
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 101, "output_tokens": 23},
                    },
                ]
            )
        else:
            events.append(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 101, "output_tokens": 0},
                }
            )
        for event in events:
            print(json.dumps(event), flush=True)
    else:
        print(final_message, flush=True)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
