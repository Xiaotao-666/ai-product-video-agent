"""Voice preparation, durable generation, history, and timing endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from web_backend.dependencies import (
    get_postproduction_repository,
    get_voice_web_service,
)
from web_backend.errors import registered_api_error
from web_backend.models.postproduction import VoiceDetail, VoiceHistoryResponse
from web_backend.models.tasks import TaskRecord
from web_backend.models.voice import (
    VoiceGenerateRequest,
    VoiceIntent,
    VoiceOptionsResponse,
    VoicePreflightRequest,
    VoicePreflightResponse,
    VoiceTimingAcceptanceRequest,
)
from web_backend.repositories.postproduction_repository import (
    PostProductionRepository,
    VoiceDataCorrupt,
    VoiceMediaNotFound,
)
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
    ProjectRepositoryError,
)
from web_backend.services.projects import ProjectBusy
from web_backend.services.task_runner import TaskRunnerClosed
from web_backend.services.voice import (
    VoiceExternalConfirmationRequired,
    VoiceInputInvalid,
    VoicePreflightStale,
    VoiceProviderUnavailable,
    VoiceTimingAcceptanceNotAllowed,
    VoiceWebService,
)


router = APIRouter(tags=["voice"])


_ERRORS: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    VoiceDataCorrupt: "VOICE_DATA_CORRUPT",
    VoiceMediaNotFound: "VOICE_MEDIA_NOT_FOUND",
    VoiceInputInvalid: "VOICE_INPUT_INVALID",
    VoicePreflightStale: "VOICE_PREFLIGHT_STALE",
    VoiceProviderUnavailable: "VOICE_PROVIDER_UNAVAILABLE",
    VoiceExternalConfirmationRequired: "VOICE_EXTERNAL_CONFIRMATION_REQUIRED",
    VoiceTimingAcceptanceNotAllowed: "VOICE_TIMING_ACCEPTANCE_NOT_ALLOWED",
    ProjectBusy: "PROJECT_BUSY",
    TaskRunnerClosed: "TASK_RUNNER_UNAVAILABLE",
}


def _raise(error: Exception) -> NoReturn:
    code = _ERRORS.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


def _media_response(path, media_type: str) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
    )


def _accepted_example() -> dict[str, object]:
    return {
        "task_id": "task_0123456789abcdef0123456789abcdef",
        "project_id": "project-id",
        "operation": "VOICE_GENERATE",
        "target_id": "voice_v001",
        "status": "QUEUED",
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": None,
        "finished_at": None,
        "correlation_id": "req_0123456789abcdef0123456789abcdef",
        "error": None,
        "result": None,
    }


@router.get(
    "/projects/{project_id}/post-production/voice/options",
    response_model=VoiceOptionsResponse,
)
def voice_options(
    project_id: str,
    service: Annotated[VoiceWebService, Depends(get_voice_web_service)],
) -> VoiceOptionsResponse:
    try:
        return service.options(project_id)
    except Exception as error:
        _raise(error)


@router.post(
    "/projects/{project_id}/post-production/voice/preflight",
    response_model=VoicePreflightResponse,
)
def voice_preflight(
    project_id: str,
    payload: VoicePreflightRequest,
    service: Annotated[VoiceWebService, Depends(get_voice_web_service)],
) -> VoicePreflightResponse:
    try:
        return service.preflight(project_id, payload)
    except Exception as error:
        _raise(error)


def _submit(
    project_id: str,
    payload: VoiceGenerateRequest,
    request: Request,
    response: Response,
    service: VoiceWebService,
    intent: VoiceIntent,
) -> TaskRecord:
    try:
        task = service.submit(
            project_id,
            payload,
            expected_intent=intent,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except Exception as error:
        _raise(error)
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.post(
    "/projects/{project_id}/post-production/voice/generate",
    response_model=TaskRecord,
    status_code=202,
    responses={202: {"content": {"application/json": {"example": _accepted_example()}}}},
)
def generate_voice(
    project_id: str,
    payload: VoiceGenerateRequest,
    request: Request,
    response: Response,
    service: Annotated[VoiceWebService, Depends(get_voice_web_service)],
) -> TaskRecord:
    return _submit(
        project_id,
        payload,
        request,
        response,
        service,
        VoiceIntent.GENERATE,
    )


@router.post(
    "/projects/{project_id}/post-production/voice/regenerate",
    response_model=TaskRecord,
    status_code=202,
    responses={202: {"content": {"application/json": {"example": _accepted_example()}}}},
)
def regenerate_voice(
    project_id: str,
    payload: VoiceGenerateRequest,
    request: Request,
    response: Response,
    service: Annotated[VoiceWebService, Depends(get_voice_web_service)],
) -> TaskRecord:
    return _submit(
        project_id,
        payload,
        request,
        response,
        service,
        VoiceIntent.REGENERATE,
    )


@router.get(
    "/projects/{project_id}/post-production/voice/history",
    response_model=VoiceHistoryResponse,
)
def voice_history(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> VoiceHistoryResponse:
    try:
        return repository.get_voice_history(project_id)
    except ProjectRepositoryError as error:
        _raise(error)


@router.get(
    "/projects/{project_id}/post-production/voice/versions/{version}",
    response_model=VoiceDetail,
)
def voice_version(
    project_id: str,
    version: int,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> VoiceDetail:
    try:
        return repository.get_voice_version(project_id, version)
    except ProjectRepositoryError as error:
        _raise(error)


@router.get(
    "/projects/{project_id}/post-production/voice/versions/{version}/audio",
    response_class=FileResponse,
)
def voice_version_audio(
    project_id: str,
    version: int,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> FileResponse:
    try:
        media = repository.resolve_voice_version_audio(project_id, version)
    except ProjectRepositoryError as error:
        _raise(error)
    return _media_response(media.path, media.media_type)


@router.post(
    "/projects/{project_id}/post-production/voice/timing-acceptance",
    response_model=VoiceDetail,
)
def accept_voice_timing(
    project_id: str,
    payload: VoiceTimingAcceptanceRequest,
    service: Annotated[VoiceWebService, Depends(get_voice_web_service)],
) -> VoiceDetail:
    try:
        return service.accept_timing(project_id, payload)
    except Exception as error:
        _raise(error)

