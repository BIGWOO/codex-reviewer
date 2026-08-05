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
    idle_timeout: Optional[int] = None
    hard_timeout: Optional[int] = None
    timed_out: bool = False
    timeout_reason: Optional[str] = None
    exit_code: Optional[int] = None
    service_tier: Optional[str] = None
    warnings: List[str] = field(default_factory=list)
    command: Optional[str] = None
    final: Optional[str] = None
    error: Optional[str] = None
    output: Optional[str] = None
    events: List[Mapping[str, Any]] = field(default_factory=list)
    partial_progress: Optional[str] = None
    execution_status: Optional[str] = None
    review_verdict: str = "not_evaluated"
    gate_status: str = "not_evaluated"
    duration_ms: Optional[int] = None
    silence_duration_ms: Optional[int] = None
    terminal_event: Optional[str] = None
    last_event: Optional[str] = None
    event_counts: Mapping[str, int] = field(default_factory=dict)
    raw_output_bytes: Optional[int] = None
    scope_fingerprint: Optional[str] = None
    packet_sha256: Optional[str] = None
    prompt_sha256: Optional[str] = None
    policy_violation: Optional[Mapping[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Return the v2 envelope while retaining v1 dictionary keys."""
        execution_status = self.execution_status
        if execution_status is None:
            if self.timed_out:
                execution_status = "timed_out"
            elif self.success or self.terminal_event == "turn.completed":
                execution_status = "completed"
            else:
                execution_status = "failed"
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
            "idle_timeout": self.idle_timeout,
            "hard_timeout": self.hard_timeout,
            "timed_out": self.timed_out,
            "timeout_reason": self.timeout_reason,
            "exit_code": self.exit_code,
            "service_tier": self.service_tier,
            "warnings": list(self.warnings),
            "sanitized_command": self.command,
            "final_result": self.final,
            "error": self.error,
            "output": self.output,
            "events": list(self.events),
            "partial_progress": self.partial_progress,
            "execution_status": execution_status,
            "review_verdict": self.review_verdict,
            "gate_status": self.gate_status,
            "duration_ms": self.duration_ms,
            "silence_duration_ms": self.silence_duration_ms,
            "terminal_event": self.terminal_event,
            "last_event": self.last_event,
            "event_counts": dict(self.event_counts),
            "raw_output_bytes": self.raw_output_bytes,
            "scope_fingerprint": self.scope_fingerprint,
            "packet_sha256": self.packet_sha256,
            "prompt_sha256": self.prompt_sha256,
            "policy_violation": (
                dict(self.policy_violation)
                if self.policy_violation is not None
                else None
            ),
            # Compatibility aliases used by v1 callers.
            "command": self.command,
            "final": self.final,
            "summary": self.final,
            "preflight_warnings": list(self.warnings),
        }


def error_result(mode: str, message: str, **kwargs: Any) -> Dict[str, Any]:
    return ReviewResult(success=False, mode=mode, error=message, **kwargs).to_dict()
