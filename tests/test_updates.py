from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.helpers import make_fake_codex, read_fake_log


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from codex_reviewer.catalog import CodexBinary  # noqa: E402
from codex_reviewer.updates import prepare_codex_binary  # noqa: E402


class UpdatePolicyTests(unittest.TestCase):
    def discovery_env(
        self, root: Path, binary: Path, *, log_path: Path, cache_path: Path
    ) -> dict[str, str]:
        python_dir = str(
            Path(shutil.which("python3") or sys.executable).resolve().parent
        )
        return {
            "PATH": os.pathsep.join([str(binary.parent), python_dir]),
            "HOME": str(root / "home"),
            "CODEX_HOME": str(root / "home" / ".codex"),
            "CODEX_INSTALL_DIR": str(root / "home" / ".local" / "bin"),
            "CODEX_REVIEWER_UPDATE_CACHE": str(cache_path),
            "FAKE_CODEX_LOG": str(log_path),
        }

    def test_npm_binary_updates_through_its_own_codex_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(
                root / "node_modules" / "@openai" / "codex", "0.143.0"
            )
            log_path = root / "calls.json"
            cache_path = root / "cache" / "update.json"
            env = self.discovery_env(
                root, binary, log_path=log_path, cache_path=cache_path
            )
            env["FAKE_CODEX_UPDATE_VERSION"] = "0.145.0"
            with mock.patch.dict(os.environ, env, clear=False):
                selected, outcome = prepare_codex_binary(force_update=True)
            calls = read_fake_log(log_path)

        self.assertEqual(selected.install_method, "npm")
        self.assertEqual(selected.version, (0, 145, 0))
        self.assertTrue(outcome.checked)
        self.assertTrue(outcome.updated)
        self.assertIn(["update"], [call["argv"] for call in calls])

    def test_fresh_cache_skips_repeated_update(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(
                root / ".codex" / "packages" / "standalone" / "releases" / "0.144.1",
                "0.144.1",
            )
            log_path = root / "calls.json"
            cache_path = root / "cache" / "update.json"
            env = self.discovery_env(
                root, binary, log_path=log_path, cache_path=cache_path
            )
            with mock.patch.dict(os.environ, env, clear=False):
                first_binary, first = prepare_codex_binary(force_update=True)
                second_binary, second = prepare_codex_binary()
            calls = read_fake_log(log_path)

        self.assertEqual(first_binary.path, second_binary.path)
        self.assertTrue(first.checked)
        self.assertTrue(second.cache_hit)
        self.assertEqual(
            sum(call["argv"] == ["update"] for call in calls),
            1,
        )

    def test_explicit_binary_is_never_modified_automatically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = make_fake_codex(root, "0.144.1")
            log_path = root / "calls.json"
            cache_path = root / "cache" / "update.json"
            env = self.discovery_env(
                root, binary, log_path=log_path, cache_path=cache_path
            )
            with mock.patch.dict(os.environ, env, clear=False):
                selected, outcome = prepare_codex_binary(str(binary), force_update=True)
            calls = read_fake_log(log_path)

        self.assertEqual(selected.path, str(binary.resolve()))
        self.assertEqual(outcome.skipped_reason, "explicit binary override")
        self.assertNotIn(["update"], [call["argv"] for call in calls])

    def test_missing_cli_bootstraps_standalone(self) -> None:
        missing = CodexBinary(requested="codex", path=None, error="missing")
        installed = CodexBinary(
            requested="codex",
            path="/tmp/standalone/codex",
            version_text="codex-cli 0.145.0",
            version=(0, 145, 0),
            install_method="standalone",
        )
        completed = subprocess.CompletedProcess([], 0, stdout="ok", stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "update.json"
            with (
                mock.patch.dict(
                    os.environ,
                    {"CODEX_REVIEWER_UPDATE_CACHE": str(cache)},
                    clear=False,
                ),
                mock.patch(
                    "codex_reviewer.updates.CodexBinary.discover",
                    side_effect=[missing, installed],
                ),
                mock.patch(
                    "codex_reviewer.updates._run_update", return_value=completed
                ) as run_update,
            ):
                selected, outcome = prepare_codex_binary(force_update=True)

        self.assertEqual(selected.install_method, "standalone")
        self.assertTrue(outcome.bootstrapped)
        self.assertTrue(outcome.checked)
        command = list(run_update.call_args.args[0])
        self.assertTrue("install.sh" in command[-1] or "install.ps1" in command[-1])

    def test_update_failure_keeps_compatible_selected_binary(self) -> None:
        selected = CodexBinary(
            requested="codex",
            path="/tmp/standalone/codex",
            version_text="codex-cli 0.144.1",
            version=(0, 144, 1),
            install_method="standalone",
        )
        failed = subprocess.CompletedProcess([], 2, stdout="", stderr="offline")
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "update.json"
            with (
                mock.patch.dict(
                    os.environ,
                    {"CODEX_REVIEWER_UPDATE_CACHE": str(cache)},
                    clear=False,
                ),
                mock.patch(
                    "codex_reviewer.updates.CodexBinary.discover",
                    return_value=selected,
                ),
                mock.patch(
                    "codex_reviewer.updates._run_update", return_value=failed
                ) as run_update,
            ):
                binary, outcome = prepare_codex_binary(force_update=True)
                _, backed_off = prepare_codex_binary()

        self.assertEqual(binary.path, selected.path)
        self.assertEqual(binary.version, (0, 144, 1))
        self.assertIsNotNone(outcome.error)
        self.assertTrue(outcome.warnings)
        self.assertEqual(backed_off.skipped_reason, "recent update failure backoff")
        self.assertEqual(run_update.call_count, 1)


if __name__ == "__main__":
    unittest.main()
