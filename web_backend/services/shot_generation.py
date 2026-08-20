"""Durable Web adapters for initial Shot generation and manual resume."""

from __future__ import annotations

import json
from copy import deepcopy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from project_manager import ProjectPaths, create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from prompt_generator import PromptGenerationError
from shot_storage import ShotStorageError, read_bundle_json, validate_bundle
from shot_generation_workflow import (
    CurrentPromptRegenerationNotAllowed,
    InitialShotGenerationNotAllowed,
    ManualPromptRegenerationNotAllowed,
    SelectedPromptVersionGenerationNotAllowed,
    ShotGenerationResumeUnavailable,
    ShotGenerationWorkflowError,
    ShotPromptSafetyRejected,
    ShotPromptSafetyUnavailable,
    generate_initial_shot,
    regenerate_shot_with_current_prompt,
    regenerate_shot_with_manual_prompt,
    regenerate_shot_with_prompt_version,
    resume_shot_generation,
)
from storyboard import CreativeBrief, Storyboard, StoryboardShot, VideoPromptPlan
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
    GenerationIntent,
    GenerationPreflightRequest,
    GenerationStartRequest,
    ModelSelectionMode,
    ShotGenerationResumeKind,
    ShotGenerationState,
    ShotGenerationStatusResponse,
)
from web_backend.models.tasks import (
    TaskOperation,
    TaskRecord,
    TaskResultReference,
    TaskStatus,
)
from web_backend.locking import ProjectLockManager
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
from web_backend.services.task_failures import raise_task_failure as _task_failure
from web_backend.services.tasks import TaskService


class ShotGenerationActionError(RuntimeError):
    pass


class PaidCallConfirmationRequired(ShotGenerationActionError):
    pass


class GenerationPreflightStale(ShotGenerationActionError):
    pass


class GenerationNotResumable(ShotGenerationActionError):
    pass


