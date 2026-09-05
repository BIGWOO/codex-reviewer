"""Shared default criteria for caller-controlled review prompts."""

DEFAULT_REVIEW_CRITERIA = (
    "Do not stop at syntax or obvious bugs. Within the declared scope, prioritize "
    "hidden side effects, backward compatibility, edge cases, performance and security risks. "
    "Report misleading names, missing tests or maintenance costs only when tied to a concrete "
    "behavioral defect or demonstrable misuse risk introduced by this change. "
    "For each finding, give the triggering condition, impact, minimal file/line evidence "
    "and a minimal-change remediation recommendation. Sort findings by severity, highest first. "
    "Ignore style-only preferences, speculative risks and unrelated pre-existing issues. "
    "Use only evidence permitted by this mode; do not expand scope to complete a checklist. "
    "State evidence gaps instead of guessing or requesting repeated broad reviews. "
    "Remain read-only: recommend fixes but do not edit files or invoke other reviewers."
)
