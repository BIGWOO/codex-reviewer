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
        return CommandBuilder(
            binary=self.binary,
            model="gpt-5.6-sol",
            effort="high",
            **kwargs,
        )

    def test_native_scope_argv_is_read_only_ephemeral_and_promptless(self) -> None:
        spec = self.builder().native(ReviewScope("base", "main"))
        argv = list(spec.argv)
        self.assertEqual(argv[0], str(self.fake.resolve()))
        self.assertIn("read-only", argv)
        self.assertIn("never", argv)
        self.assertIn('model_reasoning_effort="high"', argv)
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


if __name__ == "__main__":
    unittest.main()