class _ShotScopedProjectPaths:
    """Merge only one Shot when parallel tasks persist shared project.json."""

    def __init__(
        self,
        base: ProjectPaths,
        project_id: str,
        shot_id: int,
        lock_manager: ProjectLockManager,
    ) -> None:
        self._base = base
        self._project_id = project_id
        self._shot_key = str(int(shot_id))
        self._lock_manager = lock_manager

    def __getattr__(self, name: str) -> Any:
        return getattr(self._base, name)

    def save_json(self, path: str | Path, data: Any) -> Path:
        target = self._base.ensure_within_project(path)
        if target.resolve(strict=False) != self._base.project_state_path().resolve(
            strict=False
        ):
            return self._base.save_json(target, data)
        if not isinstance(data, Mapping):
            raise ProjectStateError("project.json payload is invalid")
        incoming_video = _mapping(data.get("video_generation"))
        incoming_shots = _mapping(incoming_video.get("shots"))
        incoming_shot = incoming_shots.get(self._shot_key)
        if not isinstance(incoming_shot, Mapping):
            raise ProjectStateError("Shot checkpoint is missing")
        with self._lock_manager.project_write(
            self._project_id,
            timeout_seconds=5.0,
        ):
            try:
                latest = json.loads(target.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ProjectStateError("project.json cannot be merged") from exc
            if not isinstance(latest, dict):
                raise ProjectStateError("project.json payload is invalid")
            latest_video = latest.setdefault("video_generation", {})
            if not isinstance(latest_video, dict):
                raise ProjectStateError("video generation state is invalid")
            latest_shots = latest_video.setdefault("shots", {})
            if not isinstance(latest_shots, dict):
                raise ProjectStateError("Shot collection state is invalid")
            latest_shots[self._shot_key] = deepcopy(dict(incoming_shot))
            if "shot_review_schema_version" in incoming_video:
                latest_video["shot_review_schema_version"] = incoming_video[
                    "shot_review_schema_version"
                ]

            completed = latest_video.setdefault("completed_shots", [])
            if not isinstance(completed, list):
                raise ProjectStateError("completed Shot state is invalid")
            shot_number = int(self._shot_key)
            incoming_completed = {
                int(value)
                for value in incoming_video.get("completed_shots", [])
                if isinstance(value, int) or str(value).isdigit()
            }
            completed_numbers = {
                int(value)
                for value in completed
                if isinstance(value, int) or str(value).isdigit()
            }
            if shot_number in incoming_completed:
                completed_numbers.add(shot_number)
            else:
                completed_numbers.discard(shot_number)
            latest_video["completed_shots"] = sorted(completed_numbers)
            if data.get("updated_at") is not None:
                latest["updated_at"] = data["updated_at"]
            return self._base.save_json(target, latest)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _resolve_completed_generation_version(
    *,
    paths: ProjectPaths,
    checkpoint: ProjectCheckpoint,
    shot_id: int,
    output: Path,
    expected_intent: GenerationIntent | None,
) -> int:
    """Reconcile a Core generation result with its durable review-ready Bundle."""

    output_path = output.resolve(strict=True)
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        raise ShotStorageError("Core generation output is missing or empty.")

    entry = checkpoint.shot_checkpoint(shot_id)
    generations = [
        item
        for item in entry.get("generation_versions", [])
        if isinstance(item, Mapping)
    ]
    matches: list[tuple[int, Mapping[str, Any]]] = []
    for generation_record in generations:
        version = _positive_int(generation_record.get("video_version"))
        if version is None:
            continue
        canonical = paths.shot_version_video_path(shot_id, version).resolve()
        if canonical == output_path:
            matches.append((version, generation_record))
    if len(matches) != 1:
        raise ShotStorageError("Core generation output has no unique durable version.")
    version, generation_record = matches[0]

    candidate = _mapping(entry.get("candidate"))
    candidate_lane = (
        str(candidate.get("status") or "").upper() == "WAITING_REVIEW"
        and _positive_int(candidate.get("video_version")) == version
    )
    active_review_lane = (
        str(entry.get("status") or "").upper() == "WAITING_REVIEW"
        and _positive_int(entry.get("active_video_version")) == version
    )
    if candidate_lane == active_review_lane:
        raise ShotStorageError("Completed generation is not in one review-ready lane.")

    lane = candidate if candidate_lane else entry
    lane_prompt_version = _positive_int(
        lane.get("prompt_version")
        if candidate_lane
        else entry.get("active_prompt_version")
    )
    lane_intent = str(lane.get("generation_intent") or "")
    record_intent = str(generation_record.get("generation_intent") or "")
    expected_intent_value = (
        "INITIAL_GENERATION"
        if expected_intent is GenerationIntent.INITIAL
        else expected_intent.value
        if expected_intent is not None
        else record_intent
    )
    if (
        not expected_intent_value
        or lane_intent != expected_intent_value
        or record_intent != expected_intent_value
    ):
        raise ShotStorageError("Completed generation intent is inconsistent.")

    bundle = validate_bundle(paths, shot_id, version, require_video=True)
    read_bundle_json(paths, shot_id, version, "safety.json")
    prompt = _mapping(bundle.get("prompt"))
    generation = _mapping(bundle.get("generation"))
    review = _mapping(bundle.get("review"))
    prompt_version = _positive_int(prompt.get("prompt_version"))
    if (
        prompt_version is None
        or prompt_version != lane_prompt_version
        or prompt_version != _positive_int(generation_record.get("prompt_version"))
        or checkpoint.prompt_version(shot_id, prompt_version) is None
        or str(generation.get("status") or "").upper() != "WAITING_REVIEW"
        or str(generation.get("generation_intent") or "") != expected_intent_value
        or bool(generation.get("submission_unknown"))
        or not generation.get("completed_at")
        or str(generation_record.get("status") or "").upper() != "WAITING_REVIEW"
        or not generation_record.get("completed_at")
        or str(review.get("review_result") or "").upper() != "WAITING_REVIEW"
    ):
        raise ShotStorageError("Completed generation Bundle is inconsistent.")
    return version


class ShotGenerationActionService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        reference_repository: ReferenceAssetRepository,
        preflight_service: ShotGenerationPreflightService,
        task_service: TaskService,
        capability_service: CapabilityService,
        lock_manager: ProjectLockManager,
    ) -> None:
        self._project_repository = project_repository
        self._reference_repository = reference_repository
        self._preflight_service = preflight_service
        self._task_service = task_service
        self._capability_service = capability_service
        self._lock_manager = lock_manager

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
        if payload.intent is not GenerationIntent.INITIAL:
            raise GenerationPreflightStale("initial generation intent is invalid")
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        canonical_shot_id, _shot_number = normalize_shot_id(shot_id)
        preflight_payload = GenerationPreflightRequest.model_validate(
            payload.model_dump(
                include={"intent", "model_selection", "requested_model", "visual_input"}
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

    def submit_batch_starts(
        self,
        project_id: str,
        prepared: list[tuple[str, GenerationStartRequest]],
        *,
        correlation_id: str | None,
    ) -> list[TaskRecord]:
        """Validate a full plan, then durably create all independent Shot tasks."""

        if not prepared:
            raise GenerationPreflightStale("generation plan is empty")
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        submissions: list[tuple[str, Any]] = []
        canonical_targets: set[str] = set()
        for shot_id, payload in prepared:
            if not payload.confirm_paid_call:
                raise PaidCallConfirmationRequired("paid call was not confirmed")
            if payload.intent is not GenerationIntent.INITIAL:
                raise GenerationPreflightStale("initial generation intent is invalid")
            canonical_shot_id, _shot_number = normalize_shot_id(shot_id)
            if canonical_shot_id in canonical_targets:
                raise GenerationPreflightStale("generation plan has duplicate Shots")
            canonical_targets.add(canonical_shot_id)
            preflight_payload = GenerationPreflightRequest.model_validate(
                payload.model_dump(
                    include={"intent", "model_selection", "requested_model", "visual_input"}
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
            submissions.append(
                (
                    canonical_shot_id,
                    lambda current_shot_id=canonical_shot_id,
                    current_payload=preflight_payload,
                    current_fingerprint=payload.preflight_fingerprint: self._run_start(
                        canonical_project_id,
                        current_shot_id,
                        current_payload,
                        current_fingerprint,
                        parallel_checkpoint=True,
                    ),
                )
            )
        return self._task_service.submit_parallel_targets(
            project_id=canonical_project_id,
            operation=TaskOperation.SHOT_GENERATE,
            submissions=submissions,
            correlation_id=correlation_id,
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

    def submit_regenerate(
        self,
        project_id: str,
        shot_id: str,
        payload: GenerationStartRequest,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        if not payload.confirm_paid_call:
            raise PaidCallConfirmationRequired("paid call was not confirmed")
        if payload.intent not in {
            GenerationIntent.REGENERATE_CURRENT_PROMPT,
            GenerationIntent.REGENERATE_MANUAL_PROMPT,
        }:
            raise GenerationPreflightStale("regeneration intent is invalid")
        canonical_project_id = self._project_repository.get_project(project_id).project_id
        canonical_shot_id, _shot_number = normalize_shot_id(shot_id)
        preflight_payload = GenerationPreflightRequest.model_validate(
            payload.model_dump(
                include={
                    "intent",
                    "model_selection",
                    "requested_model",
                    "visual_input",
                    "base_prompt_version",
                    "edited_prompt",
                }
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
            operation=TaskOperation.SHOT_REGENERATE,
            target_id=canonical_shot_id,
            correlation_id=correlation_id,
            callable_=lambda: self._run_start(
                canonical_project_id,
                canonical_shot_id,
                preflight_payload,
                payload.preflight_fingerprint,
                regenerate=True,
            ),
        )

    def submit_prompt_version_generation(
        self,
        project_id: str,
        shot_id: str,
        payload: GenerationStartRequest,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        if not payload.confirm_paid_call:
            raise PaidCallConfirmationRequired("paid call was not confirmed")
        if payload.intent is not GenerationIntent.GENERATE_WITH_PROMPT_VERSION:
            raise GenerationPreflightStale("selected Prompt generation intent is invalid")
        canonical_project_id = self._project_repository.get_project(project_id).project_id
        canonical_shot_id, _shot_number = normalize_shot_id(shot_id)
        preflight_payload = GenerationPreflightRequest.model_validate(
            payload.model_dump(
                include={
                    "intent",
                    "model_selection",
                    "requested_model",
                    "visual_input",
                    "target_prompt_version",
                }
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
            operation=TaskOperation.SHOT_PROMPT_VERSION_GENERATE,
            target_id=canonical_shot_id,
            correlation_id=correlation_id,
            callable_=lambda: self._run_start(
                canonical_project_id,
                canonical_shot_id,
                preflight_payload,
                payload.preflight_fingerprint,
                regenerate=True,
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

        candidate = _mapping(entry.get("candidate"))
        candidate_status = str(candidate.get("status") or "NONE").upper()
        candidate_active = (
            candidate_status not in {"NONE", "EDITING"}
            and str(candidate.get("generation_intent") or "")
            in {
                "REGENERATE_CURRENT_PROMPT",
                "REGENERATE_MANUAL_PROMPT",
                "GENERATE_WITH_PROMPT_VERSION",
            }
        )
        status = str(
            candidate.get("status") if candidate_active else entry.get("status") or "NOT_STARTED"
        ).upper()
        phase = str(
            candidate.get("generation_phase")
            if candidate_active
            else entry.get("generation_phase")
            or status
        ).upper()
        version = (
            _positive_int(candidate.get("video_version"))
            if candidate_active
            else _positive_int(entry.get("current_generation_version"))
            or _positive_int(entry.get("pending_video_version"))
            or _positive_int(entry.get("active_video_version"))
        )
        submission_unknown = bool(
            candidate.get("submission_unknown") if candidate_active else entry.get("submission_unknown")
        ) or phase == "SUBMISSION_UNKNOWN"
        progress = candidate if candidate_active else entry
        provider_task_id = str(progress.get("provider_task_id") or "").strip()
        file_id = str(progress.get("file_id") or "").strip()
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
        if status not in {"WAITING_REVIEW", "APPROVED"} and not submission_unknown and version is not None:
            if video_exists:
                resume_kind = ShotGenerationResumeKind.FINALIZE_LOCAL_VIDEO
            elif file_id:
                resume_kind = ShotGenerationResumeKind.DOWNLOAD_EXISTING_FILE
            elif provider_task_id:
                resume_kind = ShotGenerationResumeKind.POLL_EXISTING_TASK

        active = self._task_service.active_for_target(
            canonical_project_id,
            canonical_shot_id,
            operations={
                TaskOperation.SHOT_GENERATE,
                TaskOperation.SHOT_REGENERATE,
                TaskOperation.SHOT_PROMPT_VERSION_GENERATE,
                TaskOperation.SHOT_RESUME,
            },
        )
        active_for_shot = (
            active is not None
            and active.operation
            in {
                TaskOperation.SHOT_GENERATE,
                TaskOperation.SHOT_REGENERATE,
                TaskOperation.SHOT_PROMPT_VERSION_GENERATE,
                TaskOperation.SHOT_RESUME,
            }
            and active.target_id == canonical_shot_id
            and active.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}
        )
        if active_for_shot and not candidate_active and phase in {"APPROVED", "NOT_STARTED"}:
            public_state = ShotGenerationState.QUEUED
        elif status == "WAITING_REVIEW":
            public_state = ShotGenerationState.WAITING_REVIEW
        elif status == "APPROVED":
            public_state = ShotGenerationState.APPROVED
        elif submission_unknown:
            public_state = ShotGenerationState.SUBMISSION_UNKNOWN
        elif phase in ShotGenerationState._value2member_map_:
            public_state = ShotGenerationState(phase)
        elif status == "FAILED":
            public_state = ShotGenerationState.FAILED
        else:
            public_state = (
                ShotGenerationState.QUEUED
                if active_for_shot
                else ShotGenerationState.NOT_STARTED
            )
        raw_intent = str(
            candidate.get("generation_intent")
            if candidate_active
            else entry.get("generation_intent")
            or ""
        )
        if raw_intent in GenerationIntent._value2member_map_:
            public_intent: GenerationIntent | None = GenerationIntent(raw_intent)
        elif active_for_shot and active.operation is TaskOperation.SHOT_PROMPT_VERSION_GENERATE:
            public_intent = GenerationIntent.GENERATE_WITH_PROMPT_VERSION
        elif active_for_shot and active.operation is TaskOperation.SHOT_REGENERATE:
            public_intent = GenerationIntent.REGENERATE_CURRENT_PROMPT
        elif entry.get("generation_intent"):
            public_intent = GenerationIntent.INITIAL
        else:
            public_intent = None
        public_prompt_version = _positive_int(
            candidate.get("prompt_version")
            if candidate_active
            else entry.get("active_prompt_version")
        )
        return ShotGenerationStatusResponse(
            project_id=canonical_project_id,
            shot_id=canonical_shot_id,
            state=public_state,
            resume_available=resume_kind is not None,
            resume_kind=resume_kind,
            video_version=version,
            prompt_version=public_prompt_version,
            provider_submission_known=not submission_unknown,
            generation_intent=public_intent,
        )

    def _run_start(
        self,
        project_id: str,
        shot_id: str,
        payload: GenerationPreflightRequest,
        fingerprint: str,
        regenerate: bool = False,
        parallel_checkpoint: bool = False,
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
            regenerate=regenerate,
            preflight_payload=payload,
            parallel_checkpoint=parallel_checkpoint,
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
            regenerate=False,
            preflight_payload=None,
        )

    def _execute(
        self,
        project_id: str,
        shot_id: str,
        *,
        resume: bool,
        visual_input: dict[str, Any] | None,
        provider_selection: ProviderSelection | None,
        regenerate: bool,
        preflight_payload: GenerationPreflightRequest | None,
        parallel_checkpoint: bool = False,
    ) -> TaskResultReference:
        _canonical, shot_number = normalize_shot_id(shot_id)
        try:
            paths = create_project_paths(
                self._project_repository.resolve_project_dir(project_id)
            )
            if parallel_checkpoint:
                with self._lock_manager.project_write(
                    project_id,
                    timeout_seconds=5.0,
                ):
                    checkpoint = ProjectCheckpoint.load(paths)
                checkpoint.project = _ShotScopedProjectPaths(
                    paths,
                    project_id,
                    shot_number,
                    self._lock_manager,
                )
            else:
                checkpoint = ProjectCheckpoint.load(paths)
            board = Storyboard.model_validate_json(
                paths.storyboard_file_path().read_text(encoding="utf-8")
            )
            plan = VideoPromptPlan.model_validate_json(
                paths.video_prompts_path().read_text(encoding="utf-8")
            )
            brief = (
                CreativeBrief.model_validate_json(
                    paths.creative_brief_path().read_text(encoding="utf-8")
                )
                if preflight_payload is not None
                and preflight_payload.intent is GenerationIntent.REGENERATE_MANUAL_PROMPT
                else None
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
                else (
                    regenerate_shot_with_prompt_version(
                        paths=paths,
                        checkpoint=checkpoint,
                        plan=plan,
                        shot=shot,
                        shot_id=shot_number,
                        target_prompt_version=int(
                            preflight_payload.target_prompt_version or 0
                        ),
                        visual_input=visual_input or none_visual_input(),
                        deepseek_key=deepseek_key,
                        provider_credentials=credentials,
                        task_logger=logger,
                        provider_selection=provider_selection,
                        provider_registry=registry,
                    )
                    if regenerate
                    and preflight_payload is not None
                    and preflight_payload.intent
                    is GenerationIntent.GENERATE_WITH_PROMPT_VERSION
                    else regenerate_shot_with_manual_prompt(
                        paths=paths,
                        checkpoint=checkpoint,
                        plan=plan,
                        brief=brief,
                        shot=shot,
                        shot_id=shot_number,
                        base_prompt_version=int(
                            preflight_payload.base_prompt_version or 0
                        ),
                        edited_visual_prompt_core=str(
                            preflight_payload.edited_prompt or ""
                        ),
                        visual_input=visual_input or none_visual_input(),
                        deepseek_key=deepseek_key,
                        provider_credentials=credentials,
                        task_logger=logger,
                        product_name=str(
                            checkpoint.data.get("request", {}).get("product_name") or ""
                        ) or None,
                        provider_selection=provider_selection,
                        provider_registry=registry,
                    )
                    if regenerate
                    and preflight_payload is not None
                    and preflight_payload.intent
                    is GenerationIntent.REGENERATE_MANUAL_PROMPT
                    else regenerate_shot_with_current_prompt(
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
                    if regenerate
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
            )
        except ProviderSubmissionUnknownError:
            _task_failure(
                "SUBMISSION_UNKNOWN",
                "无法确认视频生成请求是否已提交，请不要立即重复生成。",
            )
        except (
            CurrentPromptRegenerationNotAllowed,
            InitialShotGenerationNotAllowed,
            ManualPromptRegenerationNotAllowed,
            SelectedPromptVersionGenerationNotAllowed,
            ShotGenerationResumeUnavailable,
        ):
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

        try:
            version = _resolve_completed_generation_version(
                paths=paths,
                checkpoint=checkpoint,
                shot_id=shot_number,
                output=output,
                expected_intent=(
                    preflight_payload.intent if preflight_payload is not None else None
                ),
            )
        except (OSError, TypeError, ValueError, ShotStorageError):
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
