"""Deterministic Codex argv construction with prompt-safe stdin payloads."""

from __future__ import annotations

import json
import shlex
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional, Sequence

from .catalog import CodexBinary
from .scope import ReviewScope, developer_git_environment


@dataclass(frozen=True)
class CommandSpec:
    """Everything needed to execute and safely display one Codex command."""

    argv: Sequence[str]
    stdin_payload: Optional[str]
    environment: Mapping[str, str]
    display_command: str


class CommandBuilder:
    """Build native and generic read-only reviewer commands."""

    def __init__(
        self,
        *,
        binary: CodexBinary,
        model: str,
        effort: str,
        cwd: Optional[str] = None,
        add_dirs: Optional[Sequence[str]] = None,
        profile: Optional[str] = None,
        strict_config: bool = False,
        skip_git_repo_check: bool = False,
        ephemeral: bool = True,
        ignore_user_config: bool = False,
        ignore_rules: bool = False,
        json_output: bool = True,
        last_message_output: Optional[str] = None,
        search: bool = False,
        images: Optional[Sequence[str]] = None,
        schema_file: Optional[str] = None,
        service_tier: Optional[str] = None,
        context_window: Optional[int] = None,
        auto_compact_token_limit: Optional[int] = None,
        git_path: Optional[str] = None,
        minimal_context: bool = True,
    ):
        if not binary.path:
            raise ValueError(binary.error or "Codex CLI not found")
        self.binary = binary
        self.model = model
        self.effort = effort
        self.cwd = cwd
        self.add_dirs = list(add_dirs or [])
        self.profile = profile
        self.strict_config = strict_config
        self.skip_git_repo_check = skip_git_repo_check
        self.ephemeral = ephemeral
        self.ignore_user_config = ignore_user_config
        self.ignore_rules = ignore_rules
        self.json_output = json_output
        self.last_message_output = last_message_output
        self.search = search
        self.images = list(images or [])
        self.schema_file = schema_file
        self.service_tier = service_tier
        self.context_window = context_window
        self.auto_compact_token_limit = auto_compact_token_limit
        self.git_path = git_path
        self.minimal_context = minimal_context

    def generic(self, prompt: str) -> CommandSpec:
        if not prompt.strip():
            raise ValueError("Generic review requires non-empty instructions")
        argv = self._root_args()
        argv.append("exec")
        argv.extend(self._exec_args())
        if self.schema_file:
            argv.extend(["--output-schema", self.schema_file])
        for image in self.images:
            argv.extend(["--image", image])
        self._append_output_args(argv)
        argv.append("-")
        return self._spec(argv, prompt)

    def native(
        self,
        scope: ReviewScope,
        *,
        prompt: Optional[str] = None,
        title: Optional[str] = None,
    ) -> CommandSpec:
        argv = self._root_args()
        argv.extend(["exec", "review"])
        argv.extend(scope.native_args())
        if title:
            argv.extend(["--title", title])
        argv.extend(self._exec_args())
        self._append_output_args(argv)
        payload = prompt if prompt and prompt.strip() else None
        if payload is not None:
            argv.append("-")
        return self._spec(argv, payload)

    def _root_args(self) -> List[str]:
        assert self.binary.path is not None
        environment = developer_git_environment(self.git_path)
        argv = [
            self.binary.path,
            "--model",
            self.model,
            "--sandbox",
            "read-only",
            "--ask-for-approval",
            "never",
            "--config",
            f"model_reasoning_effort={json.dumps(self.effort)}",
        ]
        if self.service_tier:
            argv.extend(["--config", f"service_tier={json.dumps(self.service_tier)}"])
        if self.minimal_context:
            argv.extend(
                [
                    "--disable",
                    "plugins",
                    "--disable",
                    "apps",
                    "--disable",
                    "multi_agent",
                ]
            )
        if self.context_window is not None:
            argv.extend(["--config", f"model_context_window={self.context_window}"])
        if self.auto_compact_token_limit is not None:
            argv.extend(
                [
                    "--config",
                    f"model_auto_compact_token_limit={self.auto_compact_token_limit}",
                ]
            )
        if self.git_path:
            argv.extend(
                [
                    "--config",
                    f"shell_environment_policy.set.PATH={json.dumps(environment.get('PATH', ''))}",
                ]
            )
            developer_dir = environment.get("DEVELOPER_DIR")
            if developer_dir:
                argv.extend(
                    [
                        "--config",
                        f"shell_environment_policy.set.DEVELOPER_DIR={json.dumps(developer_dir)}",
                    ]
                )
        if self.profile:
            argv.extend(["--profile", self.profile])
        if self.strict_config:
            argv.append("--strict-config")
        if self.search:
            argv.append("--search")
        if self.cwd:
            argv.extend(["--cd", self.cwd])
        for directory in self.add_dirs:
            argv.extend(["--add-dir", directory])
        return argv

    def _exec_args(self) -> List[str]:
        argv: List[str] = []
        if self.skip_git_repo_check:
            argv.append("--skip-git-repo-check")
        if self.ephemeral:
            argv.append("--ephemeral")
        if self.ignore_user_config:
            argv.append("--ignore-user-config")
        if self.ignore_rules:
            argv.append("--ignore-rules")
        return argv

    def _append_output_args(self, argv: List[str]) -> None:
        if self.json_output:
            argv.append("--json")
        if self.last_message_output:
            argv.extend(["--output-last-message", self.last_message_output])

    def _spec(self, argv: Sequence[str], stdin_payload: Optional[str]) -> CommandSpec:
        environment: Dict[str, str] = developer_git_environment(self.git_path)
        display_argv = [
            'shell_environment_policy.set.PATH="<injected>"'
            if argument.startswith("shell_environment_policy.set.PATH=")
            else argument
            for argument in argv
        ]
        return CommandSpec(
            argv=tuple(argv),
            stdin_payload=stdin_payload,
            environment=environment,
            display_command=shlex.join(display_argv),
        )
