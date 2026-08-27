"""Explicit rejected-attempt recovery; all generation work stays in existing Core."""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import replace
from threading import Event

from project_manager import create_project_paths
from project_state import ProjectCheckpoint
from prompt_generator import PromptSafetyReview
from shot_generation_workflow import continue_shot_generation
from shot_storage import ShotStorageError
from storyboard import Storyboard, VideoPromptPlan
from task_logger import TaskLogger
from video_generation_request import ProviderSelection
from video_generator import ProviderSubmissionUnknownError
from video_provider import VideoProviderError
from video_provider_registry import create_default_registry, load_provider_credentials_from_env, provider_secret_values
from web_backend.models.generation import GenerationIntent, GenerationPreflightRequest
from web_backend.models.shot_failure_recovery import (
    FailureRecovery, FailureRecoveryState as State, FailedRetryOptions,
    FailedRetryPreflight, FailedRetryPreflightRequest, FailedRetryRequest,
)
from web_backend.models.tasks import TaskOperation, TaskStatus
from web_backend.repositories.project_repository import ProjectRepositoryError
from web_backend.repositories.shot_bundle_readiness import video_bundle_complete
from web_backend.repositories.shot_repository import normalize_shot_id
from web_backend.services.shot_generation import (
    PaidCallConfirmationRequired, _resolve_completed_generation_version,
)
from web_backend.services.task_failures import raise_task_failure


PLAN_MESSAGE = "当前套餐不支持所选模型配置，请调整模型、时长或分辨率后重新尝试。"
REJECTION_MESSAGE = "视频生成请求被明确拒绝，请调整配置后重新尝试。"
STALE_MESSAGE = "失败恢复状态或生成配置已变化，请重新检查配置。"


class FailedRetryStale(RuntimeError):
    pass


