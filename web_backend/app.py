"""Import-safe FastAPI application for the local Web V1 backend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from ipaddress import ip_address
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web_backend.errors import register_exception_handlers
from web_backend.locking import DEFAULT_PROJECT_LOCK_MANAGER, ProjectLockManager
from web_backend.middleware import CorrelationIdMiddleware
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.repositories.reference_asset_repository import (
    ReferenceAssetRepository,
)
from web_backend.repositories.planning_content_repository import (
    PlanningContentRepository,
)
from web_backend.repositories.postproduction_repository import (
    PostProductionRepository,
)
from web_backend.repositories.prompt_revision_repository import (
    PromptRevisionDraftRepository,
)
from web_backend.repositories.shot_repository import ShotRepository
from web_backend.repositories.task_repository import TaskRepository
from web_backend.routers.capabilities import router as capabilities_router
from web_backend.routers.health import router as health_router
from web_backend.routers.planning_actions import router as planning_actions_router
from web_backend.routers.projects import router as projects_router
from web_backend.routers.prompt_revision import router as prompt_revision_router
from web_backend.routers.shot_generation import router as shot_generation_router
from web_backend.routers.tasks import router as tasks_router
from web_backend.routers.voice import router as voice_router
from web_backend.routers.subtitle import router as subtitle_router
from web_backend.routers.assembly_execution import router as assembly_execution_router
from web_backend.services.capabilities import CapabilityService
from web_backend.services.assembly_planning import AssemblyPlanningService
from web_backend.services.assembly_execution import AssemblyExecutionService
from web_backend.services.planning_actions import CreativeActionService
from web_backend.services.projects import ProjectService
from web_backend.services.prompt_revision import PromptRevisionDraftService
from web_backend.services.reference_assets import ReferenceAssetUploadService
from web_backend.services.shot_generation_preflight import (
    ShotGenerationPreflightService,
)
from web_backend.services.shot_generation import ShotGenerationActionService
from web_backend.services.multishot_generation import MultiShotGenerationService
from web_backend.services.shot_approval import ShotApprovalService
from web_backend.services.shot_versions import ShotVersionService
from web_backend.services.task_runner import TaskRunner
from web_backend.services.tasks import TaskService
from web_backend.services.voice import VoiceWebService
from web_backend.services.subtitle import SubtitleWebService
from web_backend.settings import BackendSettings


lifecycle_logger = logging.getLogger("uvicorn.error.web_lifecycle")
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent


def _is_loopback_host(host: str) -> bool:
    normalized = str(host).strip().strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _initialize_local_resources(application: FastAPI) -> None:
    """Build lightweight objects only; constructors perform no project I/O."""

    if (
        getattr(application.state, "local_resources_initialized", False)
        and not application.state.task_runner.is_shutdown
    ):
        return

    settings = application.state.settings
    lock_manager = application.state.project_lock_manager
    application.state.project_repository = ProjectRepository(settings.projects_root)
    application.state.planning_content_repository = PlanningContentRepository(
        application.state.project_repository
    )
    application.state.shot_repository = ShotRepository(
        application.state.project_repository
    )
    application.state.reference_asset_repository = ReferenceAssetRepository(
        application.state.project_repository
    )
    application.state.reference_asset_upload_service = ReferenceAssetUploadService(
        application.state.project_repository,
        application.state.reference_asset_repository,
        lock_manager,
    )
    application.state.postproduction_repository = PostProductionRepository(
        application.state.project_repository
    )
    application.state.assembly_planning_service = AssemblyPlanningService(
        application.state.project_repository,
        application.state.shot_repository,
        lock_manager,
    )
    application.state.task_repository = TaskRepository(settings.web_runtime_root)
    application.state.prompt_revision_draft_repository = (
        PromptRevisionDraftRepository(settings.web_runtime_root)
    )
    application.state.task_runner = TaskRunner(
        application.state.task_repository,
        lock_manager,
        max_workers=settings.task_workers,
    )
    application.state.task_service = TaskService(
        application.state.task_repository,
        application.state.task_runner,
        application.state.project_repository,
    )
    application.state.assembly_execution_service = AssemblyExecutionService(
        application.state.project_repository,
        application.state.assembly_planning_service,
        application.state.task_service,
    )
    application.state.project_service = ProjectService(
        settings.projects_root,
        lock_manager,
    )
    application.state.capability_service = CapabilityService()
    application.state.voice_web_service = VoiceWebService(
        application.state.project_repository,
        application.state.postproduction_repository,
        application.state.task_service,
        application.state.capability_service,
        lock_manager,
    )
    application.state.subtitle_web_service = SubtitleWebService(
        application.state.project_repository,
        application.state.postproduction_repository,
        application.state.task_service,
        lock_manager,
    )
    application.state.prompt_revision_draft_service = PromptRevisionDraftService(
        application.state.project_repository,
        application.state.reference_asset_repository,
        application.state.prompt_revision_draft_repository,
        application.state.task_service,
        application.state.capability_service,
        lock_manager,
    )
    application.state.shot_generation_preflight_service = (
        ShotGenerationPreflightService(
            application.state.project_repository,
            application.state.reference_asset_repository,
        )
    )
    application.state.shot_generation_action_service = ShotGenerationActionService(
        application.state.project_repository,
        application.state.reference_asset_repository,
        application.state.shot_generation_preflight_service,
        application.state.task_service,
        application.state.capability_service,
        lock_manager,
    )
    application.state.multishot_generation_service = MultiShotGenerationService(
        application.state.shot_repository,
        application.state.shot_generation_preflight_service,
        application.state.shot_generation_action_service,
        application.state.task_service,
        max_parallel=settings.task_workers,
    )
    application.state.shot_approval_service = ShotApprovalService(
        application.state.project_repository,
        application.state.shot_repository,
        application.state.task_service,
        lock_manager,
    )
    application.state.shot_version_service = ShotVersionService(
        application.state.project_repository,
        application.state.shot_repository,
        application.state.task_service,
        lock_manager,
    )
    application.state.creative_action_service = CreativeActionService(
        application.state.project_repository,
        application.state.task_service,
        application.state.capability_service,
        lock_manager,
    )
    application.state.local_resources_initialized = True


@asynccontextmanager
async def backend_lifespan(application: FastAPI):
    # Match the CLI credential source without exposing values through settings
    # or capability responses. Loading configuration performs no provider call.
    load_dotenv(REPOSITORY_ROOT / ".env")
    _initialize_local_resources(application)
    interrupted = application.state.task_service.recover_interrupted_tasks()
    if interrupted:
        lifecycle_logger.warning(
            "Marked %d abandoned Web task(s) as INTERRUPTED without replay",
            len(interrupted),
        )
    application.state.lifecycle_started = True
    settings = application.state.settings
    if not _is_loopback_host(settings.host):
        lifecycle_logger.warning(
            "Web backend host is not loopback; use an explicit trusted network boundary"
        )
    try:
        yield
    finally:
        application.state.task_runner.shutdown()
        application.state.local_resources_initialized = False
        application.state.lifecycle_started = False


def create_app(
    *,
    settings: BackendSettings | None = None,
    lock_manager: ProjectLockManager | None = None,
) -> FastAPI:
    application = FastAPI(
        title="AI Product Video Agent API",
        version="v1",
        lifespan=backend_lifespan,
    )
    application.state.settings = settings or BackendSettings.from_environment()
    application.state.project_lock_manager = (
        lock_manager or DEFAULT_PROJECT_LOCK_MANAGER
    )
    # Preserve compatibility with non-context-managed TestClient callers while
    # lifespan reinitializes the same lightweight resource graph at startup.
    _initialize_local_resources(application)
    application.state.lifecycle_started = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(application.state.settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Correlation-ID"],
        expose_headers=["Location", "X-Correlation-ID"],
        max_age=600,
    )
    application.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router, prefix="/api")
    application.include_router(capabilities_router, prefix="/api")
    application.include_router(projects_router, prefix="/api")
    application.include_router(shot_generation_router, prefix="/api")
    application.include_router(prompt_revision_router, prefix="/api")
    application.include_router(planning_actions_router, prefix="/api")
    application.include_router(tasks_router, prefix="/api")
    application.include_router(assembly_execution_router, prefix="/api")
    application.include_router(voice_router, prefix="/api")
    application.include_router(subtitle_router, prefix="/api")
    return application


app = create_app()
