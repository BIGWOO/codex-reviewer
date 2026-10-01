from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from tests.helpers import make_fake_codex


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from codex_reviewer.catalog import CodexBinary  # noqa: E402
from codex_reviewer.commands import CommandBuilder  # noqa: E402
from codex_reviewer.scope import ReviewScope  # noqa: E402


class CommandBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name)
        self.fake = make_fake_codex(self.root)
        self.binary = CodexBinary.discover(str(self.fake))

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def builder(self, **kwargs) -> CommandBuilder:  # type: ignore[no-untyped-def]
        options = {"binary": self.binary, "model": "gpt-5.6-sol", "effort": "high"}
        options.update(kwargs)
        return CommandBuilder(**options)

    def test_native_scope_argv_is_read_only_ephemeral_and_promptless(self) -> None:
        spec = self.builder().native(ReviewScope("base", "main"))
        argv = list(spec.argv)
        self.assertEqual(argv[0], str(self.fake.resolve()))
        self.assertIn("read-only", argv)
        self.assertIn("never", argv)
        self.assertIn('model_reasoning_effort="high"', argv)
        self.assertIn('review_model="gpt-5.6-sol"', argv)
        self.assertIn("--ephemeral", argv)
        self.assertEqual(argv[argv.index("exec") + 1], "review")
        self.assertEqual(argv[argv.index("--base") + 1], "main")
        self.assertIsNone(spec.stdin_payload)
        self.assertNotEqual(argv[-1], "-")

    def test_native_custom_prompt_uses_stdin(self) -> None:
        secret = "native secret criteria"
        spec = self.builder().native(ReviewScope("custom"), prompt=secret)
        self.assertEqual(spec.stdin_payload, secret)
        self.assertEqual(spec.argv[-1], "-")
        self.assertNotIn(secret, spec.argv)
        self.assertNotIn(secret, spec.display_command)

    def test_native_explicit_model_also_overrides_profile_review_model(self) -> None:
        for model in ("gpt-6.1-sol", "gpt-6-astra", "gpt-5.6-sol"):
            builder = CommandBuilder(binary=self.binary, model=model, effort="high", profile="reviewer")
            native = builder.native(ReviewScope("base", "main"))
            self.assertEqual(native.argv[native.argv.index("--model") + 1], model)
            self.assertIn(f'review_model="{model}"', native.argv)
            self.assertFalse(any(value.startswith("review_model=") for value in builder.generic("review").argv))

    def test_generic_supports_schema_images_search_and_stdin(self) -> None:
        schema = self.root / "schema.json"
        image = self.root / "screen.png"
        spec = self.builder(
            schema_file=str(schema),
            images=[str(image)],
            search=True,
        ).generic("generic secret criteria")
        argv = list(spec.argv)
        self.assertIn("--search", argv)
        self.assertEqual(argv[argv.index("--output-schema") + 1], str(schema))
        self.assertEqual(argv[argv.index("--image") + 1], str(image))
        self.assertEqual(argv[-1], "-")
        self.assertEqual(spec.stdin_payload, "generic secret criteria")
        self.assertNotIn("generic secret criteria", spec.display_command)

    def test_ultra_explicitly_enables_delegation_without_enabling_plugins_or_apps(self) -> None:
        for minimal_context in (True, False):
            with self.subTest(minimal_context=minimal_context):
                argv = list(self.builder(effort="ultra", minimal_context=minimal_context).generic("review").argv)
                enabled = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--enable"]
                disabled = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--disable"]
                self.assertIn("multi_agent", enabled)
                self.assertNotIn("multi_agent", disabled)
                self.assertNotIn("multi_agent_v2", enabled)
                self.assertIn("multi_agent_v2", disabled)
                self.assertIn("hooks", disabled)
                self.assertIn("agents.enabled=true", argv)
                self.assertIn("agents.max_concurrent_threads_per_session=2", argv)
                if minimal_context:
                    self.assertIn("plugins", disabled)
                    self.assertIn("apps", disabled)

    def test_no_tools_cannot_enable_ultra_delegation(self) -> None:
        argv = list(self.builder(effort="ultra", no_tools=True).generic("review").argv)
        enabled = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--enable"]
        disabled = [argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--disable"]
        self.assertNotIn("multi_agent", enabled)
        self.assertIn("multi_agent", disabled)
        self.assertIn("multi_agent_v2", disabled)
        self.assertIn("agents.enabled=false", argv)

    def test_profile_fast_context_and_strict_config_are_explicit(self) -> None:
        spec = self.builder(
            profile="review-v2",
            strict_config=True,
            service_tier="fast",
            context_window=372000,
            auto_compact_token_limit=300000,
        ).generic("review")
        argv = list(spec.argv)
        self.assertEqual(argv[argv.index("--profile") + 1], "review-v2")
        self.assertIn("--strict-config", argv)
        self.assertIn('service_tier="fast"', argv)
        self.assertIn("model_context_window=372000", argv)
        self.assertIn("model_auto_compact_token_limit=300000", argv)

    def test_developer_git_path_is_in_process_and_codex_shell_environment(self) -> None:
        git_path = "/Applications/Xcode.app/Contents/Developer/usr/bin/git"
        spec = self.builder(git_path=git_path).generic("review")
        git_dir = str(Path(git_path).parent)
        self.assertEqual(spec.environment["PATH"].split(os.pathsep)[0], git_dir)
        shell_path_values = [
            value for value in spec.argv if "shell_environment_policy.set.PATH" in value
        ]
        self.assertEqual(len(shell_path_values), 1)
        self.assertIn(git_dir, shell_path_values[0])
        self.assertNotIn(git_dir, spec.display_command)
        self.assertIn("<injected>", spec.display_command)

    def test_minimal_context_is_default_and_can_be_disabled(self) -> None:
        minimal = list(self.builder().generic("review").argv)
        full = list(self.builder(minimal_context=False).generic("review").argv)

        for feature in ("plugins", "apps"):
            self.assertIn(feature, minimal)
            self.assertNotIn(feature, full)
        for argv in (minimal, full):
            for feature in ("hooks", "multi_agent", "multi_agent_v2"):
                self.assertIn(feature, argv)
            self.assertIn("agents.enabled=false", argv)

    def test_bounded_controls_disable_code_mode_even_without_minimal_context(self) -> None:
        argv = list(self.builder(no_tools=True, minimal_context=False).generic("review").argv)
        disabled = {argv[i + 1] for i, arg in enumerate(argv[:-1]) if arg == "--disable"}
        self.assertTrue({"shell_tool", "plugins", "apps", "hooks", "multi_agent", "multi_agent_v2", "code_mode", "code_mode_only", "code_mode_host"} <= disabled)
        self.assertIn('web_search="disabled"', argv)



if __name__ == "__main__":
    unittest.main()
