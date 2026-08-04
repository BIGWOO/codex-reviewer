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
from .bounded import BoundedPacket, BoundedScopeError, build_bounded_packet
from .commands import CommandBuilder, CommandSpec
from .gate import derive_bundled_gate, gate_exit_code
from .result import ReviewResult
from .reviewer import CodexReviewer
from .scope import DiffMetrics, GitInspector, ReviewScope
from .updates import UpdateOutcome, prepare_codex_binary

__all__ = [
    "DEFAULT_PRESET",
    "MIN_CODEX_VERSION",
    "BoundedPacket",
    "BoundedScopeError",
    "CodexBinary",
    "CodexReviewer",
    "CommandBuilder",
    "CommandSpec",
    "DiffMetrics",
    "derive_bundled_gate",
    "gate_exit_code",
    "build_bounded_packet",
    "GitInspector",
    "ModelCatalog",
    "ModelInfo",
    "ModelSelection",
    "PresetResolutionError",
    "ReviewResult",
    "ReviewScope",
    "UpdateOutcome",
    "prepare_codex_binary",
    "resolve_model_selection",
]
