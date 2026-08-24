"""Local Music upload, history, media, and Mix endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse

from web_backend.dependencies import get_music_web_service, get_postproduction_repository
from web_backend.errors import registered_api_error
from web_backend.models.music import (
    MusicHistoryResponse,
    MusicMixUpdateRequest,
    MusicOptionsResponse,
)
from web_backend.models.postproduction import MusicDetail
from web_backend.repositories.postproduction_repository import (
    MusicDataCorrupt,
    MusicMediaNotFound,
    MusicVersionNotFound,
    PostProductionRepository,
)
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
)
from web_backend.services.music import (
    MusicActionNotAllowed,
    MusicFileInvalid,
    MusicFileRequired,
    MusicFileTooLarge,
    MusicFormatUnsupported,
    MusicMixInvalid,
    MusicStateChanged,
    MusicUploadFailed,
    MusicWebService,
)
from web_backend.services.projects import ProjectBusy


router = APIRouter(tags=["music"])


_ERRORS: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    MusicDataCorrupt: "MUSIC_DATA_CORRUPT",
    MusicMediaNotFound: "MUSIC_MEDIA_NOT_FOUND",
    MusicVersionNotFound: "MUSIC_VERSION_NOT_FOUND",
    MusicFileRequired: "MUSIC_FILE_REQUIRED",
    MusicFormatUnsupported: "MUSIC_FORMAT_UNSUPPORTED",
    MusicFileTooLarge: "MUSIC_FILE_TOO_LARGE",
    MusicFileInvalid: "MUSIC_FILE_INVALID",
    MusicUploadFailed: "MUSIC_UPLOAD_FAILED",
    MusicStateChanged: "MUSIC_STATE_CHANGED",
    MusicMixInvalid: "MUSIC_MIX_INVALID",
    MusicActionNotAllowed: "ACTION_NOT_ALLOWED",
    ProjectBusy: "PROJECT_BUSY",
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


@router.get(
    "/projects/{project_id}/post-production/music/options",
    response_model=MusicOptionsResponse,
)
def music_options(
    project_id: str,
    service: Annotated[MusicWebService, Depends(get_music_web_service)],
) -> MusicOptionsResponse:
    try:
        return service.options(project_id)
    except Exception as error:
        _raise(error)


@router.post(
    "/projects/{project_id}/post-production/music/upload",
    response_model=MusicDetail,
)
def upload_music(
    project_id: str,
    expected_next_version: Annotated[int, Form(ge=1)],
    service: Annotated[MusicWebService, Depends(get_music_web_service)],
    file: Annotated[UploadFile | None, File()] = None,
    expected_active_version: Annotated[int | None, Form(ge=1)] = None,
) -> MusicDetail:
    try:
        return service.upload(
            project_id,
            file,
            expected_active_version=expected_active_version,
            expected_next_version=expected_next_version,
        )
    except Exception as error:
        _raise(error)


@router.patch(
    "/projects/{project_id}/post-production/music/mix",
    response_model=MusicDetail,
)
def update_music_mix(
    project_id: str,
    payload: MusicMixUpdateRequest,
    service: Annotated[MusicWebService, Depends(get_music_web_service)],
) -> MusicDetail:
    try:
        return service.update_mix(project_id, payload)
    except Exception as error:
        _raise(error)


@router.post(
    "/projects/{project_id}/post-production/music/mix/reset",
    response_model=MusicDetail,
)
def reset_music_mix(
    project_id: str,
    service: Annotated[MusicWebService, Depends(get_music_web_service)],
) -> MusicDetail:
    try:
        return service.reset_mix(project_id)
    except Exception as error:
        _raise(error)


@router.get(
    "/projects/{project_id}/post-production/music/history",
    response_model=MusicHistoryResponse,
)
def music_history(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> MusicHistoryResponse:
    try:
        return repository.get_music_history(project_id)
    except Exception as error:
        _raise(error)


@router.get(
    "/projects/{project_id}/post-production/music/versions/{version}",
    response_model=MusicDetail,
)
def music_version(
    project_id: str,
    version: int,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> MusicDetail:
    try:
        return repository.get_music_version(project_id, version)
    except Exception as error:
        _raise(error)


@router.get(
    "/projects/{project_id}/post-production/music/versions/{version}/audio",
    response_class=FileResponse,
)
def music_version_audio(
    project_id: str,
    version: int,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> FileResponse:
    try:
        media = repository.resolve_music_version_audio(project_id, version)
    except Exception as error:
        _raise(error)
    return _media_response(media.path, media.media_type)
