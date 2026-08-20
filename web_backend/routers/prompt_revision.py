"""AI-assisted one-Shot Prompt revision draft endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Request, Response

from web_backend.dependencies import get_prompt_revision_draft_service
from web_backend.errors import registered_api_error
from web_backend.models.prompt_revision import (
    PromptRevisionDraftRequest,
    PromptRevisionDraftResponse,
)
from web_backend.models.tasks import TaskRecord
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
    ProjectRepositoryError,
)
from web_backend.repositories.prompt_revision_repository import (
    PromptRevisionDraftDataCorrupt,
    PromptRevisionDraftNotFound,
)
from web_backend.repositories.reference_asset_repository import (
    ReferenceAssetDataCorrupt,
)
from web_backend.repositories.shot_repository import InvalidShotId, ShotNotFound
from web_backend.services.planning_actions import CapabilityUnavailable
from web_backend.services.projects import ProjectBusy
from web_backend.services.prompt_revision import (
    PromptRevisionDraftService,
    PromptRevisionNotAllowed,
)
from web_backend.services.task_runner import TaskRunnerClosed


router = APIRouter(tags=["shot-prompt-revision"])


_ERROR_CODE_BY_EXCEPTION: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    InvalidShotId: "INVALID_SHOT_ID",
    ShotNotFound: "SHOT_NOT_FOUND",
    ReferenceAssetDataCorrupt: "PROJECT_DATA_CORRUPT",
    PromptRevisionDraftDataCorrupt: "PROJECT_DATA_CORRUPT",
}


def _raise_mapped(error: Exception) -> NoReturn:
    code = _ERROR_CODE_BY_EXCEPTION.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


@router.post(
    "/projects/{project_id}/shots/{shot_id}/prompt/revision/draft",
    response_model=TaskRecord,
    status_code=202,
    responses={
        202: {
            "description": "AI Prompt revision draft task accepted.",
            "content": {
                "application/json": {
                    "example": {
                        "task_id": "task_0123456789abcdef0123456789abcdef",
                        "project_id": "0123456789abcdef0123456789abcdef",
                        "operation": "SHOT_PROMPT_REVISION_DRAFT",
                        "target_id": "shot_01",
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
    },
)
def submit_prompt_revision_draft(
    project_id: str,
    shot_id: str,
    payload: PromptRevisionDraftRequest,
    request: Request,
    response: Response,
    service: Annotated[
        PromptRevisionDraftService,
        Depends(get_prompt_revision_draft_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit(
            project_id,
            shot_id,
            payload,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except PromptRevisionNotAllowed as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
    except CapabilityUnavailable as error:
        raise registered_api_error("CAPABILITY_UNAVAILABLE") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
    except TaskRunnerClosed as error:
        raise registered_api_error("TASK_RUNNER_UNAVAILABLE") from error
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.get(
    "/projects/{project_id}/shots/{shot_id}/prompt/revision/draft",
    response_model=PromptRevisionDraftResponse,
)
def get_prompt_revision_draft(
    project_id: str,
    shot_id: str,
    service: Annotated[
        PromptRevisionDraftService,
        Depends(get_prompt_revision_draft_service),
    ],
) -> PromptRevisionDraftResponse:
    try:
        return service.get(project_id, shot_id)
    except PromptRevisionDraftNotFound as error:
        raise registered_api_error("PROMPT_REVISION_DRAFT_NOT_FOUND") from error
    except PromptRevisionDraftDataCorrupt as error:
        raise registered_api_error("PROJECT_DATA_CORRUPT") from error
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except PromptRevisionNotAllowed as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
