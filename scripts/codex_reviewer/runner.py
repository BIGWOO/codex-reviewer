"""Safe Codex subprocess execution and JSONL event handling."""

from __future__ import annotations

import json
import os
import queue
import shlex
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .catalog import CodexBinary
from .result import ReviewResult


HEARTBEAT_SECONDS = 30
ERROR_DETAIL_LIMIT = 8000


def sanitize_command(cmd: Sequence[str], sensitive_values: Iterable[str] = ()) -> str:
    sensitive = {value for value in sensitive_values if value}
    redacted = ["<prompt>" if argument in sensitive else argument for argument in cmd]
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
    for event in reversed(events):
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
        if event_type == "item.completed":
            item = event.get("item")
            if (
                isinstance(item, Mapping)
                and item.get("type") == "error"
                and isinstance(item.get("message"), str)
            ):
                return item["message"]  # type: ignore[return-value]
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
    if event_type != "item.completed":
        return None
    item = event.get("item")
    if not isinstance(item, Mapping):
        return None
    item_type = item.get("type")
    if item_type == "command_execution":
        return f"command completed exit={item.get('exit_code')}"
    if item_type == "agent_message":
        return "final message received"
    if item_type == "reasoning":
        return "reasoning step completed"
    if item_type == "error":
        return "Codex reported an internal item error"
    return f"{item_type or 'item'} completed"


class CodexProcessRunner:
    """Execute one Codex command without inheriting stdin or orphaning children."""

    def __init__(
        self,
        binary: CodexBinary,
        timeout: int,
        json_output: bool = True,
        output_file: Optional[str] = None,
        last_message_output: Optional[str] = None,
        env: Optional[Mapping[str, str]] = None,
        heartbeat_seconds: int = HEARTBEAT_SECONDS,
    ):
        self.binary = binary
        self.timeout = timeout
        self.json_output = json_output
        self.output_file = output_file
        self.last_message_output = last_message_output
        self.env = dict(env or os.environ)
        self.heartbeat_seconds = heartbeat_seconds

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
        event_queue: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        started_at = time.monotonic()
        last_heartbeat = started_at
        process: Optional[subprocess.Popen[str]] = None
        timed_out = False
        stdin_thread: Optional[threading.Thread] = None
        stdin_errors: List[str] = []

        def read_stream(stream, stream_name: str) -> None:
            try:
                for line in iter(stream.readline, ""):
                    event_queue.put((stream_name, line))
            finally:
                stream.close()

        def write_stdin(stream, payload: str) -> None:
            try:
                stream.write(payload)
            except (BrokenPipeError, OSError) as exc:
                stdin_errors.append(str(exc))
            finally:
                try:
                    stream.close()
                except OSError:
                    pass

        try:
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

            print(f"[codex-review] starting: {sanitized}", file=sys.stderr, flush=True)
            process = subprocess.Popen(
                list(cmd),
                stdin=subprocess.PIPE
                if stdin_payload is not None
                else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                env=self.env,
                start_new_session=(os.name != "nt"),
            )
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
            if stdin_payload is not None and process.stdin is not None:
                stdin_thread = threading.Thread(
                    target=write_stdin,
                    args=(process.stdin, stdin_payload),
                    daemon=True,
                )
                stdin_thread.start()

            while True:
                try:
                    stream_name, line = event_queue.get(timeout=0.2)
                    self._consume_line(
                        stream_name,
                        line,
                        stdout_lines,
                        stderr_lines,
                        events,
                        output_handle,
                        sensitive,
                    )
                except queue.Empty:
                    pass

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
                    self._terminate_process_group(process)
                    break

            stdout_thread.join(timeout=2)
            stderr_thread.join(timeout=2)
            if stdin_thread:
                stdin_thread.join(timeout=2)
            while not event_queue.empty():
                stream_name, line = event_queue.get_nowait()
                self._consume_line(
                    stream_name,
                    line,
                    stdout_lines,
                    stderr_lines,
                    events,
                    output_handle,
                    sensitive,
                )
            if process.poll() is None:
                process.wait(timeout=2)

            output = "".join(stdout_lines)
            stderr = self._redact_text("".join(stderr_lines), sensitive)
            if stderr:
                print(
                    stderr,
                    end="" if stderr.endswith("\n") else "\n",
                    file=sys.stderr,
                    flush=True,
                )
            final = (
                extract_final(events) if self.json_output else output.strip() or None
            )
            if not final and self.last_message_output:
                final = self._read_last_message(
                    Path(self.last_message_output).expanduser()
                )
            usage = extract_usage(events)
            exit_code = process.returncode
            success = exit_code == 0 and not timed_out
            error = None
            if timed_out:
                suffix = (
                    f"; partial output written to {output_path}" if output_path else ""
                )
                error = f"Codex review timed out after {self.timeout} seconds{suffix}"
                if self.last_message_output and final:
                    self._write_private(
                        Path(self.last_message_output).expanduser(), final
                    )
            elif exit_code != 0:
                error = self._error_detail(stderr, exit_code, events)
            elif stdin_errors:
                success = False
                error = f"Failed to send the complete review prompt: {stdin_errors[-1]}"
            final = self._redact_text(final, sensitive) if final else None
            if final and self.last_message_output:
                self._write_private(Path(self.last_message_output).expanduser(), final)
            error = self._redact_text(error, sensitive) if error else None
            safe_output = self._redact_text(output, sensitive)
            safe_events = [self._redact_payload(event, sensitive) for event in events]

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
                timed_out=timed_out,
                exit_code=exit_code,
                service_tier=service_tier,
                warnings=list(warnings or []),
                command=sanitized,
                final=final,
                error=error,
                output=safe_output,
                events=safe_events,
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
                service_tier=service_tier,
                warnings=list(warnings or []),
                command=sanitized,
                error="Codex CLI not found",
            ).to_dict()
        except Exception as exc:
            if process is not None and process.poll() is None:
                self._terminate_process_group(process)
            return ReviewResult(
                success=False,
                mode=mode,
                binary=self.binary.path,
                version=self.binary.version_string,
                scope=scope,
                model=model,
                effort=effort,
                timeout=self.timeout,
                service_tier=service_tier,
                warnings=list(warnings or []),
                command=sanitized,
                error=f"Unexpected error: {exc}",
            ).to_dict()
        finally:
            if output_handle:
                output_handle.close()

    def _consume_line(
        self,
        stream_name: str,
        line: str,
        stdout_lines: List[str],
        stderr_lines: List[str],
        events: List[Mapping[str, object]],
        output_handle,
        sensitive_values: Sequence[str],
    ) -> None:
        if stream_name == "stderr":
            stderr_lines.append(line)
            return
        stdout_lines.append(line)
        if output_handle:
            output_handle.write(line)
            output_handle.flush()
        if not self.json_output:
            return
        event = parse_jsonl_line(line)
        if not event:
            return
        events.append(event)
        progress = _progress_event(event)
        if progress:
            print(f"[codex-review] {progress}", file=sys.stderr, flush=True)

    @staticmethod
    def _terminate_process_group(process: subprocess.Popen[str]) -> None:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                return
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
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

        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass

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
            os.fchmod(descriptor, 0o600)
            return os.fdopen(descriptor, "w", encoding="utf-8")
        except Exception:
            os.close(descriptor)
            raise
