from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from tests.helpers import ENTRYPOINT, git, init_git_fixture, make_fake_codex, run_cli
from tests.test_runner import process_is_running

sys.path.insert(0, str(ENTRYPOINT.parent))
from codex_reviewer.bounded import build_bounded_packet
from codex_reviewer.runner import CodexProcessRunner
from codex_reviewer.catalog import CodexBinary


class OutputSafetyTests(unittest.TestCase):
    def test_implicit_bundled_schema_is_also_protected(self):
        from codex_reviewer.cli import build_parser, _validate_output_paths
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bundled.json"
            path.write_text('{"type":"object"}')
            args = build_parser().parse_args(["structured-review", "--uncommitted", "--result-json", str(path)])
            with mock.patch("codex_reviewer.cli.BUNDLED_SCHEMA", path):
                self.assertIn("must not overwrite", _validate_output_paths(args))

    def test_invalid_result_destination_never_changes_any_output_or_input(self):
        for alias in ("same", "symlink", "hardlink"):
            with self.subTest(alias=alias), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                source = root / "scope.json"
                original = '{"version":1,"kind":"uncommitted_diff","files":["app.py"]}'
                source.write_text(original)
                destination = source if alias == "same" else root / "result.json"
                if alias == "symlink":
                    destination.symlink_to(source)
                elif alias == "hardlink":
                    os.link(source, destination)
                raw = root / "raw.jsonl"
                raw.write_text("existing raw output")
                final = root / "final.txt"
                final.write_text("existing final")
                result = run_cli(
                    "bounded-review", "--bounded-scope", str(source),
                    "--result-json", str(destination), "--output", str(raw),
                    "--last-message-output", str(final), "--no-update-check",
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn("must not overwrite", result.stderr)
                self.assertEqual(source.read_text(), original)
                self.assertEqual(raw.read_text(), "existing raw output")
                self.assertEqual(final.read_text(), "existing final")

    def test_safe_result_destination_still_receives_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result_path = Path(tmp) / "result.json"
            result = run_cli(
                "bounded-review", "--result-json", str(result_path), "--no-update-check"
            )
            self.assertEqual(result.returncode, 1)
            self.assertFalse(json.loads(result_path.read_text())["success"])


class LiteralPacketTests(unittest.TestCase):
    def test_special_filenames_do_not_expand_in_any_diff_layer(self):
        for selected, extra in (("[id].tsx", "i.tsx"), ("a*.py", "abc.py"), ("b?.py", "bx.py")):
            with self.subTest(selected=selected), tempfile.TemporaryDirectory() as tmp:
                repo = init_git_fixture(Path(tmp) / "repo")
                for name in (selected, extra):
                    (repo / name).write_text("original\n")
                git(repo, "add", ".")
                git(repo, "commit", "-m", "seed names")
                for name in (selected, extra):
                    (repo / name).write_text("staged\n")
                git(repo, "add", ".")
                for name in (selected, extra):
                    (repo / name).write_text("unstaged\n")
                packet = build_bounded_packet(str(repo), {
                    "version": 1, "kind": "uncommitted_diff", "files": [selected],
                })
                record = json.loads(packet.prompt)["files"][0]
                for layer in ("staged_patch", "unstaged_patch"):
                    self.assertEqual(record[layer].count("diff --git "), 1)
                    self.assertNotIn(f"b/{extra}", record[layer])
                git(repo, "add", ".")
                git(repo, "commit", "-m", "change both")
                packet = build_bounded_packet(str(repo), {
                    "version": 1, "kind": "commit_diff", "commit": "HEAD", "files": [selected],
                })
                patch = json.loads(packet.prompt)["files"][0]["patch"]
                self.assertEqual(patch.count("diff --git "), 1)
                self.assertNotIn(f"b/{extra}", patch)


class BoundedControlTests(unittest.TestCase):
    def test_file_changes_and_unknown_events_cannot_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "app.py").write_text("changed\n")
            binary = make_fake_codex(root / "fake")
            scope = root / "scope.json"
            scope.write_text(json.dumps({"version": 1, "kind": "uncommitted_diff", "files": ["app.py"]}))
            result_path = root / "result.json"
            final_path = root / "final.txt"
            events = [
                {"type": phase, "item": {"type": "file_change", "id": "change", "status": "completed"}}
                for phase in ("item.started", "item.updated", "item.completed")
            ] + [{"type": "item.completed", "item": {"type": "future_tool"}},
                 {"type": "future.event"}]
            for event in events:
                with self.subTest(event=event):
                    result = run_cli(
                        "bounded-review", "--cd", str(repo), "--codex-bin", str(binary),
                        "--bounded-scope", str(scope), "--result-json", str(result_path),
                        "--last-message-output", str(final_path), "--enforce-gate",
                        env={"FAKE_CODEX_TAIL_EVENTS": json.dumps([event])},
                    )
                    self.assertEqual(result.returncode, 1)
                    envelope = json.loads(result_path.read_text())
                    self.assertEqual(envelope["execution_status"], "policy_violation")
                    self.assertFalse(envelope["success"])
                    self.assertIsNone(envelope["final_result"])
                    self.assertEqual(final_path.read_text(), "")

    def test_required_controls_are_checked_before_inference(self):
        from tests.helpers import read_fake_log
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "app.py").write_text("changed\n")
            binary = make_fake_codex(root / "fake")
            scope = root / "scope.json"
            scope.write_text(json.dumps({"version": 1, "kind": "uncommitted_diff", "files": ["app.py"]}))
            for unsupported in (False, True):
                log = root / f"calls-{unsupported}.json"
                result = run_cli(
                    "bounded-review", "--cd", str(repo), "--codex-bin", str(binary),
                    "--bounded-scope", str(scope), "--enforce-gate",
                    env={"FAKE_CODEX_LOG": str(log), "FAKE_CODEX_UNSUPPORTED_CONTROLS": "1" if unsupported else ""},
                )
                executions = [call for call in read_fake_log(log) if "exec" in call["argv"]]
                if unsupported:
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(executions, [])
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("shell_tool", executions[0]["argv"])
                    self.assertIn('web_search="disabled"', executions[0]["argv"])


