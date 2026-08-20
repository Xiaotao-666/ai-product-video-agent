"""Durable, draft-only Web adapter for one-Shot AI Prompt revision."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, NoReturn

from pydantic import ValidationError

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
from web_backend.models.prompt_revision import (
    PromptRevisionDraftRequest,
    PromptRevisionDraftResponse,
    StoredPromptRevisionDraft,
)
from web_backend.models.tasks import (
    TaskError,
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
from web_backend.services.task_runner import TaskExecutionFailure
from web_backend.services.tasks import TaskService


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
    fingerprint: str


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


def _task_failure(
    code: str,
    message: str,
    *,
    retryable: bool = False,
) -> NoReturn:
    raise TaskExecutionFailure(
        TaskError(code=code, message=message, retryable=retryable)
    )


class PromptRevisionDraftService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        reference_repository: ReferenceAssetRepository,
        draft_repository: PromptRevisionDraftRepository,
        task_service: TaskService,
        capability_service: CapabilityService,
    ) -> None:
        self._project_repository = project_repository
        self._reference_repository = reference_repository
        self._draft_repository = draft_repository
        self._task_service = task_service
        self._capability_service = capability_service

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
                source.fingerprint,
                payload.feedback,
            ),
            allow_parallel_targets=True,
            acquire_project_lock=False,
        )

    def get(self, project_id: str, shot_id: str) -> PromptRevisionDraftResponse:
        source = self._load_source(project_id, shot_id)
        draft = self._draft_repository.get(source.project_id, source.shot_id)
        if draft.base_fingerprint != source.fingerprint:
            raise PromptRevisionDraftNotFound("draft base is stale")
        return draft.public_response()

    def _run(
        self,
        project_id: str,
        shot_id: str,
        expected_fingerprint: str,
        feedback: str,
    ) -> TaskResultReference:
        try:
            source = self._load_source(project_id, shot_id)
        except (ProjectDataCorrupt, ShotNotFound, PromptRevisionNotAllowed):
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前镜头Prompt状态已发生变化。",
            )
        if source.fingerprint != expected_fingerprint:
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
        if current.fingerprint != expected_fingerprint:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前镜头Prompt状态已发生变化。",
            )

        try:
            draft_record = StoredPromptRevisionDraft(
                project_id=source.project_id,
                shot_id=source.shot_id,
                base_prompt_version=source.prompt_version,
                base_fingerprint=source.fingerprint,
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
        fingerprint_payload = {
            "project_id": canonical_project_id,
            "shot_id": canonical_shot_id,
            "prompt_version": prompt_version,
            "prompt": prompt,
            "request": request.model_dump(mode="json"),
            "brief": brief.model_dump(mode="json"),
            "shot": shot.model_dump(mode="json"),
            "reference_asset_count": reference_asset_count,
        }
        fingerprint = hashlib.sha256(
            json.dumps(
                fingerprint_payload,
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
            fingerprint=fingerprint,
        )

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
