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
    TaskOperation,
    TaskRecord,
    TaskResultReference,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
)
from web_backend.services.capabilities import CapabilityService
from web_backend.services.task_failures import raise_task_failure as _task_failure
from web_backend.services.tasks import TaskService


class PlanningActionError(RuntimeError):
    """Base class for safe synchronous planning-action failures."""


class ActionNotAllowed(PlanningActionError):
    pass


class CapabilityUnavailable(PlanningActionError):
    pass


def _raise_creative_task_failure(error: Exception) -> None:
    """Translate Creative failures by type without inspecting private messages."""

    from prompt_generator import (
        PromptGenerationError,
        StructuredOutputExhaustedError,
    )
    from storyboard import StoryboardError

    if isinstance(error, StructuredOutputExhaustedError):
        _task_failure(
            "CREATIVE_OUTPUT_INVALID",
            "AI返回的创意内容未通过校验，可以重新尝试生成。",
            retryable=True,
        )
    if isinstance(error, PromptGenerationError):
        _task_failure(
            "PROVIDER_REQUEST_FAILED",
            "创意生成服务暂时不可用，请稍后重试。",
            retryable=True,
        )
    if isinstance(error, StoryboardError):
        _task_failure(
            "CREATIVE_OUTPUT_INVALID",
            "AI返回的创意内容未通过校验，可以重新尝试生成。",
            retryable=True,
        )


def _raise_video_prompt_task_failure(error: Exception) -> None:
    """Translate Video Prompt failures without exposing per-Shot content."""

    from project_manager import ProjectDirectoryError
    from prompt_generator import (
        PromptGenerationError,
        StructuredOutputExhaustedError,
    )
    from storyboard import StoryboardError, VideoPromptStructureError
    from shot_review import ShotReviewError

    if isinstance(error, (StructuredOutputExhaustedError, VideoPromptStructureError)):
        _task_failure(
            "VIDEO_PROMPT_OUTPUT_INVALID",
            "部分镜头的视频提示词未通过校验，可以重新尝试。",
            retryable=True,
        )
    if isinstance(error, PromptGenerationError):
        _task_failure(
            "PROVIDER_REQUEST_FAILED",
            "视频提示词生成服务暂时不可用，请稍后重试。",
            retryable=True,
        )
    if isinstance(error, ProjectDirectoryError):
        _task_failure(
            "PROJECT_WRITE_FAILED",
            "视频提示词结果无法保存。",
            retryable=True,
        )
    if isinstance(error, (StoryboardError, ShotReviewError)):
        _task_failure(
            "VIDEO_PROMPT_PROCESSING_FAILED",
            "视频提示词生成进度无法安全处理，可以重新尝试。",
            retryable=True,
        )


