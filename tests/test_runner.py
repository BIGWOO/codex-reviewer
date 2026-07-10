from __future__ import annotations

import json
import io
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from contextlib import redirect_stderr

from tests.helpers import make_fake_codex, read_fake_log


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from codex_reviewer.catalog import CodexBinary  # noqa: E402
from codex_reviewer.runner import (  # noqa: E402
    CodexProcessRunner,
    extract_final,
    extract_usage,
    parse_jsonl_line,
    sanitize_command,
)


def process_is_running(pid: int) -> bool:
    result = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        capture_output=True,
        text=True,
        check=False,
    )
    state = result.stdout.strip()
    return result.returncode == 0 and bool(state) and not state.startswith("Z")


class JsonlHelpersTests(unittest.TestCase):
    def test_parse_extract_final_and_usage(self) -> None:
        lines = [
            '{"type":"turn.started"}',
            '{"type":"item.completed","item":{"type":"agent_message","text":"done"}}',
            '{"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}',
        ]
        events = [parse_jsonl_line(line) for line in lines]
        parsed = [event for event in events if event is not None]
        self.assertEqual(extract_final(parsed), "done")
        self.assertEqual(
            extract_usage(parsed), {"input_tokens": 12, "output_tokens": 3}
        )
        self.assertIsNone(parse_jsonl_line("not-json"))

    def test_sanitize_command_redacts_sensitive_values(self) -> None:
        command = sanitize_command(
            ["codex", "exec", "secret instructions"],
            sensitive_values=["secret instructions"],
        )
        self.assertNotIn("secret instructions", command)
        self.assertIn("<prompt>", command)


