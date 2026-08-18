"""Read-only project discovery, detail, and workflow endpoints."""

from __future__ import annotations

from typing import NoReturn

from fastapi import APIRouter, Request

from web_backend.errors import WebApiError
from web_backend.models.projects import (
    ProjectDetail,
    ProjectListResponse,
    ProjectWorkflowResponse,
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
