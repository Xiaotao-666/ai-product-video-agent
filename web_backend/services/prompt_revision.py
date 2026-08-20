"""Durable, draft-only Web adapter for one-Shot AI Prompt revision."""

from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from project_manager import ProjectDirectoryError, ProjectPaths, create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from prompt_generator import (
    ProductVideoRequest,
    PromptGenerationError,
    StructuredOutputExhaustedError,
)
from storyboard import (
    CreativeBrief,
    Storyboard,
    StoryboardShot,
    VideoPromptStructureError,
    generate_prompt_revision_draft,
)
from shot_review import ShotReviewError, adopt_prompt_revision_draft
from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.prompt_revision import (
    PromptRevisionDraftAdoptResponse,
    PromptRevisionDraftRequest,
    PromptRevisionDraftResponse,
    StoredPromptRevisionDraft,
)
from web_backend.models.tasks import (
    TaskOperation,
    TaskRecord,
    TaskResultReference,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
)
from web_backend.repositories.prompt_revision_repository import (
    PromptRevisionDraftNotFound,
    PromptRevisionDraftRepository,
)
from web_backend.repositories.reference_asset_repository import (
    ReferenceAssetRepository,
)
from web_backend.repositories.shot_repository import ShotNotFound, normalize_shot_id
from web_backend.services.capabilities import CapabilityService
from web_backend.services.planning_actions import CapabilityUnavailable
from web_backend.services.task_failures import raise_task_failure as _task_failure
from web_backend.services.tasks import TaskService
from web_backend.services.projects import ProjectBusy


class PromptRevisionNotAllowed(RuntimeError):
    pass


@dataclass(frozen=True)
class _PromptRevisionSource:
    project_id: str
    shot_id: str
    prompt_version: int
    prompt: str
    request: ProductVideoRequest
    brief: CreativeBrief
    shot: StoryboardShot
    reference_asset_count: int
    content_fingerprint: str
    state_fingerprint: str
    shot_status: str
    candidate_status: str
    generation_phase: str
    submission_unknown: bool
    active_prompt_version: int | None
    approved_prompt_version: int | None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


