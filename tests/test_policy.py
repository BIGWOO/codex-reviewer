from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from tests.helpers import make_fake_codex, read_fake_log, run_cli


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from codex_reviewer.catalog import CodexBinary  # noqa: E402
from codex_reviewer.runner import (  # noqa: E402
    CodexProcessRunner,
    tool_call_descriptor,
)


class ToolPolicyHelpersTests(unittest.TestCase):
    def test_detects_command_mcp_web_browser_and_collaboration_calls(self) -> None:
        item_types = (
            "command_execution",
            "mcp_tool_call",
            "web_search",
            "browser_tool_call",
            "collab_tool_call",
            "dynamic_tool_call",
            "dynamicToolCall",
        )
        for item_type in item_types:
            with self.subTest(item_type=item_type):
                descriptor = tool_call_descriptor(
                    {
                        "type": "item.started",
                        "item": {"id": f"id-{item_type}", "type": item_type},
                    }
                )
                self.assertIsNotNone(descriptor)
                assert descriptor is not None
                self.assertEqual(descriptor["item_type"], item_type)

        self.assertIsNone(
            tool_call_descriptor(
                {
                    "type": "item.completed",
                    "item": {"id": "reasoning", "type": "reasoning"},
                }
            )
        )
        browser_dynamic = tool_call_descriptor(
            {
                "type": "item.started",
                "item": {
                    "id": "browser-dynamic",
                    "type": "dynamic_tool_call",
                    "namespace": "browser",
                },
            }
        )
        assert browser_dynamic is not None
        self.assertEqual(browser_dynamic["category"], "browser")


class RunnerPolicyTests(unittest.TestCase):
    def test_zero_budget_rejects_every_prohibited_tool_event(self) -> None:
        item_types = (
            "command_execution",
            "mcp_tool_call",
            "web_search",
            "browser_tool_call",
            "collab_tool_call",
            "dynamic_tool_call",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            for item_type in item_types:
                with self.subTest(item_type=item_type):
                    result = CodexProcessRunner(
                        binary,
                        timeout=5,
                        max_tool_calls=0,
                        env={**os.environ, "FAKE_CODEX_TOOL_TYPES": item_type},
                    ).run(
                        [str(fake), "exec", "--json", "-"],
                        stdin_payload="review",
                        mode="bounded",
                        scope={"kind": "commit_snapshot"},
                        model="gpt-5.6-sol",
                        effort="high",
                        service_tier=None,
                    )
                    self.assertFalse(result["success"])
                    self.assertEqual(result["execution_status"], "policy_violation")
                    self.assertEqual(result["policy_violation"]["reason"], "tool_call")

    def test_zero_tool_budget_terminates_immediately_and_preserves_raw_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            raw_path = root / "policy.jsonl"
            last_path = root / "last.txt"
            runner = CodexProcessRunner(
                binary,
                timeout=15,
                output_file=str(raw_path),
                last_message_output=str(last_path),
                max_tool_calls=0,
                env={
                    **os.environ,
                    "FAKE_CODEX_TOOL_TYPES": "command_execution",
                    "FAKE_CODEX_AFTER_TOOL_SLEEP": "120",
                    "FAKE_CODEX_PROGRESS_MESSAGE": "bounded progress",
                },
            )
            started = time.monotonic()
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
                mode="bounded",
                scope={"kind": "commit_snapshot"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )
            elapsed = time.monotonic() - started
            raw = raw_path.read_text(encoding="utf-8")
            last_content = last_path.read_text(encoding="utf-8")

        self.assertLess(elapsed, 10)
        self.assertFalse(result["success"])
        self.assertEqual(result["execution_status"], "policy_violation")
        self.assertEqual(result["policy_violation"]["reason"], "tool_call")
        self.assertEqual(result["policy_violation"]["item_type"], "command_execution")
        self.assertEqual(result["event_counts"]["tool_calls"], 1)
        self.assertIsNone(result["final_result"])
        self.assertEqual(result["partial_progress"], "bounded progress")
        self.assertEqual(last_content, "")
        self.assertIn("command_execution", raw)

    def test_tool_budget_counts_unique_calls_and_legacy_default_is_unlimited(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            unlimited = CodexProcessRunner(
                binary,
                timeout=5,
                env={**os.environ, "FAKE_CODEX_TOOL_TYPES": "command_execution"},
            ).run(
                [str(fake), "exec", "--json", "-"],
                stdin_payload="review",
                mode="generic",
                scope={"kind": "custom"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )
            limited = CodexProcessRunner(
                binary,
                timeout=5,
                max_tool_calls=1,
                env={
                    **os.environ,
                    "FAKE_CODEX_TOOL_TYPES": "command_execution,mcp_tool_call",
                },
            ).run(
                [str(fake), "exec", "--json", "-"],
                stdin_payload="review",
                mode="generic",
                scope={"kind": "custom"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )

        self.assertTrue(unlimited["success"], unlimited.get("error"))
        self.assertEqual(unlimited["event_counts"]["tool_calls"], 1)
        self.assertFalse(limited["success"])
        self.assertEqual(limited["policy_violation"]["reason"], "tool_call")
        self.assertEqual(limited["policy_violation"]["observed"], 2)
        self.assertEqual(limited["policy_violation"]["limit"], 1)

    def test_jsonl_byte_limit_terminates_and_keeps_offending_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            binary = CodexBinary.discover(str(fake))
            raw_path = root / "large.jsonl"
            runner = CodexProcessRunner(
                binary,
                timeout=15,
                output_file=str(raw_path),
                max_jsonl_bytes=200,
                env={
                    **os.environ,
                    "FAKE_CODEX_JSONL_FILLER_BYTES": "1000",
                    "FAKE_CODEX_AFTER_FILLER_SLEEP": "120",
                },
            )
            result = runner.run(
                [str(fake), "exec", "--json", "-"],
                stdin_payload="review",
                mode="bounded",
                scope={"kind": "commit_snapshot"},
                model="gpt-5.6-sol",
                effort="high",
                service_tier=None,
            )
            raw = raw_path.read_text(encoding="utf-8")

        self.assertFalse(result["success"])
        self.assertEqual(result["execution_status"], "policy_violation")
        self.assertEqual(
            result["policy_violation"]["reason"], "jsonl_bytes_exceeded"
        )
        self.assertGreater(result["raw_output_bytes"], 200)
        self.assertGreater(len(raw.encode("utf-8")), 200)
        self.assertIsNone(result["final_result"])

    def test_text_mode_rejects_tool_budget_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            call_log = root / "calls.json"
            result = run_cli(
                "--codex-bin",
                str(fake),
                "--skip-git-repo-check",
                "--text",
                "--max-tool-calls",
                "0",
                "custom",
                "review",
                env={"FAKE_CODEX_LOG": str(call_log)},
            )

        self.assertEqual(result.returncode, 1)
        self.assertIn("require JSONL output", result.stderr)
        calls = read_fake_log(call_log) if call_log.exists() else []
        self.assertFalse(any("exec" in call["argv"] for call in calls))


if __name__ == "__main__":
    unittest.main()