class ShotFailureRecoveryService:
    def __init__(self, projects, shots, preparation, generation, tasks):
        self.projects = projects
        self.shots = shots
        self.preparation = preparation
        self.generation = generation
        self.tasks = tasks

    def _snapshot(self, project_id, shot_id):
        detail = self.shots.get_shot(project_id, shot_id)
        canonical, number = normalize_shot_id(shot_id)
        root = self.projects.resolve_project_dir(detail.project_id).resolve()
        read = lambda path: dict(self.generation._read_json(path, root))
        project = read(root / "project.json")
        entry = project.get("video_generation", {}).get("shots", {}).get(str(number), {})
        manifest_path = root / "shots" / canonical / "shot.json"
        manifest = read(manifest_path) if manifest_path.exists() else {}
        records = [item for item in entry.get("generation_versions", []) if isinstance(item, dict)]
        version = entry.get("current_generation_version") or max(
            [int(item.get("video_version") or 0) for item in records] or [0]
        )
        record = next((item for item in records if item.get("video_version") == version), {})
        directory = root / "shots" / canonical / f"v{int(version):03d}"
        bundle = {
            name: read(directory / name) if (directory / name).exists() else {}
            for name in ("generation.json", "prompt.json", "safety.json", "review.json")
        } if version else {}
        return detail, root, number, project, entry, manifest, record, bundle

    def classify(self, project_id, shot_id, *, ignore_task_id=None):
        data = self._snapshot(project_id, shot_id)
        return self._classify(data, ignore_task_id=ignore_task_id)

    def _classify(self, data, *, ignore_task_id=None):
        detail, root, number, _project, entry, manifest, record, bundle = data
        version = int(record.get("video_version") or 0) or None
        def result(state, reason, message, *, active=None):
            allowed = state is State.RETRY_ALLOWED
            return FailureRecovery(
                state=state, reason_code=reason, safe_message=message,
                can_retry=allowed, requires_new_preflight=allowed,
                requires_external_cost_confirmation=allowed,
                last_attempt_version=version, active_task_id=active,
            )
        tasks = [task for task in self.tasks.list_for_project(detail.project_id).tasks
                 if task.task_id != ignore_task_id]
        active = next((task for task in tasks if task.status in {TaskStatus.QUEUED, TaskStatus.RUNNING}), None)
        if active:
            return result(State.ACTIVE_TASK, "ACTIVE_TASK_EXISTS",
                          "项目已有执行中的任务，请查看已有任务，不要重复提交。", active=active.task_id)
        candidate = entry.get("candidate") or {}
        progress = [entry, record, bundle.get("generation.json", {}), candidate]
        if any(item.get("submission_unknown") or item.get("generation_phase") == "SUBMISSION_UNKNOWN" for item in progress):
            return result(State.RETRY_BLOCKED_SUBMISSION_UNKNOWN, "SUBMISSION_UNKNOWN",
                          "外部请求状态未知，为避免重复收费，暂不能直接重新提交。")
        if any(video_bundle_complete(root, number, item.version) for item in detail.versions):
            return result(State.BUSINESS_ALREADY_COMPLETE, "VIDEO_BUNDLE_COMPLETE",
                          "已有完整视频，请恢复或查看已有结果，不要重新生成。")
        if any(item.get("file_id") for item in progress):
            return result(State.RESUME_AVAILABLE, "FILE_READY", "已有生成文件，请继续下载或完成本地收尾。")
        if any(item.get("provider_task_id") for item in progress):
            return result(State.RESUME_AVAILABLE, "PROVIDER_TASK_EXISTS", "已有外部生成任务，请继续检查生成结果。")
        if any((root / "shots" / detail.shot_id / f"v{item.version:03d}" / "video.mp4").exists()
               for item in detail.versions):
            return result(State.BLOCKED, "LOCAL_VIDEO_INCOMPLETE",
                          "已有本地视频但 Bundle 不完整，请核对已有结果，不要重新提交。")
        if entry.get("status") != "FAILED":
            return result(State.NOT_APPLICABLE, "SHOT_NOT_FAILED", "当前镜头不需要失败重试。")
        latest = next((task for task in tasks if task.target_id == detail.shot_id and task.operation in {
            TaskOperation.SHOT_GENERATE, TaskOperation.SHOT_REGENERATE, TaskOperation.SHOT_RESUME,
        }), None)
        error = entry.get("last_error") or {}
        generation = bundle.get("generation.json", {})
        prompt = bundle.get("prompt.json", {})
        safety = bundle.get("safety.json", {})
        prompt_version = record.get("prompt_version")
        current_prompt = next((item for item in entry.get("prompt_versions", [])
                               if item.get("version") == prompt_version), {})
        recorded_prompt = record.get("prompt_snapshot") or {}
        explicit = (
            latest is not None and latest.operation is TaskOperation.SHOT_GENERATE
            and latest.status is TaskStatus.FAILED and latest.error is not None
            and latest.error.code == "VIDEO_PROVIDER_INVALID_REQUEST"
            and error.get("type") == "VideoProviderError"
            and str(error.get("message") or "").startswith("INVALID_REQUEST:")
            and record.get("status") == "FAILED" and generation.get("status") == "FAILED"
            and manifest.get("status") == "FAILED"
            and candidate.get("status", "NONE") == "NONE"
            and entry.get("generation_phase") == "FAILED"
            and not entry.get("generation_attempt_pending")
            and not any(entry.get(key) for key in ("active_video_version", "approved_video_version", "pending_video_version"))
            and version is not None and entry.get("current_generation_version") == version
            and {item.version for item in detail.versions} == {
                item.get("video_version") for item in entry.get("generation_versions", [])
            }
            and bool(prompt_version) and prompt_version == entry.get("active_prompt_version")
            and prompt_version == prompt.get("prompt_version") == generation.get("prompt_version")
            and bool(current_prompt.get("prompt"))
            and current_prompt.get("prompt") == recorded_prompt.get("prompt") == prompt.get("prompt_text")
            and safety.get("is_safe") is True and bool(safety.get("reviewed_video_prompt"))
        )
        if not explicit:
            return result(State.BLOCKED, "REJECTION_NOT_PROVEN",
                          "当前记录不足以确认可以安全重新提交，请先核对失败原因。")
        message = PLAN_MESSAGE if re.search(r"[（(]2061[）)]", str(error.get("message"))) else REJECTION_MESSAGE
        return result(State.RETRY_ALLOWED, "VIDEO_PROVIDER_INVALID_REQUEST", message)

    def options(self, project_id, shot_id):
        data = self._snapshot(project_id, shot_id)
        recovery = self._classify(data)
        options = self.preparation.options(project_id, shot_id)
        record = data[6]
        shot = options.shot.model_copy(update={
            "duration_seconds": record.get("duration") or options.shot.duration_seconds,
            "resolution": record.get("resolution") or "768P",
            "prompt_version": record.get("prompt_version") or options.shot.prompt_version,
        })
        return FailedRetryOptions(**{
            **options.model_dump(), "shot": shot, "eligible": recovery.can_retry,
            "issues": [], "failure_recovery": recovery,
        })

    def preflight(self, project_id, shot_id, payload, *, ignore_task_id=None):
        data = self._snapshot(project_id, shot_id)
        recovery = self._classify(data, ignore_task_id=ignore_task_id)
        if not recovery.can_retry:
            raise FailedRetryStale()
        detail, _root, _number, _project, entry, manifest, record, bundle = data
        context = self.preparation._context(detail.project_id, shot_id, GenerationIntent.INITIAL)
        context = replace(
            context,
            public=context.public.model_copy(update={
                "duration_seconds": payload.duration, "resolution": payload.resolution,
                "prompt_version": record["prompt_version"],
            }),
            prompt=bundle["safety.json"]["reviewed_video_prompt"],
            state_issues=tuple(issue for issue in context.state_issues
                               if issue.code.value not in {"SHOT_ALREADY_GENERATED", "SHOT_NOT_READY"}),
        )
        checked = self.preparation._preflight_context(
            detail.project_id, context,
            GenerationPreflightRequest(
                model_selection=payload.model_selection, requested_model=payload.requested_model,
                visual_input=payload.visual_input,
            ),
        )
        fingerprint = None
        if checked.ready and checked.preflight_fingerprint:
            material = {
                "local_preflight": checked.preflight_fingerprint, "intent": "FAILED_RETRY",
                "entry": entry, "manifest": manifest, "bundle": bundle,
                "failed_tasks": [
                    task.model_dump(mode="json") for task in self.tasks.list_for_project(detail.project_id).tasks
                    if task.target_id == detail.shot_id and task.task_id != ignore_task_id
                ],
            }
            fingerprint = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        return FailedRetryPreflight(**{
            **checked.model_dump(), "failure_recovery": recovery, "preflight_fingerprint": fingerprint,
        })

    def submit(self, project_id, shot_id, payload: FailedRetryRequest, *, correlation_id):
        if payload.confirm_external_video_call is not True:
            raise PaidCallConfirmationRequired()
        config = FailedRetryPreflightRequest.model_validate(
            payload.model_dump(exclude={"preflight_fingerprint", "confirm_external_video_call"})
        )
        checked = self.preflight(project_id, shot_id, config)
        if not checked.ready or checked.preflight_fingerprint != payload.preflight_fingerprint:
            raise FailedRetryStale()
        project_id = self.projects.get_project(project_id).project_id
        shot_id, _number = normalize_shot_id(shot_id)
        registered = Event()
        own_task = []
        def run():
            # The runner may begin before submit() returns its durable Task ID.
            if not registered.wait(5):
                raise_task_failure("FAILED_RETRY_STALE", STALE_MESSAGE)
            return self._run(project_id, shot_id, config, payload.preflight_fingerprint, own_task[0])
        task = self.tasks.submit(
            project_id=project_id, operation=TaskOperation.SHOT_GENERATE,
            target_id=shot_id, correlation_id=correlation_id, callable_=run,
        )
        own_task.append(task.task_id)
        registered.set()
        return task

    def _run(self, project_id, shot_id, config, fingerprint, task_id):
        # TaskRunner holds the project lock. Ignore only this exact worker Task.
        try:
            checked = self.preflight(project_id, shot_id, config, ignore_task_id=task_id)
            if not checked.ready or checked.preflight_fingerprint != fingerprint or checked.resolved is None:
                raise FailedRetryStale()
            data = self._snapshot(project_id, shot_id)
            _detail, root, number, project, _entry, _manifest, _record, bundle = data
            paths = create_project_paths(root, ensure_directories=False)
            # This Schema-2 action must not trigger global legacy migration or
            # rewrite old failed bundles as a side effect of loading checkpoint.
            checkpoint = ProjectCheckpoint(paths, deepcopy(project))
            board = Storyboard.model_validate(self.generation._read_json(root / "storyboard" / "storyboard.json", root))
            plan = VideoPromptPlan.model_validate(self.generation._read_json(root / "storyboard" / "video_prompts.json", root))
            shot = next(item for item in board.shots if item.shot_id == number).model_copy(update={"duration": config.duration})
            visual = self.generation._visual_input(project_id, config)
            safety_data = bundle["safety.json"]
            safety = PromptSafetyReview(
                is_safe=True, reviewed_video_prompt=safety_data["reviewed_video_prompt"],
                risk_notes=safety_data.get("risk_notes") or [],
            )
        except (FailedRetryStale, ProjectRepositoryError, ValueError, OSError, StopIteration, KeyError):
            raise_task_failure("FAILED_RETRY_STALE", STALE_MESSAGE)
        credentials = load_provider_credentials_from_env()
        registry = create_default_registry(credentials)
        logger = TaskLogger(paths)
        for secret in provider_secret_values(credentials):
            logger.register_secret(secret)
        resolved = checked.resolved
        selection = ProviderSelection(resolved.provider, resolved.model, resolved.model_selection.value.lower())
        try:
            checkpoint.prepare_shot_generation(number, generation_intent="FAILED_RETRY")
            output = continue_shot_generation(
                paths=paths, checkpoint=checkpoint, plan=plan, shot=shot, shot_id=number,
                deepseek_key="", provider_credentials=credentials, task_logger=logger,
                provider_selection=selection, provider_registry=registry, visual_input=visual,
                safety=safety, resolution=config.resolution,
            )
        except ProviderSubmissionUnknownError:
            raise_task_failure("SUBMISSION_UNKNOWN", "外部请求状态未知，为避免重复收费，暂不能直接重新提交。")
        except VideoProviderError as error:
            code, message, retryable = self.generation._provider_failure(error)
            raise_task_failure(code, message, retryable=retryable)
        except (OSError, RuntimeError):
            raise_task_failure("SHOT_GENERATION_FAILED", "镜头生成结果无法安全处理。")
        try:
            version = _resolve_completed_generation_version(
                paths=paths, checkpoint=checkpoint, shot_id=number, output=output,
                expected_intent=GenerationIntent.FAILED_RETRY,
            )
        except (OSError, ValueError, TypeError, ShotStorageError):
            raise_task_failure("SHOT_GENERATION_FAILED", "镜头生成结果无法安全处理。")
        from web_backend.models.tasks import TaskResultReference
        return TaskResultReference(resource_type="SHOT_VIDEO", resource_id=shot_id, version=version)
