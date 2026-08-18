"""Read-only project discovery, detail, and workflow endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse

from web_backend.dependencies import (
    get_planning_content_repository,
    get_project_repository,
    get_project_service,
    get_shot_repository,
)
from web_backend.models.planning import (
    CreativeContentResponse,
    StoryboardContentResponse,
    VideoPromptsContentResponse,
)
from web_backend.errors import registered_api_error
from web_backend.models.projects import (
    ProjectDetail,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectListResponse,
    ProjectWorkflowResponse,
)
from web_backend.models.shots import ShotDetail, ShotListResponse
from web_backend.services.projects import (
    InvalidProjectName,
    InvalidProjectRequest,
    InvalidVideoDuration,
    ProjectBusy,
    ProjectCreateFailed,
    ProjectService,
)
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
    ProjectRepository,
    ProjectRepositoryError,
)
from web_backend.repositories.planning_content_repository import (
    PlanningContentRepository,
)
from web_backend.repositories.shot_repository import (
    InvalidShotId,
    InvalidShotVersion,
    ShotDataCorrupt,
    ShotNotFound,
    ShotRepository,
    VideoNotFound,
)


router = APIRouter(tags=["projects"])


_ERROR_CODE_BY_EXCEPTION: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    InvalidProjectName: "INVALID_PROJECT_NAME",
    InvalidVideoDuration: "INVALID_VIDEO_DURATION",
    InvalidProjectRequest: "INVALID_PROJECT_REQUEST",
    ProjectBusy: "PROJECT_BUSY",
    ProjectCreateFailed: "PROJECT_CREATE_FAILED",
    InvalidShotId: "INVALID_SHOT_ID",
    ShotNotFound: "SHOT_NOT_FOUND",
    ShotDataCorrupt: "SHOT_DATA_CORRUPT",
    InvalidShotVersion: "INVALID_SHOT_VERSION",
    VideoNotFound: "VIDEO_NOT_FOUND",
}


def _raise_mapped_error(error: Exception) -> NoReturn:
    code = _ERROR_CODE_BY_EXCEPTION.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


@router.post(
    "/projects",
    response_model=ProjectCreateResponse,
    status_code=201,
)
def create_project(
    payload: ProjectCreateRequest,
    response: Response,
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectCreateResponse:
    try:
        created = service.create_project(payload)
    except (
        InvalidProjectName,
        InvalidProjectRequest,
        InvalidVideoDuration,
        ProjectBusy,
        ProjectCreateFailed,
    ) as error:
        _raise_mapped_error(error)
    response.headers["Location"] = f"/api/projects/{created.project_id}"
    return created


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectListResponse:
    return repository.list_projects()


@router.get("/projects/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: str,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectDetail:
    try:
        return repository.get_project(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/workflow",
    response_model=ProjectWorkflowResponse,
)
async def get_project_workflow(
    project_id: str,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectWorkflowResponse:
    try:
        return repository.get_workflow(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/planning/creative",
    response_model=CreativeContentResponse,
)
async def get_project_creative_content(
    project_id: str,
    repository: Annotated[
        PlanningContentRepository, Depends(get_planning_content_repository)
    ],
) -> CreativeContentResponse:
    try:
        return repository.get_creative(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/planning/storyboard",
    response_model=StoryboardContentResponse,
)
async def get_project_storyboard_content(
    project_id: str,
    repository: Annotated[
        PlanningContentRepository, Depends(get_planning_content_repository)
    ],
) -> StoryboardContentResponse:
    try:
        return repository.get_storyboard(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/planning/video-prompts",
    response_model=VideoPromptsContentResponse,
)
async def get_project_video_prompts_content(
    project_id: str,
    repository: Annotated[
        PlanningContentRepository, Depends(get_planning_content_repository)
    ],
) -> VideoPromptsContentResponse:
    try:
        return repository.get_video_prompts(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/shots",
    response_model=ShotListResponse,
)
async def get_project_shots(
    project_id: str,
    repository: Annotated[ShotRepository, Depends(get_shot_repository)],
) -> ShotListResponse:
    try:
        return repository.list_shots(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/shots/{shot_id}",
    response_model=ShotDetail,
)
async def get_project_shot(
    project_id: str,
    shot_id: str,
    repository: Annotated[ShotRepository, Depends(get_shot_repository)],
) -> ShotDetail:
    try:
        return repository.get_shot(project_id, shot_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/shots/{shot_id}/versions/{version}/video",
    response_class=FileResponse,
)
async def get_project_shot_video(
    project_id: str,
    shot_id: str,
    version: str,
    repository: Annotated[ShotRepository, Depends(get_shot_repository)],
) -> FileResponse:
    try:
        path = repository.resolve_video(project_id, shot_id, version)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
    )
