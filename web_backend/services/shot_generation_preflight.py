"""Zero-write, zero-network Shot generation preparation service."""

from __future__ import annotations

import json
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from project_manager import create_project_paths
from video_generation_request import ProviderSelection, VideoGenerationRequest
from video_provider import VideoProvider, VideoProviderError
from video_provider_registry import (
    VideoProviderRegistry,
    create_default_registry,
    load_provider_credentials_from_env,
)
from visual_input import (
    first_frame_visual_input,
    none_visual_input,
    reference_asset_visual_input,
)
from web_backend.models.generation import (
    GenerationIntent,
    GenerationIssue,
    GenerationIssueCode,
    GenerationModelOption,
    GenerationOptionsResponse,
    GenerationPreflightRequest,
    GenerationPreflightResponse,
    GenerationShotContext,
    GenerationVisualInputMode,
    GenerationVisualInputOption,
    ModelSelectionMode,
    ResolvedGeneration,
)
from web_backend.models.projects import AvailableAction
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
)
from web_backend.repositories.reference_asset_repository import (
    ReferenceAssetDataCorrupt,
    ReferenceAssetNotFound,
    ReferenceAssetRecord,
    ReferenceAssetRepository,
)
from web_backend.repositories.shot_repository import ShotNotFound, normalize_shot_id


_RESOLUTION = "768P"
_MODE_COPY = {
    GenerationVisualInputMode.NONE: (
        "不使用参考图",
        "完全根据提示词生成。",
    ),
    GenerationVisualInputMode.REFERENCE_ASSET: (
        "主体参考",
        "保持产品或主体身份，但允许 AI 重新构图和设计场景。",
    ),
    GenerationVisualInputMode.FIRST_FRAME: (
        "作为首帧",
        "这张图片将作为视频的第一帧继续生成。",
    ),
}
_GENERATION_MODE_COPY = {
    "text_to_video": "纯文本生成",
    "first_frame": "首帧生成",
    "reference_generation": "主体参考生成",
}
_ISSUE_MESSAGES = {
    GenerationIssueCode.PROMPT_NOT_APPROVED: "视频提示词尚未正式审核通过。",
    GenerationIssueCode.SHOT_NOT_READY: "当前工作流状态不允许初次生成该镜头。",
    GenerationIssueCode.SHOT_ALREADY_GENERATED: "该镜头已有视频版本，不能使用初次生成入口。",
    GenerationIssueCode.MODEL_UNAVAILABLE: "所选视频模型不存在或未注册。",
    GenerationIssueCode.MODEL_VISUAL_INPUT_INCOMPATIBLE: "所选模型不支持当前 Visual Input。",
    GenerationIssueCode.REFERENCE_ASSET_REQUIRED: "请选择主体参考素材。",
    GenerationIssueCode.REFERENCE_ASSET_NOT_FOUND: "所选参考素材不存在。",
    GenerationIssueCode.REFERENCE_ASSET_INVALID: "所选参考素材无法安全读取。",
    GenerationIssueCode.FIRST_FRAME_REQUIRED: "请选择作为首帧的素材。",
    GenerationIssueCode.PROVIDER_NOT_CONFIGURED: "当前模式所需的视频模型尚未配置。",
    GenerationIssueCode.INVALID_DURATION: "当前镜头时长不受所选模型支持。",
    GenerationIssueCode.INVALID_RESOLUTION: "当前视频分辨率不受所选模型支持。",
    GenerationIssueCode.VISUAL_INPUT_ASSET_NOT_ALLOWED: "不使用参考图时不能同时提交素材。",
    GenerationIssueCode.VISUAL_INPUT_ASSET_COUNT_INVALID: "当前 Visual Input 必须且只能选择一张图片。",
}


def _issue(code: GenerationIssueCode) -> GenerationIssue:
    return GenerationIssue(code=code, message=_ISSUE_MESSAGES[code])


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


@dataclass(frozen=True)
class _ShotContext:
    public: GenerationShotContext
    project_dir: Path
    prompt: str
    state_issues: tuple[GenerationIssue, ...]