class PromptRevisionDraftService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        reference_repository: ReferenceAssetRepository,
        draft_repository: PromptRevisionDraftRepository,
        task_service: TaskService,
        capability_service: CapabilityService,
        project_lock_manager: ProjectLockManager,
    ) -> None:
        self._project_repository = project_repository
        self._reference_repository = reference_repository
        self._draft_repository = draft_repository
        self._task_service = task_service
        self._capability_service = capability_service
        self._project_lock_manager = project_lock_manager

    def submit(
        self,
        project_id: str,
        shot_id: str,
        payload: PromptRevisionDraftRequest,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        source = self._load_source(project_id, shot_id)
        if not self._capability_service.deepseek_api_key():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=source.project_id,
            operation=TaskOperation.SHOT_PROMPT_REVISION_DRAFT,
            target_id=source.shot_id,
            correlation_id=correlation_id,
            callable_=lambda: self._run(
                source.project_id,
                source.shot_id,
                source.content_fingerprint,
                source.state_fingerprint,
                payload.feedback,
            ),
            allow_parallel_targets=True,
            acquire_project_lock=False,
        )

    def get(self, project_id: str, shot_id: str) -> PromptRevisionDraftResponse:
        source = self._load_source(project_id, shot_id)
        draft = self._draft_repository.get(source.project_id, source.shot_id)
        try:
            self._require_read_compatible(source, draft)
        except PromptRevisionNotAllowed as error:
            raise PromptRevisionDraftNotFound("draft base is stale") from error
        return draft.public_response()

    def adopt(
        self,
        project_id: str,
        shot_id: str,
    ) -> PromptRevisionDraftAdoptResponse:
        source = self._load_source(project_id, shot_id)
        draft = self._draft_repository.get(source.project_id, source.shot_id)
        self._require_adopt_allowed(source, draft, strict_state=True)

        with self._task_service.prevent_task_submission():
            self._require_no_active_task(source.project_id)
            source = self._load_source(source.project_id, source.shot_id)
            draft = self._draft_repository.get(source.project_id, source.shot_id)
            self._require_adopt_allowed(source, draft, strict_state=True)
            try:
                with self._project_lock_manager.project_write(source.project_id):
                    self._require_no_active_task(source.project_id)
                    locked_source = self._load_source(
                        source.project_id,
                        source.shot_id,
                    )
                    locked_draft = self._draft_repository.get(
                        source.project_id,
                        source.shot_id,
                    )
                    self._require_adopt_allowed(
                        locked_source,
                        locked_draft,
                        strict_state=True,
                    )
                    paths = create_project_paths(
                        self._project_repository.resolve_project_dir(
                            source.project_id
                        ),
                        ensure_directories=False,
                    )
                    try:
                        checkpoint = ProjectCheckpoint.load(paths)
                        plan = self._load_prompt_plan(paths.video_prompts_path())
                    except (ShotReviewError, ProjectStateError) as error:
                        raise PromptRevisionNotAllowed(
                            "Prompt revision draft adoption is not allowed"
                        ) from error
                    except (
                        OSError,
                        UnicodeError,
                        ValidationError,
                        ProjectDirectoryError,
                    ) as error:
                        raise ProjectDataCorrupt(
                            "project Prompt data is unreadable"
                        ) from error
                    project_snapshot = copy.deepcopy(checkpoint.data)
                    prompt_plan_snapshot = plan.model_dump()
                    try:
                        payload = adopt_prompt_revision_draft(
                            paths=paths,
                            checkpoint=checkpoint,
                            plan=plan,
                            shot_id=locked_source.shot.shot_id,
                            base_prompt_version=locked_draft.base_prompt_version,
                            original_prompt=locked_draft.original_prompt,
                            draft_prompt=locked_draft.draft_prompt,
                            feedback=locked_draft.feedback,
                            task_logger=None,
                            draft_created_at=locked_draft.created_at.isoformat(),
                        )
                    except Exception as error:
                        self._restore_adopt_snapshots(
                            paths,
                            checkpoint,
                            project_snapshot,
                            prompt_plan_snapshot,
                        )
                        if isinstance(error, (ShotReviewError, ProjectStateError)):
                            raise PromptRevisionNotAllowed(
                                "Prompt revision draft adoption is not allowed"
                            ) from error
                        if isinstance(
                            error,
                            (
                                OSError,
                                UnicodeError,
                                ValidationError,
                                ProjectDirectoryError,
                            ),
                        ):
                            raise ProjectDataCorrupt(
                                "project Prompt data is unreadable"
                            ) from error
                        raise
            except ProjectLockBusy as error:
                raise ProjectBusy("project write lock is busy") from error

        current_entry = checkpoint.shot_checkpoint(locked_source.shot.shot_id)
        return PromptRevisionDraftAdoptResponse(
            project_id=locked_source.project_id,
            shot_id=locked_source.shot_id,
            prompt_version=int(payload["version"]),
            parent_version=int(payload["parent_version"]),
            source="ai_revision",
            active_prompt_version=int(current_entry["active_prompt_version"]),
            approved_prompt_version=_positive_int(
                current_entry.get("approved_prompt_version")
            ),
            created_at=str(payload["created_at"]),
        )

    @staticmethod
    def _restore_adopt_snapshots(
        paths: ProjectPaths,
        checkpoint: ProjectCheckpoint,
        project_snapshot: dict[str, Any],
        prompt_plan_snapshot: dict[str, Any],
    ) -> None:
        """Restore both canonical files when a synchronous adoption fails."""

        try:
            paths.save_json(paths.video_prompts_path(), prompt_plan_snapshot)
            paths.save_json(paths.project_state_path(), project_snapshot)
        except (OSError, UnicodeError, ProjectDirectoryError) as error:
            raise ProjectDataCorrupt(
                "project Prompt adoption rollback failed"
            ) from error
        checkpoint.data.clear()
        checkpoint.data.update(copy.deepcopy(project_snapshot))

    def _run(
        self,
        project_id: str,
        shot_id: str,
        expected_content_fingerprint: str,
        expected_state_fingerprint: str,
        feedback: str,
    ) -> TaskResultReference:
        try:
            source = self._load_source(project_id, shot_id)
        except (ProjectDataCorrupt, ShotNotFound, PromptRevisionNotAllowed):
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前镜头Prompt状态已发生变化。",
            )
        if (
            source.content_fingerprint != expected_content_fingerprint
            or source.state_fingerprint != expected_state_fingerprint
        ):
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前镜头Prompt状态已发生变化。",
            )

        api_key = self._capability_service.deepseek_api_key()
        if not api_key:
            _task_failure(
                "CAPABILITY_UNAVAILABLE",
                "AI Prompt修改服务尚未配置。",
                retryable=True,
            )
        try:
            result = generate_prompt_revision_draft(
                request=source.request,
                brief=source.brief,
                shot=source.shot,
                current_prompt=source.prompt,
                current_prompt_version=source.prompt_version,
                feedback=feedback,
                api_key=api_key,
                task_logger=None,
                reference_asset_context={
                    "available": source.reference_asset_count > 0,
                    "asset_count": source.reference_asset_count,
                },
            )
        except (StructuredOutputExhaustedError, VideoPromptStructureError):
            _task_failure(
                "PROMPT_REVISION_OUTPUT_INVALID",
                "AI返回的Prompt修改建议未通过校验，可以重新尝试。",
                retryable=True,
            )
        except PromptGenerationError:
            _task_failure(
                "PROVIDER_FAILED",
                "AI Prompt修改服务暂时不可用，请稍后重试。",
                retryable=True,
            )

        try:
            current = self._load_source(project_id, shot_id)
        except (ProjectDataCorrupt, ShotNotFound, PromptRevisionNotAllowed):
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前镜头Prompt状态已发生变化。",
            )
        if (
            current.content_fingerprint != expected_content_fingerprint
            or current.state_fingerprint != expected_state_fingerprint
        ):
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前镜头Prompt状态已发生变化。",
            )

        try:
            draft_record = StoredPromptRevisionDraft(
                project_id=source.project_id,
                shot_id=source.shot_id,
                base_prompt_version=source.prompt_version,
                fingerprint_schema_version=2,
                content_fingerprint=source.content_fingerprint,
                state_fingerprint=source.state_fingerprint,
                original_prompt=source.prompt,
                draft_prompt=result.prompt,
                feedback=feedback,
                created_at=datetime.now(timezone.utc),
            )
        except ValidationError:
            _task_failure(
                "PROMPT_REVISION_OUTPUT_INVALID",
                "AI返回的Prompt修改建议未通过校验，可以重新尝试。",
                retryable=True,
            )
        self._draft_repository.save(draft_record)
        return TaskResultReference(
            resource_type="PROMPT_REVISION_DRAFT",
            resource_id=source.shot_id,
        )

    def _load_source(self, project_id: str, shot_id: str) -> _PromptRevisionSource:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        canonical_shot_id, shot_number = normalize_shot_id(shot_id)
        project_dir = self._project_repository.resolve_project_dir(
            canonical_project_id
        ).resolve()
        try:
            project_data = self._read_json(project_dir, "project.json")
            request = ProductVideoRequest.model_validate(project_data["request"])
            brief = CreativeBrief.model_validate(
                self._read_json(project_dir, "concepts", "creative_brief.json")
            )
            board = Storyboard.model_validate(
                self._read_json(project_dir, "storyboard", "storyboard.json")
            )
            shot = next(item for item in board.shots if item.shot_id == shot_number)
        except (KeyError, StopIteration, ValidationError) as error:
            raise ProjectDataCorrupt("Prompt revision source is invalid") from error

        entries = _mapping(_mapping(project_data.get("video_generation")).get("shots"))
        entry = _mapping(entries.get(str(shot_number)))
        if not entry:
            raise ShotNotFound("shot was not found")
        candidate = _mapping(entry.get("candidate"))
        candidate_active = str(candidate.get("status") or "NONE").upper() != "NONE"
        prompt_version = (
            _positive_int(candidate.get("prompt_version"))
            if candidate_active
            else None
        ) or _positive_int(entry.get("active_prompt_version"))
        if prompt_version is None:
            raise PromptRevisionNotAllowed("shot has no active Prompt")
        prompt_record = next(
            (
                _mapping(item)
                for item in entry.get("prompt_versions", [])
                if _positive_int(_mapping(item).get("version")) == prompt_version
            ),
            {},
        )
        prompt = str(prompt_record.get("prompt") or "").strip()
        if not prompt:
            raise PromptRevisionNotAllowed("active Prompt is unavailable")
        reference_asset_count = len(
            self._reference_repository.list_assets(canonical_project_id).assets
        )
        shot_status = str(entry.get("status") or "NOT_STARTED").upper()
        candidate_status = str(candidate.get("status") or "NONE").upper()
        active_state = candidate if candidate_status != "NONE" else entry
        generation_phase = str(
            active_state.get("generation_phase") or ""
        ).upper()
        submission_unknown = bool(active_state.get("submission_unknown"))
        active_prompt_version = _positive_int(entry.get("active_prompt_version"))
        approved_prompt_version = _positive_int(
            entry.get("approved_prompt_version")
        )
        content_fingerprint_payload = {
            "project_id": canonical_project_id,
            "shot_id": canonical_shot_id,
            "prompt_version": prompt_version,
            "prompt": prompt,
            "request": request.model_dump(mode="json"),
            "brief": brief.model_dump(mode="json"),
            "shot": shot.model_dump(mode="json"),
            "reference_asset_count": reference_asset_count,
        }
        content_fingerprint = hashlib.sha256(
            json.dumps(
                content_fingerprint_payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        state_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "shot_status": shot_status,
                    "candidate_status": candidate_status,
                    "generation_phase": generation_phase,
                    "submission_unknown": submission_unknown,
                    "active_prompt_version": active_prompt_version,
                    "approved_prompt_version": approved_prompt_version,
                },
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        return _PromptRevisionSource(
            project_id=canonical_project_id,
            shot_id=canonical_shot_id,
            prompt_version=prompt_version,
            prompt=prompt,
            request=request,
            brief=brief,
            shot=shot,
            reference_asset_count=reference_asset_count,
            content_fingerprint=content_fingerprint,
            state_fingerprint=state_fingerprint,
            shot_status=shot_status,
            candidate_status=candidate_status,
            generation_phase=generation_phase,
            submission_unknown=submission_unknown,
            active_prompt_version=active_prompt_version,
            approved_prompt_version=approved_prompt_version,
        )

    def _require_adopt_allowed(
        self,
        source: _PromptRevisionSource,
        draft: StoredPromptRevisionDraft,
        *,
        strict_state: bool,
    ) -> None:
        self._require_read_compatible(source, draft)
        if (
            strict_state
            and draft.fingerprint_schema_version >= 2
            and draft.state_fingerprint != source.state_fingerprint
        ):
            raise PromptRevisionNotAllowed("Prompt revision draft state is stale")

    def _require_read_compatible(
        self,
        source: _PromptRevisionSource,
        draft: StoredPromptRevisionDraft,
    ) -> None:
        stored_content_fingerprint = (
            draft.base_fingerprint
            if draft.fingerprint_schema_version == 1
            else draft.content_fingerprint
        )
        if (
            draft.project_id != source.project_id
            or draft.shot_id != source.shot_id
            or draft.base_prompt_version != source.prompt_version
            or stored_content_fingerprint != source.content_fingerprint
            or draft.original_prompt.strip() != source.prompt
        ):
            raise PromptRevisionNotAllowed("Prompt revision draft is stale")
        if (
            source.shot_status == "GENERATING"
            or source.candidate_status in {"EDITING", "GENERATING"}
            or source.submission_unknown
            or source.generation_phase
            in {
                "PREPARING",
                "SUBMITTING",
                "PROVIDER_RUNNING",
                "READY_TO_DOWNLOAD",
                "DOWNLOADING",
                "LOCAL_FINALIZING",
                "SUBMISSION_UNKNOWN",
            }
        ):
            raise PromptRevisionNotAllowed("Shot state does not allow adoption")

    def _require_no_active_task(self, project_id: str) -> None:
        if self._task_service.active_for_project(project_id) is not None:
            raise ProjectBusy("project already has an active Web task")

    @staticmethod
    def _load_prompt_plan(path: Path):
        from storyboard import VideoPromptPlan

        try:
            return VideoPromptPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError) as error:
            raise ProjectDataCorrupt("Video Prompt plan is unreadable") from error

    @staticmethod
    def _read_json(project_dir: Path, *parts: str) -> Mapping[str, Any]:
        path = project_dir.joinpath(*parts)
        try:
            resolved = path.resolve(strict=True)
            resolved.relative_to(project_dir)
            if path.is_symlink() or not path.is_file():
                raise OSError("unsafe project data")
            with resolved.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
            raise ProjectDataCorrupt("project data is unreadable") from error
        if not isinstance(payload, Mapping):
            raise ProjectDataCorrupt("project data is invalid")
        return payload
