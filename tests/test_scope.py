from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from tests.helpers import git, init_git_fixture


SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in os.sys.path:
    os.sys.path.insert(0, str(SCRIPTS))

from codex_reviewer.scope import (  # noqa: E402
    GitInspector,
    ReviewScope,
    developer_git_environment,
    large_diff_error,
)


class GitScopeTests(unittest.TestCase):
    def test_uncommitted_merges_staged_unstaged_untracked_and_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            (repo / "staged space.py").write_text(
                "one = 1\ntwo = 2\nthree = 3\n", encoding="utf-8"
            )
            git(repo, "add", "staged space.py")
            with (repo / "app.py").open("a", encoding="utf-8") as handle:
                handle.write("\nvalue = total([1, 2])\n")
            (repo / "untracked space.txt").write_text("a\nb\nc\nd\n", encoding="utf-8")
            binary_bytes = b"\x00" * 160
            (repo / "untracked.bin").write_bytes(binary_bytes)

            metrics = GitInspector(str(repo)).metrics(ReviewScope("uncommitted"))

        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertEqual(metrics.changed_files, 4)
        self.assertEqual(
            set(metrics.paths),
            {"app.py", "staged space.py", "untracked space.txt", "untracked.bin"},
        )
        self.assertEqual(
            metrics.untracked_bytes, len(binary_bytes) + len("a\nb\nc\nd\n")
        )
        self.assertEqual(metrics.changed_lines, 9)
        self.assertTrue(
            any("binary" in warning.lower() for warning in metrics.warnings)
        )

    def test_base_scope_uses_merge_base_not_base_tip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            git(repo, "switch", "-c", "feature")
            (repo / "app.py").write_text(
                "def total(values):\n    return 0  # seeded bug\n", encoding="utf-8"
            )
            git(repo, "add", "app.py")
            git(repo, "commit", "-m", "feature change")
            git(repo, "switch", "main")
            (repo / "main-only.txt").write_text("base moved\n", encoding="utf-8")
            git(repo, "add", "main-only.txt")
            git(repo, "commit", "-m", "advance base")
            git(repo, "switch", "feature")

            metrics = GitInspector(str(repo)).metrics(ReviewScope("base", "main"))

        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertEqual(metrics.changed_files, 1)
        self.assertEqual(metrics.paths, ["app.py"])
        self.assertNotIn("main-only.txt", metrics.paths)

    def test_commit_scope_is_exact_change_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            (repo / "first.txt").write_text("first\n", encoding="utf-8")
            git(repo, "add", "first.txt")
            git(repo, "commit", "-m", "first change")
            (repo / "second file.txt").write_text("second\n", encoding="utf-8")
            git(repo, "add", "second file.txt")
            git(repo, "commit", "-m", "second change")
            commit = git(repo, "rev-parse", "HEAD").stdout.strip()

            metrics = GitInspector(str(repo)).metrics(ReviewScope("commit", commit))

        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertEqual(metrics.changed_files, 1)
        self.assertEqual(metrics.paths, ["second file.txt"])
        self.assertEqual(metrics.changed_lines, 1)

    def test_numstat_commands_are_nul_safe(self) -> None:
        class RecordingInspector(GitInspector):
            def __init__(self, cwd: str):
                super().__init__(cwd)
                self.calls: list[list[str]] = []

            def _run(self, args, text=True):  # type: ignore[no-untyped-def]
                self.calls.append(list(args))
                return super()._run(args, text=text)

        with tempfile.TemporaryDirectory() as tmp:
            repo = init_git_fixture(Path(tmp) / "repo")
            (repo / "line\nbreak.txt").write_text("one\ntwo\n", encoding="utf-8")
            git(repo, "add", "line\nbreak.txt")
            inspector = RecordingInspector(str(repo))
            metrics = inspector.metrics(ReviewScope("uncommitted"))

        self.assertIsNotNone(metrics)
        assert metrics is not None
        self.assertIn("line\nbreak.txt", metrics.paths)
        self.assertEqual(metrics.changed_lines, 2)
        numstat_calls = [args for args in inspector.calls if "--numstat" in args]
        self.assertTrue(numstat_calls)
        for args in numstat_calls:
            self.assertIn("-z", args)

    def test_large_diff_threshold_reports_scope_and_limits(self) -> None:
        from codex_reviewer.scope import DiffMetrics

        metrics = DiffMetrics(changed_files=3, changed_lines=101)
        message = large_diff_error(metrics, ReviewScope("uncommitted"), 2, 100)
        self.assertIsNotNone(message)
        self.assertIn("3 files", message or "")
        self.assertIn("101", message or "")
        self.assertIsNone(large_diff_error(metrics, ReviewScope("uncommitted"), 3, 101))

    def test_developer_git_directory_is_prepended_without_losing_path(self) -> None:
        git_path = "/Applications/Xcode.app/Contents/Developer/usr/bin/git"
        env = developer_git_environment(git_path, {"PATH": "/usr/local/bin:/usr/bin"})
        self.assertEqual(env["PATH"].split(os.pathsep)[0], str(Path(git_path).parent))
        self.assertIn("/usr/local/bin", env["PATH"].split(os.pathsep))
        self.assertEqual(
            env["DEVELOPER_DIR"], "/Applications/Xcode.app/Contents/Developer"
        )


if __name__ == "__main__":
    unittest.main()
