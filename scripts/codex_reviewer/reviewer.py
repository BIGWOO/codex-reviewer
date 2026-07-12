"""High-level review modes, validation, and explicit doctor diagnostics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

from .catalog import (
    DEFAULT_PRESET,
    MIN_CODEX_VERSION,
    CodexBinary,
    ModelCatalog,
    ModelSelection,
    PresetResolutionError,
    format_version,
    resolve_model_selection,
)
from .commands import CommandBuilder, CommandSpec
from .result import ReviewResult
from .runner import CodexProcessRunner
from .scope import (
    DiffMetrics,
    GitInspector,
    ReviewScope,
    ScopeError,
    developer_git_environment,
    large_diff_error,
)
from .updates import prepare_codex_binary


DEFAULT_MODEL = "gpt-5.6-sol"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_TIMEOUT = 300
DEFAULT_MAX_CHANGED_FILES = 40
DEFAULT_MAX_DIFF_LINES = 3000
UNCOMMITTED_FILE_WARNING_THRESHOLD = 10
VERIFIED_CODEX_VERSION = MIN_CODEX_VERSION
BUNDLED_SCHEMA = (
    Path(__file__).resolve().parents[2] / "references" / "review_output_schema.json"
)


def _version_failure(binary: CodexBinary) -> str:
    path = binary.path or "<not found>"
    actual = binary.version_text or binary.error or "unknown"
    minimum = format_version(MIN_CODEX_VERSION)
    return (
        f"Codex CLI {minimum}+ is required. Selected path: {path}; reported version: {actual}. "
        "Run the selected binary's `codex update`, or rerun without an explicit "
        "--codex-bin so Codex Reviewer can repair the detected installation source."
    )


def load_catalog(binary: CodexBinary) -> Tuple[ModelCatalog, List[str]]:
    """Try refreshed catalog first, then the selected binary's bundled catalog."""
    catalog = ModelCatalog.load(binary)
    return catalog, list(catalog.warnings)


def _structured_error(payload: object) -> Optional[str]:
    if not isinstance(payload, Mapping):
        return "Structured review final result is not a JSON object"
    required = {
        "findings",
        "overall_correctness",
        "overall_explanation",
        "overall_confidence_score",
    }
    missing = sorted(required - set(payload))
    if missing:
        return f"Structured review final result is missing required fields: {', '.join(missing)}"
    extras = sorted(set(payload) - required)
    if extras:
        return f"Structured review final result has unsupported fields: {', '.join(extras)}"
    findings = payload.get("findings")
    if not isinstance(findings, list):
        return "Structured review findings must be an array"
    if payload.get("overall_correctness") not in {
        "patch is correct",
        "patch is incorrect",
    }:
        return "Structured review overall_correctness is invalid"
    if not isinstance(payload.get("overall_explanation"), str):
        return "Structured review overall_explanation must be a string"
    overall_confidence = payload.get("overall_confidence_score")
    if (
        isinstance(overall_confidence, bool)
        or not isinstance(overall_confidence, (int, float))
        or not 0 <= overall_confidence <= 1
    ):
        return "Structured review overall_confidence_score must be between 0 and 1"

    finding_fields = {
        "title",
        "body",
        "confidence_score",
        "priority",
        "code_location",
    }
    for index, finding in enumerate(findings):
        label = f"Structured review finding {index}"
        if not isinstance(finding, Mapping):
            return f"{label} must be an object"
        if set(finding) != finding_fields:
            return f"{label} fields do not match the bundled schema"
        priority = finding.get("priority")
        if (
            isinstance(priority, bool)
            or not isinstance(priority, int)
            or not 0 <= priority <= 3
        ):
            return f"{label} priority must be an integer from 0 to 3"
        title = finding.get("title")
        if (
            not isinstance(title, str)
            or len(title) > 80
            or not title.startswith(f"[P{priority}]")
        ):
            return f"{label} title must be <=80 characters and begin with [P{priority}]"
        if not isinstance(finding.get("body"), str):
            return f"{label} body must be a string"
        confidence = finding.get("confidence_score")
        if (
            isinstance(confidence, bool)
            or not isinstance(confidence, (int, float))
            or not 0 <= confidence <= 1
        ):
            return f"{label} confidence_score must be between 0 and 1"
        location = finding.get("code_location")
        if not isinstance(location, Mapping) or set(location) != {
            "absolute_file_path",
            "line_range",
        }:
            return f"{label} code_location is invalid"
        file_path = location.get("absolute_file_path")
        if (
            not isinstance(file_path, str)
            or not file_path
            or not Path(file_path).is_absolute()
        ):
            return f"{label} absolute_file_path must be absolute"
        line_range = location.get("line_range")
        if not isinstance(line_range, Mapping) or set(line_range) != {"start", "end"}:
            return f"{label} line_range is invalid"
        start = line_range.get("start")
        end = line_range.get("end")
        if (
            isinstance(start, bool)
            or isinstance(end, bool)
            or not isinstance(start, int)
            or not isinstance(end, int)
            or start < 1
            or end < start
        ):
            return f"{label} line_range must be positive and ordered"
    return None