class ShotGenerationPreflightService:
    """Resolve one future provider route using only local state and capabilities."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        reference_repository: ReferenceAssetRepository,
    ) -> None:
        self.project_repository = project_repository
        self.reference_repository = reference_repository

    @staticmethod
    def _registry() -> VideoProviderRegistry:
        return create_default_registry(load_provider_credentials_from_env())

    def options(
        self,
        project_id: str,
        shot_id: str,
        intent: GenerationIntent = GenerationIntent.INITIAL,
    ) -> GenerationOptionsResponse:
        context = self._context(project_id, shot_id, intent)
        registry = self._registry()
        models = self._models(registry)
        modes = [
            GenerationVisualInputOption(
                mode=mode,
                display_name=_MODE_COPY[mode][0],
                description=_MODE_COPY[mode][1],
                compatible_model_ids=[
                    model.model_id
                    for model in models
                    if mode in model.supported_visual_input_modes
                ],
            )
            for mode in (
                GenerationVisualInputMode.NONE,
                GenerationVisualInputMode.REFERENCE_ASSET,
                GenerationVisualInputMode.FIRST_FRAME,
            )
        ]
        return GenerationOptionsResponse(
            project_id=self.project_repository.get_workflow(project_id).project_id,
            eligible=not context.state_issues,
            shot=context.public,
            selection_modes=[ModelSelectionMode.AUTO, ModelSelectionMode.MANUAL],
            visual_input_modes=modes,
            models=models,
            issues=list(context.state_issues),
        )

    def preflight(
        self,
        project_id: str,
        shot_id: str,
        payload: GenerationPreflightRequest,
    ) -> GenerationPreflightResponse:
        context = self._context(project_id, shot_id, payload.intent)
        issues = list(context.state_issues)
        selected_ids = list(payload.visual_input.asset_ids)
        mode = payload.visual_input.mode
        asset: ReferenceAssetRecord | None = None
        if mode is GenerationVisualInputMode.NONE:
            if selected_ids:
                issues.append(_issue(GenerationIssueCode.VISUAL_INPUT_ASSET_NOT_ALLOWED))
            visual_input = none_visual_input()
        else:
            if not selected_ids:
                issues.append(
                    _issue(
                        GenerationIssueCode.REFERENCE_ASSET_REQUIRED
                        if mode is GenerationVisualInputMode.REFERENCE_ASSET
                        else GenerationIssueCode.FIRST_FRAME_REQUIRED
                    )
                )
            elif len(selected_ids) != 1:
                issues.append(_issue(GenerationIssueCode.VISUAL_INPUT_ASSET_COUNT_INVALID))
            else:
                try:
                    asset = self.reference_repository.asset(project_id, selected_ids[0])
                except ReferenceAssetNotFound:
                    issues.append(_issue(GenerationIssueCode.REFERENCE_ASSET_NOT_FOUND))
                except ReferenceAssetDataCorrupt:
                    issues.append(_issue(GenerationIssueCode.REFERENCE_ASSET_INVALID))
            if asset is None:
                visual_input = {
                    "mode": mode.value,
                    "source": "user_upload",
                    "assets": [],
                }
            elif mode is GenerationVisualInputMode.REFERENCE_ASSET:
                visual_input = reference_asset_visual_input(asset.core_record())
            else:
                visual_input = first_frame_visual_input(asset.core_record())

        registry = self._registry()
        adapter = self._selected_adapter(registry, payload, issues)
        resolved: ResolvedGeneration | None = None
        provider_available = False
        if adapter is not None:
            if not adapter.supports(mode.value):
                issues.append(
                    _issue(GenerationIssueCode.MODEL_VISUAL_INPUT_INCOMPATIBLE)
                )
            else:
                generation_mode = adapter.generation_mode(mode.value)
                provider_available = bool(
                    str(getattr(adapter, "credential_value", "") or "").strip()
                )
                if not provider_available:
                    issues.append(_issue(GenerationIssueCode.PROVIDER_NOT_CONFIGURED))
                if not adapter.capabilities.supports_duration(
                    context.public.duration_seconds
                ):
                    issues.append(_issue(GenerationIssueCode.INVALID_DURATION))
                if not adapter.capabilities.supports_resolution(
                    context.public.resolution
                ):
                    issues.append(_issue(GenerationIssueCode.INVALID_RESOLUTION))
                if not any(
                    item.code
                    in {
                        GenerationIssueCode.REFERENCE_ASSET_REQUIRED,
                        GenerationIssueCode.REFERENCE_ASSET_NOT_FOUND,
                        GenerationIssueCode.REFERENCE_ASSET_INVALID,
                        GenerationIssueCode.FIRST_FRAME_REQUIRED,
                        GenerationIssueCode.VISUAL_INPUT_ASSET_COUNT_INVALID,
                        GenerationIssueCode.INVALID_DURATION,
                        GenerationIssueCode.INVALID_RESOLUTION,
                    }
                    for item in issues
                ):
                    request = VideoGenerationRequest(
                        shot_id=int(context.public.shot_id.removeprefix("shot_")),
                        prompt=context.prompt,
                        duration=context.public.duration_seconds,
                        resolution=context.public.resolution,
                        visual_input=visual_input,
                        project=create_project_paths(
                            context.project_dir, ensure_directories=False
                        ),
                        provider_selection=(
                            ProviderSelection(
                                adapter.provider_name,
                                adapter.model_name,
                                payload.model_selection.value.lower(),
                            )
                            if payload.model_selection is ModelSelectionMode.MANUAL
                            else None
                        ),
                    )
                    # Invoke only the provider-neutral Core checks. Adapter
                    # preflight may construct the mutable ReferenceAssetManager.
                    VideoProvider.preflight(adapter, request)
                resolved = ResolvedGeneration(
                    provider=str(adapter.provider_name).lower(),
                    provider_display_name=self._provider_display(adapter.provider_name),
                    model=adapter.model_name,
                    model_display_name=self._model_display(adapter.model_name),
                    api_version=adapter.api_version,
                    generation_mode=generation_mode,
                    generation_mode_display_name=_GENERATION_MODE_COPY.get(
                        generation_mode, generation_mode
                    ),
                    visual_input_mode=mode,
                    model_selection=payload.model_selection,
                )

        issues = self._unique_issues(issues)
        fingerprint = None
        if not issues and resolved is not None:
            fingerprint = self._fingerprint(
                project_id=self.project_repository.get_project(project_id).project_id,
                context=context,
                payload=payload,
                resolved=resolved,
                asset=asset,
            )
        return GenerationPreflightResponse(
            ready=not issues,
            shot=context.public,
            resolved=resolved,
            provider_available=provider_available,
            selected_asset_ids=selected_ids,
            issues=issues,
            warnings=[],
            preflight_fingerprint=fingerprint,
        )

    @staticmethod
    def _fingerprint(
        *,
        project_id: str,
        context: _ShotContext,
        payload: GenerationPreflightRequest,
        resolved: ResolvedGeneration,
        asset: ReferenceAssetRecord | None,
    ) -> str:
        material = {
            "project_id": project_id,
            "shot_id": context.public.shot_id,
            "generation_intent": payload.intent.value,
            "official_video_version": context.public.official_video_version,
            "pending_video_version": context.public.pending_video_version,
            "next_video_version": context.public.next_video_version,
            "prompt_version": context.public.prompt_version,
            "prompt_sha256": hashlib.sha256(context.prompt.encode("utf-8")).hexdigest(),
            "duration": context.public.duration_seconds,
            "resolution": context.public.resolution,
            "model_selection": payload.model_selection.value,
            "requested_model": payload.requested_model,
            "provider": resolved.provider,
            "model": resolved.model,
            "api_version": resolved.api_version,
            "generation_mode": resolved.generation_mode,
            "visual_input_mode": payload.visual_input.mode.value,
            "assets": (
                [
                    {
                        "asset_id": asset.asset_id,
                        "sha256": asset.sha256,
                        "source": asset.source,
                        "project_path": asset.project_path,
                    }
                ]
                if asset is not None
                else []
            ),
            "workflow": "shot_generation",
        }
        encoded = json.dumps(
            material,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _selected_adapter(
        registry: VideoProviderRegistry,
        payload: GenerationPreflightRequest,
        issues: list[GenerationIssue],
    ) -> VideoProvider | None:
        if payload.model_selection is ModelSelectionMode.AUTO:
            selection = registry.default_selection(payload.visual_input.mode.value)
            if selection is None:
                issues.append(_issue(GenerationIssueCode.MODEL_UNAVAILABLE))
                return None
            try:
                return registry.adapter(selection.provider, selection.model)
            except VideoProviderError:
                issues.append(_issue(GenerationIssueCode.MODEL_UNAVAILABLE))
                return None
        matches = [
            adapter
            for adapter in registry.registered_adapters()
            if adapter.model_name == payload.requested_model
        ]
        if len(matches) != 1:
            issues.append(_issue(GenerationIssueCode.MODEL_UNAVAILABLE))
            return None
        return matches[0]

    def _context(
        self,
        project_id: str,
        shot_id: str,
        intent: GenerationIntent,
    ) -> _ShotContext:
        canonical_id, shot_number = normalize_shot_id(shot_id)
        workflow = self.project_repository.get_workflow(project_id)
        project_dir = self.project_repository.resolve_project_dir(project_id).resolve()
        project = self._read_object(project_dir, ("project.json",))
        storyboard = self._read_object(
            project_dir, ("storyboard", "storyboard.json")
        )
        shots = _mapping(_mapping(project.get("video_generation")).get("shots"))
        checkpoint = _mapping(shots.get(str(shot_number)))
        if not checkpoint:
            checkpoint = next(
                (
                    _mapping(value)
                    for value in shots.values()
                    if _positive_int(_mapping(value).get("shot_id")) == shot_number
                ),
                {},
            )
        board_shot = next(
            (
                _mapping(value)
                for value in (storyboard.get("shots") or [])
                if _positive_int(_mapping(value).get("shot_id")) == shot_number
            ),
            {},
        )
        if not checkpoint or not board_shot:
            raise ShotNotFound("shot was not found")
        duration = _positive_int(board_shot.get("duration"))
        if duration is None:
            raise ProjectDataCorrupt("storyboard shot duration is invalid")

        candidate = _mapping(checkpoint.get("candidate"))
        candidate_status = str(candidate.get("status") or "NONE").upper()
        approved_version = _positive_int(checkpoint.get("approved_video_version"))
        active_version = _positive_int(checkpoint.get("active_video_version"))
        candidate_version = (
            _positive_int(candidate.get("video_version"))
            if candidate_status not in {"NONE", "EDITING"}
            else None
        )
        prompt_version = _positive_int(
            candidate.get("prompt_version")
            if intent is GenerationIntent.REGENERATE_CURRENT_PROMPT
            and candidate_status not in {"NONE", "EDITING"}
            else checkpoint.get("approved_prompt_version")
            if intent is GenerationIntent.REGENERATE_CURRENT_PROMPT
            and approved_version is not None
            else checkpoint.get("active_prompt_version")
        )
        prompt_record = next(
            (
                _mapping(value)
                for value in (checkpoint.get("prompt_versions") or [])
                if _positive_int(_mapping(value).get("version")) == prompt_version
            ),
            {},
        )
        prompt = str(prompt_record.get("prompt") or "").strip()
        stages = _mapping(project.get("stages"))
        prompt_approved = (
            str(_mapping(stages.get("VIDEO_PROMPT")).get("status") or "").upper()
            == "COMPLETED"
            and str(_mapping(stages.get("PROMPT_REVIEW")).get("status") or "").upper()
            == "APPROVED"
            and prompt_version is not None
            and bool(prompt)
        )
        issues: list[GenerationIssue] = []
        if not prompt_approved:
            issues.append(_issue(GenerationIssueCode.PROMPT_NOT_APPROVED))

        generated = any(
            (
                _positive_int(checkpoint.get("generation_count")) is not None,
                bool(checkpoint.get("generation_versions")),
                _positive_int(checkpoint.get("active_video_version")) is not None,
                _positive_int(checkpoint.get("approved_video_version")) is not None,
                _positive_int(checkpoint.get("pending_video_version")) is not None,
            )
        )
        shot_status = str(checkpoint.get("status") or "NOT_STARTED").upper()
        if intent is GenerationIntent.INITIAL:
            if generated:
                issues.append(_issue(GenerationIssueCode.SHOT_ALREADY_GENERATED))
            if (
                shot_status != "NOT_STARTED"
                or AvailableAction.GENERATE_SHOTS not in workflow.available_actions
            ) and not generated:
                issues.append(_issue(GenerationIssueCode.SHOT_NOT_READY))
        else:
            unapproved_review = (
                shot_status == "WAITING_REVIEW"
                and approved_version is None
                and active_version is not None
            )
            approved_review = (
                shot_status == "APPROVED"
                and approved_version is not None
                and candidate_status != "GENERATING"
            )
            if not (unapproved_review or approved_review):
                issues.append(_issue(GenerationIssueCode.SHOT_NOT_READY))

        generation_versions = [
            _positive_int(_mapping(item).get("video_version")) or 0
            for item in (checkpoint.get("generation_versions") or [])
        ]
        next_version = max(
            [
                active_version or 0,
                approved_version or 0,
                candidate_version or 0,
                _positive_int(checkpoint.get("generation_count")) or 0,
                *generation_versions,
            ]
        ) + 1
        pending_version = candidate_version or (
            active_version if approved_version is None and shot_status == "WAITING_REVIEW" else None
        )

        return _ShotContext(
            public=GenerationShotContext(
                shot_id=canonical_id,
                duration_seconds=duration,
                prompt_version=prompt_version,
                resolution=_RESOLUTION,
                official_video_version=approved_version,
                pending_video_version=pending_version,
                next_video_version=next_version,
            ),
            project_dir=project_dir,
            prompt=prompt or "unavailable",
            state_issues=tuple(self._unique_issues(issues)),
        )

    @staticmethod
    def _read_object(project_dir: Path, parts: tuple[str, ...]) -> Mapping[str, Any]:
        try:
            root = project_dir.resolve()
            path = project_dir.joinpath(*parts)
            resolved = path.resolve()
        except OSError as exc:
            raise ProjectDataCorrupt("project data path cannot be resolved") from exc
        if path.is_symlink() or not path.is_file() or not _within(resolved, root):
            raise ProjectDataCorrupt("project data escaped its fixed path")
        try:
            with resolved.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectDataCorrupt("project data is unreadable") from exc
        if not isinstance(payload, Mapping):
            raise ProjectDataCorrupt("project data is not an object")
        return payload

    @classmethod
    def _models(cls, registry: VideoProviderRegistry) -> list[GenerationModelOption]:
        result: list[GenerationModelOption] = []
        for adapter in registry.registered_adapters():
            modes = [
                mode
                for mode in GenerationVisualInputMode
                if adapter.supports(mode.value)
            ]
            result.append(
                GenerationModelOption(
                    model_id=adapter.model_name,
                    display_name=cls._model_display(adapter.model_name),
                    provider=str(adapter.provider_name).lower(),
                    provider_display_name=cls._provider_display(adapter.provider_name),
                    api_version=adapter.api_version,
                    available=bool(
                        str(getattr(adapter, "credential_value", "") or "").strip()
                    ),
                    supported_visual_input_modes=modes,
                    supported_resolutions=sorted(
                        adapter.capabilities.supported_resolutions
                    ),
                    supported_durations=sorted(
                        adapter.capabilities.supported_durations
                    ),
                    min_duration=adapter.capabilities.min_duration,
                    max_duration=adapter.capabilities.max_duration,
                )
            )
        return result

    @staticmethod
    def _provider_display(value: str) -> str:
        return "MiniMax" if str(value).lower() == "minimax" else str(value)

    @staticmethod
    def _model_display(value: str) -> str:
        return {
            "MiniMax-Hailuo-2.3": "MiniMax Hailuo 2.3",
            "MiniMax-H3": "MiniMax H3",
        }.get(value, value)

    @staticmethod
    def _unique_issues(issues: list[GenerationIssue]) -> list[GenerationIssue]:
        seen: set[GenerationIssueCode] = set()
        result: list[GenerationIssue] = []
        for item in issues:
            if item.code not in seen:
                seen.add(item.code)
                result.append(item)
        return result
