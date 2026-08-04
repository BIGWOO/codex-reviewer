"""Derive delivery-gate state from the bundled structured review contract."""

from __future__ import annotations

from typing import Mapping, Tuple


PASSING_GATE_STATUSES = frozenset({"passed", "passed_with_warnings"})


def derive_bundled_gate(payload: Mapping[str, object]) -> Tuple[str, str]:
    """Return normalized verdict and gate for a validated bundled result."""
    correctness = payload.get("overall_correctness")
    verdict = "correct" if correctness == "patch is correct" else "incorrect"
    findings = payload.get("findings")
    priorities = (
        [
            finding.get("priority")
            for finding in findings
            if isinstance(finding, Mapping)
        ]
        if isinstance(findings, list)
        else []
    )

    if any(priority in {0, 1, 2} for priority in priorities):
        return verdict, "blocked"
    if priorities:
        return verdict, "passed_with_warnings"
    if verdict == "incorrect":
        # An incorrect verdict without a finding cannot identify what should
        # stop delivery, so it must not silently pass or block.
        return verdict, "inconclusive"
    return verdict, "passed"


def gate_exit_code(gate_status: object) -> int:
    """Map a delivery-gate status to the public CLI exit-code contract."""
    if gate_status in PASSING_GATE_STATUSES:
        return 0
    if gate_status == "blocked":
        return 2
    return 1
