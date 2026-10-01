from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tests.helpers import make_fake_codex, read_fake_log, run_cli


class DoctorReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.binary = make_fake_codex(self.root)
        self.log = self.root / "calls.json"
        self.receipt = self.root / "doctor.json"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def doctor(self, *args, env=None):  # type: ignore[no-untyped-def]
        result = run_cli(
            "--codex-bin", str(self.binary), "doctor", *args,
            "--result-json", str(self.receipt),
            env={"FAKE_CODEX_LOG": str(self.log), **(env or {})},
        )
        payload = json.loads(self.receipt.read_text(encoding="utf-8"))
        checks = {check["name"]: check for check in payload["diagnostics"]}
        return result, payload, checks

    def test_fast_checks_skip_full_doctor_and_do_not_echo_credentials(self) -> None:
        marker = "CREDENTIAL-PREVIEW-MARKER"
        result, payload, checks = self.doctor(env={
            "FAKE_CODEX_DOCTOR_OUTPUT": "invalid unused full report",
            "FAKE_CODEX_LOGIN_OUTPUT": marker,
        })
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(checks["auth_config"]["status"], "pass")
        self.assertEqual(checks["full_diagnostics"]["status"], "skip")
        self.assertNotIn(marker, json.dumps(payload) + result.stdout + result.stderr)
        calls = read_fake_log(self.log)
        self.assertFalse(any("doctor" in call["argv"] or "exec" in call["argv"] for call in calls))

    def test_login_and_config_failures_are_distinguished(self) -> None:
        for variable, failed_probe in (("FAKE_CODEX_LOGIN_EXIT", "credentials"), ("FAKE_CODEX_CONFIG_EXIT", "config_load")):
            with self.subTest(variable=variable):
                result, payload, checks = self.doctor(env={variable: "2"})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(payload["success"])
                self.assertEqual(checks["auth_config"]["detail"][failed_probe]["status"], "fail")

    def test_full_timeout_is_incomplete_while_fast_auth_stays_healthy(self) -> None:
        result, payload, checks = self.doctor(
            "--full-diagnostics", "--diagnostic-timeout", "1",
            env={"FAKE_CODEX_DOCTOR_SLEEP": "2"},
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(payload["success"])
        self.assertEqual(payload["execution_status"], "timed_out")
        self.assertEqual(checks["auth_config"]["status"], "pass")
        self.assertEqual(checks["full_diagnostics"]["status"], "incomplete")
        self.assertEqual(checks["full_diagnostics"]["detail"]["reason"], "timeout")

    def test_full_warning_is_preserved_without_failing_readiness(self) -> None:
        report = {"overallStatus": "warning", "checks": {"terminal.metadata": {"status": "warning", "summary": "non-interactive terminal"}}}
        result, payload, checks = self.doctor("--full-diagnostics", env={"FAKE_CODEX_DOCTOR_OUTPUT": json.dumps(report)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(payload["success"])
        self.assertEqual(checks["full_diagnostics"]["status"], "warn")
        self.assertEqual(checks["full_diagnostics"]["detail"]["checks"], report["checks"])

    def test_full_report_rejects_malformed_statuses_without_crashing(self) -> None:
        for report in (
            {"overallStatus": "ok", "checks": []},
            {"overallStatus": {}, "checks": {"config.load": {"status": "ok"}}},
            {"overallStatus": "ok", "checks": {"config.load": {"status": {}}}},
        ):
            with self.subTest(report=report):
                result, payload, checks = self.doctor("--full-diagnostics", env={"FAKE_CODEX_DOCTOR_OUTPUT": json.dumps(report)})
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(payload["success"])
                self.assertEqual(checks["full_diagnostics"]["status"], "fail")
                self.assertEqual(checks["auth_config"]["status"], "pass")

    def test_doctor_options_fail_fast_outside_doctor_or_with_invalid_timeout(self) -> None:
        for args in (("custom", "review", "--full-diagnostics"), ("custom", "review", "--diagnostic-timeout", "90"), ("doctor", "--diagnostic-timeout", "0"), ("doctor", "--diagnostic-timeout", "90")):
            with self.subTest(args=args):
                result = run_cli("--codex-bin", str(self.binary), *args, env={"FAKE_CODEX_LOG": str(self.log)})
                self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