class CreativeActionService:
    """Own the supported durable planning-stage Web actions."""

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

    def submit_retry(
        self,
        project_id: str,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        self._require_no_active_task(canonical_project_id)
        self._require_retry_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.CREATIVE_RETRY,
            correlation_id=correlation_id,
            callable_=lambda: self._run_retry(canonical_project_id),
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

    def submit_storyboard_revise(
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
        self._require_storyboard_revise_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.STORYBOARD_REVISE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_storyboard_revise(
                canonical_project_id,
                feedback,
            ),
        )

    def submit_storyboard_regenerate(
        self,
        project_id: str,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        self._require_no_active_task(canonical_project_id)
        self._require_storyboard_regenerate_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.STORYBOARD_REGENERATE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_storyboard_regenerate(
                canonical_project_id
            ),
        )

    def submit_video_prompt_generate(
        self,
        project_id: str,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        self._require_no_active_task(canonical_project_id)
        self._require_video_prompt_generate_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.VIDEO_PROMPT_GENERATE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_video_prompt_generate(
                canonical_project_id
            ),
        )

    def submit_video_prompt_revise(
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
        self._require_video_prompt_revise_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.VIDEO_PROMPT_REVISE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_video_prompt_revise(
                canonical_project_id,
                feedback,
            ),
        )

    def submit_video_prompt_regenerate(
        self,
        project_id: str,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        self._require_no_active_task(canonical_project_id)
        self._require_video_prompt_regenerate_allowed(canonical_project_id)
        if not self._deepseek_available():
            raise CapabilityUnavailable("planning provider is not configured")
        return self._task_service.submit(
            project_id=canonical_project_id,
            operation=TaskOperation.VIDEO_PROMPT_REGENERATE,
            correlation_id=correlation_id,
            callable_=lambda: self._run_video_prompt_regenerate(
                canonical_project_id
            ),
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

    def approve_storyboard(self, project_id: str) -> ProjectWorkflowResponse:
        """Synchronously approve Storyboard under the project write lock."""

        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        with self._task_service.prevent_task_submission():
            self._require_no_active_task(canonical_project_id)
            self._require_storyboard_approve_allowed(canonical_project_id)

            try:
                with self._project_lock_manager.project_write(canonical_project_id):
                    # Re-read task and workflow state while holding the write
                    # lock so a stale page cannot approve a changed project.
                    self._require_no_active_task(canonical_project_id)
                    self._require_storyboard_approve_allowed(canonical_project_id)

                    from project_manager import create_project_paths
                    from project_state import ProjectCheckpoint, ProjectStateError
                    from storyboard_workflow import (
                        StoryboardApprovalError,
                        approve_storyboard_stage,
                    )

                    try:
                        paths = create_project_paths(
                            self._project_repository.resolve_project_dir(
                                canonical_project_id
                            ),
                            ensure_directories=False,
                        )
                        checkpoint = ProjectCheckpoint.load(paths)
                        approve_storyboard_stage(checkpoint)
                    except StoryboardApprovalError as error:
                        raise ActionNotAllowed(
                            "Storyboard approval is not allowed"
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

    def approve_video_prompts(self, project_id: str) -> ProjectWorkflowResponse:
        """Synchronously approve complete Video Prompts under the project lock."""

        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        with self._task_service.prevent_task_submission():
            self._require_no_active_task(canonical_project_id)
            self._require_video_prompt_approve_allowed(canonical_project_id)

            try:
                with self._project_lock_manager.project_write(canonical_project_id):
                    # Revalidate both the active-task barrier and canonical
                    # workflow state after acquiring the write lock.
                    self._require_no_active_task(canonical_project_id)
                    self._require_video_prompt_approve_allowed(
                        canonical_project_id
                    )

                    from project_manager import create_project_paths
                    from project_state import ProjectCheckpoint, ProjectStateError
                    from video_prompt_workflow import (
                        VideoPromptApprovalError,
                        approve_video_prompts_stage,
                    )

                    try:
                        paths = create_project_paths(
                            self._project_repository.resolve_project_dir(
                                canonical_project_id
                            ),
                            ensure_directories=False,
                        )
                        checkpoint = ProjectCheckpoint.load(paths)
                        approve_video_prompts_stage(paths, checkpoint)
                    except VideoPromptApprovalError as error:
                        raise ActionNotAllowed(
                            "Video Prompt approval is not allowed"
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

    def _require_retry_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.RETRY_GENERATE_CREATIVE not in workflow.available_actions:
            raise ActionNotAllowed("Creative retry is not allowed")

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

    def _require_storyboard_approve_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.APPROVE_STORYBOARD not in workflow.available_actions:
            raise ActionNotAllowed("Storyboard approval is not allowed")

    def _require_storyboard_revise_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.REVISE_STORYBOARD not in workflow.available_actions:
            raise ActionNotAllowed("Storyboard revision is not allowed")

    def _require_storyboard_regenerate_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.REGENERATE_STORYBOARD not in workflow.available_actions:
            raise ActionNotAllowed("Storyboard regeneration is not allowed")

    def _require_video_prompt_generate_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.GENERATE_VIDEO_PROMPTS not in workflow.available_actions:
            raise ActionNotAllowed("Video Prompt generation is not allowed")

    def _require_video_prompt_approve_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if AvailableAction.APPROVE_VIDEO_PROMPTS not in workflow.available_actions:
            raise ActionNotAllowed("Video Prompt approval is not allowed")

    def _require_video_prompt_revise_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if (
            workflow.stages.video_prompt.status != "WAITING_REVIEW"
            or AvailableAction.REVISE_VIDEO_PROMPTS
            not in workflow.available_actions
        ):
            raise ActionNotAllowed("Video Prompt revision is not allowed")

    def _require_video_prompt_regenerate_allowed(self, project_id: str) -> None:
        workflow = self._project_repository.get_workflow(project_id)
        if (
            workflow.stages.video_prompt.status != "WAITING_REVIEW"
            or AvailableAction.REGENERATE_VIDEO_PROMPTS
            not in workflow.available_actions
        ):
            raise ActionNotAllowed("Video Prompt regeneration is not allowed")

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
        from prompt_generator import ProductVideoRequest
        from reference_assets import ReferenceAssetManager
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
            _raise_creative_task_failure(error)
            raise

        return TaskResultReference(
            resource_type="CREATIVE",
            resource_id=project_id,
        )

    def _run_retry(self, project_id: str) -> TaskResultReference:
        # TaskRunner holds the per-project write lock before this revalidation.
        try:
            self._require_retry_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许重新尝试生成创意。",
            )

        deepseek_key = self._capability_service.deepseek_api_key()
        if not deepseek_key:
            _task_failure(
                "CAPABILITY_UNAVAILABLE",
                "创意生成服务尚未配置。",
                retryable=True,
            )

        from pydantic import ValidationError

        from creative_workflow import (
            CreativeRecoveryError,
            retry_failed_creative_stage,
        )
        from evaluation import EvaluationRecorder
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint, ProjectStateError, StageStatus
        from prompt_generator import ProductVideoRequest
        from reference_assets import ReferenceAssetManager
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
            retry_failed_creative_stage(
                paths,
                request,
                checkpoint,
                deepseek_key,
                logger,
                evaluation_recorder=evaluation_recorder,
                reference_asset_context=reference_context,
            )
        except CreativeRecoveryError:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许重新尝试生成创意。",
            )
        except Exception as error:
            if checkpoint.status == StageStatus.RUNNING.value:
                checkpoint.fail(error)
            logger.error(error, stage="creative")
            _raise_creative_task_failure(error)
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
        from prompt_generator import ProductVideoRequest
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
            _raise_creative_task_failure(error)
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

    def _run_video_prompt_generate(self, project_id: str) -> TaskResultReference:
        # TaskRunner owns the per-project write lock around this callable.
        try:
            self._require_video_prompt_generate_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许生成视频提示词。",
            )

        deepseek_key = self._capability_service.deepseek_api_key()
        if not deepseek_key:
            _task_failure(
                "CAPABILITY_UNAVAILABLE",
                "视频提示词生成服务尚未配置。",
                retryable=True,
            )

        from pydantic import ValidationError

        from evaluation import EvaluationRecorder
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint, ProjectStateError, StageStatus
        from prompt_generator import ProductVideoRequest
        from reference_assets import ReferenceAssetManager
        from task_logger import TaskLogger
        from video_prompt_workflow import (
            VideoPromptStageDataError,
            VideoPromptStageStateError,
            generate_video_prompts_stage,
        )

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
            generate_video_prompts_stage(
                paths,
                request,
                checkpoint,
                deepseek_key,
                logger,
                evaluation_recorder=evaluation_recorder,
                reference_asset_context=reference_context,
            )
        except VideoPromptStageStateError:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许生成视频提示词。",
            )
        except VideoPromptStageDataError:
            _task_failure(
                "PROJECT_DATA_CORRUPT",
                "已审核的创意或分镜无法读取。",
            )
        except Exception as error:
            if checkpoint.status == StageStatus.RUNNING.value:
                checkpoint.fail(error)
            logger.error(error, stage="video_prompt")
            _raise_video_prompt_task_failure(error)
            raise

        return TaskResultReference(
            resource_type="VIDEO_PROMPTS",
            resource_id=project_id,
        )

    def _run_video_prompt_revise(
        self,
        project_id: str,
        feedback: str,
    ) -> TaskResultReference:
        try:
            self._require_video_prompt_revise_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许修改视频提示词。",
            )
        return self._run_video_prompt_revision_core(
            project_id,
            operation=TaskOperation.VIDEO_PROMPT_REVISE,
            feedback=feedback,
        )

    def _run_video_prompt_regenerate(
        self, project_id: str
    ) -> TaskResultReference:
        try:
            self._require_video_prompt_regenerate_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许重新生成视频提示词。",
            )
        return self._run_video_prompt_revision_core(
            project_id,
            operation=TaskOperation.VIDEO_PROMPT_REGENERATE,
        )

    def _run_video_prompt_revision_core(
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
                "视频提示词生成服务尚未配置。",
                retryable=True,
            )

        from pydantic import ValidationError

        from evaluation import EvaluationRecorder
        from project_manager import create_project_paths
        from project_state import ProjectCheckpoint, ProjectStateError
        from prompt_generator import ProductVideoRequest
        from reference_assets import ReferenceAssetManager
        from task_logger import TaskLogger
        from video_prompt_workflow import (
            VideoPromptRevisionError,
            VideoPromptStageDataError,
            load_video_prompt_plan,
            regenerate_video_prompts_stage,
            revise_video_prompts_stage,
        )

        try:
            paths = create_project_paths(
                self._project_repository.resolve_project_dir(project_id)
            )
            checkpoint = ProjectCheckpoint.load(paths)
            request = ProductVideoRequest.model_validate(checkpoint.data["request"])
            current = load_video_prompt_plan(paths)
        except (
            KeyError,
            ValidationError,
            ProjectStateError,
            VideoPromptStageDataError,
        ):
            _task_failure(
                "PROJECT_DATA_CORRUPT",
                "项目数据或视频提示词无法读取。",
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
            if operation is TaskOperation.VIDEO_PROMPT_REVISE:
                revise_video_prompts_stage(
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
                regenerate_video_prompts_stage(
                    paths,
                    request,
                    checkpoint,
                    deepseek_key,
                    logger,
                    evaluation_recorder=evaluation_recorder,
                    reference_asset_context=reference_context,
                )
        except VideoPromptRevisionError:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许更新视频提示词。",
            )
        except VideoPromptStageDataError:
            _task_failure(
                "PROJECT_DATA_CORRUPT",
                "视频提示词或已审核规划内容无法读取。",
            )
        except Exception as error:
            logger.error(error, stage="video_prompt")
            _raise_video_prompt_task_failure(error)
            raise

        return TaskResultReference(
            resource_type="VIDEO_PROMPTS",
            resource_id=project_id,
        )

    def _run_storyboard_revise(
        self,
        project_id: str,
        feedback: str,
    ) -> TaskResultReference:
        try:
            self._require_storyboard_revise_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许修改分镜。",
            )
        return self._run_storyboard_revision_core(
            project_id,
            operation=TaskOperation.STORYBOARD_REVISE,
            feedback=feedback,
        )

    def _run_storyboard_regenerate(self, project_id: str) -> TaskResultReference:
        try:
            self._require_storyboard_regenerate_allowed(project_id)
        except ActionNotAllowed:
            _task_failure(
                "ACTION_NOT_ALLOWED",
                "当前项目状态不允许重新生成分镜。",
            )
        return self._run_storyboard_revision_core(
            project_id,
            operation=TaskOperation.STORYBOARD_REGENERATE,
        )

    def _run_storyboard_revision_core(
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
                "分镜生成服务尚未配置。",
                retryable=True,
            )

        from pydantic import ValidationError

        from evaluation import EvaluationRecorder
        from project_manager import ProjectDirectoryError, create_project_paths
        from project_state import ProjectCheckpoint, ProjectStateError
        from prompt_generator import ProductVideoRequest, PromptGenerationError
        from reference_assets import ReferenceAssetManager
        from storyboard import StoryboardError
        from storyboard_workflow import (
            StoryboardStageDataError,
            StoryboardStageStateError,
            load_storyboard,
            regenerate_storyboard_stage,
            revise_storyboard_stage,
        )
        from task_logger import TaskLogger

        try:
            paths = create_project_paths(
                self._project_repository.resolve_project_dir(project_id)
            )
            checkpoint = ProjectCheckpoint.load(paths)
            request = ProductVideoRequest.model_validate(checkpoint.data["request"])
            current = load_storyboard(paths)
        except (
            KeyError,
            ValidationError,
            ProjectStateError,
            StoryboardStageDataError,
        ):
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
            if operation is TaskOperation.STORYBOARD_REVISE:
                revise_storyboard_stage(
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
                regenerate_storyboard_stage(
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
                "当前项目状态不允许更新分镜。",
            )
        except StoryboardStageDataError:
            _task_failure(
                "PROJECT_DATA_CORRUPT",
                "分镜或已审核创意无法读取。",
            )
        except Exception as error:
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
