"""Business-scoped Web actions for planning stages."""

from __future__ import annotations

from web_backend.locking import (
    DEFAULT_PROJECT_LOCK_MANAGER,
    ProjectLockBusy,
    ProjectLockManager,
)
from web_backend.models.projects import (
    AvailableAction,
    ProjectWorkflowResponse,
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
    """Own the supported Creative generation and review Web actions."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        task_service: TaskService,
        capability_service: CapabilityService,
        project_lock_manager: ProjectLockManager = DEFAULT_PROJECT_LOCK_MANAGER,
    ) -> None:
        self._project_repository = project_repository
        self._task_service = task_service
        self._capability_service = capability_service
        self._project_lock_manager = project_lock_manager

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

    def submit_revise(
        self,
        project_id: str,
        *,
        feedback: str,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        self._require_no_active_task(canonical_project_id)
        self._require_revise_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.CREATIVE_REVISE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_revise(canonical_project_id, feedback),
        )

    def submit_regenerate(
        self,
        project_id: str,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        self._require_no_active_task(canonical_project_id)
        self._require_regenerate_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.CREATIVE_REGENERATE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_regenerate(canonical_project_id),
        )

    def submit_storyboard_generate(
        self,
        project_id: str,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        self._require_no_active_task(canonical_project_id)
        self._require_storyboard_generate_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.STORYBOARD_GENERATE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_storyboard_generate(canonical_project_id),
        )

    def approve(self, project_id: str) -> ProjectWorkflowResponse:
        """Synchronously approve Creative under the per-project write lock."""

        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        with self._task_service.prevent_task_submission():
            self._require_no_active_task(canonical_project_id)
            self._require_approve_allowed(canonical_project_id)

            try:
                with self._project_lock_manager.project_write(canonical_project_id):
                    # Both checks intentionally repeat inside the lock. State
                    # may have changed after preflight; task submission stays
                    # blocked until this short transaction finishes.
                    self._require_no_active_task(canonical_project_id)
                    self._require_approve_allowed(canonical_project_id)

                    from creative_workflow import (
                        CreativeApprovalError,
                        approve_creative_stage,
                    )
                    from project_manager import create_project_paths
                    from project_state import ProjectCheckpoint, ProjectStateError

                    try:
                        paths = create_project_paths(
                            self._project_repository.resolve_project_dir(
                                canonical_project_id
                            ),
                            ensure_directories=False,
                        )
                        checkpoint = ProjectCheckpoint.load(paths)
                        approve_creative_stage(checkpoint)
                    except CreativeApprovalError as error:
                        raise ActionNotAllowed(
                            "Creative approval is not allowed"
                        ) from error
                    except ProjectStateError as error:
                        raise ProjectDataCorrupt(
                            "project checkpoint is unreadable"
                        ) from error

                    return self._project_repository.get_workflow(
                        canonical_project_id
                    )
            except ProjectLockBusy as error:
                from web_backend.services.projects import ProjectBusy

                raise ProjectBusy("project write lock is busy") from error

    def _require_generate_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.GENERATE_CREATIVE not in workflow.available_actions:
            raise ActionNotAllowed("Creative generation is not allowed")

    def _require_approve_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.APPROVE_CREATIVE not in workflow.available_actions:
            raise ActionNotAllowed("Creative approval is not allowed")

    def _require_revise_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.REVISE_CREATIVE not in workflow.available_actions:
            raise ActionNotAllowed("Creative revision is not allowed")

    def _require_regenerate_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.REGENERATE_CREATIVE not in workflow.available_actions:
            raise ActionNotAllowed("Creative regeneration is not allowed")

    def _require_storyboard_generate_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.GENERATE_STORYBOARD not in workflow.available_actions:
            raise ActionNotAllowed("Storyboard generation is not allowed")

    def _require_no_active_task(self, project_id: str) -> None:
        if self._task_service.active_for_project(project_id) is not None:
            from web_backend.services.projects import ProjectBusy

            raise ProjectBusy("project already has an active Web task")

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

    def _run_revise(
        self,
        project_id: str,
        feedback: str,
    ) -> TaskResultReference:
        try:
            self._require_revise_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许修改创意。",
            )
        return self._run_revision_core(
            project_id,
            operation=TaskOperation.CREATIVE_REVISE,
            feedback=feedback,
        )

    def _run_regenerate(self, project_id: str) -> TaskResultReference:
        try:
            self._require_regenerate_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许重新生成创意。",
            )
        return self._run_revision_core(
            project_id,
            operation=TaskOperation.CREATIVE_REGENERATE,
        )

    def _run_revision_core(
        self,
        project_id: str,
        *,
        operation: TaskOperation,
        feedback: str | None = None,
    ) -> TaskResultReference:
        deepseek_key = self._capability_service.deepseek_api_key()
        if not deepseek_key:
            _task_failure(
                "CAPABILITY_UNAVAILABLE",
                "创意生成服务尚未配置。",
                retryable=True,
            )

        from pydantic import ValidationError

        from creative_workflow import (
            CreativeRevisionError,
            load_creative_brief,
            regenerate_creative_stage,
            revise_creative_stage,
        )
        from evaluation import EvaluationRecorder
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint, ProjectStateError
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
            current = load_creative_brief(paths)
        except (KeyError, ValidationError, ProjectStateError, CreativeRevisionError):
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
            if operation is TaskOperation.CREATIVE_REVISE:
                revise_creative_stage(
                    paths,
                    request,
                    checkpoint,
                    current,
                    feedback or "",
                    deepseek_key,
                    logger,
                    evaluation_recorder=evaluation_recorder,
                    reference_asset_context=reference_context,
                )
            else:
                regenerate_creative_stage(
                    paths,
                    request,
                    checkpoint,
                    deepseek_key,
                    logger,
                    evaluation_recorder=evaluation_recorder,
                    reference_asset_context=reference_context,
                )
        except CreativeRevisionError:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许更新创意。",
            )
        except Exception as error:
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

    def _run_storyboard_generate(self, project_id: str) -> TaskResultReference:
        try:
            self._require_storyboard_generate_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许生成分镜。",
            )

        deepseek_key = self._capability_service.deepseek_api_key()
        if not deepseek_key:
            _task_failure(
                "CAPABILITY_UNAVAILABLE",
                "分镜生成服务尚未配置。",
                retryable=True,
            )

        from pydantic import ValidationError

        from evaluation import EvaluationRecorder
        from project_manager import ProjectDirectoryError, create_project_paths
        from project_state import ProjectCheckpoint, ProjectStateError, StageStatus
        from prompt_generator import ProductVideoRequest, PromptGenerationError
        from reference_assets import ReferenceAssetManager
        from storyboard import StoryboardError
        from storyboard_workflow import (
            StoryboardStageDataError,
            StoryboardStageStateError,
            generate_storyboard_stage,
        )
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
            generate_storyboard_stage(
                paths,
                request,
                checkpoint,
                deepseek_key,
                logger,
                evaluation_recorder=evaluation_recorder,
                reference_asset_context=reference_context,
            )
        except StoryboardStageStateError:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许生成分镜。",
            )
        except StoryboardStageDataError:
            _task_failure(
                "PROJECT_DATA_CORRUPT",
                "已审核创意无法读取。",
            )
        except Exception as error:
            if checkpoint.status == StageStatus.RUNNING.value:
                checkpoint.fail(error)
            logger.error(error, stage="storyboard")
            if isinstance(error, PromptGenerationError):
                _task_failure(
                    "PROVIDER_REQUEST_FAILED",
                    "分镜生成服务暂时不可用，请稍后重试。",
                    retryable=True,
                )
            if isinstance(error, StoryboardError):
                if "SCHEDULE_UNSATISFIABLE" in str(error):
                    _task_failure(
                        "SCHEDULE_UNSATISFIABLE",
                        "分镜视听时间规划无法满足当前约束。",
                        retryable=True,
                    )
                _task_failure(
                    "STORYBOARD_OUTPUT_INVALID",
                    "分镜生成结果无法使用。",
                    retryable=True,
                )
            if isinstance(error, ProjectDirectoryError):
                _task_failure(
                    "PROJECT_WRITE_FAILED",
                    "分镜结果无法保存。",
                    retryable=True,
                )
            raise

        return TaskResultReference(
            resource_type="STORYBOARD",
            resource_id=project_id,
        )