@unittest.skipIf(os.name == "nt", "POSIX process-group and signal contract")
class CancellationTests(unittest.TestCase):
    def test_signal_handlers_are_restored_after_interruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = make_fake_codex(Path(tmp))
            binary = CodexBinary.discover(str(fake))
            before = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
            with mock.patch("codex_reviewer.runner.subprocess.Popen", side_effect=KeyboardInterrupt):
                result = CodexProcessRunner(binary, timeout=5).run(
                    [str(fake)], mode="bounded", scope=None, model=None, effort=None, service_tier=None,
                )
            self.assertEqual(result["execution_status"], "interrupted")
            self.assertEqual(before, {sig: signal.getsignal(sig) for sig in before})

    def test_interrupt_reaps_descendants_before_releasing_lock(self):
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                repo = init_git_fixture(root / "repo")
                binary = make_fake_codex(root / "fake")
                child_file = root / "child.pid"
                result_path = root / "result.json"
                final_path = root / "final.txt"
                parent = subprocess.Popen(
                    [sys.executable, str(ENTRYPOINT), "structured-review", "--uncommitted",
                     "--codex-bin", str(binary), "--cd", str(repo), "--result-json",
                     str(result_path), "--last-message-output", str(final_path)],
                    env={**os.environ, "FAKE_CODEX_CHILD_PID": str(child_file),
                         "FAKE_CODEX_CHILD_IGNORE_TERM": "1", "FAKE_CODEX_SLEEP": "120",
                         "PYTHONDONTWRITEBYTECODE": "1"},
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                child_pid = None
                try:
                    deadline = time.monotonic() + 8
                    while not child_file.exists() and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(child_file.exists(), "fixture failed to start")
                    child_pid = int(child_file.read_text())
                    parent.send_signal(signum)
                    parent.wait(timeout=12)
                    self.assertEqual(parent.returncode, 128 + signum)
                    self.assertFalse(process_is_running(child_pid))
                    payload = json.loads(result_path.read_text())
                    self.assertFalse(payload["success"])
                    self.assertEqual(payload["execution_status"], "interrupted")
                    self.assertEqual(payload["gate_status"], "inconclusive")
                    self.assertEqual(final_path.read_text(), "")
                    descriptor, error = CodexProcessRunner._acquire_execution_lock(
                        f"root:{repo.resolve()}"
                    )
                    self.assertIsNone(error)
                    CodexProcessRunner._release_execution_lock(descriptor)
                finally:
                    if parent.poll() is None:
                        parent.kill()
                        parent.wait()
                    if child_pid and process_is_running(child_pid):
                        os.kill(child_pid, signal.SIGKILL)


if __name__ == "__main__":
    unittest.main()
