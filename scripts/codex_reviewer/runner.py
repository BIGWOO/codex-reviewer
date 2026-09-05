"""Safe Codex subprocess execution and JSONL event handling."""

from __future__ import annotations

import hashlib
import errno
import json
import os
import queue
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .compat import restrict_file_permissions
from .catalog import CodexBinary
from .result import ReviewResult


HEARTBEAT_SECONDS = 30
ERROR_DETAIL_LIMIT = 8000
ITEM_WARNING_DETAIL_LIMIT = 500


class _ReviewInterrupted(BaseException):
    def __init__(self, signum: int):
        self.signum = signum


TOOL_ITEM_TYPES = {
    "command_execution": "command",
    "file_change": "file_change",
    "mcp_tool_call": "mcp",
    "web_search": "web",
    "browser_tool_call": "browser",
    "collab_tool_call": "collaboration",
    "collaboration_tool_call": "collaboration",
    "dynamic_tool_call": "dynamic",
}

BOUNDED_EVENT_TYPES = frozenset({
    "thread.started", "turn.started", "turn.completed", "turn.failed", "error",
})
BOUNDED_ITEM_EVENTS = frozenset({"item.started", "item.updated", "item.completed"})
BOUNDED_SAFE_ITEMS = frozenset({"agent_message", "reasoning", "error"})

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]

try:
    import msvcrt
except ImportError:
    msvcrt = None


def sanitize_command(cmd: Sequence[str], sensitive_values: Iterable[str] = ()) -> str:
    sensitive = {value for value in sensitive_values if value}
    redacted = []
    for argument in cmd:
        if argument in sensitive:
            redacted.append("<prompt>")
        elif argument.startswith("shell_environment_policy.set.PATH="):
            redacted.append('shell_environment_policy.set.PATH="<injected>"')
        else:
            redacted.append(argument)
    return shlex.join(redacted)


def parse_jsonl_line(line: str) -> Optional[Dict[str, object]]:
    if not line.strip():
        return None
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def extract_final(events: Sequence[Mapping[str, object]]) -> Optional[str]:
    completion_index = next(
        (
            index
            for index in range(len(events) - 1, -1, -1)
            if events[index].get("type") == "turn.completed"
        ),
        None,
    )
    if completion_index is None:
        return None
    for event in reversed(events[: completion_index + 1]):
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, Mapping) and item.get("type") == "agent_message":
                text = item.get("text")
                if isinstance(text, str):
                    return text
                nested = _nested_message_text(item)
                if nested:
                    return nested
        if event.get("type") == "agent_message":
            nested = _nested_message_text(event)
            if nested:
                return nested
    return None


def extract_last_agent_message(
    events: Sequence[Mapping[str, object]],
) -> Optional[str]:
    """Return non-terminal agent progress without treating it as a final result."""
    for event in reversed(events):
        if event.get("type") == "item.completed":
            item = event.get("item")
            if isinstance(item, Mapping) and item.get("type") == "agent_message":
                nested = _nested_message_text(item)
                if nested:
                    return nested
        if event.get("type") == "agent_message":
            nested = _nested_message_text(event)
            if nested:
                return nested
    return None


def has_turn_completed(events: Sequence[Mapping[str, object]]) -> bool:
    return any(event.get("type") == "turn.completed" for event in events)


def terminal_event_type(
    events: Sequence[Mapping[str, object]],
) -> Optional[str]:
    for event in reversed(events):
        event_type = event.get("type")
        if event_type in {"turn.completed", "turn.failed"}:
            return str(event_type)
    return None


def last_event_type(
    events: Sequence[Mapping[str, object]],
) -> Optional[str]:
    """Return the final parsed JSONL event type, including an item subtype."""
    if not events:
        return None
    event = events[-1]
    event_type = event.get("type")
    if not isinstance(event_type, str):
        return None
    item = event.get("item")
    if isinstance(item, Mapping) and isinstance(item.get("type"), str):
        return f"{event_type}:{item['type']}"
    return event_type


