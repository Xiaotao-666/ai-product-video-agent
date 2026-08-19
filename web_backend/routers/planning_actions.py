"""Explicit planning business action endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Request, Response

from web_backend.dependencies import get_creative_action_service
from web_backend.errors import registered_api_error
from web_backend.models.planning import CreativeReviseRequest, StoryboardReviseRequest
from web_backend.models.projects import ProjectWorkflowResponse
from web_backend.models.tasks import TaskOperation, TaskRecord
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


def _accepted_task_response(operation: TaskOperation) -> dict[int, dict[str, object]]:
    """Document the operation produced by one task-submission endpoint."""

    return {
        202: {
            "description": "Planning task accepted.",
            "content": {
                "application/json": {
                    "example": {
                        "task_id": "task_0123456789abcdef0123456789abcdef",
                        "project_id": "0123456789abcdef0123456789abcdef",
                        "operation": operation.value,
                        "status": "QUEUED",
                        "created_at": "2026-01-01T00:00:00Z",
                        "started_at": None,
                        "finished_at": None,
                        "correlation_id": "req_0123456789abcdef0123456789abcdef",
                        "error": None,
                        "result": None,
                    }
                }
            },
        }
    }


@router.post(
    "/projects/{project_id}/planning/creative/generate",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.CREATIVE_GENERATE),
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
    "/projects/{project_id}/planning/creative/retry",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.CREATIVE_RETRY),
)
def retry_creative(
    project_id: str,
    request: Request,
    response: Response,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_retry(
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


def _submit_task_response(
    task: TaskRecord,
    response: Response,
) -> TaskRecord:
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.post(
    "/projects/{project_id}/planning/creative/revise",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.CREATIVE_REVISE),
)
def revise_creative(
    project_id: str,
    payload: CreativeReviseRequest,
    request: Request,
    response: Response,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_revise(
            project_id,
            feedback=payload.feedback,
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
    return _submit_task_response(task, response)


@router.post(
    "/projects/{project_id}/planning/creative/regenerate",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.CREATIVE_REGENERATE),
)
def regenerate_creative(
    project_id: str,
    request: Request,
    response: Response,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_regenerate(
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
    return _submit_task_response(task, response)


@router.post(
    "/projects/{project_id}/planning/storyboard/generate",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.STORYBOARD_GENERATE),
)
def generate_storyboard(
    project_id: str,
    request: Request,
    response: Response,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_storyboard_generate(
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
    return _submit_task_response(task, response)


@router.post(
    "/projects/{project_id}/planning/storyboard/revise",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.STORYBOARD_REVISE),
)
def revise_storyboard(
    project_id: str,
    payload: StoryboardReviseRequest,
    request: Request,
    response: Response,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_storyboard_revise(
            project_id,
            feedback=payload.feedback,
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
    return _submit_task_response(task, response)


@router.post(
    "/projects/{project_id}/planning/storyboard/regenerate",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.STORYBOARD_REGENERATE),
)
def regenerate_storyboard(
    project_id: str,
    request: Request,
    response: Response,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_storyboard_regenerate(
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
    return _submit_task_response(task, response)


@router.post(
    "/projects/{project_id}/planning/video-prompts/generate",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.VIDEO_PROMPT_GENERATE),
)
def generate_video_prompts(
    project_id: str,
    request: Request,
    response: Response,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_video_prompt_generate(
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
    return _submit_task_response(task, response)


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


@router.post(
    "/projects/{project_id}/planning/storyboard/approve",
    response_model=ProjectWorkflowResponse,
    status_code=200,
)
def approve_storyboard(
    project_id: str,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> ProjectWorkflowResponse:
    try:
        return service.approve_storyboard(project_id)
    except ProjectRepositoryError as error:
        _raise_project_error(error)
    except ActionNotAllowed as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error


@router.post(
    "/projects/{project_id}/planning/video-prompts/approve",
    response_model=ProjectWorkflowResponse,
    status_code=200,
)
def approve_video_prompts(
    project_id: str,
    service: Annotated[
        CreativeActionService,
        Depends(get_creative_action_service),
    ],
) -> ProjectWorkflowResponse:
    try:
        return service.approve_video_prompts(project_id)
    except ProjectRepositoryError as error:
        _raise_project_error(error)
    except ActionNotAllowed as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
