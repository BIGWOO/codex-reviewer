#!/usr/bin/env python3
"""
Codex Review Helper Script

Runs read-only GPT-5.5 code reviews through Codex CLI with repeatable defaults.
"""

import argparse
import json
import queue
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

DEFAULT_MODEL = "gpt-5.5"
DEFAULT_REASONING_EFFORT = "high"
DEFAULT_TIMEOUT = 300
DEFAULT_CONTEXT_WINDOW = 272000
AUTO_COMPACT_RATIO = 0.8
HEARTBEAT_SECONDS = 30
UNCOMMITTED_FILE_WARNING_THRESHOLD = 10
DEFAULT_MAX_CHANGED_FILES = 40
DEFAULT_MAX_DIFF_LINES = 3000


class CodexReviewer:
    """Helper class for running read-only Codex code reviews."""

    def __init__(
        self,
        model: str = DEFAULT_MODEL,
        json_output: bool = True,
        long_context: bool = False,
        cwd: Optional[str] = None,
        add_dirs: Optional[List[str]] = None,
        schema_file: Optional[str] = None,
        timeout: int = DEFAULT_TIMEOUT,
        skip_git_repo_check: bool = False,
        reasoning_effort: str = DEFAULT_REASONING_EFFORT,
        output_file: Optional[str] = None,
        last_message_output: Optional[str] = None,
        ephemeral: bool = True,
        ignore_user_config: bool = False,
        ignore_rules: bool = False,
        search: bool = False,
        images: Optional[List[str]] = None,
        context_window: Optional[int] = None,
        auto_compact_token_limit: Optional[int] = None,
        review_range: Optional[str] = None,
        allow_large_diff: bool = False,
        max_changed_files: int = DEFAULT_MAX_CHANGED_FILES,
        max_diff_lines: int = DEFAULT_MAX_DIFF_LINES,
    ):
        self.model = model
        self.json_output = json_output
        self.long_context = long_context
        self.cwd = cwd
        self.add_dirs = add_dirs or []
        self.schema_file = schema_file
        self.timeout = timeout
        self.skip_git_repo_check = skip_git_repo_check
        self.reasoning_effort = reasoning_effort
        self.output_file = output_file
        self.last_message_output = last_message_output
        self.ephemeral = ephemeral
        self.ignore_user_config = ignore_user_config
        self.ignore_rules = ignore_rules
        self.search = search
        self.images = images or []
        self.context_window = context_window
        self.auto_compact_token_limit = auto_compact_token_limit
        self.review_range = review_range
        self.allow_large_diff = allow_large_diff
        self.max_changed_files = max_changed_files
        self.max_diff_lines = max_diff_lines

    def run_review(
        self,
        prompt: str,
        reasoning_effort: Optional[str] = None,
        output_file: Optional[str] = None,
    ) -> Dict:
        """Run a read-only generic Codex review prompt."""
        cmd = self._base_codex_cmd(reasoning_effort)
        cmd.append("exec")
        cmd.extend(self._exec_common_options())

        if self.long_context:
            cmd.extend(self._long_context_options())

        if self.schema_file:
            cmd.extend(["--output-schema", self.schema_file])

        for image in self.images:
            cmd.extend(["--image", image])

        if self.json_output:
            cmd.append("--json")

        if self.last_message_output:
            cmd.extend(["--output-last-message", self.last_message_output])

        cmd.append(prompt)
        return self._run_command(cmd, output_file or self.output_file)

    def native_review(
        self,
        prompt: str = "",
        base: Optional[str] = None,
        commit: Optional[str] = None,
        uncommitted: bool = False,
        title: Optional[str] = None,
    ) -> Dict:
        """Run Codex CLI's built-in code review command."""
        if uncommitted and prompt.strip():
            return {
                "success": False,
                "output": None,
                "error": (
                    "native-review --uncommitted cannot be combined with a prompt in the current Codex CLI; "
                    "use custom/focused review and ask Codex to inspect git diff plus untracked files."
                ),
            }
        if self.schema_file:
            return {
                "success": False,
                "output": None,
                "error": "native-review does not support --schema; use diff/custom review for output-schema.",
            }
        if self.images:
            return {
                "success": False,
                "output": None,
                "error": "native-review does not support --image; use custom review for screenshot/UI reviews.",
            }

        cmd = self._base_codex_cmd()
        cmd.extend(["exec", "review"])

        if base:
            cmd.extend(["--base", base])
        if commit:
            cmd.extend(["--commit", commit])
        if uncommitted:
            cmd.append("--uncommitted")
        if title:
            cmd.extend(["--title", title])

        cmd.extend(self._exec_review_options())

        if self.json_output:
            cmd.append("--json")

        if self.last_message_output:
            cmd.extend(["--output-last-message", self.last_message_output])

        if prompt:
            cmd.append(prompt)

        return self._run_command(cmd, self.output_file)

    def _base_codex_cmd(self, reasoning_effort: Optional[str] = None) -> List[str]:
        cmd = ["codex"]

        if self.search:
            cmd.append("--search")

        cmd.extend(
            [
                "--model",
                self.model,
                "--sandbox",
                "read-only",
                "--config",
                f"reasoning_effort={reasoning_effort or self.reasoning_effort}",
                "--config",
                "approval_policy=never",
            ]
        )

        if self.cwd:
            cmd.extend(["--cd", self.cwd])

        for directory in self.add_dirs:
            cmd.extend(["--add-dir", directory])

        return cmd

    def _exec_common_options(self) -> List[str]:
        options = []
        if self.skip_git_repo_check:
            options.append("--skip-git-repo-check")
        if self.ephemeral:
            options.append("--ephemeral")
        if self.ignore_user_config:
            options.append("--ignore-user-config")
        if self.ignore_rules:
            options.append("--ignore-rules")
        return options

    def _exec_review_options(self) -> List[str]:
        options = []
        if self.skip_git_repo_check:
            options.append("--skip-git-repo-check")
        if self.ephemeral:
            options.append("--ephemeral")
        if self.ignore_user_config:
            options.append("--ignore-user-config")
        if self.ignore_rules:
            options.append("--ignore-rules")
        return options

    def _long_context_options(self) -> List[str]:
        model_limit = self._detect_model_context_window()
        requested_window = self.context_window or model_limit
        context_window = min(requested_window, model_limit)
        auto_compact = self.auto_compact_token_limit or int(context_window * AUTO_COMPACT_RATIO)
        auto_compact = min(auto_compact, max(context_window - 1000, 1))

        return [
            "--config",
            f"model_context_window={context_window}",
            "--config",
            f"model_auto_compact_token_limit={auto_compact}",
        ]

    def _detect_model_context_window(self) -> int:
        try:
            result = subprocess.run(
                ["codex", "debug", "models"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            if result.returncode != 0:
                return DEFAULT_CONTEXT_WINDOW
            payload = json.loads(result.stdout)
            for model_info in payload.get("models", []):
                if model_info.get("slug") == self.model:
                    return int(
                        model_info.get("max_context_window")
                        or model_info.get("context_window")
                        or DEFAULT_CONTEXT_WINDOW
                    )
        except Exception:
            return DEFAULT_CONTEXT_WINDOW
        return DEFAULT_CONTEXT_WINDOW

    def _run_command(self, cmd: List[str], output_file: Optional[str]) -> Dict:
        preflight_error, preflight_warnings = self._preflight(cmd)
        if preflight_error:
            return {
                "success": False,
                "output": None,
                "error": preflight_error,
                "command": self._redact_prompt(cmd),
            }
        for warning in preflight_warnings:
            print(f"[codex-review] warning: {warning}", file=sys.stderr, flush=True)

        output_path = Path(output_file) if output_file else None
        output_handle = None
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        events: List[Dict] = []
        event_queue: "queue.Queue[Tuple[str, str]]" = queue.Queue()
        started_at = time.monotonic()
        last_heartbeat = started_at

        def read_stream(stream, stream_name: str) -> None:
            try:
                for line in iter(stream.readline, ""):
                    event_queue.put((stream_name, line))
            finally:
                stream.close()

        try:
            if output_path:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_handle = output_path.open("w", encoding="utf-8")

            print(f"[codex-review] starting: {self._redact_prompt(cmd)}", file=sys.stderr, flush=True)
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
            if process.stdout is None or process.stderr is None:
                raise RuntimeError("failed to capture Codex stdout/stderr")

            stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, "stdout"), daemon=True)
            stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, "stderr"), daemon=True)
            stdout_thread.start()
            stderr_thread.start()

            timed_out = False
            while True:
                try:
                    stream_name, line = event_queue.get(timeout=0.2)
                    if stream_name == "stdout":
                        stdout_lines.append(line)
                        if output_handle:
                            output_handle.write(line)
                            output_handle.flush()
                        if self.json_output:
                            event = self._parse_jsonl_line(line)
                            if event:
                                events.append(event)
                                progress = self._format_progress_event(event)
                                if progress:
                                    print(f"[codex-review] {progress}", file=sys.stderr, flush=True)
                    else:
                        stderr_lines.append(line)
                        print(line, end="", file=sys.stderr, flush=True)
                except queue.Empty:
                    pass

                now = time.monotonic()
                if process.poll() is not None and event_queue.empty():
                    break
                if now - last_heartbeat >= HEARTBEAT_SECONDS:
                    elapsed = int(now - started_at)
                    print(f"[codex-review] still running ({elapsed}s elapsed)...", file=sys.stderr, flush=True)
                    last_heartbeat = now
                if now - started_at > self.timeout:
                    timed_out = True
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                    break

            stdout_thread.join(timeout=1)
            stderr_thread.join(timeout=1)
            while not event_queue.empty():
                stream_name, line = event_queue.get_nowait()
                if stream_name == "stdout":
                    stdout_lines.append(line)
                    if output_handle:
                        output_handle.write(line)
                    if self.json_output:
                        event = self._parse_jsonl_line(line)
                        if event:
                            events.append(event)
                else:
                    stderr_lines.append(line)

            if output_handle:
                output_handle.flush()

            output = "".join(stdout_lines)
            stderr = "".join(stderr_lines)
            response = {
                "success": (process.returncode == 0) and not timed_out,
                "output": output,
                "error": None,
                "command": self._redact_prompt(cmd),
                "preflight_warnings": preflight_warnings,
            }

            if self.json_output:
                if not events:
                    events = self._parse_jsonl(output)
                response["events"] = events
                response["summary"] = self._extract_summary(events)

            if timed_out:
                target = f"; partial output written to {output_path}" if output_path else ""
                response["error"] = (
                    f"Codex review timed out after {self.timeout} seconds{target}. "
                    "Retry with --quick, --review-range <base>..<head>, a focused/custom prompt naming files, "
                    "or split the review by task/commit range."
                )
                if self.last_message_output and response.get("summary"):
                    Path(self.last_message_output).write_text(response["summary"], encoding="utf-8")
            elif process.returncode != 0:
                response["error"] = stderr or f"Codex exited with status {process.returncode}"

            print(f"[codex-review] finished in {int(time.monotonic() - started_at)}s", file=sys.stderr, flush=True)
            return response

        except FileNotFoundError:
            return {
                "success": False,
                "output": None,
                "error": "Codex CLI not found. Please install it first or add it to PATH.",
            }
        except Exception as exc:
            return {
                "success": False,
                "output": None,
                "error": f"Unexpected error: {exc}",
            }
        finally:
            if output_handle:
                output_handle.close()

    def _preflight(self, cmd: List[str]) -> Tuple[Optional[str], List[str]]:
        warnings = []
        executable = cmd[0] if cmd else "codex"
        if not shutil.which(executable):
            return "Codex CLI not found. Please install it first or add it to PATH.", warnings
        if self.cwd and not Path(self.cwd).exists():
            return f"Working directory does not exist: {self.cwd}", warnings
        if self.cwd and "--uncommitted" in cmd:
            changed = self._uncommitted_paths()
            if len(changed) > UNCOMMITTED_FILE_WARNING_THRESHOLD:
                warnings.append(
                    f"--uncommitted includes {len(changed)} files; consider focused/custom review with an explicit file scope."
                )
        if self.cwd and not self.allow_large_diff:
            scope = self._review_diff_scope(cmd)
            if scope:
                metrics = self._diff_metrics(scope)
                if metrics:
                    changed_files, changed_lines = metrics
                    if changed_files > self.max_changed_files or changed_lines > self.max_diff_lines:
                        return self._large_diff_error(scope, changed_files, changed_lines), warnings
        return None, warnings

    def _review_diff_scope(self, cmd: List[str]) -> Optional[str]:
        if self.review_range:
            return self.review_range
        if "--uncommitted" in cmd:
            return "--uncommitted"
        if "review" in cmd and "--base" in cmd:
            base = self._option_value(cmd, "--base")
            commit = self._option_value(cmd, "--commit") or "HEAD"
            return f"{base}..{commit}" if base else None
        return None

    @staticmethod
    def _option_value(cmd: List[str], option: str) -> Optional[str]:
        try:
            index = cmd.index(option)
        except ValueError:
            return None
        if index + 1 >= len(cmd):
            return None
        return cmd[index + 1]

    def _diff_metrics(self, scope: str) -> Optional[Tuple[int, int]]:
        if scope == "--uncommitted":
            paths = self._uncommitted_paths()
            line_count = self._git_diff_line_count([])
            return len(paths), line_count

        paths = self._git_lines(["diff", "--name-only", scope])
        if paths is None:
            return None
        line_count = self._git_diff_line_count([scope])
        return len(paths), line_count

    def _git_diff_line_count(self, diff_args: List[str]) -> int:
        result = self._git_output(["diff", "--numstat"] + diff_args)
        if result is None:
            return 0
        total = 0
        for line in result.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            try:
                total += int(parts[0]) + int(parts[1])
            except ValueError:
                continue
        return total

    def _git_lines(self, args: List[str]) -> Optional[List[str]]:
        output = self._git_output(args)
        if output is None:
            return None
        return [line.strip() for line in output.splitlines() if line.strip()]

    def _git_output(self, args: List[str]) -> Optional[str]:
        if not self.cwd:
            return None
        try:
            result = subprocess.run(
                ["git", "-C", self.cwd] + args,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            if result.returncode != 0:
                return None
            return result.stdout
        except Exception:
            return None

    def _large_diff_error(self, scope: str, changed_files: int, changed_lines: int) -> str:
        return (
            f"Review scope {scope} is large ({changed_files} files, {changed_lines} changed lines). "
            f"Default guard is {self.max_changed_files} files / {self.max_diff_lines} lines. "
            "Split by task/commit range, use --quick for a blocking-issue pass, or pass --allow-large-diff "
            "when you intentionally want a broad long review."
        )

    def _uncommitted_paths(self) -> List[str]:
        if not self.cwd:
            return []
        try:
            tracked = subprocess.run(
                ["git", "-C", self.cwd, "diff", "--name-only"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            untracked = subprocess.run(
                ["git", "-C", self.cwd, "ls-files", "--others", "--exclude-standard"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            paths = set()
            if tracked.returncode == 0:
                paths.update(line.strip() for line in tracked.stdout.splitlines() if line.strip())
            if untracked.returncode == 0:
                paths.update(line.strip() for line in untracked.stdout.splitlines() if line.strip())
            return sorted(paths)
        except Exception:
            return []

    def _redact_prompt(self, cmd: List[str]) -> str:
        if not cmd:
            return ""
        if cmd[-1] and "\n" in cmd[-1]:
            return " ".join(cmd[:-1]) + " <prompt>"
        return " ".join(cmd)

    def _parse_jsonl(self, jsonl_text: str) -> List[Dict]:
        """Parse JSONL output into event objects."""
        events = []
        for line in jsonl_text.strip().splitlines():
            event = self._parse_jsonl_line(line)
            if event:
                events.append(event)
        return events

    def _parse_jsonl_line(self, line: str) -> Optional[Dict]:
        if not line.strip():
            return None
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            return None

    def _format_progress_event(self, event: Dict) -> Optional[str]:
        event_type = event.get("type")
        if event_type == "thread.started":
            thread_id = event.get("thread_id")
            return f"thread started {thread_id}" if thread_id else "thread started"
        if event_type == "turn.started":
            return "turn started"
        if event_type == "turn.completed":
            return "turn completed"
        if event_type != "item.completed":
            return None
        item = event.get("item", {})
        item_type = item.get("type")
        if item_type == "command_execution":
            command = str(item.get("command") or "").strip()
            exit_code = item.get("exit_code")
            label = command if len(command) <= 90 else command[:87] + "..."
            return f"command completed exit={exit_code}: {label}"
        if item_type == "agent_message":
            return "final message received"
        if item_type == "reasoning":
            text = str(item.get("text") or "").strip().splitlines()
            if text:
                first = text[0]
                return first if len(first) <= 120 else first[:117] + "..."
            return "reasoning step completed"
        return f"{item_type or 'item'} completed"

    def _extract_summary(self, events: List[Dict]) -> Optional[str]:
        """Extract the final agent message from Codex JSONL events."""
        for event in reversed(events):
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message":
                    return item.get("text") or self._extract_nested_message(item)
            if event.get("type") == "agent_message":
                return self._extract_nested_message(event)
        return None

    def _extract_nested_message(self, payload: Dict) -> Optional[str]:
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, list) and content:
                first = content[0]
                if isinstance(first, dict):
                    return first.get("text")
        text = payload.get("text")
        return text if isinstance(text, str) else None

    def security_review(self, target: str) -> Dict:
        """Run a defensive security review."""
        prompt = f"""Perform a defensive security audit of {target}. Check for:

1. Authentication and authorization vulnerabilities
2. Input validation issues, including injection risks
3. Cryptographic weaknesses
4. Sensitive data exposure
5. Rate limiting and DoS resilience gaps
6. Session management issues
7. CSRF protection
8. Security misconfiguration

Provide:
- Severity rating (Critical/High/Medium/Low) for each issue
- Specific file paths and line numbers
- Defensive impact and affected trust boundary
- Concrete remediation recommendations

Do not provide payloads or step-by-step exploitation instructions."""

        return self.run_review(prompt)

    def performance_review(self, target: str) -> Dict:
        """Run a performance-focused review."""
        prompt = f"""Analyze {target} for performance issues:

1. Algorithmic complexity problems
2. Database query inefficiencies, including N+1 queries and missing indexes
3. Memory leaks or excessive allocations
4. Blocking operations that should be async
5. Resource cleanup issues
6. Caching opportunities
7. Unnecessary computations in loops

For each issue, explain impact, provide file paths and line numbers, and suggest optimized alternatives."""

        return self.run_review(prompt)

    def architecture_review(self, target: str, context: str = "") -> Dict:
        """Review architectural decisions and patterns."""
        prompt = f"""Review the architecture of {target}. {context}

Evaluate:
1. Separation of concerns and modularity
2. Coupling and cohesion
3. Design pattern usage and appropriateness
4. SOLID principles adherence
5. Scalability considerations
6. Maintainability and extensibility
7. Error handling strategy
8. Dependency management

Provide architectural strengths, design issues, refactoring suggestions, and alternative approaches."""

        return self.run_review(prompt)

    def code_quality_review(self, target: str) -> Dict:
        """Review code quality and maintainability."""
        prompt = f"""Review {target} for code quality:

1. Complexity hotspots
2. Code duplication and DRY violations
3. Naming clarity
4. Comment quality and necessity
5. Error handling completeness
6. Function and method responsibility boundaries
7. Test coverage and testability
8. Documentation completeness

Rate code quality 1-10 and provide specific improvements with file paths and line numbers."""

        return self.run_review(prompt)

    def diff_review(self, base: str = "main", head: str = "HEAD", instructions: str = "") -> Dict:
        """Review changes between two git refs."""
        prompt = f"""Review the git diff between {base} and {head}:

1. Identify breaking changes and backward compatibility issues
2. Check for regression risks
3. Evaluate test coverage for new or modified behavior
4. Verify documentation updates
5. Assess security implications of changes defensively
6. Check for performance regressions
7. Review error handling in new code

Organize feedback by file path, severity, category, risk, and recommendation."""

        if instructions:
            prompt += f"\n\nAdditional review instructions:\n{instructions}"

        return self.run_review(prompt)

    def focused_review(self, target: str, focus_areas: List[str]) -> Dict:
        """Run a review focused on specific concerns."""
        areas = "\n".join(f"{index + 1}. {area}" for index, area in enumerate(focus_areas))
        prompt = f"""Review {target} focusing ONLY on these concerns:

{areas}

Ignore unrelated style issues. Provide detailed findings with file paths and line numbers."""

        return self.run_review(prompt)

    def custom_review(self, prompt: str) -> Dict:
        """Run a caller-provided prompt."""
        return self.run_review(prompt)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run a read-only GPT-5.5 Codex code review.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "review_type",
        choices=[
            "native-review",
            "security",
            "performance",
            "architecture",
            "quality",
            "diff",
            "focused",
            "custom",
        ],
        help="Review pattern to run.",
    )
    parser.add_argument("target", nargs="?", help="Target path/ref or custom prompt.")
    parser.add_argument("extra", nargs="*", help="Additional args: diff base, architecture context, or focus areas.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Codex model. Keep gpt-5.5 unless explicitly requested.")
    parser.add_argument(
        "--reasoning-effort",
        default=argparse.SUPPRESS,
        choices=["low", "medium", "high", "xhigh"],
        help=f"Reasoning effort passed to Codex. Defaults to {DEFAULT_REASONING_EFFORT}, or medium with --quick.",
    )
    parser.add_argument("--cd", dest="cwd", help="Repository root passed to codex --cd.")
    parser.add_argument("--add-dir", dest="add_dirs", action="append", default=[], help="Additional readable directory.")
    parser.add_argument("--output", help="Write raw Codex stdout, usually JSONL, to this file.")
    parser.add_argument("--last-message-output", help="Write Codex final message to this file.")
    parser.add_argument("--schema", dest="schema_file", help="JSON Schema file for Codex final response.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="Timeout in seconds.")
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Fast pass: medium reasoning and 240s timeout unless explicitly overridden.",
    )
    parser.add_argument("--long-context", action="store_true", help="Enable model-catalog-clamped long context settings.")
    parser.add_argument("--context-window", type=int, help="Requested context window before clamping to model catalog.")
    parser.add_argument("--auto-compact-token-limit", type=int, help="Requested auto compact token limit.")
    parser.add_argument(
        "--review-range",
        help="Git diff range used for preflight sizing, e.g. main..HEAD or <task-start>..<task-end>.",
    )
    parser.add_argument(
        "--allow-large-diff",
        action="store_true",
        help="Bypass the large diff guard. Prefer splitting by task/commit range first.",
    )
    parser.add_argument(
        "--max-changed-files",
        type=int,
        default=DEFAULT_MAX_CHANGED_FILES,
        help="Large diff guard file threshold.",
    )
    parser.add_argument(
        "--max-diff-lines",
        type=int,
        default=DEFAULT_MAX_DIFF_LINES,
        help="Large diff guard changed-line threshold.",
    )
    parser.add_argument("--text", action="store_true", help="Disable JSONL output and print plain Codex output.")
    parser.add_argument("--skip-git-repo-check", action="store_true", help="Allow reviews outside a git repository.")
    parser.add_argument("--persist-session", action="store_true", help="Do not pass --ephemeral; useful before resume.")
    parser.add_argument("--ignore-user-config", action="store_true", help="Pass --ignore-user-config to codex exec.")
    parser.add_argument("--ignore-rules", action="store_true", help="Pass --ignore-rules to codex exec.")
    parser.add_argument("--isolated", action="store_true", help="Shortcut for --ignore-user-config --ignore-rules.")
    parser.add_argument("--search", action="store_true", help="Enable Codex native web search for this review.")
    parser.add_argument("--image", dest="images", action="append", default=[], help="Attach image to generic reviews.")
    parser.add_argument("--base", help="Native review base branch.")
    parser.add_argument("--commit", help="Native review commit SHA.")
    parser.add_argument("--uncommitted", action="store_true", help="Native review staged, unstaged, and untracked changes.")
    parser.add_argument("--title", help="Native review title.")
    return parser


def require_target(args: argparse.Namespace) -> Optional[Dict]:
    if args.target:
        return None
    return {
        "success": False,
        "output": None,
        "error": f"{args.review_type} review requires a target or prompt",
    }


def run_from_args(args: argparse.Namespace) -> Dict:
    ignore_user_config = args.ignore_user_config or args.isolated
    ignore_rules = args.ignore_rules or args.isolated
    requested_reasoning_effort = getattr(args, "reasoning_effort", None)
    reasoning_effort = requested_reasoning_effort or DEFAULT_REASONING_EFFORT
    timeout = args.timeout
    if args.quick:
        if requested_reasoning_effort is None:
            reasoning_effort = "medium"
        if timeout == DEFAULT_TIMEOUT:
            timeout = 240
    review_range = args.review_range
    if not review_range and args.review_type == "diff" and args.target:
        base = args.extra[0] if args.extra else "main"
        review_range = f"{base}..{args.target}"

    reviewer = CodexReviewer(
        model=args.model,
        json_output=not args.text,
        long_context=args.long_context,
        cwd=args.cwd,
        add_dirs=args.add_dirs,
        schema_file=args.schema_file,
        timeout=timeout,
        skip_git_repo_check=args.skip_git_repo_check,
        reasoning_effort=reasoning_effort,
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
        allow_large_diff=args.allow_large_diff,
        max_changed_files=args.max_changed_files,
        max_diff_lines=args.max_diff_lines,
    )

    if args.review_type == "native-review":
        prompt = " ".join(([args.target] if args.target else []) + args.extra)
        return reviewer.native_review(
            prompt=prompt,
            base=args.base,
            commit=args.commit,
            uncommitted=args.uncommitted,
            title=args.title,
        )

    target_error = require_target(args)
    if target_error:
        return target_error

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
            return {
                "success": False,
                "output": None,
                "error": "focused review requires at least one focus area in extra args",
            }
        return reviewer.focused_review(args.target, args.extra)
    if args.review_type == "custom":
        prompt = " ".join([args.target] + args.extra)
        return reviewer.custom_review(prompt)

    return {"success": False, "output": None, "error": f"Unknown review type: {args.review_type}"}


def main() -> int:
    parser = build_parser()
    args = parser.parse_intermixed_args()
    result = run_from_args(args)

    if result["success"]:
        if result.get("summary"):
            print(result["summary"])
        else:
            print(result.get("output") or "")
        return 0

    print(f"Error: {result['error']}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
