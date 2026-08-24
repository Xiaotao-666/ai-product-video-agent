"""Synchronous local Subtitle generation and version inspection endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends

from web_backend.dependencies import (
    get_postproduction_repository,
    get_subtitle_web_service,
)
from web_backend.errors import registered_api_error
from web_backend.models.postproduction import (
    SubtitleDetail,
    SubtitleHistoryResponse,
)
from web_backend.models.subtitle import (
    SubtitleGenerateRequest,
    SubtitleOptionsResponse,
)
from web_backend.repositories.postproduction_repository import (
    PostProductionRepository,
    SubtitleDataCorrupt,
    SubtitleVersionNotFound,
)
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
    ProjectRepositoryError,
)
from web_backend.services.projects import ProjectBusy
from web_backend.services.subtitle import (
    ActiveVoiceRequiredForSubtitle,
    SubtitleActionNotAllowed,
    SubtitleGenerationFailed,
    SubtitleNotApplicable,
    SubtitleSourceChanged,
    SubtitleSourceInvalid,
    SubtitleSourceUnavailable,
    SubtitleWebService,
)


router = APIRouter(tags=["subtitle"])


_ERRORS: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    SubtitleDataCorrupt: "SUBTITLE_DATA_CORRUPT",
    SubtitleVersionNotFound: "SUBTITLE_VERSION_NOT_FOUND",
    SubtitleSourceUnavailable: "SUBTITLE_SOURCE_UNAVAILABLE",
    SubtitleSourceInvalid: "SUBTITLE_SOURCE_INVALID",
    ActiveVoiceRequiredForSubtitle: "ACTIVE_VOICE_REQUIRED",
    SubtitleNotApplicable: "SUBTITLE_NOT_APPLICABLE",
    SubtitleSourceChanged: "SUBTITLE_SOURCE_CHANGED",
    SubtitleGenerationFailed: "SUBTITLE_GENERATION_FAILED",
    SubtitleActionNotAllowed: "ACTION_NOT_ALLOWED",
    ProjectBusy: "PROJECT_BUSY",
}


def _raise(error: Exception) -> NoReturn:
    code = _ERRORS.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


@router.get(
    "/projects/{project_id}/post-production/subtitle/options",
    response_model=SubtitleOptionsResponse,
)
def subtitle_options(
    project_id: str,
    service: Annotated[SubtitleWebService, Depends(get_subtitle_web_service)],
) -> SubtitleOptionsResponse:
    try:
        return service.options(project_id)
    except Exception as error:
        _raise(error)


def _generate(
    project_id: str,
    payload: SubtitleGenerateRequest,
    service: SubtitleWebService,
    *,
    regenerate: bool,
) -> SubtitleDetail:
    try:
        return service.generate(
            project_id,
            payload,
            regenerate=regenerate,
        )
    except Exception as error:
        _raise(error)


@router.post(
    "/projects/{project_id}/post-production/subtitle/generate",
    response_model=SubtitleDetail,
)
def generate_subtitle(
    project_id: str,
    payload: SubtitleGenerateRequest,
    service: Annotated[SubtitleWebService, Depends(get_subtitle_web_service)],
) -> SubtitleDetail:
    return _generate(project_id, payload, service, regenerate=False)


@router.post(
    "/projects/{project_id}/post-production/subtitle/regenerate",
    response_model=SubtitleDetail,
)
def regenerate_subtitle(
    project_id: str,
    payload: SubtitleGenerateRequest,
    service: Annotated[SubtitleWebService, Depends(get_subtitle_web_service)],
) -> SubtitleDetail:
    return _generate(project_id, payload, service, regenerate=True)


@router.get(
    "/projects/{project_id}/post-production/subtitle/history",
    response_model=SubtitleHistoryResponse,
)
def subtitle_history(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> SubtitleHistoryResponse:
    try:
        return repository.get_subtitle_history(project_id)
    except ProjectRepositoryError as error:
        _raise(error)


@router.get(
    "/projects/{project_id}/post-production/subtitle/versions/{version}",
    response_model=SubtitleDetail,
)
def subtitle_version(
    project_id: str,
    version: int,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> SubtitleDetail:
    try:
        return repository.get_subtitle_version(project_id, version)
    except ProjectRepositoryError as error:
        _raise(error)