class ProcessRunnerTests(unittest.TestCase):
    def test_prompt_is_stdin_only_and_result_never_contains_it(self) -> None:
        secret = "TOP-SECRET-REVIEW-INSTRUCTIONS"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            log_path = root / "calls.json"
            runner = CodexProcessRunner(
                binary,
                timeout=5,
                env={**os.environ, "FAKE_CODEX_LOG": str(log_path)},
            )
            result = runner.run(
                [str(fake), "exec", "--json", "-"],
                stdin_payload=secret,
                mode="generic",
                scope={"kind": "custom"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )
            execution = read_fake_log(log_path)[-1]

        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(execution["stdin"], secret)
        self.assertNotIn(secret, execution["argv"])
        self.assertNotIn(secret, json.dumps(result))

    def test_no_prompt_uses_devnull(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            log_path = root / "calls.json"
            runner = CodexProcessRunner(
                binary,
                timeout=5,
                env={**os.environ, "FAKE_CODEX_LOG": str(log_path)},
            )
            result = runner.run(
                [str(fake), "exec", "--json"],
                stdin_payload=None,
                mode="native",
                scope={"kind": "uncommitted"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )
            execution = read_fake_log(log_path)[-1]

        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(execution["stdin"], "")

    def test_jsonl_output_usage_and_last_message_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            raw_path = root / "raw.jsonl"
            last_path = root / "last.txt"
            runner = CodexProcessRunner(
                binary,
                timeout=5,
                output_file=str(raw_path),
                last_message_output=str(last_path),
            )
            result = runner.run(
                [
                    str(fake),
                    "exec",
                    "--json",
                    "--output-last-message",
                    str(last_path),
                    "-",
                ],
                stdin_payload="review",
                mode="generic",
                scope={"kind": "custom"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )

            raw_events = [
                json.loads(line)
                for line in raw_path.read_text(encoding="utf-8").splitlines()
            ]
            last = last_path.read_text(encoding="utf-8")
            raw_mode = raw_path.stat().st_mode & 0o777
            last_mode = last_path.stat().st_mode & 0o777

        self.assertTrue(result["success"], result.get("error"))
        self.assertEqual(result["usage"], {"input_tokens": 101, "output_tokens": 23})
        self.assertEqual(result["final_result"], last)
        self.assertEqual(len(raw_events), 4)
        self.assertEqual(raw_mode, 0o600)
        self.assertEqual(last_mode, 0o600)

    def test_stale_last_message_is_removed_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            last_path = root / "last.txt"
            last_path.write_text("STALE PASS", encoding="utf-8")
            runner = CodexProcessRunner(
                binary,
                timeout=5,
                last_message_output=str(last_path),
                env={
                    **os.environ,
                    "FAKE_CODEX_NO_FINAL": "1",
                    "FAKE_CODEX_SKIP_LAST_MESSAGE": "1",
                },
            )
            result = runner.run(
                [
                    str(fake),
                    "exec",
                    "--json",
                    "--output-last-message",
                    str(last_path),
                    "-",
                ],
                stdin_payload="review",
                mode="generic",
                scope={"kind": "custom"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )
            last_exists = last_path.exists()
            last_content = last_path.read_text(encoding="utf-8")
            last_mode = last_path.stat().st_mode & 0o777

        self.assertTrue(result["success"])
        self.assertIsNone(result["final_result"])
        self.assertTrue(last_exists)
        self.assertEqual(last_content, "")
        self.assertEqual(last_mode, 0o600)

    def test_sensitive_instruction_fragment_is_redacted_everywhere(self) -> None:
        secret = 'ONLY-THIS-INSTRUCTION-IS-SECRET\n"秘密"'
        echoed_final = json.dumps({"note": secret}, ensure_ascii=False)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            runner = CodexProcessRunner(
                binary,
                timeout=5,
                env={
                    **os.environ,
                    "FAKE_CODEX_STDERR": f"echo {secret}",
                    "FAKE_CODEX_FINAL": echoed_final,
                },
            )
            captured = io.StringIO()
            with redirect_stderr(captured):
                result = runner.run(
                    [str(fake), "exec", "--json", "-"],
                    stdin_payload=f"boilerplate\n{secret}\nmore boilerplate",
                    sensitive_values=(secret,),
                    mode="generic",
                    scope={"kind": "custom"},
                    model="gpt-5.6-sol",
                    effort="high",
                    service_tier=None,
                )

        self.assertTrue(result["success"])
        self.assertNotIn(secret, captured.getvalue())
        serialized = json.dumps(result, ensure_ascii=False)
        self.assertNotIn(secret, serialized)
        variants = {secret}
        frontier = {secret}
        for _ in range(10):
            frontier = {
                encoded
                for item in frontier
                for encoded in (
                    json.dumps(item)[1:-1],
                    json.dumps(item, ensure_ascii=False)[1:-1],
                )
            }
            variants.update(frontier)
        for variant in variants:
            self.assertNotIn(variant, serialized)
        deeply_escaped = secret
        for _ in range(10):
            deeply_escaped = json.dumps(deeply_escaped)[1:-1]
        self.assertEqual(
            CodexProcessRunner._redact_text(
                f"before:{deeply_escaped}:after", (secret,)
            ),
            "before:<prompt>:after",
        )

    def test_large_stdin_write_remains_timeout_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            runner = CodexProcessRunner(
                binary,
                timeout=1,
                env={
                    **os.environ,
                    "FAKE_CODEX_SKIP_STDIN": "1",
                    "FAKE_CODEX_SLEEP": "120",
                },
            )
            started = time.monotonic()
            result = runner.run(
                [str(fake), "exec", "--json", "-"],
                stdin_payload="x" * 2_000_000,
                mode="generic",
                scope={"kind": "custom"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )
            elapsed = time.monotonic() - started

        self.assertTrue(result["timed_out"])
        self.assertLess(elapsed, 10)

    def test_nonzero_exit_preserves_jsonl_and_extracts_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            raw_path = root / "failed.jsonl"
            runner = CodexProcessRunner(
                binary,
                timeout=5,
                output_file=str(raw_path),
                env={**os.environ, "FAKE_CODEX_EXIT": "7"},
            )
            result = runner.run(
                [str(fake), "exec", "--json", "-"],
                stdin_payload="review",
                mode="generic",
                scope={"kind": "custom"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )

            raw = raw_path.read_text(encoding="utf-8")

        self.assertFalse(result["success"])
        self.assertEqual(result["exit_code"], 7)
        self.assertIn("fake inference failure", result["error"])
        self.assertIn("turn.failed", raw)

    @unittest.skipIf(os.name == "nt", "process-group semantics are POSIX-only")
    def test_timeout_terminates_process_group_and_preserves_partial_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            raw_path = root / "partial.jsonl"
            child_pid_path = root / "child.pid"
            runner = CodexProcessRunner(
                binary,
                timeout=1,
                output_file=str(raw_path),
                env={
                    **os.environ,
                    "FAKE_CODEX_SLEEP": "120",
                    "FAKE_CODEX_CHILD_PID": str(child_pid_path),
                    "FAKE_CODEX_CHILD_IGNORE_TERM": "1",
                },
            )
            result = runner.run(
                [str(fake), "exec", "--json", "-"],
                stdin_payload="review",
                mode="generic",
                scope={"kind": "custom"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )
            child_pid = int(child_pid_path.read_text(encoding="utf-8"))
            deadline = time.monotonic() + 3
            while process_is_running(child_pid) and time.monotonic() < deadline:
                time.sleep(0.05)
            partial = raw_path.read_text(encoding="utf-8")

        self.assertFalse(result["success"])
        self.assertTrue(result["timed_out"])
        self.assertFalse(process_is_running(child_pid))
        self.assertIn("thread.started", partial)


if __name__ == "__main__":
    unittest.main()