class CodexReviewer:
    """Compatibility facade over the v2 command, catalog, scope, and output modules."""

    def __init__(
        self,
        model: Optional[str] = None,
        json_output: bool = True,
        long_context: bool = False,
        cwd: Optional[str] = None,
        add_dirs: Optional[Sequence[str]] = None,
        schema_file: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        skip_git_repo_check: bool = False,
        reasoning_effort: Optional[str] = None,
        output_file: Optional[str] = None,
        last_message_output: Optional[str] = None,
        ephemeral: bool = True,
        ignore_user_config: bool = False,
        ignore_rules: bool = False,
        search: bool = False,
        images: Optional[Sequence[str]] = None,
        context_window: Optional[int] = None,
        auto_compact_token_limit: Optional[int] = None,
        review_range: Optional[str] = None,
        allow_large_diff: bool = False,
        max_changed_files: int = DEFAULT_MAX_CHANGED_FILES,
        max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
        *,
        codex_bin: Optional[str] = None,
        preset: str = DEFAULT_PRESET,
        instructions: Optional[str] = None,
        profile: Optional[str] = None,
        strict_config: bool = False,
        fast: bool = False,
        dry_run: bool = False,
        update_check: bool = True,
        force_update_check: bool = False,
    ):
        self.explicit_model = model
        self.explicit_effort = reasoning_effort
        self.json_output = json_output
        self.long_context = long_context
        self.cwd = str(Path(cwd).expanduser().resolve()) if cwd else None
        self.add_dirs = [
            str(Path(item).expanduser().resolve()) for item in (add_dirs or [])
        ]
        self.schema_file = (
            str(Path(schema_file).expanduser().resolve()) if schema_file else None
        )
        self.timeout = timeout
        self.skip_git_repo_check = skip_git_repo_check
        self.output_file = output_file
        self.last_message_output = last_message_output
        self.ephemeral = ephemeral
        self.ignore_user_config = ignore_user_config
        self.ignore_rules = ignore_rules
        self.search = search
        self.images = [
            str(Path(item).expanduser().resolve()) for item in (images or [])
        ]
        self.context_window = context_window
        self.auto_compact_token_limit = auto_compact_token_limit
        self.review_range = review_range
        self.allow_large_diff = allow_large_diff
        self.max_changed_files = max_changed_files
        self.max_diff_lines = max_diff_lines
        self.preset = preset
        self.instructions = instructions or ""
        self.profile = profile
        self.strict_config = strict_config
        self.fast = fast
        self.dry_run = dry_run
        self.binary, self.update_outcome = prepare_codex_binary(
            codex_bin,
            check_updates=update_check,
            force_update=force_update_check,
        )
        self.catalog: Optional[ModelCatalog] = None
        self.selection: Optional[ModelSelection] = None
        self._base_warnings: List[str] = list(self.update_outcome.warnings)
        self.git_path: Optional[str] = None

    def _with_runtime(self, result: Dict[str, object]) -> Dict[str, object]:
        result["install_method"] = self.binary.install_method
        result["update"] = self.update_outcome.to_dict()
        return result

    def _prepare(
        self, *, native: bool, effort_override: Optional[str] = None
    ) -> Optional[Dict[str, object]]:
        if not self.binary.path or self.binary.error or not self.binary.supported:
            return self._failure(
                "native" if native else "generic", _version_failure(self.binary)
            )
        if self.binary.version and self.binary.version > VERIFIED_CODEX_VERSION:
            verified = format_version(VERIFIED_CODEX_VERSION)
            warning = (
                f"Codex CLI {self.binary.version_text} is newer than verified {verified}; "
                "using conservative v0.144.1 capability rules"
            )
            if warning not in self._base_warnings:
                self._base_warnings.append(warning)

        if self.catalog is None:
            self.catalog, catalog_warnings = load_catalog(self.binary)
            self._base_warnings.extend(catalog_warnings)
        try:
            self.selection = resolve_model_selection(
                self.preset,
                self.catalog,
                explicit_model=self.explicit_model,
                explicit_effort=effort_override or self.explicit_effort,
            )
        except PresetResolutionError as exc:
            return self._failure("native" if native else "generic", str(exc))
        self._base_warnings.extend(
            warning
            for warning in self.selection.warnings
            if warning not in self._base_warnings
        )

        if native and (self.preset == "ultra" or self.selection.effort == "ultra"):
            return self._failure(
                "native",
                "Native review disables collaboration and does not support Ultra; use a generic review mode",
            )
        if native and self.selection.effort not in {
            "low",
            "medium",
            "high",
            "xhigh",
            "max",
        }:
            return self._failure(
                "native", "Native review reasoning effort cannot exceed max"
            )
        validation_error = self._validate_capabilities(native=native)
        if validation_error:
            return self._failure("native" if native else "generic", validation_error)
        return None

    def _validate_capabilities(self, *, native: bool) -> Optional[str]:
        if self.timeout <= 0:
            return "--timeout must be greater than zero"
        if self.max_changed_files < 0 or self.max_diff_lines < 0:
            return "Large-diff thresholds cannot be negative"
        if self.cwd and not Path(self.cwd).is_dir():
            return f"Working directory does not exist: {self.cwd}"
        for directory in self.add_dirs:
            if not Path(directory).is_dir():
                return f"Additional directory does not exist: {directory}"
        if self.profile and self.ignore_user_config:
            return (
                "--profile cannot be combined with --ignore-user-config or --isolated"
            )
        if native:
            if self.instructions:
                return (
                    "Native review scope cannot be combined with --instructions; "
                    "use structured-review or another generic mode"
                )
            if self.schema_file:
                return (
                    "Native review cannot use --schema: Codex CLI 0.144.1 accepts the flag but "
                    "the review implementation ignores it; use structured-review or generic mode"
                )
            if self.images:
                return (
                    "Native review cannot use --image: Codex CLI 0.144.1 accepts image input but "
                    "the review implementation ignores it; use generic mode"
                )
            if self.search:
                return "Native review disables web search; use generic mode"

        if self.schema_file:
            schema_error = self._validate_schema(Path(self.schema_file))
            if schema_error:
                return schema_error
        for image in self.images:
            if not Path(image).is_file():
                return f"Image does not exist: {image}"

        assert self.catalog is not None and self.selection is not None
        model_info = self.catalog.get(self.selection.model)
        if self.fast:
            if model_info is None:
                return "--fast requires a valid model catalog entry"
            if "fast" not in model_info.service_tiers:
                return f"Model {self.selection.model} does not declare the Fast tier"
        if self.long_context:
            warning = "--long-context is deprecated; use explicit --context-window/--auto-compact-token-limit"
            if warning not in self._base_warnings:
                self._base_warnings.append(warning)
            if self.context_window is None:
                if model_info is None:
                    return "--long-context requires a valid model catalog entry"
                limit = model_info.max_context_window or model_info.context_window
                if limit is None:
                    return f"Model catalog does not declare a context limit for {self.selection.model}"
                self.context_window = limit
        if self.context_window is not None or self.auto_compact_token_limit is not None:
            if model_info is None:
                return "Explicit context settings require a valid model catalog entry"
            limit = model_info.max_context_window or model_info.context_window
            if limit is None:
                return f"Model catalog does not declare a context limit for {self.selection.model}"
            if self.context_window is not None and not 0 < self.context_window <= limit:
                return f"--context-window must be between 1 and catalog limit {limit}"
            effective_window = self.context_window or limit
            if (
                self.auto_compact_token_limit is not None
                and not 0 < self.auto_compact_token_limit <= effective_window
            ):
                return f"--auto-compact-token-limit must be between 1 and {effective_window}"
        return None

    @staticmethod
    def _validate_schema(path: Path) -> Optional[str]:
        if not path.is_file():
            return f"JSON Schema does not exist: {path}"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return f"Invalid JSON Schema {path}: {exc}"
        if not isinstance(payload, Mapping) or payload.get("type") != "object":
            return f"JSON Schema must describe an object: {path}"
        return None

    def _inspector(self) -> GitInspector:
        root = self.cwd or os.getcwd()
        inspector = GitInspector(root)
        self.git_path = inspector.git_path
        return inspector

    def _scope_payload(
        self, scope: ReviewScope
    ) -> Tuple[Optional[Dict[str, object]], List[str], Optional[str]]:
        warnings = list(self._base_warnings)
        metrics: Optional[DiffMetrics] = None
        use_review_range = bool(self.review_range and scope.kind == "custom")
        sizing_scope = (
            ReviewScope("range", self.review_range) if use_review_range else scope
        )
        if self.review_range and not use_review_range:
            warnings.append(
                "--review-range is ignored for native/structured sizing; the actual review scope was measured"
            )
        if sizing_scope.kind != "custom":
            try:
                inspector = self._inspector()
                metrics = inspector.metrics(sizing_scope)
            except ScopeError as exc:
                return None, warnings, str(exc)
            if metrics:
                warnings.extend(metrics.warnings)
                if (
                    scope.kind == "uncommitted"
                    and metrics.changed_files > UNCOMMITTED_FILE_WARNING_THRESHOLD
                ):
                    warnings.append(
                        f"--uncommitted includes {metrics.changed_files} files; consider a narrower task or module scope"
                    )
                if not self.allow_large_diff:
                    error = large_diff_error(
                        metrics,
                        sizing_scope,
                        self.max_changed_files,
                        self.max_diff_lines,
                    )
                    if error:
                        return None, warnings, error
        else:
            # Resolve developer Git for every review so the child shell never falls back
            # to Apple's /usr/bin/git shim inside a read-only sandbox.
            inspector = self._inspector()
            warnings.extend(inspector.environment_warnings)

        payload: Dict[str, object] = dict(scope.to_dict())
        if metrics is not None:
            payload["metrics"] = metrics.to_dict()
        if use_review_range:
            payload["preflight_range"] = self.review_range
        return payload, list(dict.fromkeys(warnings)), None

    def _builder(self, *, schema_file: Optional[str] = None) -> CommandBuilder:
        assert self.selection is not None
        return CommandBuilder(
            binary=self.binary,
            model=self.selection.model,
            effort=self.selection.effort,
            cwd=self.cwd,
            add_dirs=self.add_dirs,
            profile=self.profile,
            strict_config=self.strict_config,
            skip_git_repo_check=self.skip_git_repo_check,
            ephemeral=self.ephemeral,
            ignore_user_config=self.ignore_user_config,
            ignore_rules=self.ignore_rules,
            json_output=self.json_output,
            last_message_output=self.last_message_output,
            search=self.search,
            images=self.images,
            schema_file=schema_file if schema_file is not None else self.schema_file,
            service_tier="fast" if self.fast else None,
            context_window=self.context_window,
            auto_compact_token_limit=self.auto_compact_token_limit,
            git_path=self.git_path,
        )

    def _execute(
        self,
        spec: CommandSpec,
        *,
        mode: str,
        scope_payload: Mapping[str, object],
        warnings: Sequence[str],
        output_file: Optional[str] = None,
        structured: bool = False,
        validate_bundled_shape: bool = False,
        sensitive_values: Sequence[str] = (),
    ) -> Dict[str, object]:
        assert self.selection is not None
        for warning in warnings:
            print(f"[codex-review] warning: {warning}", file=sys.stderr, flush=True)
        if self.dry_run:
            return self._with_runtime(
                ReviewResult(
                    success=True,
                    mode=mode,
                    binary=self.binary.path,
                    version=self.binary.version_string,
                    scope=scope_payload,
                    model=self.selection.model,
                    effort=self.selection.effort,
                    timeout=self.timeout,
                    service_tier="fast" if self.fast else None,
                    warnings=list(warnings),
                    command=spec.display_command,
                    final=f"Dry run: {spec.display_command}",
                ).to_dict()
            )

        runner = CodexProcessRunner(
            self.binary,
            timeout=self.timeout,
            json_output=self.json_output,
            output_file=output_file or self.output_file,
            last_message_output=self.last_message_output,
            env=spec.environment,
        )
        result = runner.run(
            spec.argv,
            stdin_payload=spec.stdin_payload,
            sensitive_values=tuple(sensitive_values) + (spec.stdin_payload or "",),
            mode=mode,
            scope=scope_payload,
            model=self.selection.model,
            effort=self.selection.effort,
            service_tier="fast" if self.fast else None,
            warnings=warnings,
        )
        result = self._with_runtime(result)
        if structured and result.get("success"):
            final = result.get("final_result")
            if not isinstance(final, str) or not final.strip():
                result["success"] = False
                result["error"] = "Structured review completed without a final result"
                return result
            try:
                structured_result = json.loads(final)
            except json.JSONDecodeError as exc:
                result["success"] = False
                result["error"] = f"Structured review returned invalid JSON: {exc}"
                return result
            if validate_bundled_shape:
                validation_error = _structured_error(structured_result)
                if validation_error:
                    result["success"] = False
                    result["error"] = validation_error
            result["structured_result"] = structured_result
        return result

    def _failure(
        self, mode: str, message: str, scope: Optional[ReviewScope] = None
    ) -> Dict[str, object]:
        selection = self.selection
        return self._with_runtime(
            ReviewResult(
                success=False,
                mode=mode,
                binary=self.binary.path,
                version=self.binary.version_string,
                scope=scope.to_dict() if scope else None,
                model=selection.model if selection else self.explicit_model,
                effort=selection.effort if selection else self.explicit_effort,
                timeout=self.timeout,
                service_tier="fast" if self.fast else None,
                warnings=list(self._base_warnings),
                error=message,
            ).to_dict()
        )

    def native_review(
        self,
        prompt: str = "",
        base: Optional[str] = None,
        commit: Optional[str] = None,
        uncommitted: bool = False,
        title: Optional[str] = None,
    ) -> Dict[str, object]:
        choices = (
            int(bool(prompt.strip()))
            + int(bool(base))
            + int(bool(commit))
            + int(uncommitted)
        )
        if choices != 1:
            return self._failure(
                "native",
                "native-review requires exactly one of --base, --commit, --uncommitted, or a custom prompt",
            )
        if title and not commit:
            return self._failure("native", "--title can only be used with --commit")
        scope = (
            ReviewScope("base", base)
            if base
            else ReviewScope("commit", commit)
            if commit
            else ReviewScope("uncommitted")
            if uncommitted
            else ReviewScope("custom")
        )
        preparation_error = self._prepare(native=True)
        if preparation_error:
            return preparation_error
        scope_payload, warnings, scope_error = self._scope_payload(scope)
        if scope_error or scope_payload is None:
            return self._failure(
                "native", scope_error or "Could not resolve native scope", scope
            )
        spec = self._builder().native(scope, prompt=prompt or None, title=title)
        return self._execute(
            spec,
            mode="native",
            scope_payload=scope_payload,
            warnings=warnings,
            sensitive_values=(prompt,),
        )

    def structured_review(
        self,
        *,
        base: Optional[str] = None,
        commit: Optional[str] = None,
        uncommitted: bool = False,
        instructions: Optional[str] = None,
    ) -> Dict[str, object]:
        choices = int(bool(base)) + int(bool(commit)) + int(uncommitted)
        if choices != 1:
            return self._failure(
                "structured",
                "structured-review requires exactly one of --base, --commit, or --uncommitted",
            )
        scope = (
            ReviewScope("base", base)
            if base
            else ReviewScope("commit", commit)
            if commit
            else ReviewScope("uncommitted")
        )
        uses_bundled_schema = self.schema_file is None
        schema = self.schema_file or str(BUNDLED_SCHEMA)
        original_schema = self.schema_file
        self.schema_file = schema
        preparation_error = self._prepare(native=False)
        self.schema_file = original_schema
        if preparation_error:
            return preparation_error
        schema_error = self._validate_schema(Path(schema))
        if schema_error:
            return self._failure("structured", schema_error, scope)
        scope_payload, warnings, scope_error = self._scope_payload(scope)
        if scope_error or scope_payload is None:
            return self._failure(
                "structured", scope_error or "Could not resolve structured scope", scope
            )
        extra = instructions or self.instructions
        prompt = (
            "Act only as a read-only code reviewer. "
            f"{scope.prompt_instruction()} "
            "Inspect the relevant diff, affected callers, tests, schemas, and repository instructions. "
            "Return every discrete actionable defect introduced by this scope, with minimal absolute file and line evidence. "
            "Ignore style-only preferences and unrelated pre-existing issues. "
            "Return only JSON matching the supplied schema."
        )
        if extra:
            prompt += f"\n\nAdditional review instructions:\n{extra}"
        spec = self._builder(schema_file=schema).generic(prompt)
        return self._execute(
            spec,
            mode="structured",
            scope_payload=scope_payload,
            warnings=warnings,
            structured=True,
            validate_bundled_shape=uses_bundled_schema,
            sensitive_values=(extra,),
        )

    def run_review(
        self,
        prompt: str,
        reasoning_effort: Optional[str] = None,
        output_file: Optional[str] = None,
        sensitive_values: Sequence[str] = (),
    ) -> Dict[str, object]:
        preparation_error = self._prepare(
            native=False, effort_override=reasoning_effort
        )
        if preparation_error:
            return preparation_error
        scope = ReviewScope("custom")
        scope_payload, warnings, scope_error = self._scope_payload(scope)
        if scope_error or scope_payload is None:
            return self._failure(
                "generic", scope_error or "Could not resolve review scope", scope
            )
        full_prompt = prompt
        if self.instructions:
            full_prompt += f"\n\nAdditional review instructions:\n{self.instructions}"
        spec = self._builder().generic(full_prompt)
        return self._execute(
            spec,
            mode="generic",
            scope_payload=scope_payload,
            warnings=warnings,
            output_file=output_file,
            structured=bool(self.schema_file),
            validate_bundled_shape=False,
            sensitive_values=(prompt, self.instructions, *sensitive_values),
        )

    def security_review(self, target: str) -> Dict[str, object]:
        return self.run_review(
            f"Perform a defensive security review of {target}. Report only confirmed, actionable issues with "
            "triggering conditions, trust-boundary impact, minimal file/line evidence, and defensive remediation. "
            "Check authorization, validation, tenant isolation, secrets, injection, unsafe deserialization, races, "
            "and abuse controls relevant to the changed code."
        )

    def performance_review(self, target: str) -> Dict[str, object]:
        return self.run_review(
            f"Review {target} for performance regressions. Trace plausible hot paths and report only issues with "
            "demonstrable workload impact, minimal file/line evidence, and a concrete verification or fix."
        )

    def architecture_review(self, target: str, context: str = "") -> Dict[str, object]:
        return self.run_review(
            f"Review the architecture of {target}. {context} Evaluate dependency direction, ownership, failure "
            "handling, compatibility, migration/rollback, observability, and public interfaces. Separate defects "
            "from optional future improvements.",
            sensitive_values=(context,),
        )

    def code_quality_review(self, target: str) -> Dict[str, object]:
        return self.run_review(
            f"Review {target} for actionable code-quality defects affecting correctness, maintainability, error "
            "handling, tests, or documentation. Ignore style-only preferences. Cite minimal file/line evidence."
        )

    def diff_review(
        self, base: str = "main", head: str = "HEAD", instructions: str = ""
    ) -> Dict[str, object]:
        prompt = (
            f"Review only the git diff between {base} and {head}. Focus on regressions, compatibility, security, "
            "performance, error handling, and missing tests. Verify each finding was introduced by this diff."
        )
        if instructions:
            prompt += f"\n\nAdditional review instructions:\n{instructions}"
        return self.run_review(prompt, sensitive_values=(instructions,))

    def focused_review(
        self, target: str, focus_areas: Sequence[str]
    ) -> Dict[str, object]:
        areas = "\n".join(
            f"{index + 1}. {area}" for index, area in enumerate(focus_areas)
        )
        return self.run_review(
            f"Review {target} focusing only on these concerns:\n{areas}\n"
            "Ignore unrelated style issues and cite minimal file/line evidence.",
            sensitive_values=tuple(focus_areas),
        )

    def custom_review(self, prompt: str) -> Dict[str, object]:
        return self.run_review(prompt)


