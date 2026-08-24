"""Final Export preparation, durable execution, history, and media routes."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse

from web_backend.dependencies import get_final_export_web_service
from web_backend.errors import registered_api_error
from web_backend.models.final_export import (
    ExportHistoryResponse,
    ExportVersionDetail,
    FinalExportExecuteRequest,
    FinalExportPreflightResponse,
)
from web_backend.models.tasks import TaskRecord
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
)
from web_backend.services.final_export import (
    ExportHistoryInvalid,
    ExportVersionNotFound,
    ExportVersionVideoNotFound,
    FinalExportAlreadyCurrent,
    FinalExportConfirmationRequired,
    FinalExportNotReady,
    FinalExportPreflightStale,
    FinalExportWebService,
)
from web_backend.services.projects import ProjectBusy
from web_backend.services.task_runner import TaskRunnerClosed


router = APIRouter(tags=["final-export"])


_ERRORS: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    FinalExportConfirmationRequired: "EXPORT_CONFIRMATION_REQUIRED",
    FinalExportPreflightStale: "EXPORT_PREFLIGHT_STALE",
    FinalExportNotReady: "EXPORT_NOT_READY",
    FinalExportAlreadyCurrent: "EXPORT_ALREADY_CURRENT",
    ExportHistoryInvalid: "EXPORT_DATA_CORRUPT",
    ExportVersionNotFound: "EXPORT_VERSION_NOT_FOUND",
    ExportVersionVideoNotFound: "EXPORT_MEDIA_NOT_FOUND",
    ProjectBusy: "PROJECT_BUSY",
    TaskRunnerClosed: "TASK_RUNNER_UNAVAILABLE",
}


def _raise(error: Exception) -> NoReturn:
    code = _ERRORS.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


@router.post(
    "/projects/{project_id}/export/preflight",
    response_model=FinalExportPreflightResponse,
)
def preflight_final_export(
    project_id: str,
    service: Annotated[FinalExportWebService, Depends(get_final_export_web_service)],
) -> FinalExportPreflightResponse:
    try:
        return service.preflight(project_id)
    except Exception as error:
        _raise(error)


@router.post(
    "/projects/{project_id}/export/execute",
    response_model=TaskRecord,
    status_code=202,
)
def execute_final_export(
    project_id: str,
    payload: FinalExportExecuteRequest,
    request: Request,
    response: Response,
    service: Annotated[FinalExportWebService, Depends(get_final_export_web_service)],
) -> TaskRecord:
    try:
        task = service.submit(
            project_id,
            payload,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except Exception as error:
        _raise(error)
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.get(
    "/projects/{project_id}/export/history",
    response_model=ExportHistoryResponse,
)
def final_export_history(
    project_id: str,
    service: Annotated[FinalExportWebService, Depends(get_final_export_web_service)],
) -> ExportHistoryResponse:
    try:
        return service.history(project_id)
    except Exception as error:
        _raise(error)


@router.get(
    "/projects/{project_id}/export/versions/{version}",
    response_model=ExportVersionDetail,
)
def final_export_version(
    project_id: str,
    version: int,
    service: Annotated[FinalExportWebService, Depends(get_final_export_web_service)],
) -> ExportVersionDetail:
    try:
        return service.version(project_id, version)
    except Exception as error:
        _raise(error)


@router.get(
    "/projects/{project_id}/export/versions/{version}/video",
    response_class=FileResponse,
)
def final_export_version_video(
    project_id: str,
    version: int,
    service: Annotated[FinalExportWebService, Depends(get_final_export_web_service)],
) -> FileResponse:
    try:
        path = service.resolve_version_video(project_id, version)
    except Exception as error:
        _raise(error)
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
    )
