"""Read-only project discovery, detail, and workflow endpoints."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Request, Response

from web_backend.errors import WebApiError
from web_backend.models.projects import (
    ProjectDetail,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectListResponse,
    ProjectWorkflowResponse,
)
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


router = APIRouter(tags=["projects"])


def _repository(request: Request) -> ProjectRepository:
    return ProjectRepository(request.app.state.settings.projects_root)


def _project_service(request: Request) -> ProjectService:
    return ProjectService(
        request.app.state.settings.projects_root,
        request.app.state.project_lock_manager,
    )


def _raise_api_error(error: ProjectRepositoryError) -> NoReturn:
    if isinstance(error, InvalidProjectId):
        raise WebApiError(
            status_code=422,
            error_type="PROJECT_ERROR",
            code="INVALID_PROJECT_ID",
            message="项目标识无效。",
        ) from error
    if isinstance(error, ProjectNotFound):
        raise WebApiError(
            status_code=404,
            error_type="PROJECT_ERROR",
            code="PROJECT_NOT_FOUND",
            message="项目不存在。",
        ) from error
    if isinstance(error, ProjectDataCorrupt):
        raise WebApiError(
            status_code=422,
            error_type="PROJECT_ERROR",
            code="PROJECT_DATA_CORRUPT",
            message="项目数据无法读取。",
        ) from error
    if isinstance(error, ProjectDataUnsupported):
        raise WebApiError(
            status_code=422,
            error_type="PROJECT_ERROR",
            code="PROJECT_DATA_UNSUPPORTED",
            message="项目数据版本暂不支持。",
        ) from error
    raise error


def _raise_create_error(error: Exception) -> NoReturn:
    if isinstance(error, InvalidProjectName):
        raise WebApiError(
            status_code=422,
            error_type="VALIDATION_ERROR",
            code="INVALID_PROJECT_NAME",
            message="项目名称无效。",
        ) from error
    if isinstance(error, InvalidVideoDuration):
        raise WebApiError(
            status_code=422,
            error_type="VALIDATION_ERROR",
            code="INVALID_VIDEO_DURATION",
            message="视频总时长必须由支持的镜头时长组合构成。",
        ) from error
    if isinstance(error, InvalidProjectRequest):
        raise WebApiError(
            status_code=422,
            error_type="VALIDATION_ERROR",
            code="INVALID_PROJECT_REQUEST",
            message="产品需求无效。",
        ) from error
    if isinstance(error, ProjectBusy):
        raise WebApiError(
            status_code=409,
            error_type="PROJECT_ERROR",
            code="PROJECT_BUSY",
            message="项目当前正在执行其他操作，请稍后重试。",
            retryable=True,
        ) from error
    if isinstance(error, ProjectCreateFailed):
        raise WebApiError(
            status_code=500,
            error_type="PROJECT_ERROR",
            code="PROJECT_CREATE_FAILED",
            message="项目创建失败。",
        ) from error
    raise error


@router.post(
    "/projects",
    response_model=ProjectCreateResponse,
    status_code=201,
)
def create_project(
    payload: ProjectCreateRequest,
    request: Request,
    response: Response,
) -> ProjectCreateResponse:
    try:
        created = _project_service(request).create_project(payload)
    except (
        InvalidProjectName,
        InvalidProjectRequest,
        InvalidVideoDuration,
        ProjectBusy,
        ProjectCreateFailed,
    ) as error:
        _raise_create_error(error)
    response.headers["Location"] = f"/api/projects/{created.project_id}"
    return created


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(request: Request) -> ProjectListResponse:
    return _repository(request).list_projects()


@router.get("/projects/{project_id}", response_model=ProjectDetail)
async def get_project(project_id: str, request: Request) -> ProjectDetail:
    try:
        return _repository(request).get_project(project_id)
    except ProjectRepositoryError as error:
        _raise_api_error(error)


@router.get(
    "/projects/{project_id}/workflow",
    response_model=ProjectWorkflowResponse,
)
async def get_project_workflow(
    project_id: str,
    request: Request,
) -> ProjectWorkflowResponse:
    try:
        return _repository(request).get_workflow(project_id)
    except ProjectRepositoryError as error:
        _raise_api_error(error)