def run_doctor(
    *,
    codex_bin: Optional[str] = None,
    cwd: Optional[str] = None,
    strict_config: bool = False,
    profile: Optional[str] = None,
    update_check: bool = True,
    force_update_check: bool = False,
) -> Dict[str, object]:
    """Run explicit, non-inference health checks with the same selected binary."""
    binary, update_outcome = prepare_codex_binary(
        codex_bin,
        check_updates=update_check,
        force_update=force_update_check,
    )
    root = str(Path(cwd).expanduser().resolve()) if cwd else os.getcwd()
    checks: List[Dict[str, object]] = []
    warnings: List[str] = list(update_outcome.warnings)

    def add(name: str, status: str, detail: object) -> None:
        checks.append({"name": name, "status": status, "detail": detail})

    update_status = "warn" if update_outcome.error else "pass"
    if (
        not update_outcome.enabled
        or update_outcome.skipped_reason == "explicit binary override"
    ):
        update_status = "skip"
    add("update", update_status, update_outcome.to_dict())

    candidates = []
    for path in CodexBinary._path_candidates("codex"):
        inspected = CodexBinary._inspect("codex", path, 10)
        candidates.append(
            {
                "path": inspected.path,
                "version": inspected.version_text,
                "install_method": inspected.install_method,
                "error": inspected.error,
            }
        )
    versions = {item.get("version") for item in candidates if item.get("version")}
    drift_status = "warn" if len(versions) > 1 else "pass"
    add(
        "binary_drift",
        drift_status,
        {"selected": binary.path, "path_candidates": candidates},
    )
    if drift_status == "warn":
        warnings.append(
            "Multiple Codex versions were found on PATH; the selected absolute binary is pinned for this run"
        )

    if not binary.path or binary.error:
        add("binary", "fail", binary.error or "Codex CLI not found")
    elif not binary.supported:
        add("binary", "fail", _version_failure(binary))
    else:
        add(
            "binary",
            "pass",
            {
                "path": binary.path,
                "version": binary.version_text,
                "install_method": binary.install_method,
            },
        )
        if binary.version and binary.version > VERIFIED_CODEX_VERSION:
            warnings.append(
                f"Codex CLI {binary.version_text} is newer than verified {format_version(VERIFIED_CODEX_VERSION)}"
            )

    catalog = ModelCatalog(error="binary unavailable", source="unavailable")
    if binary.path and binary.supported:
        doctor_cmd = [binary.path]
        if profile:
            doctor_cmd.extend(["--profile", profile])
        if strict_config:
            doctor_cmd.append("--strict-config")
        doctor_cmd.extend(["doctor", "--json"])
        try:
            result = subprocess.run(
                doctor_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            detail: object
            try:
                doctor_payload = (
                    json.loads(result.stdout) if result.stdout.strip() else None
                )
            except json.JSONDecodeError:
                doctor_payload = None
            if isinstance(doctor_payload, Mapping) and isinstance(
                doctor_payload.get("checks"), Mapping
            ):
                raw_checks = doctor_payload["checks"]
                relevant_ids = (
                    "auth.credentials",
                    "config.load",
                    "installation",
                    "runtime.provenance",
                )
                relevant = {
                    check_id: {
                        "status": raw_checks[check_id].get("status"),
                        "summary": raw_checks[check_id].get("summary"),
                    }
                    for check_id in relevant_ids
                    if isinstance(raw_checks.get(check_id), Mapping)
                }
                relevant_failed = [
                    check_id
                    for check_id, item in relevant.items()
                    if item.get("status") not in {"ok", "pass"}
                ]
                detail = {
                    "overall_status": doctor_payload.get("overallStatus"),
                    "relevant_checks": relevant,
                }
                add(
                    "auth_config",
                    "fail" if relevant_failed or not relevant else "pass",
                    detail,
                )
                if result.returncode != 0 and not relevant_failed:
                    warnings.append(
                        "codex doctor reported an unrelated non-review failure; auth/config checks are healthy"
                    )
            elif (
                isinstance(doctor_payload, Mapping)
                and doctor_payload.get("status") == "healthy"
                and doctor_payload.get("auth") == "ok"
                and doctor_payload.get("config") == "ok"
            ):
                add("auth_config", "pass", dict(doctor_payload))
            else:
                detail = result.stdout.strip() or result.stderr.strip() or {}
                add("auth_config", "fail", detail or "Unparseable codex doctor output")
        except (OSError, subprocess.SubprocessError) as exc:
            add("auth_config", "fail", str(exc))

        catalog, catalog_warnings = load_catalog(binary)
        warnings.extend(catalog_warnings)
        add(
            "catalog",
            "pass" if catalog.models else "fail",
            {
                "source": catalog.source,
                "models": sorted(catalog.models),
                "error": catalog.error,
            },
        )
        try:
            selection = resolve_model_selection(DEFAULT_PRESET, catalog)
            add(
                "preset_standard",
                "pass",
                {"model": selection.model, "effort": selection.effort},
            )
        except PresetResolutionError as exc:
            add("preset_standard", "fail", str(exc))

    schema_error = CodexReviewer._validate_schema(BUNDLED_SCHEMA)
    add(
        "schema",
        "fail" if schema_error else "pass",
        schema_error or str(BUNDLED_SCHEMA),
    )

    inspector = GitInspector(root)
    repository_error = inspector.validate_repository()
    warnings.extend(inspector.environment_warnings)
    sandbox_root = root
    temporary_repo: Optional[tempfile.TemporaryDirectory[str]] = None
    git_error = repository_error
    git_detail: Dict[str, object] = {
        "path": inspector.git_path,
        "cwd": root,
        "repository_error": repository_error,
    }
    if cwd is None and inspector.git_path:
        version_result = subprocess.run(
            [inspector.git_path, "--version"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            env=developer_git_environment(inspector.git_path),
        )
        git_error = (
            None
            if version_result.returncode == 0
            else version_result.stderr.strip() or "Git version check failed"
        )
        git_detail["version"] = version_result.stdout.strip()
        if repository_error:
            temporary_repo = tempfile.TemporaryDirectory(
                prefix="codex-reviewer-doctor-"
            )
            sandbox_root = temporary_repo.name
            init_result = subprocess.run(
                [inspector.git_path, "-C", sandbox_root, "init", "-q"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
                env=developer_git_environment(inspector.git_path),
            )
            if init_result.returncode != 0:
                git_error = (
                    init_result.stderr.strip() or "Temporary Git repository init failed"
                )
            git_detail["sandbox_fixture"] = sandbox_root
    add(
        "git",
        "fail" if git_error else "pass",
        {**git_detail, "error": git_error},
    )

    if (
        sys.platform == "darwin"
        and binary.path
        and binary.supported
        and inspector.git_path
        and not git_error
    ):
        sandbox_results = []
        env = developer_git_environment(inspector.git_path)
        for git_args in (["status", "--short"], ["diff", "--stat"]):
            command = [
                binary.path,
                "sandbox",
                "-P",
                ":read-only",
                "-C",
                sandbox_root,
                inspector.git_path,
                *git_args,
            ]
            try:
                result = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                    env=env,
                )
                sandbox_results.append(
                    {
                        "command": "git " + " ".join(git_args),
                        "exit_code": result.returncode,
                        "stderr": result.stderr.strip(),
                    }
                )
            except (OSError, subprocess.SubprocessError) as exc:
                sandbox_results.append(
                    {
                        "command": "git " + " ".join(git_args),
                        "exit_code": None,
                        "stderr": str(exc),
                    }
                )
        sandbox_ok = all(item["exit_code"] == 0 for item in sandbox_results)
        add("macos_read_only_git", "pass" if sandbox_ok else "fail", sandbox_results)
    elif sys.platform == "darwin":
        add(
            "macos_read_only_git", "fail", "Binary or developer Git prerequisite failed"
        )
    else:
        add("macos_read_only_git", "skip", "macOS-only check")

    if temporary_repo is not None:
        temporary_repo.cleanup()

    failed = [check for check in checks if check["status"] == "fail"]
    rows = []
    for check in checks:
        rendered = json.dumps(check["detail"], ensure_ascii=False, sort_keys=True)
        if len(rendered) > 600:
            rendered = rendered[:597] + "..."
        rows.append(f"[{str(check['status']).upper()}] {check['name']}: {rendered}")
    final = "Codex Reviewer doctor\n" + "\n".join(rows)
    result = ReviewResult(
        success=not failed,
        mode="doctor",
        binary=binary.path,
        version=binary.version_string,
        scope={"kind": "doctor", "cwd": root},
        warnings=list(dict.fromkeys(warnings)),
        final=final,
        error=f"Doctor found {len(failed)} failing checks" if failed else None,
    ).to_dict()
    result["install_method"] = binary.install_method
    result["update"] = update_outcome.to_dict()
    result["diagnostics"] = checks
    return result
