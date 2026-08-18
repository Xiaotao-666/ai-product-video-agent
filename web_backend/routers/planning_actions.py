"""Explicit planning business action endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Request, Response

from web_backend.dependencies import get_creative_action_service
from web_backend.errors import registered_api_error
from web_backend.models.projects import ProjectWorkflowResponse
from web_backend.models.tasks import TaskRecord
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
    ProjectRepositoryError,
)
from web_backend.services.planning_actions import (
    ActionNotAllowed,
    CapabilityUnavailable,
    CreativeActionService,
)
from web_backend.services.projects import ProjectBusy
from web_backend.services.task_runner import TaskRunnerClosed


router = APIRouter(tags=["planning-actions"])


_PROJECT_ERROR_CODES: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
}


def _raise_project_error(error: ProjectRepositoryError) -> NoReturn:
    code = _PROJECT_ERROR_CODES.get(type(error), "PROJECT_DATA_CORRUPT")
    raise registered_api_error(code) from error


@router.post(
    "/projects/{project_id}/planning/creative/generate",
    response_model=TaskRecord,
    status_code=202,
)
def generate_creative(
    project_id: str,
    request: Request,
    response: Response,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_generate(
            project_id,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except ProjectRepositoryError as error:
        _raise_project_error(error)
    except ActionNotAllowed as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
    except CapabilityUnavailable as error:
        raise registered_api_error("CAPABILITY_UNAVAILABLE") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
    except TaskRunnerClosed as error:
        raise registered_api_error("TASK_RUNNER_UNAVAILABLE") from error

    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.post(
    "/projects/{project_id}/planning/creative/approve",
    response_model=ProjectWorkflowResponse,
    status_code=200,
)
def approve_creative(
    project_id: str,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> ProjectWorkflowResponse:
    try:
        return service.approve(project_id)
    except ProjectRepositoryError as error:
        _raise_project_error(error)
    except ActionNotAllowed as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
