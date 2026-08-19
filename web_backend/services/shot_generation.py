"""Durable Web adapters for initial Shot generation and manual resume."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from prompt_generator import PromptGenerationError
from shot_generation_workflow import (
    InitialShotGenerationNotAllowed,
    ShotGenerationResumeUnavailable,
    ShotGenerationWorkflowError,
    ShotPromptSafetyRejected,
    ShotPromptSafetyUnavailable,
    generate_initial_shot,
    resume_shot_generation,
)
from storyboard import Storyboard, StoryboardShot, VideoPromptPlan
from task_logger import TaskLogger
from video_generation_request import ProviderSelection
from video_generator import ProviderSubmissionUnknownError
from video_provider import ProviderErrorCode, VideoProviderError
from video_provider_registry import (
    create_default_registry,
    load_provider_credentials_from_env,
    provider_secret_values,
)
from visual_input import (
    first_frame_visual_input,
    none_visual_input,
    reference_asset_visual_input,
)
from web_backend.models.generation import (
    GenerationPreflightRequest,
    GenerationStartRequest,
    ModelSelectionMode,
    ShotGenerationResumeKind,
    ShotGenerationState,
    ShotGenerationStatusResponse,
)
from web_backend.models.tasks import (
    TaskError,
    TaskOperation,
    TaskRecord,
    TaskResultReference,
    TaskStatus,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
)
from web_backend.repositories.reference_asset_repository import (
    ReferenceAssetRepository,
)
from web_backend.repositories.shot_repository import ShotNotFound, normalize_shot_id
from web_backend.services.capabilities import CapabilityService
from web_backend.services.shot_generation_preflight import (
    ShotGenerationPreflightService,
)
from web_backend.services.task_runner import TaskExecutionFailure
from web_backend.services.tasks import TaskService


class ShotGenerationActionError(RuntimeError):
    pass


class PaidCallConfirmationRequired(ShotGenerationActionError):
    pass


class GenerationPreflightStale(ShotGenerationActionError):
    pass


class GenerationNotResumable(ShotGenerationActionError):
    pass


def _task_failure(code: str, message: str, *, retryable: bool = False) -> None:
    raise TaskExecutionFailure(
        TaskError(code=code, message=message, retryable=retryable)
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


class ShotGenerationActionService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        reference_repository: ReferenceAssetRepository,
        preflight_service: ShotGenerationPreflightService,
        task_service: TaskService,
        capability_service: CapabilityService,
    ) -> None:
        self._project_repository = project_repository
        self._reference_repository = reference_repository
        self._preflight_service = preflight_service
        self._task_service = task_service
        self._capability_service = capability_service

    def submit_start(
        self,
        project_id: str,
        shot_id: str,
        payload: GenerationStartRequest,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        if not payload.confirm_paid_call:
            raise PaidCallConfirmationRequired("paid call was not confirmed")
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        canonical_shot_id, _shot_number = normalize_shot_id(shot_id)
        preflight_payload = GenerationPreflightRequest.model_validate(
            payload.model_dump(
                include={"model_selection", "requested_model", "visual_input"}
            )
        )
        current = self._preflight_service.preflight(
            canonical_project_id, canonical_shot_id, preflight_payload
        )
        if (
            not current.ready
            or current.preflight_fingerprint is None
            or current.preflight_fingerprint != payload.preflight_fingerprint
        ):
            raise GenerationPreflightStale("generation preflight changed")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.SHOT_GENERATE,
            target_id=canonical_shot_id,
            correlation_id=correlation_id,
            callable_=lambda: self._run_start(
                canonical_project_id,
                canonical_shot_id,
                preflight_payload,
                payload.preflight_fingerprint,
            ),
        )

    def submit_resume(
        self,
        project_id: str,
        shot_id: str,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        canonical_shot_id, _shot_number = normalize_shot_id(shot_id)
        if not self.status(canonical_project_id, canonical_shot_id).resume_available:
            raise GenerationNotResumable("generation has no safe resume point")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.SHOT_RESUME,
            target_id=canonical_shot_id,
            correlation_id=correlation_id,
            callable_=lambda: self._run_resume(
                canonical_project_id, canonical_shot_id
            ),
        )

    def status(
        self, project_id: str, shot_id: str
    ) -> ShotGenerationStatusResponse:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        canonical_shot_id, shot_number = normalize_shot_id(shot_id)
        project_dir = self._project_repository.resolve_project_dir(
            canonical_project_id
        ).resolve()
        project = self._read_json(project_dir / "project.json", project_dir)
        shots = _mapping(_mapping(project.get("video_generation")).get("shots"))
        entry = _mapping(shots.get(str(shot_number)))
        if not entry:
            raise ShotNotFound("shot was not found")

        status = str(entry.get("status") or "NOT_STARTED").upper()
        phase = str(entry.get("generation_phase") or status).upper()
        version = (
            _positive_int(entry.get("current_generation_version"))
            or _positive_int(entry.get("pending_video_version"))
            or _positive_int(entry.get("active_video_version"))
        )
        submission_unknown = bool(entry.get("submission_unknown")) or phase == "SUBMISSION_UNKNOWN"
        provider_task_id = str(entry.get("provider_task_id") or "").strip()
        file_id = str(entry.get("file_id") or "").strip()
        video_exists = False
        if version is not None:
            video = (
                project_dir
                / "shots"
                / canonical_shot_id
                / f"v{version:03d}"
                / "video.mp4"
            )
            try:
                resolved = video.resolve()
                video_exists = (
                    video.is_file()
                    and video.stat().st_size > 0
                    and resolved.parent.parent.parent == (project_dir / "shots").resolve()
                )
            except OSError:
                video_exists = False

        resume_kind: ShotGenerationResumeKind | None = None
        if status != "WAITING_REVIEW" and not submission_unknown and version is not None:
            if video_exists:
                resume_kind = ShotGenerationResumeKind.FINALIZE_LOCAL_VIDEO
            elif file_id:
                resume_kind = ShotGenerationResumeKind.DOWNLOAD_EXISTING_FILE
            elif provider_task_id:
                resume_kind = ShotGenerationResumeKind.POLL_EXISTING_TASK

        if status == "WAITING_REVIEW":
            public_state = ShotGenerationState.WAITING_REVIEW
        elif submission_unknown:
            public_state = ShotGenerationState.SUBMISSION_UNKNOWN
        elif phase in ShotGenerationState._value2member_map_:
            public_state = ShotGenerationState(phase)
        elif status == "FAILED":
            public_state = ShotGenerationState.FAILED
        else:
            active = self._task_service.active_for_project(canonical_project_id)
            public_state = (
                ShotGenerationState.QUEUED
                if active is not None
                and active.operation
                in {TaskOperation.SHOT_GENERATE, TaskOperation.SHOT_RESUME}
                and active.target_id == canonical_shot_id
                and active.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
                else ShotGenerationState.NOT_STARTED
            )
        return ShotGenerationStatusResponse(
            project_id=canonical_project_id,
            shot_id=canonical_shot_id,
            state=public_state,
            resume_available=resume_kind is not None,
            resume_kind=resume_kind,
            video_version=version,
            provider_submission_known=not submission_unknown,
        )

    def _run_start(
        self,
        project_id: str,
        shot_id: str,
        payload: GenerationPreflightRequest,
        fingerprint: str,
    ) -> TaskResultReference:
        current = self._preflight_service.preflight(project_id, shot_id, payload)
        if (
            not current.ready
            or current.preflight_fingerprint != fingerprint
            or current.resolved is None
        ):
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "生成配置或镜头状态已发生变化。",
            )
        visual_input = self._visual_input(project_id, payload)
        provider_selection = (
            ProviderSelection(
                current.resolved.provider,
                current.resolved.model,
                "manual",
            )
            if payload.model_selection is ModelSelectionMode.MANUAL
            else None
        )
        return self._execute(
            project_id,
            shot_id,
            resume=False,
            visual_input=visual_input,
            provider_selection=provider_selection,
        )

    def _run_resume(self, project_id: str, shot_id: str) -> TaskResultReference:
        if not self.status(project_id, shot_id).resume_available:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前镜头没有可安全继续的生成进度。",
            )
        return self._execute(
            project_id,
            shot_id,
            resume=True,
            visual_input=None,
            provider_selection=None,
        )

    def _execute(
        self,
        project_id: str,
        shot_id: str,
        *,
        resume: bool,
        visual_input: dict[str, Any] | None,
        provider_selection: ProviderSelection | None,
    ) -> TaskResultReference:
        _canonical, shot_number = normalize_shot_id(shot_id)
        try:
            paths = create_project_paths(
                self._project_repository.resolve_project_dir(project_id)
            )
            checkpoint = ProjectCheckpoint.load(paths)
            board = Storyboard.model_validate_json(
                paths.storyboard_file_path().read_text(encoding="utf-8")
            )
            plan = VideoPromptPlan.model_validate_json(
                paths.video_prompts_path().read_text(encoding="utf-8")
            )
            shot = next(item for item in board.shots if item.shot_id == shot_number)
        except (OSError, StopIteration, ValidationError, ProjectStateError):
            _task_failure("PROJECT_DATA_CORRUPT", "镜头生成所需项目数据无法读取。")

        credentials = load_provider_credentials_from_env()
        registry = create_default_registry(credentials)
        deepseek_key = self._capability_service.deepseek_api_key()
        logger = TaskLogger(paths)
        for secret in provider_secret_values(credentials):
            logger.register_secret(secret)
        if deepseek_key:
            logger.register_secret(deepseek_key)
        try:
            output = (
                resume_shot_generation(
                    paths=paths,
                    checkpoint=checkpoint,
                    plan=plan,
                    shot=shot,
                    shot_id=shot_number,
                    deepseek_key=deepseek_key,
                    provider_credentials=credentials,
                    task_logger=logger,
                    provider_registry=registry,
                )
                if resume
                else generate_initial_shot(
                    paths=paths,
                    checkpoint=checkpoint,
                    plan=plan,
                    shot=shot,
                    shot_id=shot_number,
                    visual_input=visual_input or none_visual_input(),
                    deepseek_key=deepseek_key,
                    provider_credentials=credentials,
                    task_logger=logger,
                    provider_selection=provider_selection,
                    provider_registry=registry,
                )
            )
        except ProviderSubmissionUnknownError:
            _task_failure(
                "SUBMISSION_UNKNOWN",
                "无法确认视频生成请求是否已提交，请不要立即重复生成。",
            )
        except (InitialShotGenerationNotAllowed, ShotGenerationResumeUnavailable):
            _task_failure("ACTION_NOT_ALLOWED", "当前镜头状态不允许执行此操作。")
        except ShotPromptSafetyUnavailable:
            _task_failure(
                "PROMPT_SAFETY_UNAVAILABLE",
                "视频提示词安全检查服务尚未配置。",
                retryable=True,
            )
        except ShotPromptSafetyRejected:
            _task_failure(
                "PROMPT_SAFETY_REJECTED",
                "视频提示词未通过安全检查。",
            )
        except PromptGenerationError:
            _task_failure(
                "PROMPT_SAFETY_FAILED",
                "视频提示词安全检查暂时无法完成。",
                retryable=True,
            )
        except VideoProviderError as error:
            code, message, retryable = self._provider_failure(error)
            _task_failure(code, message, retryable=retryable)
        except (OSError, ShotGenerationWorkflowError):
            _task_failure(
                "SHOT_GENERATION_FAILED",
                "镜头生成结果无法安全处理。",
                retryable=False,
            )

        version = _positive_int(checkpoint.shot_checkpoint(shot_number).get("active_video_version"))
        if version is None or not output.is_file():
            _task_failure(
                "SHOT_GENERATION_FAILED",
                "镜头生成结果无法安全处理。",
            )
        return TaskResultReference(
            resource_type="SHOT_VIDEO",
            resource_id=shot_id,
            version=version,
        )

    def _visual_input(
        self, project_id: str, payload: GenerationPreflightRequest
    ) -> dict[str, Any]:
        mode = payload.visual_input.mode.value
        if mode == "none":
            return none_visual_input()
        asset = self._reference_repository.asset(
            project_id, payload.visual_input.asset_ids[0]
        ).core_record()
        return (
            reference_asset_visual_input(asset)
            if mode == "reference_asset"
            else first_frame_visual_input(asset)
        )

    @staticmethod
    def _provider_failure(error: VideoProviderError) -> tuple[str, str, bool]:
        mapping = {
            ProviderErrorCode.AUTH_ERROR: (
                "VIDEO_PROVIDER_AUTH_ERROR",
                "视频生成服务认证失败。",
            ),
            ProviderErrorCode.QUOTA_ERROR: (
                "VIDEO_PROVIDER_QUOTA_ERROR",
                "视频生成服务额度不足。",
            ),
            ProviderErrorCode.RATE_LIMIT: (
                "VIDEO_PROVIDER_RATE_LIMIT",
                "视频生成服务请求过于频繁。",
            ),
            ProviderErrorCode.INVALID_REQUEST: (
                "VIDEO_PROVIDER_INVALID_REQUEST",
                "视频生成请求未被服务接受。",
            ),
            ProviderErrorCode.TASK_FAILED: (
                "VIDEO_PROVIDER_TASK_FAILED",
                "视频生成任务执行失败。",
            ),
            ProviderErrorCode.DOWNLOAD_FAILED: (
                "VIDEO_DOWNLOAD_FAILED",
                "视频结果下载失败，可稍后继续。",
            ),
        }
        code, message = mapping.get(
            error.code,
            ("VIDEO_PROVIDER_FAILED", "视频生成服务暂时无法完成请求。"),
        )
        return code, message, bool(error.retryable)

    @staticmethod
    def _read_json(path: Path, project_dir: Path) -> Mapping[str, Any]:
        try:
            resolved = path.resolve()
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
