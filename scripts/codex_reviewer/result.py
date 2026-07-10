"""Stable v2 result envelope with compatibility aliases."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional


@dataclass
class ReviewResult:
    success: bool
    mode: str
    binary: Optional[str] = None
    version: Optional[str] = None
    scope: Optional[Mapping[str, Any]] = None
    model: Optional[str] = None
    effort: Optional[str] = None
    usage: Optional[Mapping[str, Any]] = None
    timeout: Optional[int] = None
    timed_out: bool = False
    exit_code: Optional[int] = None
    service_tier: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    command: Optional[str] = None
    final: Optional[str] = None
    error: Optional[str] = None
    output: Optional[str] = None
    events: List[Mapping[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Return the v2 envelope while retaining v1 dictionary keys."""
        return {
            "schema_version": 2,
            "success": self.success,
            "mode": self.mode,
            "binary": self.binary,
            "version": self.version,
            "scope": dict(self.scope) if self.scope is not None else None,
            "model": self.model,
            "effort": self.effort,
            "usage": dict(self.usage) if self.usage is not None else None,
            "timeout": self.timeout,
            "timed_out": self.timed_out,
            "exit_code": self.exit_code,
            "service_tier": self.service_tier,
            "warnings": list(self.warnings),
            "sanitized_command": self.command,
            "final_result": self.final,
            "error": self.error,
            "output": self.output,
            "events": list(self.events),
            # Compatibility aliases used by v1 callers.
            "command": self.command,
            "final": self.final,
            "summary": self.final,
            "preflight_warnings": list(self.warnings),
        }


def error_result(mode: str, message: str, **kwargs: Any) -> Dict[str, Any]:
    return ReviewResult(success=False, mode=mode, error=message, **kwargs).to_dict()
