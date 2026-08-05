"""Public command-line contract for Codex Reviewer v2."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Dict, Mapping, Optional, Sequence

from .bounded import BoundedScopeError, build_bounded_packet, load_json_object
from .catalog import DEFAULT_PRESET, PRESET_NAMES
from .gate import gate_exit_code
from .result import error_result
from .reviewer import (
    DEFAULT_BOUNDED_IDLE_TIMEOUT,
    DEFAULT_BOUNDED_TIMEOUT,
    DEFAULT_MAX_CHANGED_FILES,
    DEFAULT_MAX_DIFF_LINES,
    DEFAULT_IDLE_TIMEOUT,
    DEFAULT_TIMEOUT,
    CodexReviewer,
    run_doctor,
)


REVIEW_TYPES = (
    "native-review",
    "structured-review",
    "bounded-review",
    "security",
    "performance",
    "architecture",
    "quality",
    "diff",
    "focused",
    "custom",
    "doctor",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an independent read-only Codex CLI review gate.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "review_type", choices=REVIEW_TYPES, help="Review pattern to run."
    )
    parser.add_argument("target", nargs="?", help="Target path/ref or custom prompt.")
    parser.add_argument(
        "extra",
        nargs="*",
        help="Additional diff, architecture, focus, or prompt arguments.",
    )
    parser.add_argument(
        "--codex-bin",
        help="Explicit Codex CLI binary; overrides CODEX_REVIEWER_CODEX_BIN and PATH.",
    )
    update_group = parser.add_mutually_exclusive_group()
    update_group.add_argument(
        "--no-update-check",
        action="store_true",
        help="Skip the automatic Codex install/update check for this run.",
    )
    update_group.add_argument(
        "--force-update-check",
        action="store_true",
        help="Ignore the update cache and run the install-source-aware update now.",
    )
    parser.add_argument(
        "--preset",
        choices=PRESET_NAMES,
        help=f"Review preset (default: {DEFAULT_PRESET}).",
    )
    parser.add_argument(
        "--model", help="Explicit model override; gpt-5.6 aliases gpt-5.6-sol."
    )
    parser.add_argument(
        "--reasoning-effort",
        help="Explicit catalog-validated reasoning effort override.",
    )
    parser.add_argument(
        "--instructions",
        help="Additional generic review criteria passed only through stdin.",
    )
    parser.add_argument(
        "--profile", help="Codex V2 profile name from $CODEX_HOME/<name>.config.toml."
    )
    parser.add_argument(
        "--strict-config",
        action="store_true",
        help="Reject unknown Codex config fields.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Use Fast tier only when the model catalog declares it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the sanitized command without inference.",
    )
    parser.add_argument("--cd", dest="cwd", help="Repository root passed to Codex.")
    parser.add_argument(
        "--add-dir",
        dest="add_dirs",
        action="append",
        default=[],
        help="Additional readable directory.",
    )
    parser.add_argument(
        "--output", help="Write raw Codex stdout, usually JSONL, to this file."
    )
    parser.add_argument(
        "--last-message-output", help="Write the final reviewer message to this file."
    )
    parser.add_argument(
        "--result-json", help="Write the additional v2 result envelope to this file."
    )
    parser.add_argument(
        "--include-events",
        action="store_true",
        help="Include sanitized JSONL events in --result-json (raw JSONL still belongs in --output).",
    )
    parser.add_argument(
        "--enforce-gate",
        action="store_true",
        help="Exit 2 for blocked findings and 1 for failed, inconclusive, or unevaluated reviews.",
    )
    parser.add_argument(
        "--schema", dest="schema_file", help="JSON Schema for generic final output."
    )
    parser.add_argument(
        "--timeout",
        type=int,
        help=(
            "Compatible hard timeout in seconds; defaults to 600 for bounded-review "
            "and 300 for existing modes."
        ),
    )
    parser.add_argument(
        "--hard-timeout",
        type=int,
        help="Absolute runtime limit; overrides the compatible --timeout value.",
    )
    parser.add_argument(
        "--idle-timeout",
        type=int,
        help=(
            "Terminate when Codex emits no stdout or stderr activity for this many "
            "seconds; defaults to 0 for bounded-review and 180 for existing modes."
        ),
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Alias for --preset quick; not a completion gate.",
    )
    parser.add_argument(
        "--long-context",
        action="store_true",
        help="Deprecated compatibility flag: use the selected model's catalog context without auto-compaction tuning.",
    )
    parser.add_argument(
        "--context-window", type=int, help="Explicit catalog-validated context window."
    )
    parser.add_argument(
        "--auto-compact-token-limit",
        type=int,
        help="Explicit catalog-validated compact threshold.",
    )
    parser.add_argument(
        "--review-range",
        help="Generic Git range used only for preflight sizing; it never rewrites the review prompt.",
    )
    parser.add_argument(
        "--scope-manifest",
        help="JSON manifest declaring every repository and Git scope for a generic review.",
    )
    parser.add_argument(
        "--bounded-scope",
        help="Version 1 bounded scope JSON file; required by bounded-review.",
    )
    parser.add_argument(
        "--evidence-json",
        help="Optional caller-executed check evidence JSON file for bounded-review.",
    )
    parser.add_argument(
        "--allow-large-diff",
        action="store_true",
        help="Bypass the large-diff guard intentionally.",
    )
    parser.add_argument(
        "--max-changed-files",
        type=int,
        default=DEFAULT_MAX_CHANGED_FILES,
        help="Large-diff file threshold.",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=DEFAULT_MAX_DIFF_LINES,
        help="Large-diff changed-line threshold.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        help="Maximum unique JSONL tool calls; unset means unlimited.",
    )
    parser.add_argument(
        "--max-jsonl-bytes",
        type=int,
        help="Maximum raw JSONL stdout bytes; unset means unlimited.",
    )
    parser.add_argument(
        "--text", action="store_true", help="Disable JSONL and use plain Codex output."
    )
    parser.add_argument(
        "--skip-git-repo-check",
        action="store_true",
        help="Allow generic review outside Git.",
    )
    parser.add_argument(
        "--persist-session", action="store_true", help="Do not pass --ephemeral."
    )
    parser.add_argument(
        "--ignore-user-config", action="store_true", help="Ignore base user config."
    )
    parser.add_argument(
        "--ignore-rules",
        action="store_true",
        help="Ignore user/project execpolicy rules.",
    )
    parser.add_argument(
        "--isolated",
        action="store_true",
        help=(
            "Alias for --ignore-user-config --ignore-rules; does not disable "
            "skill discovery, skills, or plugins."
        ),
    )
    context_group = parser.add_mutually_exclusive_group()
    context_group.add_argument(
        "--minimal-context",
        dest="minimal_context",
        action="store_true",
        help="Disable Codex plugins, apps, and multi-agent features for a lean review session.",
    )
    context_group.add_argument(
        "--full-context",
        dest="minimal_context",
        action="store_false",
        help="Keep Codex plugins, apps, and multi-agent features available.",
    )
    parser.set_defaults(minimal_context=True)
    parser.add_argument(
        "--search",
        action="store_true",
        help="Enable live web search for generic review only.",
    )
    parser.add_argument(
        "--image",
        dest="images",
        action="append",
        default=[],
        help="Attach an image to generic review.",
    )
    parser.add_argument("--base", help="Review current branch against this merge base.")
    parser.add_argument(
        "--commit", help="Review the exact change set introduced by this commit."
    )
    parser.add_argument(
        "--uncommitted",
        action="store_true",
        help="Review staged, unstaged, and untracked changes.",
    )
    parser.add_argument(
        "--title", help="Native commit review title; valid only with --commit."
    )
    return parser


def require_target(args: argparse.Namespace) -> Optional[Dict[str, object]]:
    if args.target:
        return None
    return error_result(
        args.review_type, f"{args.review_type} review requires a target or prompt"
    )


def _error(mode: str, message: str) -> Dict[str, object]:
    return error_result(mode, message)


def _validate_output_paths(args: argparse.Namespace) -> Optional[str]:
    output_values = [
        value
        for value in (args.output, args.last_message_output, args.result_json)
        if value
    ]
    resolved_outputs = {
        str(Path(value).expanduser().resolve()) for value in output_values
    }
    if len(resolved_outputs) != len(output_values):
        return (
            "--output, --last-message-output, and --result-json must use distinct paths"
        )
    for index, left in enumerate(output_values):
        for right in output_values[index + 1 :]:
            try:
                if os.path.samefile(Path(left).expanduser(), Path(right).expanduser()):
                    return (
                        "--output, --last-message-output, and --result-json "
                        "must not be hard-link aliases"
                    )
            except OSError:
                pass
    input_values = [
        value
        for value in (
            args.schema_file,
            args.scope_manifest,
            args.bounded_scope,
            args.evidence_json,
            *args.images,
        )
        if value
    ]
    resolved_inputs = {
        str(Path(value).expanduser().resolve()) for value in input_values
    }
    collisions = sorted(resolved_outputs & resolved_inputs)
    for output_value in output_values:
        for input_value in input_values:
            try:
                if os.path.samefile(
                    Path(output_value).expanduser(), Path(input_value).expanduser()
                ):
                    collisions.append(str(Path(input_value).expanduser().resolve()))
            except OSError:
                pass
    collisions = sorted(set(collisions))
    if collisions:
        return (
            "Output paths must not overwrite schema, scope, evidence, or image inputs: "
            + ", ".join(collisions)
        )
    return None


def _reviewer(args: argparse.Namespace, preset: str) -> CodexReviewer:
    bounded = args.review_type == "bounded-review"
    hard_timeout = args.hard_timeout
    if hard_timeout is None:
        hard_timeout = args.timeout
    if hard_timeout is None:
        hard_timeout = DEFAULT_BOUNDED_TIMEOUT if bounded else DEFAULT_TIMEOUT
    idle_timeout = args.idle_timeout
    if idle_timeout is None:
        idle_timeout = (
            DEFAULT_BOUNDED_IDLE_TIMEOUT if bounded else DEFAULT_IDLE_TIMEOUT
        )
    max_tool_calls = (
        0 if bounded and args.max_tool_calls is None else args.max_tool_calls
    )
    max_jsonl_bytes = (
        262144
        if bounded and args.max_jsonl_bytes is None
        else args.max_jsonl_bytes
    )
    ignore_user_config = bounded or args.ignore_user_config or args.isolated
    ignore_rules = args.ignore_rules or args.isolated
    review_range = args.review_range
    if not review_range and args.review_type == "diff" and args.target:
        base = args.extra[0] if args.extra else "main"
        review_range = f"{base}..{args.target}"
    return CodexReviewer(
        model=args.model,
        json_output=not args.text,
        long_context=args.long_context,
        cwd=args.cwd,
        add_dirs=args.add_dirs,
        schema_file=args.schema_file,
        timeout=hard_timeout,
        idle_timeout=idle_timeout,
        skip_git_repo_check=args.skip_git_repo_check,
        reasoning_effort=args.reasoning_effort,
        output_file=args.output,
        last_message_output=args.last_message_output,
        ephemeral=not args.persist_session,
        ignore_user_config=ignore_user_config,
        ignore_rules=ignore_rules,
        search=args.search,
        images=args.images,
        context_window=args.context_window,
        auto_compact_token_limit=args.auto_compact_token_limit,
        review_range=review_range,
        scope_manifest=args.scope_manifest,
        allow_large_diff=args.allow_large_diff,
        max_changed_files=args.max_changed_files,
        max_diff_lines=args.max_diff_lines,
        max_tool_calls=max_tool_calls,
        max_jsonl_bytes=max_jsonl_bytes,
        codex_bin=args.codex_bin,
        preset=preset,
        instructions=args.instructions,
        profile=args.profile,
        strict_config=args.strict_config,
        fast=args.fast,
        dry_run=args.dry_run,
        minimal_context=True if bounded else args.minimal_context,
        update_check=not args.no_update_check,
        force_update_check=args.force_update_check,
    )


def _validate_bounded_args(args: argparse.Namespace, preset: str) -> Optional[str]:
    if not args.bounded_scope:
        return "bounded-review requires --bounded-scope"
    if args.target or args.extra:
        return "bounded-review does not accept a target or positional prompt"
    if preset not in {"standard", "deep"}:
        return "bounded-review supports only --preset standard or explicit --preset deep"
    if args.max_tool_calls not in {None, 0}:
        return "bounded-review enforces --max-tool-calls 0"
    conflicts = []
    if args.model:
        conflicts.append("--model")
    if args.reasoning_effort:
        conflicts.append("--reasoning-effort")
    if args.quick:
        conflicts.append("--quick")
    if args.instructions:
        conflicts.append("--instructions")
    if args.profile:
        conflicts.append("--profile")
    if args.schema_file:
        conflicts.append("--schema")
    if args.search:
        conflicts.append("--search")
    if args.images:
        conflicts.append("--image")
    if args.add_dirs:
        conflicts.append("--add-dir")
    if args.scope_manifest:
        conflicts.append("--scope-manifest")
    if args.review_range:
        conflicts.append("--review-range")
    if not args.minimal_context:
        conflicts.append("--full-context")
    if args.isolated:
        conflicts.append("--isolated")
    if args.fast:
        conflicts.append("--fast")
    if args.text:
        conflicts.append("--text")
    if args.skip_git_repo_check:
        conflicts.append("--skip-git-repo-check")
    if args.persist_session:
        conflicts.append("--persist-session")
    if args.long_context:
        conflicts.append("--long-context")
    if args.context_window is not None:
        conflicts.append("--context-window")
    if args.auto_compact_token_limit is not None:
        conflicts.append("--auto-compact-token-limit")
    if args.base:
        conflicts.append("--base")
    if args.commit:
        conflicts.append("--commit")
    if args.uncommitted:
        conflicts.append("--uncommitted")
    if args.title:
        conflicts.append("--title")
    if conflicts:
        return "bounded-review cannot be combined with: " + ", ".join(conflicts)
    return None


def run_from_args(args: argparse.Namespace) -> Dict[str, object]:
    output_error = _validate_output_paths(args)
    if output_error:
        return _error(args.review_type, output_error)
    if args.quick and args.preset and args.preset != "quick":
        return _error(
            args.review_type, "--quick cannot be combined with a non-quick --preset"
        )
    preset = "quick" if args.quick else (args.preset or DEFAULT_PRESET)
    if args.review_type != "bounded-review" and (
        args.bounded_scope or args.evidence_json
    ):
        return _error(
            args.review_type,
            "--bounded-scope and --evidence-json are only valid for bounded-review",
        )
    if args.profile and (args.ignore_user_config or args.isolated):
        return _error(
            args.review_type,
            "--profile cannot be combined with --ignore-user-config or --isolated",
        )
    if args.scope_manifest and args.review_range:
        return _error(
            args.review_type,
            "--scope-manifest cannot be combined with --review-range",
        )
    if args.scope_manifest and args.review_type in {
        "native-review",
        "structured-review",
        "bounded-review",
        "doctor",
    }:
        return _error(
            args.review_type,
            "--scope-manifest is only valid for generic review modes",
        )

    if args.review_type == "doctor":
        if args.target or args.extra:
            return _error("doctor", "doctor does not accept a target or prompt")
        result = run_doctor(
            codex_bin=args.codex_bin,
            cwd=args.cwd,
            strict_config=args.strict_config,
            profile=args.profile,
            update_check=not args.no_update_check,
            force_update_check=args.force_update_check,
        )
        return result

    if args.review_type == "bounded-review":
        bounded_error = _validate_bounded_args(args, preset)
        if bounded_error:
            return _error("bounded", bounded_error)
        assert args.bounded_scope is not None
        try:
            scope = load_json_object(args.bounded_scope, "bounded scope")
            evidence = (
                load_json_object(args.evidence_json, "evidence")
                if args.evidence_json
                else None
            )
            packet = build_bounded_packet(
                args.cwd or os.getcwd(), scope, evidence=evidence
            )
        except BoundedScopeError as exc:
            return _error("bounded", str(exc))
        if not args.allow_large_diff and (
            packet.metrics["files"] > args.max_changed_files
            or packet.metrics["lines"] > args.max_diff_lines
        ):
            return _error(
                "bounded",
                "Bounded packet exceeds the large-review threshold: "
                f"{packet.metrics['files']} files and {packet.metrics['lines']} lines",
            )
        return _reviewer(args, preset).bounded_review(packet)

    if args.review_type not in {"native-review", "structured-review"}:
        if args.base or args.commit or args.uncommitted or args.title:
            return _error(
                args.review_type,
                "--base, --commit, --uncommitted, and --title are only valid for native-review or structured-review",
            )

    reviewer = _reviewer(args, preset)
    if args.review_type == "native-review":
        prompt = " ".join(([args.target] if args.target else []) + args.extra)
        return reviewer.native_review(
            prompt=prompt,
            base=args.base,
            commit=args.commit,
            uncommitted=args.uncommitted,
            title=args.title,
        )

    if args.review_type == "structured-review":
        if args.target or args.extra:
            return _error(
                "structured-review",
                "structured-review scope uses --base, --commit, or --uncommitted; add criteria with --instructions",
            )
        if args.title:
            return _error(
                "structured-review", "--title is only valid for native-review --commit"
            )
        return reviewer.structured_review(
            base=args.base,
            commit=args.commit,
            uncommitted=args.uncommitted,
            instructions=args.instructions,
        )

    target_error = require_target(args)
    if target_error:
        return target_error
    assert args.target is not None
    if args.review_type == "security":
        return reviewer.security_review(args.target)
    if args.review_type == "performance":
        return reviewer.performance_review(args.target)
    if args.review_type == "architecture":
        return reviewer.architecture_review(args.target, " ".join(args.extra))
    if args.review_type == "quality":
        return reviewer.code_quality_review(args.target)
    if args.review_type == "diff":
        base = args.extra[0] if args.extra else "main"
        instructions = " ".join(args.extra[1:]) if len(args.extra) > 1 else ""
        return reviewer.diff_review(base, args.target, instructions)
    if args.review_type == "focused":
        if not args.extra:
            return _error("focused", "focused review requires at least one focus area")
        return reviewer.focused_review(args.target, args.extra)
    if args.review_type == "custom":
        return reviewer.custom_review(" ".join([args.target, *args.extra]))
    return _error(args.review_type, f"Unknown review type: {args.review_type}")


def _public_result_envelope(
    result: Mapping[str, object], *, include_events: bool
) -> Dict[str, object]:
    keys = (
        "schema_version",
        "success",
        "mode",
        "binary",
        "version",
        "install_method",
        "update",
        "scope",
        "model",
        "effort",
        "usage",
        "timeout",
        "idle_timeout",
        "hard_timeout",
        "timed_out",
        "timeout_reason",
        "exit_code",
        "service_tier",
        "warnings",
        "sanitized_command",
        "final_result",
        "partial_progress",
        "error",
        "structured_result",
        "diagnostics",
        "execution_status",
        "review_verdict",
        "gate_status",
        "duration_ms",
        "silence_duration_ms",
        "terminal_event",
        "last_event",
        "event_counts",
        "raw_output_bytes",
        "scope_fingerprint",
        "packet_sha256",
        "prompt_sha256",
        "policy_violation",
    )
    envelope = {key: result[key] for key in keys if key in result}
    if include_events and "events" in result:
        envelope["events"] = result["events"]
    return envelope


def _write_result(
    path_value: str, result: Mapping[str, object], *, include_events: bool = False
) -> Optional[str]:
    path = Path(path_value).expanduser()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(path, flags, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = -1
                envelope = _public_result_envelope(
                    result, include_events=include_events
                )
                handle.write(json.dumps(envelope, ensure_ascii=False, indent=2) + "\n")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except (OSError, TypeError, ValueError) as exc:
        return f"Could not write --result-json {path}: {exc}"
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    if argv is None:
        args = parser.parse_intermixed_args()
    else:
        args = parser.parse_intermixed_args(list(argv))
    result = run_from_args(args)
    if args.result_json:
        write_error = _write_result(
            args.result_json, result, include_events=args.include_events
        )
        if write_error:
            print(f"Error: {write_error}", file=sys.stderr)
            return 1
    if result.get("success"):
        final = (
            result.get("final_result")
            or result.get("summary")
            or result.get("output")
            or ""
        )
        print(final)
        if not args.enforce_gate:
            return 0
        return gate_exit_code(result.get("gate_status"))
    if result.get("mode") == "doctor" and result.get("final_result"):
        print(result["final_result"], file=sys.stderr)
    print(f"Error: {result.get('error') or 'Codex review failed'}", file=sys.stderr)
    return 1
