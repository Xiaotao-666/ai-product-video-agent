"""Read-only task query endpoints; task submission remains internal."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends

from web_backend.dependencies import get_task_service
from web_backend.errors import registered_api_error
from web_backend.models.tasks import ProjectTaskListResponse, TaskRecord
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
    ProjectRepositoryError,
)
from web_backend.repositories.task_repository import (
    InvalidTaskId,
    TaskDataCorrupt,
    TaskNotFound,
    TaskRepositoryError,
)
from web_backend.services.tasks import TaskService


router = APIRouter(tags=["tasks"])

_ERROR_CODE_BY_EXCEPTION: dict[type[Exception], str] = {
    InvalidTaskId: "INVALID_TASK_ID",
    TaskNotFound: "TASK_NOT_FOUND",
    TaskDataCorrupt: "TASK_DATA_CORRUPT",
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
}


def _raise_mapped_error(error: Exception) -> NoReturn:
    code = _ERROR_CODE_BY_EXCEPTION.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


@router.get("/tasks/{task_id}", response_model=TaskRecord)
async def get_task(
    task_id: str,
    service: Annotated[TaskService, Depends(get_task_service)],
) -> TaskRecord:
    try:
        return service.get(task_id)
    except TaskRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/tasks",
    response_model=ProjectTaskListResponse,
)
async def get_project_tasks(
    project_id: str,
    service: Annotated[TaskService, Depends(get_task_service)],
) -> ProjectTaskListResponse:
    try:
        return service.list_for_project(project_id)
    except (ProjectRepositoryError, TaskRepositoryError) as error:
        _raise_mapped_error(error)
