from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from tests.helpers import git, init_git_fixture, make_fake_codex, read_fake_log, run_cli


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from codex_reviewer.bounded import (  # noqa: E402
    BoundedScopeError,
    build_bounded_packet,
)


def write_json(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class BoundedPacketTests(unittest.TestCase):
    def test_commit_snapshot_contains_only_requested_inclusive_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            (repo / "app.py").write_text(
                "one\ntwo\nthree\nfour\n", encoding="utf-8"
            )
            git(repo, "add", "app.py")
            git(repo, "commit", "-m", "four lines")
            packet = build_bounded_packet(
                str(repo),
                {
                    "version": 1,
                    "kind": "commit_snapshot",
                    "commit": "HEAD",
                    "files": [
                        {
                            "path": "app.py",
                            "ranges": [
                                {"start": 2, "end": 2},
                                {"start": 4, "end": 4},
                            ],
                        }
                    ],
                },
            )
            payload = json.loads(packet.prompt)

        self.assertEqual(payload["scope"]["kind"], "commit_snapshot")
        self.assertEqual(
            payload["files"][0]["absolute_path"], str((repo / "app.py").resolve())
        )
        self.assertEqual(
            payload["files"][0]["ranges"],
            [
                {"start": 2, "end": 2, "lines": [{"number": 2, "text": "two"}]},
                {"start": 4, "end": 4, "lines": [{"number": 4, "text": "four"}]},
            ],
        )
        self.assertEqual(packet.metrics["files"], 1)
        self.assertEqual(packet.metrics["lines"], 2)
        self.assertEqual(packet.metrics["bytes"], 9)
        self.assertEqual(
            packet.packet_sha256,
            hashlib.sha256(packet.prompt.encode("utf-8")).hexdigest(),
        )

    def test_commit_diff_is_limited_to_changed_file_allowlist(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            (repo / "app.py").write_text("changed\n", encoding="utf-8")
            (repo / "other.py").write_text("other\n", encoding="utf-8")
            git(repo, "add", "app.py", "other.py")
            git(repo, "commit", "-m", "change two files")
            packet = build_bounded_packet(
                str(repo),
                {
                    "version": 1,
                    "kind": "commit_diff",
                    "commit": "HEAD",
                    "files": ["app.py"],
                },
            )
            payload = json.loads(packet.prompt)

        self.assertEqual([item["path"] for item in payload["files"]], ["app.py"])
        self.assertIn("changed", payload["files"][0]["patch"])
        self.assertNotIn("other.py", packet.prompt)

    def test_uncommitted_diff_preserves_staged_unstaged_and_untracked_layers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            (repo / "app.py").write_text("staged\n", encoding="utf-8")
            git(repo, "add", "app.py")
            (repo / "app.py").write_text("unstaged\n", encoding="utf-8")
            (repo / "new.py").write_text("new file\n", encoding="utf-8")
            packet = build_bounded_packet(
                str(repo),
                {
                    "version": 1,
                    "kind": "uncommitted_diff",
                    "files": ["app.py", "new.py"],
                },
            )
            payload = json.loads(packet.prompt)
            by_path = {item["path"]: item for item in payload["files"]}

        self.assertIn("staged_patch", by_path["app.py"])
        self.assertIn("unstaged_patch", by_path["app.py"])
        self.assertEqual(
            by_path["app.py"]["layers"], ["staged", "unstaged"]
        )
        self.assertEqual(by_path["new.py"]["layers"], ["untracked"])
        self.assertEqual(
            by_path["new.py"]["lines"], [{"number": 1, "text": "new file"}]
        )

    def test_scope_rejects_unknown_fields_unsafe_paths_and_bad_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            cases = {
                "unknown": {
                    "version": 1,
                    "kind": "commit_diff",
                    "commit": "HEAD",
                    "files": ["app.py"],
                    "extra": True,
                },
                "absolute": {
                    "version": 1,
                    "kind": "commit_diff",
                    "commit": "HEAD",
                    "files": ["/tmp/app.py"],
                },
                "escape": {
                    "version": 1,
                    "kind": "commit_diff",
                    "commit": "HEAD",
                    "files": ["../app.py"],
                },
                "duplicate": {
                    "version": 1,
                    "kind": "commit_diff",
                    "commit": "HEAD",
                    "files": ["app.py", "app.py"],
                },
                "overlap": {
                    "version": 1,
                    "kind": "commit_snapshot",
                    "commit": "HEAD",
                    "files": [
                        {
                            "path": "app.py",
                            "ranges": [
                                {"start": 1, "end": 2},
                                {"start": 2, "end": 2},
                            ],
                        }
                    ],
                },
                "out_of_range": {
                    "version": 1,
                    "kind": "commit_snapshot",
                    "commit": "HEAD",
                    "files": [
                        {"path": "app.py", "ranges": [{"start": 1, "end": 99}]}
                    ],
                },
                "invalid_ref": {
                    "version": 1,
                    "kind": "commit_diff",
                    "commit": "missing-ref",
                    "files": ["app.py"],
                },
                "float_version": {
                    "version": 1.0,
                    "kind": "commit_diff",
                    "commit": "HEAD",
                    "files": ["app.py"],
                },
            }

            for name, scope in cases.items():
                with self.subTest(case=name):
                    with self.assertRaises(BoundedScopeError):
                        build_bounded_packet(str(repo), scope)

    def test_scope_rejects_binary_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            (repo / "binary.dat").write_bytes(b"text\x00binary")
            git(repo, "add", "binary.dat")
            git(repo, "commit", "-m", "binary")

            with self.assertRaisesRegex(BoundedScopeError, "binary"):
                build_bounded_packet(
                    str(repo),
                    {
                        "version": 1,
                        "kind": "commit_snapshot",
                        "commit": "HEAD",
                        "files": [
                            {
                                "path": "binary.dat",
                                "ranges": [{"start": 1, "end": 1}],
                            }
                        ],
                    },
                )

    def test_evidence_accepts_only_declared_check_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            scope = {
                "version": 1,
                "kind": "commit_snapshot",
                "commit": "HEAD",
                "files": [
                    {"path": "app.py", "ranges": [{"start": 1, "end": 2}]}
                ],
            }
            packet = build_bounded_packet(
                str(repo),
                scope,
                evidence={
                    "version": 1,
                    "checks": [
                        {"name": "unit", "status": "passed", "detail": "23/23"},
                        {"name": "integration", "status": "not_run"},
                    ],
                },
            )
            payload = json.loads(packet.prompt)
            self.assertEqual(payload["caller_evidence"]["checks"][0]["status"], "passed")

            invalid_evidence = (
                {"version": 1, "checks": [{"name": "unit", "status": "skipped"}]},
                {"version": 1.0, "checks": [{"name": "unit", "status": "passed"}]},
                {"version": 1, "checks": [{"name": "unit", "status": []}]},
                {"version": 1, "checks": [{"name": "unit", "status": "passed", "extra": 1}]},
                {"version": 1, "checks": []},
            )
            for evidence in invalid_evidence:
                with self.subTest(evidence=evidence):
                    with self.assertRaises(BoundedScopeError):
                        build_bounded_packet(str(repo), scope, evidence=evidence)


class BoundedCliTests(unittest.TestCase):
    def test_preparation_failures_preserve_bounded_and_legacy_structured_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "app.py").write_text("changed\n", encoding="utf-8")
            scope_path = write_json(
                root / "scope.json",
                {
                    "version": 1,
                    "kind": "uncommitted_diff",
                    "files": ["app.py"],
                },
            )
            binary = make_fake_codex(root, version="0.143.0")
            bounded_path = root / "bounded.json"
            bounded = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--bounded-scope",
                str(scope_path),
                "--result-json",
                str(bounded_path),
                "bounded-review",
            )
            structured_path = root / "structured.json"
            structured = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--result-json",
                str(structured_path),
                "structured-review",
                "--uncommitted",
            )
            bounded_envelope = json.loads(bounded_path.read_text(encoding="utf-8"))
            structured_envelope = json.loads(
                structured_path.read_text(encoding="utf-8")
            )

        self.assertNotEqual(bounded.returncode, 0)
        self.assertNotEqual(structured.returncode, 0)
        self.assertEqual(bounded_envelope["mode"], "bounded")
        self.assertEqual(bounded_envelope["scope"]["kind"], "uncommitted_diff")
        self.assertIsNotNone(bounded_envelope["scope_fingerprint"])
        self.assertIsNotNone(bounded_envelope["packet_sha256"])
        self.assertEqual(
            bounded_envelope["prompt_sha256"], bounded_envelope["packet_sha256"]
        )
        self.assertEqual(structured_envelope["mode"], "generic")

    def test_aes_acceptance_fixture_blocks_two_p2_then_passes_post_fix(self) -> None:
        original_result = {
            "findings": [
                {
                    "title": "[P2] Reject malformed Base64",
                    "body": "Malformed ciphertext is accepted.",
                    "confidence_score": 0.99,
                    "priority": 2,
                    "code_location": {
                        "absolute_file_path": "/tmp/aes.js",
                        "line_range": {"start": 1, "end": 1},
                    },
                },
                {
                    "title": "[P2] Enforce exact key sizes",
                    "body": "HashKey and IV lengths are not exact.",
                    "confidence_score": 0.99,
                    "priority": 2,
                    "code_location": {
                        "absolute_file_path": "/tmp/aes.js",
                        "line_range": {"start": 2, "end": 2},
                    },
                },
            ],
            "overall_correctness": "patch is incorrect",
            "overall_explanation": "Two validation defects remain.",
            "overall_confidence_score": 0.99,
        }
        post_fix_result = {
            "findings": [],
            "overall_correctness": "patch is correct",
            "overall_explanation": "The bounded fix is correct.",
            "overall_confidence_score": 0.99,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "aes.js").write_text(
                "decodeBase64(input);\nvalidateKey(key, iv);\n", encoding="utf-8"
            )
            git(repo, "add", "aes.js")
            git(repo, "commit", "-m", "AES fixture")
            scope_path = write_json(
                root / "scope.json",
                {
                    "version": 1,
                    "kind": "commit_snapshot",
                    "commit": "HEAD",
                    "files": [
                        {"path": "aes.js", "ranges": [{"start": 1, "end": 2}]}
                    ],
                },
            )
            binary = make_fake_codex(root)
            blocked_path = root / "blocked.json"
            blocked = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--bounded-scope",
                str(scope_path),
                "--result-json",
                str(blocked_path),
                "bounded-review",
                env={"FAKE_CODEX_FINAL": json.dumps(original_result)},
            )
            passed_path = root / "passed.json"
            passed = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--bounded-scope",
                str(scope_path),
                "--result-json",
                str(passed_path),
                "bounded-review",
                env={"FAKE_CODEX_FINAL": json.dumps(post_fix_result)},
            )
            blocked_envelope = json.loads(blocked_path.read_text(encoding="utf-8"))
            passed_envelope = json.loads(passed_path.read_text(encoding="utf-8"))

        self.assertEqual(blocked.returncode, 0, blocked.stderr)
        self.assertTrue(blocked_envelope["success"])
        self.assertEqual(len(blocked_envelope["structured_result"]["findings"]), 2)
        self.assertEqual(blocked_envelope["gate_status"], "blocked")
        self.assertEqual(passed.returncode, 0, passed.stderr)
        self.assertEqual(passed_envelope["gate_status"], "passed")

    def test_bounded_review_sends_only_packet_and_records_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            scope_path = write_json(
                root / "scope.json",
                {
                    "version": 1,
                    "kind": "commit_snapshot",
                    "commit": "HEAD",
                    "files": [
                        {"path": "app.py", "ranges": [{"start": 1, "end": 2}]}
                    ],
                },
            )
            evidence_path = write_json(
                root / "evidence.json",
                {
                    "version": 1,
                    "checks": [{"name": "unit", "status": "passed", "detail": "2/2"}],
                },
            )
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            result_path = root / "result.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--bounded-scope",
                str(scope_path),
                "--evidence-json",
                str(evidence_path),
                "--result-json",
                str(result_path),
                "bounded-review",
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            envelope = json.loads(result_path.read_text(encoding="utf-8"))
            execution = next(
                call for call in reversed(read_fake_log(log_path)) if "exec" in call["argv"]
            )
            packet = json.loads(execution["stdin"])

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(packet["contract"]["tool_policy"], "none")
        self.assertEqual(packet["caller_evidence"]["checks"][0]["status"], "passed")
        self.assertIn("--ignore-user-config", execution["argv"])
        self.assertIn("--output-schema", execution["argv"])
        self.assertIn("plugins", execution["argv"])
        self.assertEqual(envelope["mode"], "bounded")
        self.assertEqual(envelope["scope"]["metrics"]["files"], 1)
        self.assertEqual(envelope["scope"]["metrics"]["lines"], 2)
        self.assertEqual(envelope["scope_fingerprint"], packet["scope_fingerprint"])
        self.assertEqual(
            envelope["packet_sha256"],
            hashlib.sha256(execution["stdin"].encode("utf-8")).hexdigest(),
        )
        self.assertEqual(envelope["prompt_sha256"], envelope["packet_sha256"])
        self.assertEqual(envelope["gate_status"], "passed")

    def test_bounded_review_rejects_context_and_scope_expansion_flags(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            scope_path = write_json(
                root / "scope.json",
                {
                    "version": 1,
                    "kind": "commit_snapshot",
                    "commit": "HEAD",
                    "files": [
                        {"path": "app.py", "ranges": [{"start": 1, "end": 2}]}
                    ],
                },
            )
            binary = make_fake_codex(root)
            manifest = write_json(root / "manifest.json", {"version": 1, "scopes": []})
            schema = write_json(root / "schema.json", {"type": "object"})
            cases = {
                "search": ["--search"],
                "full_context": ["--full-context"],
                "isolated": ["--isolated"],
                "add_dir": ["--add-dir", str(root)],
                "manifest": ["--scope-manifest", str(manifest)],
                "schema": ["--schema", str(schema)],
                "quick": ["--preset", "quick"],
                "review_range": ["--review-range", "HEAD~1..HEAD"],
            }
            for name, flags in cases.items():
                with self.subTest(case=name):
                    result = run_cli(
                        "--codex-bin",
                        str(binary),
                        "--cd",
                        str(repo),
                        "--bounded-scope",
                        str(scope_path),
                        *flags,
                        "bounded-review",
                    )
                    self.assertNotEqual(result.returncode, 0)

    def test_bounded_review_allows_explicit_deep_without_implicit_upgrade(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            scope_path = write_json(
                root / "scope.json",
                {
                    "version": 1,
                    "kind": "commit_snapshot",
                    "commit": "HEAD",
                    "files": [
                        {"path": "app.py", "ranges": [{"start": 1, "end": 2}]}
                    ],
                },
            )
            binary = make_fake_codex(root)
            standard_path = root / "standard.json"
            standard = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--bounded-scope",
                str(scope_path),
                "--result-json",
                str(standard_path),
                "bounded-review",
            )
            deep_path = root / "deep.json"
            deep = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--bounded-scope",
                str(scope_path),
                "--preset",
                "deep",
                "--result-json",
                str(deep_path),
                "bounded-review",
            )
            standard_envelope = json.loads(standard_path.read_text(encoding="utf-8"))
            deep_envelope = json.loads(deep_path.read_text(encoding="utf-8"))

        self.assertEqual(standard.returncode, 0, standard.stderr)
        self.assertEqual(deep.returncode, 0, deep.stderr)
        self.assertEqual(standard_envelope["effort"], "high")
        self.assertEqual(deep_envelope["effort"], "xhigh")


if __name__ == "__main__":
    unittest.main()
