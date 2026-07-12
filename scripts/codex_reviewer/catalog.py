"""Codex binary discovery, model catalog parsing, and review presets."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


CODEX_BIN_ENV = "CODEX_REVIEWER_CODEX_BIN"
MIN_CODEX_VERSION = (0, 144, 1)
DEFAULT_PRESET = "standard"
PRESET_NAMES = ("quick", "standard", "deep", "ultra")
INSTALL_NPM = "npm"
INSTALL_STANDALONE = "standalone"
INSTALL_OTHER = "other"


@dataclass(frozen=True)
class PresetCandidate:
    model: str
    effort: str


PRESET_CANDIDATES: Mapping[str, Tuple[PresetCandidate, ...]] = {
    "quick": (
        PresetCandidate("gpt-5.6-terra", "medium"),
        PresetCandidate("gpt-5.6-sol", "medium"),
        PresetCandidate("gpt-5.5", "medium"),
    ),
    "standard": (
        PresetCandidate("gpt-5.6-sol", "high"),
        PresetCandidate("gpt-5.5", "high"),
    ),
    "deep": (
        PresetCandidate("gpt-5.6-sol", "max"),
        PresetCandidate("gpt-5.5", "xhigh"),
    ),
    "ultra": (PresetCandidate("gpt-5.6-sol", "ultra"),),
}


class PresetResolutionError(ValueError):
    """Raised when a requested model/preset cannot be satisfied."""


def _parse_version(value: str) -> Tuple[Optional[Tuple[int, int, int]], Optional[str]]:
    match = re.search(
        r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?![0-9A-Za-z.-])", value
    )
    if not match:
        return None, None
    version = tuple(int(part) for part in match.groups()[:3])
    return version, match.group(4)  # type: ignore[return-value]


def format_version(version: Optional[Tuple[int, int, int]]) -> Optional[str]:
    if version is None:
        return None
    return ".".join(str(part) for part in version)


@dataclass(frozen=True)
class CodexBinary:
    """Resolved Codex executable and its reported version."""

    requested: str
    path: Optional[str]
    version_text: Optional[str] = None
    version: Optional[Tuple[int, int, int]] = None
    prerelease: Optional[str] = None
    install_method: str = INSTALL_OTHER
    error: Optional[str] = None

    @classmethod
    def discover(
        cls, requested: Optional[str] = None, timeout: int = 10
    ) -> "CodexBinary":
        env_value = os.environ.get(CODEX_BIN_ENV)
        if requested is not None or env_value:
            requested_value = requested or env_value or "codex"
            candidates = cls._explicit_candidates(requested_value)
        else:
            requested_value = "codex"
            candidates = cls._path_candidates("codex")
        if not candidates:
            return cls(
                requested=requested_value,
                path=None,
                error=f"Codex CLI not found: {requested_value}",
            )
        inspected = [
            cls._inspect(requested_value, path, timeout) for path in candidates
        ]
        if requested is not None or env_value:
            return inspected[0]
        # Preserve the user's package-manager choice. An npm install wins even
        # when a standalone or app-bundled binary is also present. Otherwise,
        # prefer the official standalone layout and only then another manager.
        for install_method in (INSTALL_NPM, INSTALL_STANDALONE, INSTALL_OTHER):
            group = [
                item for item in inspected if item.install_method == install_method
            ]
            stable = [
                item
                for item in group
                if item.version is not None
                and item.prerelease is None
                and item.error is None
            ]
            if stable:
                return max(stable, key=lambda item: item.version or (0, 0, 0))
            runnable = [item for item in group if item.path]
            if runnable:
                return max(runnable, key=lambda item: item.version or (0, 0, 0))
        details = "; ".join(
            item.error or item.version_text or item.path or "unknown"
            for item in inspected
        )
        return cls(
            requested=requested_value,
            path=None,
            error=f"No stable Codex CLI found on PATH: {details}",
        )

    @staticmethod
    def _explicit_candidates(requested: str) -> List[str]:
        expanded = Path(requested).expanduser()
        if os.sep in requested or (os.altsep and os.altsep in requested):
            return (
                [str(expanded.resolve())]
                if CodexBinary._is_executable(expanded)
                else []
            )
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            for candidate_name in CodexBinary._candidate_names(requested):
                candidate = Path(directory or os.curdir) / candidate_name
                if CodexBinary._is_executable(candidate):
                    return [str(candidate.resolve())]
        return []

    @staticmethod
    def _path_candidates(name: str) -> List[str]:
        candidates: List[str] = []
        seen = set()
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            for candidate_name in CodexBinary._candidate_names(name):
                candidate = Path(directory or os.curdir) / candidate_name
                if not CodexBinary._is_executable(candidate):
                    continue
                resolved = str(candidate.resolve())
                if resolved not in seen:
                    seen.add(resolved)
                    candidates.append(resolved)
        for candidate in (
            *CodexBinary._npm_global_candidates(),
            *CodexBinary._standalone_candidates(),
        ):
            if candidate not in seen:
                seen.add(candidate)
                candidates.append(candidate)
        return candidates

    @staticmethod
    def _candidate_names(name: str) -> Tuple[str, ...]:
        if os.name != "nt" or Path(name).suffix:
            return (name,)
        return (f"{name}.exe", f"{name}.cmd", f"{name}.bat", name)

    @staticmethod
    def _is_executable(path: Path) -> bool:
        return path.is_file() and (os.name == "nt" or os.access(path, os.X_OK))

    @staticmethod
    def _npm_global_candidates(timeout: int = 5) -> List[str]:
        npm = shutil.which("npm")
        if not npm:
            return []
        try:
            result = subprocess.run(
                [npm, "prefix", "-g"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return []
        if result.returncode != 0 or not result.stdout.strip():
            return []
        prefix = Path(result.stdout.strip()).expanduser()
        candidates: List[str] = []
        for directory in (prefix / "bin", prefix):
            for candidate_name in CodexBinary._candidate_names("codex"):
                candidate = directory / candidate_name
                if not CodexBinary._is_executable(candidate):
                    continue
                resolved = str(candidate.resolve())
                if CodexBinary._classify_install_method(resolved) == INSTALL_NPM:
                    candidates.append(resolved)
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _standalone_candidates() -> List[str]:
        home = Path.home()
        codex_home = Path(os.environ.get("CODEX_HOME", home / ".codex")).expanduser()
        install_dir = os.environ.get("CODEX_INSTALL_DIR")
        visible_dirs = [Path(install_dir).expanduser()] if install_dir else []
        if os.name == "nt":
            local_app_data = os.environ.get("LOCALAPPDATA")
            if local_app_data:
                visible_dirs.append(
                    Path(local_app_data) / "Programs" / "OpenAI" / "Codex" / "bin"
                )
        else:
            visible_dirs.append(home / ".local" / "bin")
        direct = [
            codex_home / "packages" / "standalone" / "current" / "bin" / "codex",
            codex_home / "packages" / "standalone" / "current" / "codex",
        ]
        if os.name == "nt":
            direct = [path.with_suffix(".exe") for path in direct]
        candidates: List[str] = []
        for directory in visible_dirs:
            for candidate_name in CodexBinary._candidate_names("codex"):
                direct.append(directory / candidate_name)
        for candidate in direct:
            if CodexBinary._is_executable(candidate):
                candidates.append(str(candidate.resolve()))
        return list(dict.fromkeys(candidates))

    @staticmethod
    def _classify_install_method(path: str) -> str:
        normalized = path.replace("\\", "/").lower()
        if "/node_modules/@openai/codex/" in normalized or normalized.endswith(
            "/codex.js"
        ):
            return INSTALL_NPM
        if "/packages/standalone/" in normalized:
            return INSTALL_STANDALONE
        try:
            if Path(path).suffix.lower() in {".cmd", ".bat", ".js"}:
                head = Path(path).read_text(encoding="utf-8", errors="ignore")[:4096]
                if "node_modules" in head and "@openai" in head and "codex" in head:
                    return INSTALL_NPM
        except OSError:
            pass
        return INSTALL_OTHER

    @classmethod
    def _inspect(cls, requested: str, path: str, timeout: int) -> "CodexBinary":
        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return cls(
                requested=requested,
                path=path,
                install_method=cls._classify_install_method(path),
                error=f"Failed to inspect Codex CLI at {path}: {exc}",
            )

        version_text = (result.stdout or result.stderr).strip() or None
        if result.returncode != 0:
            return cls(
                requested=requested,
                path=path,
                version_text=version_text,
                install_method=cls._classify_install_method(path),
                error=version_text
                or f"Codex --version exited with status {result.returncode}",
            )
        version, prerelease = _parse_version(version_text or "")
        error = (
            None if version else f"Could not parse Codex version from: {version_text!r}"
        )
        return cls(
            requested=requested,
            path=str(Path(path).resolve()),
            version_text=version_text,
            version=version,
            prerelease=prerelease,
            install_method=cls._classify_install_method(path),
            error=error,
        )

    @property
    def supported(self) -> bool:
        return (
            self.version is not None
            and self.prerelease is None
            and self.version >= MIN_CODEX_VERSION
        )

    @property
    def version_string(self) -> Optional[str]:
        return format_version(self.version)


@dataclass(frozen=True)
class ModelInfo:
    slug: str
    display_name: str
    description: str
    reasoning_efforts: Tuple[str, ...]
    default_reasoning_effort: Optional[str]
    context_window: Optional[int]
    max_context_window: Optional[int]
    effective_context_window_percent: Optional[int]
    service_tiers: Tuple[str, ...]
    supports_search: bool
    input_modalities: Tuple[str, ...]
    visibility: Optional[str]

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "ModelInfo":
        raw_levels = payload.get("supported_reasoning_levels") or []
        efforts: List[str] = []
        if isinstance(raw_levels, list):
            for item in raw_levels:
                if isinstance(item, str):
                    efforts.append(item)
                elif isinstance(item, Mapping):
                    effort = item.get("effort")
                    if isinstance(effort, str):
                        efforts.append(effort)

        raw_tiers = payload.get("service_tiers") or []
        tiers: List[str] = []
        if isinstance(raw_tiers, list):
            for item in raw_tiers:
                if isinstance(item, str):
                    tiers.append(item)
                elif isinstance(item, Mapping):
                    tier_id = item.get("id")
                    if isinstance(tier_id, str):
                        tiers.append(tier_id)
        raw_speed_tiers = payload.get("additional_speed_tiers") or []
        if isinstance(raw_speed_tiers, list):
            tiers.extend(item for item in raw_speed_tiers if isinstance(item, str))

        raw_modalities = payload.get("input_modalities") or []
        modalities = (
            tuple(item for item in raw_modalities if isinstance(item, str))
            if isinstance(raw_modalities, list)
            else ()
        )

        def optional_int(key: str) -> Optional[int]:
            value = payload.get(key)
            return int(value) if isinstance(value, (int, float)) else None

        return cls(
            slug=str(payload.get("slug") or ""),
            display_name=str(payload.get("display_name") or payload.get("slug") or ""),
            description=str(payload.get("description") or ""),
            reasoning_efforts=tuple(efforts),
            default_reasoning_effort=(
                str(payload["default_reasoning_level"])
                if isinstance(payload.get("default_reasoning_level"), str)
                else None
            ),
            context_window=optional_int("context_window"),
            max_context_window=optional_int("max_context_window"),
            effective_context_window_percent=optional_int(
                "effective_context_window_percent"
            ),
            service_tiers=tuple(dict.fromkeys(tiers)),
            supports_search=bool(payload.get("supports_search_tool", False)),
            input_modalities=modalities,
            visibility=str(payload["visibility"])
            if isinstance(payload.get("visibility"), str)
            else None,
        )


@dataclass
class ModelCatalog:
    models: Dict[str, ModelInfo] = field(default_factory=dict)
    error: Optional[str] = None
    source: str = "remote"
    warnings: List[str] = field(default_factory=list)

    @classmethod
    def load(
        cls, binary: CodexBinary, timeout: int = 15, bundled: bool = False
    ) -> "ModelCatalog":
        refreshed = cls._load_once(binary, timeout=timeout, bundled=bundled)
        if bundled or not refreshed.error:
            return refreshed
        fallback = cls._load_once(binary, timeout=timeout, bundled=True)
        if not fallback.error:
            fallback.warnings.append(
                f"Refreshed model catalog failed; using bundled catalog: {refreshed.error}"
            )
            return fallback
        return cls(
            error=f"Refreshed model catalog failed: {refreshed.error}; bundled catalog failed: {fallback.error}",
            source="unavailable",
        )

    @classmethod
    def _load_once(
        cls, binary: CodexBinary, timeout: int, bundled: bool
    ) -> "ModelCatalog":
        if not binary.path:
            return cls(
                error=binary.error or "Codex CLI not found", source="unavailable"
            )
        cmd = [binary.path, "debug", "models"]
        if bundled:
            cmd.append("--bundled")
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return cls(
                error=f"Failed to load Codex model catalog: {exc}",
                source="bundled" if bundled else "remote",
            )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            return cls(
                error=detail
                or f"codex debug models exited with status {result.returncode}",
                source="bundled" if bundled else "remote",
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return cls(
                error=f"Invalid model catalog JSON: {exc}",
                source="bundled" if bundled else "remote",
            )

        raw_models = payload.get("models") if isinstance(payload, Mapping) else None
        if not isinstance(raw_models, list):
            return cls(
                error="Model catalog does not contain a models array",
                source="bundled" if bundled else "remote",
            )
        models = {}
        for item in raw_models:
            if not isinstance(item, Mapping):
                continue
            model = ModelInfo.from_payload(item)
            if model.slug:
                models[model.slug] = model
        if not models:
            return cls(
                error="Model catalog is empty",
                source="bundled" if bundled else "remote",
            )
        return cls(models=models, source="bundled" if bundled else "remote")

    def get(self, slug: str) -> Optional[ModelInfo]:
        return self.models.get(slug)


@dataclass(frozen=True)
class ModelSelection:
    preset: str
    model: str
    effort: str
    fallback_used: bool
    warnings: Tuple[str, ...] = ()


def _matching_candidate(preset: str, model: str) -> Optional[PresetCandidate]:
    return next(
        (
            candidate
            for candidate in PRESET_CANDIDATES[preset]
            if candidate.model == model
        ),
        None,
    )


def _validate_effort(model: ModelInfo, effort: str) -> None:
    if not model.reasoning_efforts:
        raise PresetResolutionError(
            f"Model catalog does not declare supported reasoning efforts for {model.slug}"
        )
    if effort not in model.reasoning_efforts:
        supported = ", ".join(model.reasoning_efforts)
        raise PresetResolutionError(
            f"Model {model.slug} does not support reasoning effort {effort}; supported: {supported}"
        )


def resolve_model_selection(
    preset: str,
    catalog: ModelCatalog,
    explicit_model: Optional[str] = None,
    explicit_effort: Optional[str] = None,
) -> ModelSelection:
    if preset not in PRESET_CANDIDATES:
        raise PresetResolutionError(f"Unknown review preset: {preset}")
    candidates = PRESET_CANDIDATES[preset]
    warnings: List[str] = []

    if explicit_model == "gpt-5.6":
        explicit_model = "gpt-5.6-sol"

    if catalog.error and not catalog.models and (explicit_model or explicit_effort):
        raise PresetResolutionError(
            f"Model catalog is required to validate an explicit model or reasoning effort: {catalog.error}"
        )

    if explicit_effort and not explicit_model:
        preferred = candidates[0]
        model_info = catalog.get(preferred.model)
        if model_info is None:
            raise PresetResolutionError(
                f"Preset {preset} requires preferred model {preferred.model} when reasoning effort is explicit"
            )
        _validate_effort(model_info, explicit_effort)
        return ModelSelection(
            preset, preferred.model, explicit_effort, False, tuple(warnings)
        )

    if explicit_model:
        matching = _matching_candidate(preset, explicit_model)
        effort = explicit_effort or (
            matching.effort if matching else candidates[0].effort
        )
        model_info = catalog.get(explicit_model)
        if model_info is None:
            raise PresetResolutionError(
                f"Requested model is not available in the Codex catalog: {explicit_model}"
            )
        _validate_effort(model_info, effort)
        return ModelSelection(preset, explicit_model, effort, False, tuple(warnings))

    for index, candidate in enumerate(candidates):
        model_info = catalog.get(candidate.model)
        if model_info is None:
            continue
        effort = explicit_effort or candidate.effort
        try:
            _validate_effort(model_info, effort)
        except PresetResolutionError as exc:
            warnings.append(
                f"Skipping automatic preset candidate {candidate.model}: {exc}"
            )
            continue
        if index:
            warnings.append(
                f"Preset {preset} fell back to {candidate.model} because preferred models are unavailable"
            )
        return ModelSelection(
            preset, candidate.model, effort, index > 0, tuple(warnings)
        )

    if preset == "ultra":
        raise PresetResolutionError(
            "Preset ultra requires gpt-5.6-sol with ultra reasoning; no fallback is allowed"
        )

    if catalog.error:
        conservative = {
            "quick": PresetCandidate("gpt-5.5", "medium"),
            "standard": PresetCandidate("gpt-5.5", "high"),
            "deep": PresetCandidate("gpt-5.5", "xhigh"),
        }[preset]
        warnings.append(
            f"Model catalog unavailable; conservatively falling back to gpt-5.5: {catalog.error}"
        )
        return ModelSelection(
            preset, conservative.model, conservative.effort, True, tuple(warnings)
        )

    available = ", ".join(sorted(catalog.models)) or "none"
    raise PresetResolutionError(
        f"No model candidate for preset {preset} is available; catalog contains: {available}"
    )


def supported_efforts(catalog: ModelCatalog, model: str) -> Sequence[str]:
    model_info = catalog.get(model)
    return model_info.reasoning_efforts if model_info else ()
