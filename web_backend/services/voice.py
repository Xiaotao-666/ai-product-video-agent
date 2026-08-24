"""Thin Web adapter over the frozen Voice Core."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from post_production import PostProductionPipeline
from project_manager import ProjectPaths, create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from voice_assets import VoiceAssetError, VoiceAssetManager
from voice_provider import VoiceGenerationRequest, VoiceProvider, VoiceProviderError
from voice_provider_registry import (
    VoiceProviderRegistry,
    build_voice_provider_registry,
)
from voice_script_builder import (
    StoryboardVoiceScript,
    VoiceScriptBuilderError,
    load_storyboard_voice_script,
)
from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.postproduction import VoiceDetail
from web_backend.models.tasks import TaskOperation, TaskRecord, TaskResultReference
from web_backend.models.voice import (
    VoiceGenerateRequest,
    VoiceIntent,
    VoiceIssue,
    VoiceOptionsResponse,
    VoicePlannedTiming,
    VoicePreflightRequest,
    VoicePreflightResponse,
    VoiceProviderOption,
    VoiceScriptSummary,
    VoiceTimingAcceptanceRequest,
)
from web_backend.repositories.postproduction_repository import (
    PostProductionRepository,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
)
from web_backend.services.capabilities import CapabilityService
from web_backend.services.projects import ProjectBusy
from web_backend.services.task_failures import raise_task_failure
from web_backend.services.tasks import TaskService


class VoiceInputInvalid(RuntimeError):
    pass


class VoicePreflightStale(RuntimeError):
    pass


class VoiceProviderUnavailable(RuntimeError):
    pass


class VoiceExternalConfirmationRequired(RuntimeError):
    pass


class VoiceTimingAcceptanceNotAllowed(RuntimeError):
    pass


RegistryFactory = Callable[[], VoiceProviderRegistry]


@dataclass(frozen=True)
class _PreparedVoice:
    project_id: str
    paths: ProjectPaths
    registry: VoiceProviderRegistry
    provider: VoiceProvider | None
    provider_option: VoiceProviderOption | None
    request: VoiceGenerationRequest | None
    script: VoiceScriptSummary | None
    planned_timing: VoicePlannedTiming
    next_version: int
    issues: tuple[VoiceIssue, ...]
    warnings: tuple[VoiceIssue, ...]
    fingerprint: str | None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _canonical_hash(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _sha256(encoded)


def _provider_display_name(provider_id: str) -> str:
    return {
        "xfyun_tts": "讯飞 TTS",
        "aliyun_tts": "阿里云 TTS",
    }.get(provider_id, provider_id.replace("_", " ").strip().title())


class VoiceWebService:
    """Prepare, submit, and inspect Voice without duplicating Core logic."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        postproduction_repository: PostProductionRepository,
        task_service: TaskService,
        capability_service: CapabilityService,
        project_lock_manager: ProjectLockManager,
        *,
        registry_factory: RegistryFactory = build_voice_provider_registry,
    ) -> None:
        self._project_repository = project_repository
        self._postproduction_repository = postproduction_repository
        self._task_service = task_service
        self._capability_service = capability_service
        self._project_lock_manager = project_lock_manager
        self._registry_factory = registry_factory

    def options(self, project_id: str) -> VoiceOptionsResponse:
        canonical_id, paths, project_data = self._context(project_id)
        registry = self._registry_factory()
        manifest = self._manifest(paths)
        active = VoiceAssetManager(paths).active_version()
        next_version = self._next_version(manifest)
        voice_config = _mapping(project_data.get("voice_config"))
        provider_options = self._provider_options(registry)
        selected_provider = self._selected_provider_name(registry, voice_config, None)
        selected_option = next(
            (item for item in provider_options if item.provider_id == selected_provider),
            None,
        )
        plan, script, settings = self._default_script(paths, active)
        summary = self._script_summary(script, settings)
        timing = self._planned_timing(settings)
        default_voice = str(
            voice_config.get("voice")
            or (selected_option.default_voice if selected_option else "")
            or "xiaoyun"
        ).strip()
        language = str(
            voice_config.get("language")
            or (selected_option.language if selected_option else "")
            or "zh-CN"
        ).strip()
        return VoiceOptionsResponse(
            project_id=canonical_id,
            enabled=bool(provider_options),
            has_active_voice=active is not None,
            active_version=(int(active["version"]) if active else None),
            next_version=next_version,
            script=summary,
            planned_timing=timing,
            providers=provider_options,
            default_provider=selected_provider or None,
            default_voice=default_voice or None,
            default_language=language,
            manual_script_required=plan is None and script is None,
        )

    def preflight(
        self, project_id: str, payload: VoicePreflightRequest
    ) -> VoicePreflightResponse:
        prepared = self._prepare(project_id, payload)
        return VoicePreflightResponse(
            project_id=prepared.project_id,
            ready=not prepared.issues and prepared.request is not None,
            intent=payload.intent,
            next_voice_version=prepared.next_version,
            script=prepared.script,
            provider=prepared.provider_option,
            planned_timing=prepared.planned_timing,
            issues=list(prepared.issues),
            warnings=list(prepared.warnings),
            preflight_fingerprint=prepared.fingerprint,
        )

    def submit(
        self,
        project_id: str,
        payload: VoiceGenerateRequest,
        *,
        expected_intent: VoiceIntent,
        correlation_id: str | None,
    ) -> TaskRecord:
        if payload.intent is not expected_intent:
            raise VoiceInputInvalid("voice intent does not match endpoint")
        if not payload.confirm_external_tts_call:
            raise VoiceExternalConfirmationRequired(
                "external TTS confirmation is required"
            )
        request_payload = VoicePreflightRequest.model_validate(
            payload.model_dump(
                exclude={"preflight_fingerprint", "confirm_external_tts_call"}
            )
        )
        prepared = self._prepare(project_id, request_payload)
        self._require_ready(prepared)
        if prepared.fingerprint != payload.preflight_fingerprint:
            raise VoicePreflightStale("voice preflight is stale")
        return self._task_service.submit(
            project_id=prepared.project_id,
            operation=TaskOperation.VOICE_GENERATE,
            target_id=f"voice_v{prepared.next_version:03d}",
            correlation_id=correlation_id,
            callable_=lambda: self._execute(
                prepared.project_id,
                request_payload,
                payload.preflight_fingerprint,
            ),
        )

    def accept_timing(
        self, project_id: str, payload: VoiceTimingAcceptanceRequest
    ) -> VoiceDetail:
        canonical_id = self._project_repository.get_project(project_id).project_id
        with self._task_service.prevent_task_submission():
            self._require_no_active_task(canonical_id)
            try:
                with self._project_lock_manager.project_write(canonical_id):
                    self._require_no_active_task(canonical_id)
                    paths = self._paths(canonical_id)
                    manager = VoiceAssetManager(paths)
                    active = manager.active_version()
                    if (
                        active is None
                        or int(active.get("version") or 0)
                        != payload.expected_voice_version
                    ):
                        raise VoiceTimingAcceptanceNotAllowed(
                            "active voice version changed"
                        )
                    calibration = _mapping(active.get("timing_calibration"))
                    if str(calibration.get("status") or "") != "OUT_OF_TOLERANCE":
                        raise VoiceTimingAcceptanceNotAllowed(
                            "voice timing cannot be accepted"
                        )
                    acceptance = _mapping(active.get("timing_acceptance"))
                    if acceptance.get("accepted") is not True:
                        manager.set_timing_acceptance(
                            payload.expected_voice_version,
                            accepted=True,
                        )
            except ProjectLockBusy as error:
                raise ProjectBusy("project write lock is busy") from error
        return self._postproduction_repository.get_voice(canonical_id)

    def _execute(
        self,
        project_id: str,
        payload: VoicePreflightRequest,
        expected_fingerprint: str,
    ) -> TaskResultReference:
        try:
            prepared = self._prepare(project_id, payload)
        except Exception:
            raise_task_failure(
                "VOICE_PREFLIGHT_STALE",
                "配音生成条件已变化，请重新检查后再次确认。",
            )
        if (
            prepared.fingerprint != expected_fingerprint
            or prepared.request is None
            or prepared.provider is None
        ):
            raise_task_failure(
                "VOICE_PREFLIGHT_STALE",
                "配音生成条件已变化，请重新检查后再次确认。",
            )
        if prepared.issues:
            issue = prepared.issues[0]
            raise_task_failure(issue.code, issue.message)

        try:
            provider = prepared.registry.preflight(
                prepared.request,
                prepared.provider.provider_name,
            )
            entry = VoiceAssetManager(prepared.paths).generate_and_save(
                prepared.request,
                provider,
            )
        except VoiceProviderError:
            raise_task_failure(
                "VOICE_PROVIDER_FAILED",
                "外部 TTS 服务未能完成配音生成；不会自动重试。",
            )
        except (VoiceAssetError, ValueError):
            raise_task_failure(
                "VOICE_GENERATION_FAILED",
                "配音结果未能安全保存；不会自动重试。",
            )

        try:
            checkpoint = ProjectCheckpoint.load(prepared.paths)
            config = checkpoint.data["voice_config"]
            config.update(
                {
                    "enabled": True,
                    "provider": entry.get("provider"),
                    "voice": entry.get("voice") or prepared.request.voice,
                    "language": entry.get("language") or prepared.request.language,
                }
            )
            PostProductionPipeline(checkpoint).mark_component_completed(
                "voice",
                version=int(entry["version"]),
                path=str(entry["audio_path"]),
                created_at=entry.get("created_at"),
            )
        except (ProjectStateError, KeyError, TypeError, ValueError):
            raise_task_failure(
                "VOICE_GENERATION_FAILED",
                "配音已生成，但项目状态未能完整更新；请先检查当前配音版本。",
            )
        version = int(entry["version"])
        return TaskResultReference(
            resource_type="VOICE",
            resource_id=f"voice_v{version:03d}",
            version=version,
        )

    def _prepare(
        self, project_id: str, payload: VoicePreflightRequest
    ) -> _PreparedVoice:
        canonical_id, paths, project_data = self._context(project_id)
        registry = self._registry_factory()
        manifest = self._manifest(paths)
        active = VoiceAssetManager(paths).active_version()
        next_version = self._next_version(manifest)
        voice_config = _mapping(project_data.get("voice_config"))
        provider_name = self._selected_provider_name(
            registry,
            voice_config,
            payload.provider,
        )
        provider_options = self._provider_options(registry)
        provider_option = next(
            (item for item in provider_options if item.provider_id == provider_name),
            None,
        )
        issues: list[VoiceIssue] = []
        warnings = [
            VoiceIssue(
                code="VOICE_EXTERNAL_COST_POSSIBLE",
                message="确认后将调用外部 TTS 服务，可能产生费用。",
            )
        ]

        plan, default_script, settings = self._default_script(paths, active)
        script = payload.script_override or default_script
        if payload.script_override is not None:
            settings = dict(settings)
            settings["script_source"] = (
                "storyboard_edited"
                if plan is not None and payload.script_override != plan.script
                else "compiled_storyboard"
                if plan is not None
                else "manual"
            )
        if script is None or not script.strip():
            issues.append(
                VoiceIssue(
                    code="VOICE_INPUT_INVALID",
                    message="当前 Storyboard 没有配音 Cue，请输入手动配音脚本。",
                )
            )
            script = None
        summary = self._script_summary(script, settings)
        timing = self._planned_timing(settings)

        provider: VoiceProvider | None = None
        request: VoiceGenerationRequest | None = None
        if provider_option is None:
            issues.append(
                VoiceIssue(
                    code="VOICE_PROVIDER_UNAVAILABLE",
                    message="所选 TTS Provider 当前不可用。",
                )
            )
        elif script is not None:
            try:
                request = VoiceGenerationRequest(
                    script=script,
                    voice=payload.voice,
                    language=payload.language,
                    output_format="wav",
                    settings=settings,
                )
                provider = registry.preflight(request, provider_name)
            except (VoiceProviderError, ValueError):
                request = None
                provider = None
                issues.append(
                    VoiceIssue(
                        code="VOICE_PROVIDER_UNAVAILABLE",
                        message="TTS Provider 配置或本次输入尚未就绪；未发送外部请求。",
                    )
                )

        fingerprint = None
        if not issues and request is not None and provider is not None:
            storyboard_path = paths.storyboard_file_path()
            storyboard_identity = (
                _sha256(storyboard_path.read_bytes())
                if storyboard_path.is_file()
                else None
            )
            provider_settings = _mapping(
                _mapping(registry.config.get("providers")).get(provider_name)
            )
            fingerprint = "voice_pf_" + _canonical_hash(
                {
                    "project": canonical_id,
                    "post_production": project_data.get("post_production"),
                    "intent": payload.intent.value,
                    "active_voice_version": (
                        int(active["version"]) if active is not None else None
                    ),
                    "next_voice_version": next_version,
                    "script_sha256": _sha256(request.script.encode("utf-8")),
                    "script_source": request.settings.get("script_source"),
                    "storyboard_identity": storyboard_identity,
                    "planned_timing": timing.model_dump(),
                    "provider": provider_name,
                    "voice": request.voice,
                    "language": request.language,
                    "output_format": request.output_format,
                    "provider_settings": provider_settings,
                }
            )

        return _PreparedVoice(
            project_id=canonical_id,
            paths=paths,
            registry=registry,
            provider=provider,
            provider_option=provider_option,
            request=request,
            script=summary,
            planned_timing=timing,
            next_version=next_version,
            issues=tuple(issues),
            warnings=tuple(warnings),
            fingerprint=fingerprint,
        )

    def _context(
        self, project_id: str
    ) -> tuple[str, ProjectPaths, Mapping[str, Any]]:
        detail = self._project_repository.get_project(project_id)
        paths = self._paths(detail.project_id)
        try:
            payload = json.loads(paths.project_state_path().read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as error:
            raise ProjectDataCorrupt("project checkpoint is unreadable") from error
        if not isinstance(payload, Mapping):
            raise ProjectDataCorrupt("project checkpoint is invalid")
        return detail.project_id, paths, payload

    def _paths(self, project_id: str) -> ProjectPaths:
        return create_project_paths(
            self._project_repository.resolve_project_dir(project_id),
            ensure_directories=False,
        )

    @staticmethod
    def _manifest(paths: ProjectPaths) -> Mapping[str, Any]:
        try:
            manifest = VoiceAssetManager(paths).load_manifest()
        except VoiceAssetError as error:
            raise ProjectDataCorrupt("voice manifest is unreadable") from error
        versions = manifest.get("versions")
        if not isinstance(versions, list):
            raise ProjectDataCorrupt("voice manifest history is invalid")
        return manifest

    @staticmethod
    def _next_version(manifest: Mapping[str, Any]) -> int:
        try:
            return max(
                (int(_mapping(item).get("version") or 0) for item in manifest["versions"]),
                default=0,
            ) + 1
        except (TypeError, ValueError) as error:
            raise ProjectDataCorrupt("voice manifest version is invalid") from error

    @staticmethod
    def _selected_provider_name(
        registry: VoiceProviderRegistry,
        voice_config: Mapping[str, Any],
        requested: str | None,
    ) -> str:
        return str(
            requested
            or voice_config.get("provider")
            or registry.config.get("default_provider")
            or ""
        ).strip().lower()

    def _provider_options(
        self, registry: VoiceProviderRegistry
    ) -> list[VoiceProviderOption]:
        capabilities = self._capability_service.get_capabilities().voice
        provider_settings = _mapping(registry.config.get("providers"))
        options: list[VoiceProviderOption] = []
        for provider in registry.registered_providers():
            provider_id = str(provider.provider_name).strip().lower()
            settings = _mapping(provider_settings.get(provider_id))
            known_available = {
                "xfyun_tts": capabilities.xfyun_tts.available,
                "aliyun_tts": capabilities.aliyun_tts.available,
            }.get(provider_id, True)
            languages = sorted(provider.capabilities.supported_languages)
            language = str(
                settings.get("language")
                or (languages[0] if languages else "zh-CN")
            ).strip()
            options.append(
                VoiceProviderOption(
                    provider_id=provider_id,
                    display_name=_provider_display_name(provider_id),
                    model=str(provider.model_name),
                    default_voice=(
                        str(settings.get("default_voice")).strip()
                        if settings.get("default_voice")
                        else None
                    ),
                    language=language,
                    supported_languages=languages,
                    allowed_voices=[
                        str(item)
                        for item in getattr(provider, "allowed_voices", ())
                        if str(item).strip()
                    ],
                    available=known_available,
                )
            )
        return options

    @staticmethod
    def _default_script(
        paths: ProjectPaths,
        active: Mapping[str, Any] | None,
    ) -> tuple[StoryboardVoiceScript | None, str | None, dict[str, Any]]:
        try:
            plan = load_storyboard_voice_script(paths)
        except VoiceScriptBuilderError as error:
            raise ProjectDataCorrupt("storyboard voice plan is unreadable") from error
        if plan is not None:
            return plan, plan.script, plan.request_settings()
        if active is None:
            return None, None, {"script_source": "manual"}
        version = int(active.get("version") or 0)
        if version <= 0:
            raise ProjectDataCorrupt("active voice version is invalid")
        try:
            script = paths.voice_version_script_path(version).read_text(
                encoding="utf-8"
            )
        except (OSError, UnicodeError) as error:
            raise ProjectDataCorrupt("active voice script is unreadable") from error
        settings = {
            key: active.get(key)
            for key in (
                "script_source",
                "source_storyboard_path",
                "planned_narration_duration",
                "planned_first_voice_start",
                "planned_last_voice_end",
                "planned_voice_span",
                "total_video_duration",
                "cue_count",
            )
        }
        settings["script_source"] = settings.get("script_source") or "manual"
        return None, script, settings

    @staticmethod
    def _script_summary(
        script: str | None, settings: Mapping[str, Any]
    ) -> VoiceScriptSummary | None:
        if script is None:
            return None
        return VoiceScriptSummary(
            source=str(settings.get("script_source") or "manual"),
            text=script,
            character_count=len(script),
            cue_count=max(0, int(settings.get("cue_count") or 0)),
        )

    @staticmethod
    def _planned_timing(settings: Mapping[str, Any]) -> VoicePlannedTiming:
        def number(name: str) -> float | None:
            value = settings.get(name)
            if value is None or isinstance(value, bool):
                return None
            try:
                result = float(value)
            except (TypeError, ValueError):
                return None
            return result if result >= 0 else None

        return VoicePlannedTiming(
            first_start=number("planned_first_voice_start"),
            last_end=number("planned_last_voice_end"),
            span=number("planned_voice_span"),
            narration_duration=number("planned_narration_duration"),
        )

    @staticmethod
    def _require_ready(prepared: _PreparedVoice) -> None:
        if not prepared.issues and prepared.request is not None:
            return
        if any(item.code == "VOICE_INPUT_INVALID" for item in prepared.issues):
            raise VoiceInputInvalid("voice input is invalid")
        raise VoiceProviderUnavailable("voice provider is unavailable")

    def _require_no_active_task(self, project_id: str) -> None:
        if self._task_service.active_for_project(project_id) is not None:
            raise ProjectBusy("project already has an active Web task")

