"""Durable Assembly execution endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Request, Response

from web_backend.dependencies import get_assembly_execution_service
from web_backend.errors import registered_api_error
from web_backend.models.assembly_execution import AssemblyExecuteRequest
from web_backend.models.tasks import TaskRecord
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
)
from web_backend.services.assembly_execution import (
    AssemblyAlreadyExecuted,
    AssemblyExecutionService,
    AssemblyNotResumable,
)
from web_backend.services.assembly_planning import (
    AssemblyPlanNotFound,
    AssemblyPlanOutdated,
)
from web_backend.services.projects import ProjectBusy
from web_backend.services.task_runner import TaskRunnerClosed


router = APIRouter(tags=["assembly"])


_ERRORS: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    AssemblyPlanNotFound: "ASSEMBLY_PLAN_NOT_FOUND",
    AssemblyPlanOutdated: "ASSEMBLY_PLAN_OUTDATED",
    AssemblyAlreadyExecuted: "ASSEMBLY_ALREADY_EXECUTED",
    AssemblyNotResumable: "ASSEMBLY_NOT_RESUMABLE",
    ProjectBusy: "PROJECT_BUSY",
    TaskRunnerClosed: "TASK_RUNNER_UNAVAILABLE",
}


def _raise(error: Exception) -> NoReturn:
    code = _ERRORS.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


def _example() -> dict[str, object]:
    return {
        "task_id": "task_0123456789abcdef0123456789abcdef",
        "project_id": "project-id",
        "operation": "ASSEMBLY_EXECUTE",
        "target_id": "assembly_v001",
        "status": "QUEUED",
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": None,
        "finished_at": None,
        "correlation_id": "req_0123456789abcdef0123456789abcdef",
        "error": None,
        "result": None,
    }


@router.post(
    "/projects/{project_id}/assembly/execute",
    response_model=TaskRecord,
    status_code=202,
    responses={202: {"content": {"application/json": {"example": _example()}}}},
)
def execute_assembly(
    project_id: str,
    payload: AssemblyExecuteRequest,
    request: Request,
    response: Response,
    service: Annotated[
        AssemblyExecutionService, Depends(get_assembly_execution_service)
    ],
) -> TaskRecord:
    try:
        task = service.submit_execute(
            project_id,
            payload.assembly_version,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except Exception as error:
        _raise(error)
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.post(
    "/projects/{project_id}/assembly/resume",
    response_model=TaskRecord,
    status_code=202,
    responses={202: {"content": {"application/json": {"example": _example()}}}},
)
def resume_assembly(
    project_id: str,
    payload: AssemblyExecuteRequest,
    request: Request,
    response: Response,
    service: Annotated[
        AssemblyExecutionService, Depends(get_assembly_execution_service)
    ],
) -> TaskRecord:
    try:
        task = service.submit_resume(
            project_id,
            payload.assembly_version,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except Exception as error:
        _raise(error)
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task
