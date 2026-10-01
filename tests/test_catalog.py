from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

from tests.helpers import make_fake_codex, read_fake_log


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from codex_reviewer.catalog import (  # noqa: E402
    CODEX_BIN_ENV,
    PRESET_EFFORTS,
    CodexBinary,
    ModelCatalog,
    ModelInfo,
    PresetResolutionError,
    resolve_model_selection,
)


def model(slug: str, efforts: tuple[str, ...], *, fast: bool = False) -> ModelInfo:
    return ModelInfo.from_payload(
        {
            "slug": slug,
            "display_name": slug,
            "supported_reasoning_levels": [{"effort": effort} for effort in efforts],
            "context_window": 372000,
            "max_context_window": 372000,
            "additional_speed_tiers": ["fast"] if fast else [],
            "service_tiers": [],
        }
    )


class CodexBinaryTests(unittest.TestCase):
    def test_path_discovery_selects_highest_stable_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = make_fake_codex(root / "old", "0.142.5")
            stable = make_fake_codex(root / "stable", "0.159.3")
            alpha = make_fake_codex(root / "alpha", "0.160.0-alpha.1")
            python_dir = str(
                Path(shutil.which("python3") or sys.executable).resolve().parent
            )
            path = os.pathsep.join(
                [str(old.parent), str(alpha.parent), str(stable.parent), python_dir]
            )
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": path,
                    "HOME": str(root / "home"),
                    "CODEX_HOME": str(root / "home" / ".codex"),
                    "CODEX_INSTALL_DIR": str(root / "home" / ".local" / "bin"),
                },
                clear=False,
            ):
                resolved = CodexBinary.discover()

        self.assertEqual(Path(resolved.path or "").resolve(), stable.resolve())
        self.assertEqual(resolved.version, (0, 159, 3))

    def test_npm_install_wins_over_newer_standalone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            npm_binary = make_fake_codex(
                root / "node_modules" / "@openai" / "codex", "0.159.3"
            )
            standalone = make_fake_codex(
                root / ".codex" / "packages" / "standalone" / "releases" / "0.160.0",
                "0.160.0",
            )
            python_dir = str(
                Path(shutil.which("python3") or sys.executable).resolve().parent
            )
            path = os.pathsep.join(
                [str(standalone.parent), str(npm_binary.parent), python_dir]
            )
            with mock.patch.dict(
                os.environ,
                {
                    "PATH": path,
                    "HOME": str(root / "home"),
                    "CODEX_HOME": str(root / "home" / ".codex-empty"),
                    "CODEX_INSTALL_DIR": str(root / "home" / ".local" / "bin"),
                },
                clear=False,
            ):
                resolved = CodexBinary.discover()

        self.assertEqual(Path(resolved.path or "").resolve(), npm_binary.resolve())
        self.assertEqual(resolved.install_method, "npm")

    def test_flag_precedes_environment_and_environment_precedes_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path_binary = make_fake_codex(root / "path", "0.159.3")
            env_binary = make_fake_codex(root / "env", "0.160.0")
            flag_binary = make_fake_codex(root / "flag", "0.146.0")
            python_dir = str(
                Path(shutil.which("python3") or sys.executable).resolve().parent
            )
            path = os.pathsep.join([str(path_binary.parent), python_dir])
            with mock.patch.dict(
                os.environ,
                {"PATH": path, CODEX_BIN_ENV: str(env_binary)},
                clear=False,
            ):
                from_env = CodexBinary.discover()
                from_flag = CodexBinary.discover(str(flag_binary))

        self.assertEqual(Path(from_env.path or "").resolve(), env_binary.resolve())
        self.assertEqual(Path(from_flag.path or "").resolve(), flag_binary.resolve())


class CatalogAndPresetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = ModelCatalog(
            models={
                "gpt-6.1-sol": model(
                    "gpt-6.1-sol", ("low", "medium", "high", "xhigh", "max", "ultra"), fast=True,
                ),
                "gpt-6-astra": model(
                    "gpt-6-astra", ("low", "medium", "high", "xhigh", "max", "ultra"), fast=True,
                ),
                "gpt-5.6-sol": model(
                    "gpt-5.6-sol",
                    ("low", "medium", "high", "xhigh", "max", "ultra"),
                    fast=True,
                ),
                "gpt-5.6-terra": model(
                    "gpt-5.6-terra", ("low", "medium", "high", "xhigh", "max", "ultra")
                ),
            }
        )

    def test_presets_choose_expected_model_and_effort(self) -> None:
        expected = {
            "quick": ("gpt-6.1-sol", "medium"),
            "standard": ("gpt-6.1-sol", "high"),
            "deep": ("gpt-6.1-sol", "xhigh"),
            "ultra": ("gpt-6.1-sol", "ultra"),
        }
        for preset, pair in expected.items():
            with self.subTest(preset=preset):
                selection = resolve_model_selection(preset, self.catalog)
                self.assertEqual((selection.model, selection.effort), pair)

    def test_every_preset_requires_default_model_without_substitution(self) -> None:
        alternatives = ModelCatalog(models={"gpt-6-astra": self.catalog.models["gpt-6-astra"]})
        for preset in PRESET_EFFORTS:
            with self.subTest(preset=preset), self.assertRaises(PresetResolutionError):
                resolve_model_selection(preset, alternatives)

    def test_required_effort_failure_does_not_select_another_model(self) -> None:
        partial = ModelCatalog(models={
            "gpt-6.1-sol": model("gpt-6.1-sol", ("low", "medium")),
            "gpt-6-astra": self.catalog.models["gpt-6-astra"],
        })
        with self.assertRaises(PresetResolutionError):
            resolve_model_selection("standard", partial)

    def test_catalog_failure_never_launches_an_unverified_model(self) -> None:
        unavailable = ModelCatalog(error="refresh and bundled catalog failed", source="unavailable")
        for kwargs in ({}, {"explicit_model": "gpt-6.1-sol"}, {"explicit_effort": "high"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PresetResolutionError):
                resolve_model_selection("standard", unavailable, **kwargs)

    def test_alternative_model_requires_explicit_selection(self) -> None:
        alternatives = ModelCatalog(models={"gpt-6-astra": self.catalog.models["gpt-6-astra"]})
        selection = resolve_model_selection("standard", alternatives, explicit_model="gpt-6-astra")
        self.assertEqual((selection.model, selection.effort), ("gpt-6-astra", "high"))
        self.assertFalse(selection.fallback_used)
        with self.assertRaises(PresetResolutionError):
            resolve_model_selection("standard", alternatives, explicit_effort="high")

    def test_retired_model_is_rejected_even_when_catalog_advertises_it(self) -> None:
        retired = "gpt-5.5"
        catalog = ModelCatalog(models={retired: model(retired, ("high",))})
        with self.assertRaisesRegex(PresetResolutionError, "retired"):
            resolve_model_selection("standard", catalog, explicit_model=retired)

    def test_retired_model_is_filtered_from_loaded_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            payload = {"models": [{"slug": "gpt-6.1-sol", "supported_reasoning_levels": [{"effort": "high"}]}, {"slug": "gpt-5.5"}]}
            (fake.parent / ".fake_catalog.json").write_text(json.dumps(payload), encoding="utf-8")
            catalog = ModelCatalog.load(CodexBinary.discover(str(fake)))
        self.assertIsNone(catalog.get("gpt-5.5"))
        self.assertIsNotNone(catalog.get("gpt-6.1-sol"))

    def test_model_alias_and_effort_validation(self) -> None:
        selection = resolve_model_selection(
            "standard", self.catalog, explicit_model="gpt-5.6", explicit_effort="max"
        )
        self.assertEqual(selection.model, "gpt-5.6-sol")
        with self.assertRaises(PresetResolutionError):
            resolve_model_selection(
                "standard",
                self.catalog,
                explicit_model="gpt-6.1-sol",
                explicit_effort="unsupported",
            )

    def test_fast_tier_is_read_from_catalog(self) -> None:
        sol = self.catalog.models["gpt-5.6-sol"]
        self.assertIn("fast", sol.service_tiers)

    def test_refreshed_catalog_falls_back_to_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            fake = make_fake_codex(root)
            (fake.parent / ".fake_refresh_error").touch()
            log_path = root / "calls.json"
            binary = CodexBinary.discover(str(fake))
            with mock.patch.dict(
                os.environ, {"FAKE_CODEX_LOG": str(log_path)}, clear=False
            ):
                catalog = ModelCatalog.load(binary)
            calls = read_fake_log(log_path)

        self.assertEqual(catalog.source, "bundled")
        self.assertIn("gpt-5.6-sol", catalog.models)
        self.assertTrue(catalog.warnings)
        model_calls = [call["argv"] for call in calls if "debug" in call["argv"]]
        self.assertEqual(len(model_calls), 2)
        self.assertNotIn("--bundled", model_calls[0])
        self.assertIn("--bundled", model_calls[1])

    def test_invalid_refreshed_and_bundled_catalog_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            fake = make_fake_codex(Path(tmp))
            (fake.parent / ".fake_invalid_catalog").touch()
            catalog = ModelCatalog.load(CodexBinary.discover(str(fake)))

        self.assertEqual(catalog.source, "unavailable")
        self.assertFalse(catalog.models)
        self.assertIn("bundled catalog failed", catalog.error or "")


if __name__ == "__main__":
    unittest.main()
