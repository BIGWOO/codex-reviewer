from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from tests.helpers import init_git_fixture, make_fake_codex, read_fake_log, run_cli


class CliContractTests(unittest.TestCase):
    def test_help_lists_v2_surface(self) -> None:
        result = run_cli("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for review_type in (
            "native-review",
            "security",
            "performance",
            "architecture",
            "quality",
            "diff",
            "focused",
            "custom",
        ):
            self.assertIn(review_type, result.stdout)
        for token in (
            "structured-review",
            "doctor",
            "--codex-bin",
            "--preset",
            "--instructions",
            "--strict-config",
            "--fast",
            "--dry-run",
            "--result-json",
        ):
            self.assertIn(token, result.stdout)

    def test_explicit_binary_below_minimum_fails_with_diagnostics(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = make_fake_codex(Path(tmp), version="0.143.0")
            result = run_cli("--codex-bin", str(binary), "doctor")

        self.assertNotEqual(result.returncode, 0)
        diagnostics = result.stdout + result.stderr
        self.assertIn(str(binary.resolve()), diagnostics)
        self.assertIn("0.143.0", diagnostics)
        self.assertIn("0.144.1", diagnostics)

    def test_native_prompt_uses_stdin_and_sanitized_argv(self) -> None:
        secret = "SECRET-NATIVE-PROMPT-42"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--model",
                "gpt-5.6-sol",
                "--reasoning-effort",
                "high",
                "--skip-git-repo-check",
                "native-review",
                secret,
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(log_path.exists(), result.stderr)
            calls = read_fake_log(log_path)

        execution = calls[-1]
        self.assertNotIn(secret, execution["argv"])
        self.assertEqual(execution["stdin"], secret)
        argv = execution["argv"]
        self.assertIn("exec", argv)
        self.assertIn("review", argv)
        self.assertIn("read-only", argv)
        self.assertIn("never", argv)
        self.assertTrue(any("model_reasoning_effort" in value for value in argv))

    def test_generic_outputs_preserve_contract_and_envelope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            raw_path = root / "raw.jsonl"
            last_path = root / "last.txt"
            result_path = root / "result.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--output",
                str(raw_path),
                "--last-message-output",
                str(last_path),
                "--result-json",
                str(result_path),
                "custom",
                "Review this fixture",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result_path.exists(), result.stderr)
            envelope = json.loads(result_path.read_text(encoding="utf-8"))
            raw_lines = raw_path.read_text(encoding="utf-8").splitlines()
            final_message = last_path.read_text(encoding="utf-8")

        self.assertEqual(result.stdout.strip(), final_message.strip())
        self.assertGreaterEqual(len(raw_lines), 4)
        for line in raw_lines:
            self.assertIsInstance(json.loads(line), dict)
        self.assertEqual(envelope["schema_version"], 2)
        self.assertTrue(envelope["success"])
        for key in (
            "binary",
            "version",
            "scope",
            "model",
            "effort",
            "usage",
            "timeout",
            "timed_out",
            "warnings",
            "sanitized_command",
            "final_result",
            "error",
        ):
            self.assertIn(key, envelope)
        self.assertEqual(envelope["binary"], str(binary.resolve()))
        self.assertEqual(envelope["version"], "0.144.1")
        self.assertEqual(envelope["scope"]["kind"], "custom")
        self.assertNotIn("Review this fixture", envelope["sanitized_command"])

    def test_native_scope_and_profile_conflicts_fail_before_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = make_fake_codex(Path(tmp))
            scope = run_cli(
                "--codex-bin",
                str(binary),
                "native-review",
                "--base",
                "main",
                "--commit",
                "HEAD",
            )
            profile = run_cli(
                "--codex-bin",
                str(binary),
                "--profile",
                "review",
                "--isolated",
                "custom",
                "review",
            )
            title = run_cli(
                "--codex-bin",
                str(binary),
                "native-review",
                "review this",
                "--title",
                "invalid title",
            )

        self.assertNotEqual(scope.returncode, 0)
        self.assertNotEqual(profile.returncode, 0)
        self.assertNotEqual(title.returncode, 0)
        self.assertIn("--commit", title.stderr)

    def test_native_rejects_generic_only_capabilities(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            schema = root / "schema.json"
            schema.write_text("{}", encoding="utf-8")
            image = root / "screen.png"
            image.write_bytes(b"fake")
            cases = {
                "schema": ["--schema", str(schema)],
                "image": ["--image", str(image)],
                "search": ["--search"],
                "ultra": ["--preset", "ultra"],
            }
            for name, extra in cases.items():
                with self.subTest(capability=name):
                    result = run_cli(
                        "--codex-bin",
                        str(binary),
                        *extra,
                        "native-review",
                        "--uncommitted",
                    )
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("generic", (result.stdout + result.stderr).lower())

    def test_structured_review_uses_bundled_schema_and_scope_prompt_via_stdin(
        self,
    ) -> None:
        instructions = "CHECK-SEEDED-STRUCTURED-BUG"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "app.py").write_text(
                "def total(values):\n    return 0\n", encoding="utf-8"
            )
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--instructions",
                instructions,
                "structured-review",
                "--uncommitted",
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = read_fake_log(log_path)

        execution = next(call for call in reversed(calls) if "exec" in call["argv"])
        argv = execution["argv"]
        self.assertNotIn(
            "review", argv[argv.index("exec") + 1 : argv.index("exec") + 2]
        )
        schema_path = argv[argv.index("--output-schema") + 1]
        self.assertTrue(schema_path.endswith("references/review_output_schema.json"))
        self.assertIn(instructions, execution["stdin"])
        self.assertIn("staged, unstaged, and untracked", execution["stdin"])
        self.assertNotIn(instructions, argv)

    def test_fast_requires_catalog_tier_and_emits_service_tier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            supported = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--fast",
                "custom",
                "review",
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(supported.returncode, 0, supported.stderr)
            calls = read_fake_log(log_path)
            unsupported = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--model",
                "gpt-5.5",
                "--fast",
                "custom",
                "review",
            )

        execution = next(call for call in reversed(calls) if "exec" in call["argv"])
        self.assertTrue(
            any(
                "service_tier" in value and "fast" in value
                for value in execution["argv"]
            )
        )
        self.assertNotEqual(unsupported.returncode, 0)

    def test_dry_run_does_not_execute_inference_and_redacts_prompt(self) -> None:
        secret = "DRY-RUN-SECRET-PROMPT"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--dry-run",
                "custom",
                secret,
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = read_fake_log(log_path)

        self.assertFalse(any("exec" in call["argv"] for call in calls))
        self.assertNotIn(secret, result.stdout + result.stderr)
        self.assertIn("read-only", result.stdout)

    def test_doctor_is_explicit_and_never_runs_inference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            result_path = root / "doctor.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--result-json",
                str(result_path),
                "doctor",
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result_path.exists(), result.stderr)
            calls = read_fake_log(log_path)
            envelope = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertFalse(any("exec" in call["argv"] for call in calls))
        self.assertTrue(any("doctor" in call["argv"] for call in calls))
        self.assertEqual(envelope["schema_version"], 2)
        check_names = {check["name"] for check in envelope["diagnostics"]}
        self.assertTrue(
            {
                "binary_drift",
                "binary",
                "auth_config",
                "catalog",
                "preset_standard",
                "schema",
                "git",
                "macos_read_only_git",
            }
            <= check_names
        )

    def test_output_paths_must_be_distinct(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            same = root / "same.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--output",
                str(same),
                "--result-json",
                str(same),
                "custom",
                "review",
            )
            schema = root / "schema.json"
            schema_content = json.dumps(
                {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                }
            )
            schema.write_text(schema_content, encoding="utf-8")
            schema_collision = run_cli(
                "--codex-bin",
                str(binary),
                "--schema",
                str(schema),
                "--output",
                str(schema),
                "custom",
                "review",
            )
            preserved_schema = schema.read_text(encoding="utf-8")
            hardlink_output = root / "hardlink-output.json"
            os.link(schema, hardlink_output)
            hardlink_collision = run_cli(
                "--codex-bin",
                str(binary),
                "--schema",
                str(schema),
                "--output",
                str(hardlink_output),
                "custom",
                "review",
            )
            preserved_after_hardlink = schema.read_text(encoding="utf-8")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("distinct", (result.stdout + result.stderr).lower())
        self.assertNotEqual(schema_collision.returncode, 0)
        self.assertIn("must not overwrite", schema_collision.stderr)
        self.assertEqual(preserved_schema, schema_content)
        self.assertNotEqual(hardlink_collision.returncode, 0)
        self.assertIn("must not overwrite", hardlink_collision.stderr)
        self.assertEqual(preserved_after_hardlink, schema_content)

    def test_long_context_is_catalog_validated_without_implicit_auto_compaction(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--long-context",
                "--context-window",
                "372000",
                "custom",
                "review",
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = read_fake_log(log_path)
            invalid = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--context-window",
                "372001",
                "--dry-run",
                "custom",
                "review",
            )
            implicit_log = root / "implicit-calls.json"
            implicit = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--long-context",
                "custom",
                "review",
                env={"FAKE_CODEX_LOG": str(implicit_log)},
            )
            self.assertEqual(implicit.returncode, 0, implicit.stderr)
            implicit_execution = next(
                call
                for call in reversed(read_fake_log(implicit_log))
                if "exec" in call["argv"]
            )

        execution = next(call for call in reversed(calls) if "exec" in call["argv"])
        self.assertIn("model_context_window=372000", execution["argv"])
        self.assertFalse(any("auto_compact" in value for value in execution["argv"]))
        self.assertIn("deprecat", result.stderr.lower())
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("372000", invalid.stderr)
        self.assertIn("model_context_window=372000", implicit_execution["argv"])

    def test_catalog_failure_falls_back_only_for_automatic_selection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            (binary.parent / ".fake_invalid_catalog").touch()
            log_path = root / "calls.json"
            automatic = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "custom",
                "review",
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(automatic.returncode, 0, automatic.stderr)
            calls = read_fake_log(log_path)
            explicit = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--model",
                "gpt-5.6-sol",
                "custom",
                "review",
            )

        execution = next(call for call in reversed(calls) if "exec" in call["argv"])
        self.assertEqual(
            execution["argv"][execution["argv"].index("--model") + 1], "gpt-5.5"
        )
        self.assertIn("fall", automatic.stderr.lower())
        self.assertNotEqual(explicit.returncode, 0)

    def test_refreshed_catalog_failure_reports_bundled_fallback_warning(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            (binary.parent / ".fake_refresh_error").touch()
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--dry-run",
                "custom",
                "review",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("bundled", result.stderr.lower())

    def test_structured_invalid_final_output_fails_and_is_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "app.py").write_text(
                "def total(values):\n    return 0\n", encoding="utf-8"
            )
            binary = make_fake_codex(root)
            result_path = root / "invalid-structured.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--result-json",
                str(result_path),
                "structured-review",
                "--uncommitted",
                env={"FAKE_CODEX_FINAL": "not-json"},
            )
            envelope = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(envelope["success"])
        self.assertIn("invalid JSON", envelope["error"])

    def test_structured_missing_final_output_is_not_a_false_green(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "app.py").write_text(
                "def total(values):\n    return 0\n", encoding="utf-8"
            )
            binary = make_fake_codex(root)
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "structured-review",
                "--uncommitted",
                env={"FAKE_CODEX_NO_FINAL": "1"},
            )
            custom_schema = root / "custom-schema.json"
            custom_schema.write_text(
                json.dumps(
                    {
                        "type": "object",
                        "additionalProperties": False,
                        "required": ["verdict"],
                        "properties": {"verdict": {"type": "string"}},
                    }
                ),
                encoding="utf-8",
            )
            custom_result = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--schema",
                str(custom_schema),
                "structured-review",
                "--uncommitted",
                env={"FAKE_CODEX_NO_FINAL": "1"},
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("without a final result", result.stderr)
        self.assertNotEqual(custom_result.returncode, 0)
        self.assertIn("without a final result", custom_result.stderr)

    def test_focused_criteria_fragment_is_redacted(self) -> None:
        secret = "FOCUSED-CRITERIA-SECRET"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "focused",
                "module.py",
                secret,
                env={"FAKE_CODEX_STDERR": f"echo {secret}"},
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_structured_runtime_validates_each_finding(self) -> None:
        invalid_finding = {
            "findings": [
                {
                    "title": "missing priority prefix",
                    "body": "bad",
                    "confidence_score": 2,
                    "priority": 2,
                    "code_location": {
                        "absolute_file_path": "relative.py",
                        "line_range": {"start": 9, "end": 2},
                    },
                }
            ],
            "overall_correctness": "patch is incorrect",
            "overall_explanation": "bad",
            "overall_confidence_score": 0.9,
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "app.py").write_text(
                "def total(values):\n    return 0\n", encoding="utf-8"
            )
            binary = make_fake_codex(root)
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "structured-review",
                "--uncommitted",
                env={"FAKE_CODEX_FINAL": json.dumps(invalid_finding)},
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("title", result.stderr)

    def test_quick_alias_resolves_terra_medium(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--quick",
                "custom",
                "review",
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            calls = read_fake_log(log_path)

        execution = next(call for call in reversed(calls) if "exec" in call["argv"])
        argv = execution["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.6-terra")
        self.assertTrue(
            any('model_reasoning_effort="medium"' == value for value in argv)
        )

    def test_newer_unverified_binary_warns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = make_fake_codex(Path(tmp), version="0.145.0")
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--skip-git-repo-check",
                "--dry-run",
                "custom",
                "review",
            )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0.145.0", result.stderr)
        self.assertIn("warning", result.stderr.lower())

    def test_large_diff_guard_requires_explicit_override(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "one.txt").write_text("one\n", encoding="utf-8")
            (repo / "two.txt").write_text("two\n", encoding="utf-8")
            binary = make_fake_codex(root)
            blocked = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--max-changed-files",
                "1",
                "--dry-run",
                "structured-review",
                "--uncommitted",
            )
            allowed = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--max-changed-files",
                "1",
                "--allow-large-diff",
                "--dry-run",
                "structured-review",
                "--uncommitted",
            )

        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("large", blocked.stderr.lower())
        self.assertEqual(allowed.returncode, 0, allowed.stderr)

    def test_review_range_only_sizes_scope_and_does_not_rewrite_prompt(self) -> None:
        secret = "PROMPT-MUST-STAY-UNCHANGED"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            from tests.helpers import git

            git(repo, "switch", "-c", "feature")
            (repo / "app.py").write_text(
                "def total(values):\n    return 0\n", encoding="utf-8"
            )
            git(repo, "add", "app.py")
            git(repo, "commit", "-m", "feature")
            binary = make_fake_codex(root)
            log_path = root / "calls.json"
            result_path = root / "result.json"
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--review-range",
                "main..HEAD",
                "--result-json",
                str(result_path),
                "custom",
                secret,
                env={"FAKE_CODEX_LOG": str(log_path)},
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            execution = next(
                call
                for call in reversed(read_fake_log(log_path))
                if "exec" in call["argv"]
            )
            envelope = json.loads(result_path.read_text(encoding="utf-8"))

        self.assertEqual(execution["stdin"], secret)
        self.assertNotIn("main..HEAD", execution["stdin"])
        self.assertEqual(envelope["scope"]["preflight_range"], "main..HEAD")
        self.assertEqual(envelope["scope"]["kind"], "custom")

    def test_review_range_cannot_replace_structured_scope_sizing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repo = init_git_fixture(root / "repo")
            (repo / "one.txt").write_text("one\n", encoding="utf-8")
            (repo / "two.txt").write_text("two\n", encoding="utf-8")
            binary = make_fake_codex(root)
            result = run_cli(
                "--codex-bin",
                str(binary),
                "--cd",
                str(repo),
                "--review-range",
                "main..HEAD",
                "--max-changed-files",
                "1",
                "--dry-run",
                "structured-review",
                "--uncommitted",
            )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("large", result.stderr.lower())

    def test_doctor_rejects_malformed_health_output_and_works_outside_repo(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root)
            malformed = run_cli(
                "--codex-bin",
                str(binary),
                "doctor",
                env={"FAKE_CODEX_DOCTOR_OUTPUT": "not-json"},
            )
            nonrepo = root / "not-a-repo"
            nonrepo.mkdir()
            healthy = run_cli(
                "--codex-bin",
                str(binary),
                "doctor",
                cwd=nonrepo,
            )

        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("auth_config", malformed.stderr)
        self.assertEqual(healthy.returncode, 0, healthy.stderr)


if __name__ == "__main__":
    unittest.main()
