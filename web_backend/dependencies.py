"""FastAPI dependency accessors for application-scoped backend resources."""

from __future__ import annotations

from fastapi import Request

from web_backend.locking import ProjectLockManager
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
from web_backend.services.capabilities import CapabilityService
from web_backend.services.planning_actions import CreativeActionService
from web_backend.services.projects import ProjectService
from web_backend.services.prompt_revision import PromptRevisionDraftService
from web_backend.services.reference_assets import ReferenceAssetUploadService
from web_backend.services.shot_generation_preflight import (
    ShotGenerationPreflightService,
)
from web_backend.services.shot_generation import ShotGenerationActionService
from web_backend.services.shot_approval import ShotApprovalService
from web_backend.services.shot_versions import ShotVersionService
from web_backend.services.task_runner import TaskRunner
from web_backend.services.tasks import TaskService
from web_backend.settings import BackendSettings


def get_settings(request: Request) -> BackendSettings:
    return request.app.state.settings


def get_project_repository(request: Request) -> ProjectRepository:
    return request.app.state.project_repository


def get_planning_content_repository(request: Request) -> PlanningContentRepository:
    return request.app.state.planning_content_repository


def get_shot_repository(request: Request) -> ShotRepository:
    return request.app.state.shot_repository


def get_reference_asset_repository(request: Request) -> ReferenceAssetRepository:
    return request.app.state.reference_asset_repository


def get_reference_asset_upload_service(request: Request) -> ReferenceAssetUploadService:
    return request.app.state.reference_asset_upload_service


def get_postproduction_repository(request: Request) -> PostProductionRepository:
    return request.app.state.postproduction_repository


def get_task_repository(request: Request) -> TaskRepository:
    return request.app.state.task_repository


def get_prompt_revision_draft_repository(
    request: Request,
) -> PromptRevisionDraftRepository:
    return request.app.state.prompt_revision_draft_repository


def get_project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


def get_project_lock_manager(request: Request) -> ProjectLockManager:
    return request.app.state.project_lock_manager


def get_capability_service(request: Request) -> CapabilityService:
    return request.app.state.capability_service


def get_task_runner(request: Request) -> TaskRunner:
    return request.app.state.task_runner


def get_task_service(request: Request) -> TaskService:
    return request.app.state.task_service


def get_prompt_revision_draft_service(
    request: Request,
) -> PromptRevisionDraftService:
    return request.app.state.prompt_revision_draft_service


def get_creative_action_service(request: Request) -> CreativeActionService:
    return request.app.state.creative_action_service


def get_shot_generation_preflight_service(
    request: Request,
) -> ShotGenerationPreflightService:
    return request.app.state.shot_generation_preflight_service


def get_shot_generation_action_service(
    request: Request,
) -> ShotGenerationActionService:
    return request.app.state.shot_generation_action_service


def get_shot_approval_service(request: Request) -> ShotApprovalService:
    return request.app.state.shot_approval_service


def get_shot_version_service(request: Request) -> ShotVersionService:
    return request.app.state.shot_version_service