def count_events(events: Sequence[Mapping[str, object]]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for event in events:
        event_type = event.get("type")
        if isinstance(event_type, str):
            counts[event_type] = counts.get(event_type, 0) + 1
        item = event.get("item")
        if isinstance(item, Mapping) and isinstance(item.get("type"), str):
            item_key = f"item.{item['type']}"
            counts[item_key] = counts.get(item_key, 0) + 1
    return counts


def tool_call_descriptor(
    event: Mapping[str, object],
) -> Optional[Dict[str, object]]:
    """Return a normalized descriptor for JSONL events that prove tool use."""
    event_type = event.get("type")
    item = event.get("item")
    payload = item if isinstance(item, Mapping) else event
    item_type = payload.get("type")
    if not isinstance(item_type, str):
        return None
    normalized = item_type.lower()
    category = TOOL_ITEM_TYPES.get(normalized)
    if (
        category is None
        and "dynamic" in normalized
        and "tool" in normalized
        and "call" in normalized
    ):
        category = "dynamic"
    if category is None:
        if "command" in normalized and (
            "execution" in normalized or "call" in normalized
        ):
            category = "command"
        elif "mcp" in normalized:
            category = "mcp"
        elif "web" in normalized and "search" in normalized:
            category = "web"
        elif "browser" in normalized:
            category = "browser"
        elif "collab" in normalized or "collaboration" in normalized:
            category = "collaboration"
    if category is None:
        return None
    namespace = payload.get("namespace")
    if category == "dynamic" and isinstance(namespace, str):
        normalized_namespace = namespace.lower()
        if normalized_namespace == "browser" or normalized_namespace.startswith("browser."):
            category = "browser"
    call_id = payload.get("id") or event.get("id")
    return {
        "category": category,
        "event_type": event_type,
        "item_type": item_type,
        "call_id": call_id if isinstance(call_id, str) else None,
    }


def extract_item_warnings(
    events: Sequence[Mapping[str, object]],
) -> List[str]:
    warnings: List[str] = []
    for event in events:
        if event.get("type") != "item.completed":
            continue
        item = event.get("item")
        if not isinstance(item, Mapping) or item.get("type") != "error":
            continue
        message = item.get("message")
        if isinstance(message, str) and message and message not in warnings:
            warnings.append(message)
    return warnings


def extract_usage(
    events: Sequence[Mapping[str, object]],
) -> Optional[Mapping[str, object]]:
    for event in reversed(events):
        if event.get("type") == "turn.completed" and isinstance(
            event.get("usage"), Mapping
        ):
            return event["usage"]  # type: ignore[return-value]
    return None


def extract_error(events: Sequence[Mapping[str, object]]) -> Optional[str]:
    for event in reversed(events):
        event_type = event.get("type")
        if event_type in {"turn.failed", "error"}:
            error = event.get("error")
            if isinstance(error, Mapping) and isinstance(error.get("message"), str):
                return error["message"]
            if isinstance(error, str):
                return error
            if isinstance(event.get("message"), str):
                return event["message"]  # type: ignore[return-value]
    return None


def _nested_message_text(payload: Mapping[str, object]) -> Optional[str]:
    direct = payload.get("text")
    if isinstance(direct, str):
        return direct
    message = payload.get("message")
    if not isinstance(message, Mapping):
        return None
    content = message.get("content")
    if not isinstance(content, list):
        return None
    texts = []
    for item in content:
        if isinstance(item, Mapping) and isinstance(item.get("text"), str):
            texts.append(item["text"])
    return "\n".join(texts) if texts else None


def _progress_event(event: Mapping[str, object]) -> Optional[str]:
    event_type = event.get("type")
    if event_type == "thread.started":
        thread_id = event.get("thread_id")
        return f"thread started {thread_id}" if thread_id else "thread started"
    if event_type == "turn.started":
        return "turn started"
    if event_type == "turn.completed":
        return "turn completed"
    if event_type == "turn.failed":
        return "turn failed"
    if event_type != "item.completed":
        return None
    item = event.get("item")
    if not isinstance(item, Mapping):
        return None
    item_type = item.get("type")
    if item_type == "command_execution":
        return f"command completed exit={item.get('exit_code')}"
    if item_type == "agent_message":
        return "agent message received; waiting for turn.completed"
    if item_type == "reasoning":
        return "reasoning step completed"
    if item_type == "error":
        message = item.get("message")
        if isinstance(message, str) and message:
            if len(message) > ITEM_WARNING_DETAIL_LIMIT:
                message = message[: ITEM_WARNING_DETAIL_LIMIT - 3] + "..."
            return f"warning: {message}"
        return "warning: Codex reported a non-terminal item error"
    return f"{item_type or 'item'} completed"


class CodexProcessRunner:
    """Execute one Codex command without inheriting stdin or orphaning children."""

    def __init__(
        self,
        binary: CodexBinary,
        timeout: int,
        idle_timeout: int = 180,
        json_output: bool = True,
        output_file: Optional[str] = None,
        last_message_output: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        heartbeat_seconds: int = HEARTBEAT_SECONDS,
        max_tool_calls: Optional[int] = None,
        max_jsonl_bytes: Optional[int] = None,
    ):
        self.binary = binary
        self.timeout = timeout
        self.idle_timeout = idle_timeout
        self.json_output = json_output
        self.output_file = output_file
        self.last_message_output = last_message_output
        self.env = dict(env or os.environ)
        self.heartbeat_seconds = heartbeat_seconds
        self.max_tool_calls = max_tool_calls
        self.max_jsonl_bytes = max_jsonl_bytes
        if max_tool_calls is not None and max_tool_calls < 0:
            raise ValueError("max_tool_calls cannot be negative")
        if max_jsonl_bytes is not None and max_jsonl_bytes < 1:
            raise ValueError("max_jsonl_bytes must be positive")

    def run(
        self,
        cmd: Sequence[str],
        *,
        mode: str,
        scope: Optional[Mapping[str, object]],
        model: Optional[str],
        effort: Optional[str],
        service_tier: Optional[str],
        warnings: Optional[Sequence[str]] = None,
        sensitive_values: Iterable[str] = (),
        stdin_payload: Optional[str] = None,
        lock_key: Optional[str] = None,
        lock_keys: Optional[Sequence[str]] = None,
    ) -> Dict[str, object]:
        sensitive = tuple(value for value in sensitive_values if value)
        if stdin_payload:
            sensitive = (*sensitive, stdin_payload)
        sanitized = sanitize_command(cmd, sensitive)
        output_path = Path(self.output_file).expanduser() if self.output_file else None
        last_message_path = (
            Path(self.last_message_output).expanduser()
            if self.last_message_output
            else None
        )
        output_handle = None
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        events: List[Mapping[str, object]] = []
        event_queue: "queue.Queue[Tuple[str, str, float]]" = queue.Queue()
        started_at = time.monotonic()
        last_activity_at = started_at
        last_heartbeat = started_at
        process: Optional[subprocess.Popen[str]] = None
        windows_job = None
        timed_out = False
        timeout_reason: Optional[str] = None
        timeout_silence_duration_ms: Optional[int] = None
        stdin_thread: Optional[threading.Thread] = None
        stdin_errors: List[str] = []
        stream_errors: List[str] = []
        lock_descriptors: List[int] = []
        runtime_warnings = list(warnings or [])
        policy_violation: Optional[Dict[str, object]] = None
        tool_call_keys = set()
        tool_call_count = 0
        raw_output_bytes_seen = 0
        interrupted = False
        last_message_initialized = False
        previous_handlers = {}
        stdout_thread = None
        stderr_thread = None

        def interrupt(signum, _frame) -> None:
            raise _ReviewInterrupted(signum)

        def read_stream(stream, stream_name: str) -> None:
            try:
                for line in iter(stream.readline, ""):
                    event_queue.put((stream_name, line, time.monotonic()))
            except (OSError, UnicodeError):
                stream_errors.append(f"Could not read Codex {stream_name} as UTF-8")
            finally:
                stream.close()

        def write_stdin(stream, payload: str) -> None:
            try:
                stream.write(payload)
            except (OSError, UnicodeError) as exc:
                stdin_errors.append(str(exc))
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        def consume(stream_name: str, line: str, activity_at: float) -> None:
            nonlocal last_activity_at, raw_output_bytes_seen, tool_call_count, policy_violation
            event = self._consume_line(
                stream_name, line, stdout_lines, stderr_lines, events,
                output_handle, sensitive,
            )
            last_activity_at = max(last_activity_at, activity_at)
            if stream_name != "stdout":
                return
            raw_output_bytes_seen += len(line.encode("utf-8"))
            descriptor = tool_call_descriptor(event) if event is not None else None
            if descriptor is not None:
                call_id = descriptor.get("call_id")
                call_key = (
                    f"{descriptor['category']}:{call_id}" if call_id
                    else f"event:{len(events)}:{descriptor['item_type']}"
                )
                if call_key not in tool_call_keys:
                    tool_call_keys.add(call_key)
                    tool_call_count += 1
                if self.max_tool_calls is not None and tool_call_count > self.max_tool_calls:
                    policy_violation = policy_violation or {
                        "reason": "tool_call", "observed": tool_call_count,
                        "limit": self.max_tool_calls, **descriptor,
                    }
            if mode == "bounded" and line.strip():
                event_type = event.get("type") if event is not None else None
                item = event.get("item") if event is not None else None
                known = event_type in BOUNDED_EVENT_TYPES or (
                    event_type in BOUNDED_ITEM_EVENTS
                    and isinstance(item, Mapping)
                    and item.get("type") in BOUNDED_SAFE_ITEMS
                )
                if not known:
                    policy_violation = policy_violation or {"reason": "unknown_event"}
            if self.max_jsonl_bytes is not None and raw_output_bytes_seen > self.max_jsonl_bytes:
                policy_violation = policy_violation or {
                    "reason": "jsonl_bytes_exceeded", "observed": raw_output_bytes_seen,
                    "limit": self.max_jsonl_bytes,
                }

        try:
            if threading.current_thread() is threading.main_thread():
                signals = [signal.SIGINT, signal.SIGTERM]
                if os.name == "nt":
                    signals.append(signal.SIGBREAK)
                for signum in signals:
                    previous_handlers[signum] = signal.getsignal(signum)
                    signal.signal(signum, interrupt)
            requested_lock_keys = list(lock_keys or [])
            if lock_key:
                requested_lock_keys.append(lock_key)
            for requested_lock_key in sorted(set(requested_lock_keys)):
                lock_descriptor, lock_error = self._acquire_execution_lock(
                    requested_lock_key
                )
                if lock_error:
                    for descriptor in reversed(lock_descriptors):
                        self._release_execution_lock(descriptor)
                    lock_descriptors.clear()
                    return ReviewResult(
                        success=False,
                        mode=mode,
                        binary=self.binary.path,
                        version=self.binary.version_string,
                        scope=scope,
                        model=model,
                        effort=effort,
                        timeout=self.timeout,
                        idle_timeout=self.idle_timeout,
                        hard_timeout=self.timeout,
                        service_tier=service_tier,
                        warnings=runtime_warnings,
                        command=sanitized,
                        error=lock_error,
                    ).to_dict()
                if lock_descriptor is None:
                    runtime_warnings.append(
                        "Single-flight locking is unavailable on this platform"
                    )
                else:
                    lock_descriptors.append(lock_descriptor)
            if (
                output_path
                and last_message_path
                and output_path.resolve() == last_message_path.resolve()
            ):
                raise ValueError("Raw output and last-message paths must be distinct")
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_handle = self._open_private(output_path)
            if last_message_path:
                last_message_path.parent.mkdir(parents=True, exist_ok=True)
                with self._open_private(last_message_path):
                    pass
                last_message_initialized = True

            print(f"[codex-review] starting: {sanitized}", file=sys.stderr, flush=True)
            launch_cmd = list(cmd)
            launch_payload = stdin_payload
            if os.name == "nt":
                from .windows_job import WindowsJob, launcher_command
                windows_job = WindowsJob()
                launch_cmd = launcher_command(cmd)
                launch_payload = "\0" + (stdin_payload or "")
            process = subprocess.Popen(
                launch_cmd,
                stdin=subprocess.PIPE
                if launch_payload is not None
                else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True, encoding="utf-8",
                bufsize=1,
                env=self.env,
                start_new_session=(os.name != "nt"),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
            )
            if windows_job is not None:
                windows_job.assign(process.pid)
            if process.stdout is None or process.stderr is None:
                raise RuntimeError("failed to capture Codex stdout/stderr")

            stdout_thread = threading.Thread(
                target=read_stream, args=(process.stdout, "stdout"), daemon=True
            )
            stderr_thread = threading.Thread(
                target=read_stream, args=(process.stderr, "stderr"), daemon=True
            )
            stdout_thread.start()
            stderr_thread.start()
            if launch_payload is not None and process.stdin is not None:
                stdin_thread = threading.Thread(
                    target=write_stdin,
                    args=(process.stdin, launch_payload),
                    daemon=True,
                )
                stdin_thread.start()

            while True:
                try:
                    stream_name, line, activity_at = event_queue.get(timeout=0.2)
                    consume(stream_name, line, activity_at)
                    if policy_violation is not None:
                        self._terminate_process_group(process, windows_job)
                        break
                except queue.Empty:
                    pass

                if stream_errors:
                    raise RuntimeError(stream_errors[0])

                now = time.monotonic()
                if (
                    process.poll() is not None
                    and event_queue.empty()
                    and not stdout_thread.is_alive()
                    and not stderr_thread.is_alive()
                ):
                    break
                if now - last_heartbeat >= self.heartbeat_seconds:
                    print(
                        f"[codex-review] still running ({int(now - started_at)}s elapsed)...",
                        file=sys.stderr,
                        flush=True,
                    )
                    last_heartbeat = now
                if now - started_at > self.timeout:
                    timed_out = True
                    timeout_reason = "hard"
                    timeout_silence_duration_ms = max(
                        0, int((now - last_activity_at) * 1000)
                    )
                    self._terminate_process_group(process, windows_job)
                    break
                if self.idle_timeout and now - last_activity_at > self.idle_timeout:
                    timed_out = True
                    timeout_reason = "idle"
                    timeout_silence_duration_ms = max(
                        0, int((now - last_activity_at) * 1000)
                    )
                    self._terminate_process_group(process, windows_job)
                    break

            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            if stdin_thread:
                stdin_thread.join(timeout=2)
            while not event_queue.empty():
                stream_name, line, activity_at = event_queue.get_nowait()
                consume(stream_name, line, activity_at)
            if process.poll() is None:
                process.wait(timeout=2)

            if stream_errors:
                raise RuntimeError(stream_errors[0])

            output = "".join(stdout_lines)
            stderr = self._summarize_stderr(
                self._redact_text("".join(stderr_lines), sensitive)
            )
            if stderr:
                print(
                    stderr,
                    end="" if stderr.endswith("\n") else "\n",
                    file=sys.stderr,
                    flush=True,
                )
            turn_completed = has_turn_completed(events)
            if policy_violation is not None:
                final = None
            elif self.json_output:
                final = extract_final(events)
            else:
                final = output.strip() or None
            if (
                not final
                and policy_violation is None
                and self.last_message_output
                and (not self.json_output or turn_completed)
            ):
                final = self._read_last_message(
                    Path(self.last_message_output).expanduser()
                )
            usage = extract_usage(events)
            exit_code = process.returncode
            terminal_error = extract_error(events)
            success = (
                exit_code == 0
                and not timed_out
                and policy_violation is None
                and not stdin_errors
                and terminal_error is None
                and (not self.json_output or turn_completed)
                and final is not None
            )
            error = None
            if policy_violation is not None:
                reason = str(policy_violation.get("reason") or "policy_violation")
                error = f"Codex review violated execution policy: {reason}"
            elif timed_out:
                suffix = (
                    f"; partial output written to {output_path}" if output_path else ""
                )
                elapsed_limit = (
                    self.idle_timeout if timeout_reason == "idle" else self.timeout
                )
                error = (
                    f"Codex review {timeout_reason or 'hard'} timed out after "
                    f"{elapsed_limit} seconds{suffix}"
                )
            elif exit_code != 0:
                error = self._error_detail(stderr, exit_code, events)
            elif stdin_errors:
                error = f"Failed to send the complete review prompt: {stdin_errors[-1]}"
            elif terminal_error:
                error = terminal_error
            elif self.json_output and not turn_completed:
                error = "Codex JSONL ended without a terminal turn.completed event"
            elif final is None:
                error = "Codex review completed without a final result"
            for warning in extract_item_warnings(events):
                if warning not in runtime_warnings:
                    runtime_warnings.append(warning)
            final = self._redact_text(final, sensitive) if final else None
            if policy_violation is not None and self.last_message_output:
                self._write_private(
                    Path(self.last_message_output).expanduser(), ""
                )
            elif final and self.last_message_output:
                self._write_private(Path(self.last_message_output).expanduser(), final)
            error = self._redact_text(error, sensitive) if error else None
            safe_warnings = [
                self._redact_text(warning, sensitive) for warning in runtime_warnings
            ]
            safe_output = self._redact_text(output, sensitive)
            safe_events = [self._redact_payload(event, sensitive) for event in events]
            safe_policy_violation = (
                self._redact_payload(policy_violation, sensitive)
                if policy_violation is not None
                else None
            )
            finished_at = time.monotonic()
            duration_ms = int((finished_at - started_at) * 1000)
            silence_duration_ms = (
                timeout_silence_duration_ms
                if timeout_silence_duration_ms is not None
                else max(0, int((finished_at - last_activity_at) * 1000))
            )
            terminal_event = terminal_event_type(events)
            last_event = last_event_type(events)
            event_counts = count_events(events)
            event_counts["tool_calls"] = tool_call_count
            partial_progress = None
            if not success:
                partial = extract_last_agent_message(events)
                if partial:
                    partial_progress = self._redact_text(partial, sensitive)

            print(
                f"[codex-review] finished in {int(time.monotonic() - started_at)}s",
                file=sys.stderr,
                flush=True,
            )
            return ReviewResult(
                success=success,
                mode=mode,
                binary=self.binary.path,
                version=self.binary.version_string,
                scope=scope,
                model=model,
                effort=effort,
                usage=usage,
                timeout=self.timeout,
                idle_timeout=self.idle_timeout,
                hard_timeout=self.timeout,
                timed_out=timed_out,
                timeout_reason=timeout_reason,
                exit_code=exit_code,
                service_tier=service_tier,
                warnings=safe_warnings,
                command=sanitized,
                final=final,
                error=error,
                output=safe_output,
                events=safe_events,
                partial_progress=partial_progress,
                execution_status=(
                    "policy_violation"
                    if policy_violation is not None
                    else "timed_out"
                    if timed_out
                    else "completed"
                    if turn_completed
                    else "failed"
                ),
                duration_ms=duration_ms,
                silence_duration_ms=silence_duration_ms,
                terminal_event=terminal_event,
                last_event=last_event,
                event_counts=event_counts,
                raw_output_bytes=len(output.encode("utf-8")),
                policy_violation=safe_policy_violation,
            ).to_dict()
        except (_ReviewInterrupted, KeyboardInterrupt) as exc:
            interrupted = True
            signum = exc.signum if isinstance(exc, _ReviewInterrupted) else signal.SIGINT
            if signum == getattr(signal, "SIGBREAK", None):
                signum = signal.SIGINT
            return ReviewResult(
                success=False,
                mode=mode,
                binary=self.binary.path,
                version=self.binary.version_string,
                scope=scope,
                model=model,
                effort=effort,
                timeout=self.timeout,
                idle_timeout=self.idle_timeout,
                hard_timeout=self.timeout,
                service_tier=service_tier,
                warnings=runtime_warnings,
                command=sanitized,
                error=f"Codex review interrupted by signal {signum}",
                exit_code=128 + int(signum),
                execution_status="interrupted",
                review_verdict="inconclusive",
                gate_status="inconclusive",
                duration_ms=int((time.monotonic() - started_at) * 1000),
            ).to_dict()
        except FileNotFoundError:
            return ReviewResult(
                success=False,
                mode=mode,
                binary=self.binary.path,
                version=self.binary.version_string,
                scope=scope,
                model=model,
                effort=effort,
                timeout=self.timeout,
                idle_timeout=self.idle_timeout,
                hard_timeout=self.timeout,
                service_tier=service_tier,
                warnings=list(warnings or []),
                command=sanitized,
                error="Codex CLI not found",
            ).to_dict()
        except Exception as exc:
            return ReviewResult(
                success=False,
                mode=mode,
                binary=self.binary.path,
                version=self.binary.version_string,
                scope=scope,
                model=model,
                effort=effort,
                timeout=self.timeout,
                idle_timeout=self.idle_timeout,
                hard_timeout=self.timeout,
                service_tier=service_tier,
                warnings=list(warnings or []),
                command=sanitized,
                error=f"Unexpected error: {exc}",
            ).to_dict()
        finally:
            try:
                # A second cancellation must not interrupt child reaping or release
                # the single-flight lock while the previous reviewer still runs.
                for signum in previous_handlers:
                    signal.signal(signum, signal.SIG_IGN)
                if process is not None and (
                    windows_job is not None or interrupted or policy_violation is not None or process.poll() is None
                ):
                    self._terminate_process_group(process, windows_job)
                if windows_job is not None:
                    windows_job.close()
                for worker in (stdout_thread, stderr_thread, stdin_thread):
                    if worker is not None:
                        worker.join(timeout=2)
                if interrupted and last_message_initialized and last_message_path:
                    self._write_private(last_message_path, "")
            finally:
                try:
                    if windows_job is not None:
                        windows_job.close()
                    if output_handle:
                        output_handle.close()
                    for descriptor in reversed(lock_descriptors):
                        self._release_execution_lock(descriptor)
                finally:
                    for signum, handler in previous_handlers.items():
                        signal.signal(signum, handler)

    def _consume_line(
        self,
        stream_name: str,
        line: str,
        stdout_lines: List[str],
        stderr_lines: List[str],
        events: List[Mapping[str, object]],
        output_handle,
        sensitive_values: Sequence[str],
    ) -> Optional[Mapping[str, object]]:
        if stream_name == "stderr":
            stderr_lines.append(line)
            return None
        stdout_lines.append(line)
        if output_handle:
            output_handle.write(line)
            output_handle.flush()
        if not self.json_output:
            return None
        event = parse_jsonl_line(line)
        if not event:
            return None
        events.append(event)
        progress = _progress_event(event)
        if progress:
            safe_progress = self._redact_text(progress, sensitive_values)
            print(f"[codex-review] {safe_progress}", file=sys.stderr, flush=True)
        return event

    @staticmethod
    def _acquire_execution_lock(lock_key: str) -> Tuple[Optional[int], Optional[str]]:
        if fcntl is None and msvcrt is None:
            raise RuntimeError("No supported reviewer execution lock is available")
        if os.name == "nt":
            lock_key = os.path.normcase(lock_key)
        digest = hashlib.sha256(lock_key.encode("utf-8")).hexdigest()
        lock_directory = Path(tempfile.gettempdir()) / "codex-reviewer-locks"
        lock_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            lock_directory.chmod(0o700)
        except OSError:
            pass
        lock_path = lock_directory / f"{digest}.lock"
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        try:
            restrict_file_permissions(descriptor)
            try:
                if fcntl is not None:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                owner = ""
                if fcntl is not None:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    owner = os.read(descriptor, 64).decode("ascii", errors="ignore").strip()
                owner_detail = f" (owner PID {owner})" if owner.isdigit() else ""
                os.close(descriptor)
                return None, (
                    "Another Codex review is already running for this repository "
                    f"and scope{owner_detail}; wait for it to finish or terminate "
                    "it before retrying"
                )
            # Keep the locked byte in place on Windows; do not truncate a live region.
            if fcntl is not None:
                os.ftruncate(descriptor, 0)
            else:
                os.lseek(descriptor, 1, os.SEEK_SET)
            os.write(descriptor, str(os.getpid()).encode("ascii"))
            os.fsync(descriptor)
            return descriptor, None
        except Exception:
            os.close(descriptor)
            raise

    @staticmethod
    def _release_execution_lock(descriptor: Optional[int]) -> None:
        if descriptor is None:
            return
        try:
            if fcntl is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            elif msvcrt is not None:
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(descriptor)

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str], windows_job=None) -> None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                process.poll()
                try:
                    os.killpg(process.pid, 0)
                except ProcessLookupError:
                    break
                except PermissionError:
                    break
                time.sleep(0.05)
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                pass
            return

        if windows_job is not None:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                process.wait(timeout=1)
            except (OSError, subprocess.TimeoutExpired):
                pass
            windows_job.terminate()
        # Assignment failure leaves only the waiting launcher, which must also be reaped.
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)

    @staticmethod
    def _error_detail(
        stderr: str,
        exit_code: Optional[int],
        events: Sequence[Mapping[str, object]],
    ) -> str:
        detail = stderr.strip()
        if len(detail) > ERROR_DETAIL_LIMIT:
            detail = detail[-ERROR_DETAIL_LIMIT:]
        return (
            detail or extract_error(events) or f"Codex exited with status {exit_code}"
        )

    @staticmethod
    def _summarize_stderr(value: str) -> str:
        if not value:
            return value
        ordered: List[str] = []
        counts: Dict[str, int] = {}
        for line in value.splitlines():
            if line not in counts:
                ordered.append(line)
                counts[line] = 0
            counts[line] += 1
        rendered = [
            f"{line} [repeated {counts[line]} times]" if counts[line] > 1 else line
            for line in ordered
        ]
        return "\n".join(rendered) + ("\n" if value.endswith("\n") else "")

    @staticmethod
    def _redact_text(value: str, sensitive_values: Sequence[str]) -> str:
        redacted = value
        for sensitive in sorted(sensitive_values, key=len, reverse=True):
            variants = {sensitive}
            frontier = {sensitive}
            while frontier:
                next_frontier = set()
                for item in frontier:
                    for encoded in (
                        json.dumps(item)[1:-1],
                        json.dumps(item, ensure_ascii=False)[1:-1],
                    ):
                        if encoded in variants or len(encoded) > len(redacted):
                            continue
                        variants.add(encoded)
                        next_frontier.add(encoded)
                frontier = next_frontier
            for variant in sorted(variants, key=len, reverse=True):
                redacted = redacted.replace(variant, "<prompt>")
        return redacted

    @classmethod
    def _redact_payload(cls, value, sensitive_values: Sequence[str]):
        if isinstance(value, str):
            return cls._redact_text(value, sensitive_values)
        if isinstance(value, Mapping):
            return {
                key: cls._redact_payload(item, sensitive_values)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [cls._redact_payload(item, sensitive_values) for item in value]
        return value

    @staticmethod
    def _read_last_message(path: Path) -> Optional[str]:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        return value or None

    @staticmethod
    def _write_private(path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with CodexProcessRunner._open_private(path) as handle:
            handle.write(content)

    @staticmethod
    def _open_private(path: Path):
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            restrict_file_permissions(descriptor)
            return os.fdopen(descriptor, "w", encoding="utf-8")
        except Exception:
            os.close(descriptor)
            raise
