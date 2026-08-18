"""Business-scoped Web actions for planning stages."""

from __future__ import annotations

from web_backend.models.projects import AvailableAction
from web_backend.models.tasks import (
    TaskError,
    TaskOperation,
    TaskRecord,
    TaskResultReference,
)
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.services.capabilities import CapabilityService
from web_backend.services.task_runner import TaskExecutionFailure
from web_backend.services.tasks import TaskService


class PlanningActionError(RuntimeError):
    """Base class for safe synchronous planning-action failures."""


class ActionNotAllowed(PlanningActionError):
    pass


class CapabilityUnavailable(PlanningActionError):
    pass


def _task_failure(code: str, message: str, *, retryable: bool = False) -> None:
    raise TaskExecutionFailure(
        TaskError(code=code, message=message, retryable=retryable)
    )


class CreativeActionService:
    """Validate and enqueue the one supported Creative generation action."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        task_service: TaskService,
        capability_service: CapabilityService,
    ) -> None:
        self._project_repository = project_repository
        self._task_service = task_service
        self._capability_service = capability_service

    def submit_generate(
        self,
        project_id: str,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        self._require_generate_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")

        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.CREATIVE_GENERATE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_generate(canonical_project_id),
        )

    def _require_generate_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.GENERATE_CREATIVE not in workflow.available_actions:
            raise ActionNotAllowed("Creative generation is not allowed")

    def _deepseek_available(self) -> bool:
        return self._capability_service.get_capabilities().planning.deepseek.available

    def _run_generate(self, project_id: str) -> TaskResultReference:
        # TaskRunner owns the project write lock around this entire callable.
        try:
            self._require_generate_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许生成创意。",
            )

        deepseek_key = self._capability_service.deepseek_api_key()
        if not deepseek_key:
            _task_failure(
                "CAPABILITY_UNAVAILABLE",
                "创意生成服务尚未配置。",
                retryable=True,
            )

        # Core imports remain lazy so ordinary Backend startup and GET requests
        # stay lightweight and cannot initialize provider workflows.
        from pydantic import ValidationError

        from creative_workflow import generate_creative_stage
        from evaluation import EvaluationRecorder
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint, ProjectStateError, StageStatus
        from prompt_generator import ProductVideoRequest, PromptGenerationError
        from reference_assets import ReferenceAssetManager
        from storyboard import StoryboardError
        from task_logger import TaskLogger

        try:
            paths = create_project_paths(
                self._project_repository.resolve_project_dir(project_id)
            )
            checkpoint = ProjectCheckpoint.load(paths)
            request = ProductVideoRequest.model_validate(checkpoint.data["request"])
        except (KeyError, ValidationError, ProjectStateError):
            _task_failure(
                "PROJECT_DATA_CORRUPT",
                "项目数据无法读取。",
            )

        logger = TaskLogger(paths)
        logger.register_secret(deepseek_key)
        reference_manager = ReferenceAssetManager(paths, logger)
        reference_assets = reference_manager.list_assets()
        reference_context = {
            "available": bool(reference_assets),
            "asset_count": len(reference_assets),
            "asset_ids": [str(item.get("asset_id")) for item in reference_assets],
        }
        evaluation_recorder = EvaluationRecorder(paths)

        try:
            generate_creative_stage(
                paths,
                request,
                checkpoint,
                deepseek_key,
                logger,
                evaluation_recorder=evaluation_recorder,
                reference_asset_context=reference_context,
            )
        except Exception as error:
            if checkpoint.status == StageStatus.RUNNING.value:
                checkpoint.fail(error)
            logger.error(error, stage="creative")
            if isinstance(error, PromptGenerationError):
                _task_failure(
                    "PROVIDER_REQUEST_FAILED",
                    "创意生成服务暂时不可用，请稍后重试。",
                    retryable=True,
                )
            if isinstance(error, StoryboardError):
                _task_failure(
                    "CREATIVE_OUTPUT_INVALID",
                    "创意生成结果无法使用。",
                    retryable=True,
                )
            raise

        return TaskResultReference(
            resource_type="CREATIVE",
            resource_id=project_id,
        )
