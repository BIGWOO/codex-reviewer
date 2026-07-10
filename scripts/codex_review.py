#!/usr/bin/env python3
"""Thin compatibility entrypoint for Codex Reviewer v2."""

from codex_reviewer.cli import build_parser, main, require_target, run_from_args
from codex_reviewer.reviewer import (
    DEFAULT_MAX_CHANGED_FILES,
    DEFAULT_MAX_DIFF_LINES,
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    DEFAULT_TIMEOUT,
    CodexReviewer,
)

__all__ = [
    "CodexReviewer",
    "DEFAULT_MAX_CHANGED_FILES",
    "DEFAULT_MAX_DIFF_LINES",
    "DEFAULT_MODEL",
    "DEFAULT_REASONING_EFFORT",
    "DEFAULT_TIMEOUT",
    "build_parser",
    "main",
    "require_target",
    "run_from_args",
]


if __name__ == "__main__":
    raise SystemExit(main())
