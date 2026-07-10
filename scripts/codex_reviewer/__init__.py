"""Internal implementation package for the Codex Reviewer CLI."""

from .catalog import (
    DEFAULT_PRESET,
    MIN_CODEX_VERSION,
    CodexBinary,
    ModelCatalog,
    ModelInfo,
    ModelSelection,
    PresetResolutionError,
    resolve_model_selection,
)
from .commands import CommandBuilder, CommandSpec
from .result import ReviewResult
from .reviewer import CodexReviewer
from .scope import DiffMetrics, GitInspector, ReviewScope

__all__ = [
    "DEFAULT_PRESET",
    "MIN_CODEX_VERSION",
    "CodexBinary",
    "CodexReviewer",
    "CommandBuilder",
    "CommandSpec",
    "DiffMetrics",
    "GitInspector",
    "ModelCatalog",
    "ModelInfo",
    "ModelSelection",
    "PresetResolutionError",
    "ReviewResult",
    "ReviewScope",
    "resolve_model_selection",
]
