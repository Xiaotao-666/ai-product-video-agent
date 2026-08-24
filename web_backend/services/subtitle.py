"""Thin synchronous Web adapter over the frozen Subtitle Core."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from post_production import PostProductionPipeline
from project_manager import ProjectPaths, create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from subtitle_assets import SubtitleAssetError, SubtitleAssetManager
from subtitle_generation import (
    ActiveVoiceRequired,
    ActiveVoiceSubtitleSource,
    NarrationSubtitleNotApplicable,
    generate_subtitle_for_project,
    load_active_voice_subtitle_source,
    narration_caption_enabled,
)
from subtitle_provider import SubtitleProviderError
from subtitle_provider_registry import (
    SubtitleProviderRegistry,
    build_subtitle_provider_registry,
)
from voice_assets import VoiceAssetError
from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.postproduction import SubtitleDetail
from web_backend.models.subtitle import (
    SubtitleGenerateRequest,
    SubtitleIssue,
    SubtitleOptionsResponse,
    SubtitleSourceSummary,
    SubtitleSourceType,
)
from web_backend.repositories.postproduction_repository import (
    PostProductionRepository,
)
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.services.projects import ProjectBusy
from web_backend.services.tasks import TaskService


class SubtitleSourceUnavailable(RuntimeError):
    pass


class SubtitleSourceInvalid(RuntimeError):
    pass


class SubtitleNotApplicable(RuntimeError):
    pass


class ActiveVoiceRequiredForSubtitle(RuntimeError):
    pass


class SubtitleSourceChanged(RuntimeError):
    pass


class SubtitleGenerationFailed(RuntimeError):
    pass


class SubtitleActionNotAllowed(RuntimeError):
    pass


RegistryFactory = Callable[[], SubtitleProviderRegistry]


@dataclass(frozen=True)
class _PreparedSubtitle:
    project_id: str
    paths: ProjectPaths
    active_version: int | None
    next_version: int
    applicable: bool
    stale: bool
    stale_reason: str | None
    source: SubtitleSourceSummary | None
    issues: tuple[SubtitleIssue, ...]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


class SubtitleWebService:
    """Inspect and generate local Subtitle bundles without duplicating Core."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        postproduction_repository: PostProductionRepository,
        task_service: TaskService,
        project_lock_manager: ProjectLockManager,
        *,
        registry_factory: RegistryFactory = build_subtitle_provider_registry,
    ) -> None:
        self._project_repository = project_repository
        self._postproduction_repository = postproduction_repository
        self._task_service = task_service
        self._project_lock_manager = project_lock_manager
        self._registry_factory = registry_factory

    def options(self, project_id: str) -> SubtitleOptionsResponse:
        prepared = self._prepare(project_id)
        return SubtitleOptionsResponse(
            project_id=prepared.project_id,
            applicable=prepared.applicable,
            ready=not prepared.issues and prepared.source is not None,
            stale=prepared.stale,
            stale_reason=prepared.stale_reason,
            active_version=prepared.active_version,
            next_version=prepared.next_version,
            source=prepared.source,
            issues=list(prepared.issues),
        )

    def generate(
        self,
        project_id: str,
        payload: SubtitleGenerateRequest,
        *,
        regenerate: bool,
    ) -> SubtitleDetail:
        canonical_id = self._project_repository.get_project(project_id).project_id
        self._require_no_active_task(canonical_id)
        try:
            with self._project_lock_manager.project_write(canonical_id):
                self._require_no_active_task(canonical_id)
                prepared = self._prepare(canonical_id)
                self._validate_action(prepared, payload, regenerate=regenerate)
                manager = SubtitleAssetManager(prepared.paths)
                try:
                    entry = generate_subtitle_for_project(
                        manager,
                        self._registry_factory(),
                    )
                except NarrationSubtitleNotApplicable as error:
                    raise SubtitleNotApplicable(
                        "narration captions are not applicable"
                    ) from error
                except ActiveVoiceRequired as error:
                    raise ActiveVoiceRequiredForSubtitle(
                        "active Voice is required"
                    ) from error
                except (SubtitleProviderError, SubtitleAssetError, ValueError) as error:
                    raise SubtitleGenerationFailed("Core subtitle generation failed") from error
                try:
                    checkpoint = ProjectCheckpoint.load(prepared.paths)
                    PostProductionPipeline(checkpoint).mark_component_completed(
                        "subtitle",
                        version=int(entry["version"]),
                        path=str(entry["subtitle_path"]),
                        created_at=entry.get("created_at"),
                    )
                except (ProjectStateError, KeyError, TypeError, ValueError) as error:
                    raise SubtitleGenerationFailed(
                        "subtitle checkpoint update failed"
                    ) from error
        except ProjectLockBusy as error:
            raise ProjectBusy("project write lock is busy") from error
        return self._postproduction_repository.get_subtitle(canonical_id)

    def _prepare(self, project_id: str) -> _PreparedSubtitle:
        project = self._project_repository.get_project(project_id)
        canonical_id = project.project_id
        paths = create_project_paths(
            self._project_repository.resolve_project_dir(canonical_id)
        )
        manager = SubtitleAssetManager(paths)
        try:
            manifest = manager.load_manifest()
            active = manager.active_version()
            active_version = int(active["version"]) if active else None
            next_version = self._next_version(manifest)
        except (SubtitleAssetError, KeyError, TypeError, ValueError) as error:
            raise SubtitleSourceInvalid("subtitle manifest is invalid") from error

        issues: list[SubtitleIssue] = []
        assembly = project.workflow.stages.assembly
        if assembly.status != "COMPLETED" or assembly.needs_update:
            issues.append(
                SubtitleIssue(
                    code="PROJECT_NOT_READY",
                    message="当前项目尚未完成可用的 Assembly。",
                )
            )

        source: SubtitleSourceSummary | None = None
        applicable = True
        try:
            applicable = narration_caption_enabled(paths)
            if not applicable:
                issues.append(
                    SubtitleIssue(
                        code="NARRATION_DISABLED",
                        message="当前项目未启用旁白，不适用旁白字幕。",
                    )
                )
            else:
                source = self._source_summary(
                    load_active_voice_subtitle_source(paths),
                    self._registry_factory(),
                )
        except ActiveVoiceRequired:
            issues.append(
                SubtitleIssue(
                    code="ACTIVE_VOICE_REQUIRED",
                    message="请先完成并激活一个 Voice 版本。",
                )
            )
        except (SubtitleAssetError, SubtitleProviderError, VoiceAssetError, ValueError):
            issues.append(
                SubtitleIssue(
                    code="SUBTITLE_SOURCE_INVALID",
                    message="当前字幕来源数据无法安全读取。",
                )
            )

        stale, stale_reason = self._stale_state(active, source)

        return _PreparedSubtitle(
            project_id=canonical_id,
            paths=paths,
            active_version=active_version,
            next_version=next_version,
            applicable=applicable,
            stale=stale,
            stale_reason=stale_reason,
            source=source,
            issues=tuple(issues),
        )

    @staticmethod
    def _source_summary(
        source: ActiveVoiceSubtitleSource,
        registry: SubtitleProviderRegistry,
    ) -> SubtitleSourceSummary:
        result = registry.generate_subtitle(source.request)
        return SubtitleSourceSummary(
            type=SubtitleSourceType.ACTIVE_VOICE,
            label=f"Voice v{source.voice_version:03d}",
            cue_count=len(result.cues),
            timing_source=str(result.metadata.get("timing_source") or "unknown"),
            voice_version=source.voice_version,
            semantic_type="NARRATION_CAPTION",
            script=source.request.script,
            actual_audio_duration=source.actual_audio_duration,
            voice_track_start=source.voice_track_start,
            actual_voice_end=source.actual_voice_end,
            cue_level_alignment=False,
        )

    @staticmethod
    def _stale_state(
        active: Mapping[str, Any] | None,
        source: SubtitleSourceSummary | None,
    ) -> tuple[bool, str | None]:
        if active is None:
            return False, None
        semantic_type = str(active.get("semantic_type") or "").strip()
        raw_voice_version = active.get("source_voice_version")
        try:
            subtitle_voice_version = (
                None
                if raw_voice_version is None or isinstance(raw_voice_version, bool)
                else int(raw_voice_version)
            )
        except (TypeError, ValueError):
            subtitle_voice_version = None
        if not semantic_type:
            semantic_type = (
                "NARRATION_CAPTION"
                if subtitle_voice_version is not None
                else "LEGACY_SCREEN_TEXT"
                if active.get("source") == "compiled_storyboard"
                else ""
            )
        if semantic_type == "LEGACY_SCREEN_TEXT":
            return True, "LEGACY_SCREEN_TEXT"
        if (
            source is not None
            and subtitle_voice_version != source.voice_version
        ):
            return True, "VOICE_VERSION_CHANGED"
        return False, None

    @staticmethod
    def _next_version(manifest: Mapping[str, Any]) -> int:
        versions = manifest.get("versions")
        if not isinstance(versions, list):
            raise SubtitleSourceInvalid("subtitle versions are invalid")
        parsed: list[int] = []
        for raw in versions:
            value = _mapping(raw).get("version")
            if isinstance(value, bool):
                raise SubtitleSourceInvalid("subtitle version is invalid")
            try:
                version = int(value)
            except (TypeError, ValueError) as error:
                raise SubtitleSourceInvalid("subtitle version is invalid") from error
            if version <= 0:
                raise SubtitleSourceInvalid("subtitle version is invalid")
            parsed.append(version)
        if len(parsed) != len(set(parsed)):
            raise SubtitleSourceInvalid("subtitle versions are duplicated")
        return max(parsed, default=0) + 1

    @staticmethod
    def _validate_action(
        prepared: _PreparedSubtitle,
        payload: SubtitleGenerateRequest,
        *,
        regenerate: bool,
    ) -> None:
        if not prepared.applicable:
            raise SubtitleNotApplicable("narration captions are not applicable")
        if prepared.issues or prepared.source is None:
            if any(
                issue.code == "ACTIVE_VOICE_REQUIRED"
                for issue in prepared.issues
            ):
                raise ActiveVoiceRequiredForSubtitle("active Voice is required")
            if any(
                issue.code == "SUBTITLE_SOURCE_UNAVAILABLE"
                for issue in prepared.issues
            ):
                raise SubtitleSourceUnavailable("subtitle source is unavailable")
            if any(issue.code == "SUBTITLE_SOURCE_INVALID" for issue in prepared.issues):
                raise SubtitleSourceInvalid("subtitle source is invalid")
            raise SubtitleActionNotAllowed("project is not ready")
        if payload.expected_voice_version != prepared.source.voice_version:
            raise SubtitleSourceChanged("active Voice version changed")
        if regenerate:
            if prepared.active_version is None:
                raise SubtitleActionNotAllowed("no active subtitle to regenerate")
        elif prepared.active_version is not None:
            raise SubtitleActionNotAllowed("active subtitle already exists")
        if (
            payload.expected_active_version != prepared.active_version
            or payload.expected_next_version != prepared.next_version
        ):
            raise SubtitleActionNotAllowed("subtitle version state changed")

    def _require_no_active_task(self, project_id: str) -> None:
        if self._task_service.active_for_project(project_id) is not None:
            raise ProjectBusy("project already has an active Web task")
