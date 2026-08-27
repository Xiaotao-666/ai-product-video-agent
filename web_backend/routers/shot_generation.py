"""Local-only Shot generation preparation endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, File, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from web_backend.dependencies import (
    get_reference_asset_repository,
    get_reference_asset_upload_service,
    get_shot_approval_service,
    get_shot_generation_action_service,
    get_shot_generation_preflight_service,
    get_shot_failure_recovery_service,
    get_multishot_generation_service,
    get_shot_version_service,
)
from web_backend.errors import registered_api_error
from web_backend.models.generation import (
    GenerationIntent,
    GenerationOptionsResponse,
    GenerationPreflightRequest,
    GenerationPreflightResponse,
    GenerationStartRequest,
    ReferenceAssetListResponse,
    ReferenceAssetUploadResponse,
    ShotGenerationStatusResponse,
)
from web_backend.models.tasks import TaskOperation, TaskRecord
from web_backend.models.shot_failure_recovery import (
    FailedRetryOptions, FailedRetryPreflight, FailedRetryPreflightRequest, FailedRetryRequest,
)
from web_backend.services.shot_failure_recovery import FailedRetryStale, ShotFailureRecoveryService
from web_backend.models.multishot_generation import (
    MultiShotGenerationOptionsResponse,
    MultiShotGenerationPlanResponse,
    MultiShotGenerationStartRequest,
)
from web_backend.models.shots import ShotDetail
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
    ProjectRepositoryError,
)
from web_backend.repositories.reference_asset_repository import (
    InvalidReferenceAssetId,
    ReferenceAssetDataCorrupt,
    ReferenceAssetNotFound,
    ReferenceAssetRepository,
)
from web_backend.repositories.shot_repository import (
    InvalidShotId,
    InvalidShotVersion,
    ShotDataCorrupt,
    ShotNotFound,
)
from web_backend.services.shot_approval import (
    ShotApprovalNotAllowed,
    ShotApprovalService,
)
from web_backend.services.shot_versions import (
    HistoricalVersionSelectionNotAllowed,
    PendingVersionRequiresReview,
    ShotVersionService,
)
from web_backend.services.shot_generation_preflight import (
    ShotGenerationPreflightService,
)
from web_backend.services.shot_generation import (
    GenerationNotResumable,
    GenerationPreflightStale,
    PaidCallConfirmationRequired,
    ShotGenerationActionService,
)
from web_backend.services.projects import ProjectBusy
from web_backend.services.multishot_generation import (
    MultiShotGenerationNotAllowed,
    MultiShotGenerationService,
)
from web_backend.services.task_runner import TaskRunnerClosed
from web_backend.services.reference_assets import (
    InvalidReferenceFile,
    ReferenceAssetUploadError,
    ReferenceAssetUploadService,
    ReferenceFileTooLarge,
    ReferenceImageInvalid,
    ReferenceImportFailed,
    ReferenceUploadBusy,
    UnsupportedReferenceImageFormat,
)


router = APIRouter(tags=["shot-generation"])


@router.get("/projects/{project_id}/shots/{shot_id}/generation/failed-retry/options", response_model=FailedRetryOptions)
def failed_retry_options(
    project_id: str, shot_id: str,
    service: Annotated[ShotFailureRecoveryService, Depends(get_shot_failure_recovery_service)],
) -> FailedRetryOptions:
    try:
        return service.options(project_id, shot_id)
    except ProjectRepositoryError as error:
        _raise_mapped(error)


@router.post("/projects/{project_id}/shots/{shot_id}/generation/failed-retry/preflight", response_model=FailedRetryPreflight)
def failed_retry_preflight(
    project_id: str, shot_id: str, payload: FailedRetryPreflightRequest,
    service: Annotated[ShotFailureRecoveryService, Depends(get_shot_failure_recovery_service)],
) -> FailedRetryPreflight:
    try:
        return service.preflight(project_id, shot_id, payload)
    except FailedRetryStale as error:
        raise registered_api_error("FAILED_RETRY_STALE") from error
    except ProjectRepositoryError as error:
        _raise_mapped(error)


@router.post("/projects/{project_id}/shots/{shot_id}/generation/failed-retry", response_model=TaskRecord, status_code=202)
def failed_retry_execute(
    project_id: str, shot_id: str, payload: FailedRetryRequest, request: Request, response: Response,
    service: Annotated[ShotFailureRecoveryService, Depends(get_shot_failure_recovery_service)],
) -> TaskRecord:
    try:
        task = service.submit(
            project_id, shot_id, payload,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except FailedRetryStale as error:
        raise registered_api_error("FAILED_RETRY_STALE") from error
    except PaidCallConfirmationRequired as error:
        raise registered_api_error("PAID_CALL_CONFIRMATION_REQUIRED") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
    except TaskRunnerClosed as error:
        raise registered_api_error("TASK_RUNNER_UNAVAILABLE") from error
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


_ERROR_CODE_BY_EXCEPTION: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    InvalidShotId: "INVALID_SHOT_ID",
    InvalidShotVersion: "INVALID_SHOT_VERSION",
    ShotNotFound: "SHOT_NOT_FOUND",
    ShotDataCorrupt: "SHOT_DATA_CORRUPT",
    InvalidReferenceAssetId: "INVALID_REFERENCE_ASSET_ID",
    ReferenceAssetNotFound: "REFERENCE_ASSET_NOT_FOUND",
    ReferenceAssetDataCorrupt: "REFERENCE_ASSET_DATA_CORRUPT",
    InvalidReferenceFile: "INVALID_REFERENCE_FILE",
    UnsupportedReferenceImageFormat: "UNSUPPORTED_IMAGE_FORMAT",
    ReferenceImageInvalid: "REFERENCE_IMAGE_INVALID",
    ReferenceFileTooLarge: "REFERENCE_FILE_TOO_LARGE",
    ReferenceImportFailed: "REFERENCE_IMPORT_FAILED",
    ReferenceUploadBusy: "PROJECT_BUSY",
}


def _raise_mapped(error: Exception) -> NoReturn:
    code = _ERROR_CODE_BY_EXCEPTION.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


def _accepted_task_response(operation: TaskOperation) -> dict[int, dict[str, object]]:
    return {
        202: {
            "description": "Shot generation task accepted.",
            "content": {
                "application/json": {
                    "example": {
                        "task_id": "task_0123456789abcdef0123456789abcdef",
                        "project_id": "0123456789abcdef0123456789abcdef",
                        "operation": operation.value,
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
    }


@router.get(
    "/projects/{project_id}/shots/generation/options",
    response_model=MultiShotGenerationOptionsResponse,
)
def multishot_generation_options(
    project_id: str,
    service: Annotated[
        MultiShotGenerationService,
        Depends(get_multishot_generation_service),
    ],
) -> MultiShotGenerationOptionsResponse:
    try:
        return service.options(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped(error)


@router.post(
    "/projects/{project_id}/shots/generation/start",
    response_model=MultiShotGenerationPlanResponse,
    status_code=202,
)
def start_multishot_generation(
    project_id: str,
    payload: MultiShotGenerationStartRequest,
    request: Request,
    response: Response,
    service: Annotated[
        MultiShotGenerationService,
        Depends(get_multishot_generation_service),
    ],
) -> MultiShotGenerationPlanResponse:
    try:
        plan = service.start(
            project_id,
            payload,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except PaidCallConfirmationRequired as error:
        raise registered_api_error("PAID_CALL_CONFIRMATION_REQUIRED") from error
    except (MultiShotGenerationNotAllowed, GenerationPreflightStale) as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
    except TaskRunnerClosed as error:
        raise registered_api_error("TASK_RUNNER_UNAVAILABLE") from error
    response.headers["Location"] = (
        f"/api/projects/{plan.project_id}/shots/generation/options"
    )
    return plan


@router.get(
    "/projects/{project_id}/references",
    response_model=ReferenceAssetListResponse,
)
def list_reference_assets(
    project_id: str,
    repository: Annotated[
        ReferenceAssetRepository, Depends(get_reference_asset_repository)
    ],
) -> ReferenceAssetListResponse:
    try:
        return repository.list_assets(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped(error)


@router.post(
    "/projects/{project_id}/references",
    response_model=ReferenceAssetUploadResponse,
    status_code=201,
)
def upload_reference_asset(
    project_id: str,
    file: Annotated[UploadFile, File(...)],
    service: Annotated[
        ReferenceAssetUploadService, Depends(get_reference_asset_upload_service)
    ],
) -> ReferenceAssetUploadResponse:
    try:
        return service.upload(project_id, file)
    except (ProjectRepositoryError, ReferenceAssetUploadError) as error:
        _raise_mapped(error)


@router.get(
    "/projects/{project_id}/references/{asset_id}/image",
    response_class=FileResponse,
)
def get_reference_image(
    project_id: str,
    asset_id: str,
    repository: Annotated[
        ReferenceAssetRepository, Depends(get_reference_asset_repository)
    ],
) -> FileResponse:
    try:
        path, media_type = repository.resolve_image(project_id, asset_id)
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "no-store"},
    )


@router.get(
    "/projects/{project_id}/shots/{shot_id}/generation/options",
    response_model=GenerationOptionsResponse,
)
def generation_options(
    project_id: str,
    shot_id: str,
    service: Annotated[
        ShotGenerationPreflightService,
        Depends(get_shot_generation_preflight_service),
    ],
    intent: GenerationIntent = GenerationIntent.INITIAL,
    target_prompt_version: Annotated[int | None, Query(ge=1)] = None,
) -> GenerationOptionsResponse:
    try:
        return service.options(
            project_id,
            shot_id,
            intent,
            target_prompt_version=target_prompt_version,
        )
    except ProjectRepositoryError as error:
        _raise_mapped(error)


@router.post(
    "/projects/{project_id}/shots/{shot_id}/generation/preflight",
    response_model=GenerationPreflightResponse,
    responses={
        200: {
            "description": "Local-only generation configuration result",
            "content": {
                "application/json": {
                    "example": {
                        "ready": True,
                        "shot": {
                            "shot_id": "shot_01",
                            "duration_seconds": 6,
                            "prompt_version": 2,
                            "resolution": "768P",
                        },
                        "resolved": {
                            "provider": "minimax",
                            "provider_display_name": "MiniMax",
                            "model": "MiniMax-Hailuo-2.3",
                            "model_display_name": "MiniMax Hailuo 2.3",
                            "api_version": "v1",
                            "generation_mode": "text_to_video",
                            "generation_mode_display_name": "纯文本生成",
                            "visual_input_mode": "none",
                            "model_selection": "AUTO",
                        },
                        "provider_available": True,
                        "selected_asset_ids": [],
                        "issues": [],
                        "warnings": [],
                        "paid_call_required": True,
                    }
                }
            },
        }
    },
)
def generation_preflight(
    project_id: str,
    shot_id: str,
    payload: GenerationPreflightRequest,
    service: Annotated[
        ShotGenerationPreflightService,
        Depends(get_shot_generation_preflight_service),
    ],
) -> GenerationPreflightResponse:
    try:
        return service.preflight(project_id, shot_id, payload)
    except ProjectRepositoryError as error:
        _raise_mapped(error)


@router.post(
    "/projects/{project_id}/shots/{shot_id}/generation/start",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.SHOT_GENERATE),
)
def start_generation(
    project_id: str,
    shot_id: str,
    payload: GenerationStartRequest,
    request: Request,
    response: Response,
    service: Annotated[
        ShotGenerationActionService,
        Depends(get_shot_generation_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_start(
            project_id,
            shot_id,
            payload,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except PaidCallConfirmationRequired as error:
        raise registered_api_error("PAID_CALL_CONFIRMATION_REQUIRED") from error
    except GenerationPreflightStale as error:
        raise registered_api_error("GENERATION_PREFLIGHT_STALE") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
    except TaskRunnerClosed as error:
        raise registered_api_error("TASK_RUNNER_UNAVAILABLE") from error
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.post(
    "/projects/{project_id}/shots/{shot_id}/generation/regenerate",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.SHOT_REGENERATE),
)
def regenerate_generation(
    project_id: str,
    shot_id: str,
    payload: GenerationStartRequest,
    request: Request,
    response: Response,
    service: Annotated[
        ShotGenerationActionService,
        Depends(get_shot_generation_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_regenerate(
            project_id,
            shot_id,
            payload,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except PaidCallConfirmationRequired as error:
        raise registered_api_error("PAID_CALL_CONFIRMATION_REQUIRED") from error
    except GenerationPreflightStale as error:
        raise registered_api_error("GENERATION_PREFLIGHT_STALE") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
    except TaskRunnerClosed as error:
        raise registered_api_error("TASK_RUNNER_UNAVAILABLE") from error
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.post(
    "/projects/{project_id}/shots/{shot_id}/generation/prompt-version",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.SHOT_PROMPT_VERSION_GENERATE),
)
def generate_with_prompt_version(
    project_id: str,
    shot_id: str,
    payload: GenerationStartRequest,
    request: Request,
    response: Response,
    service: Annotated[
        ShotGenerationActionService,
        Depends(get_shot_generation_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_prompt_version_generation(
            project_id,
            shot_id,
            payload,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except PaidCallConfirmationRequired as error:
        raise registered_api_error("PAID_CALL_CONFIRMATION_REQUIRED") from error
    except GenerationPreflightStale as error:
        raise registered_api_error("GENERATION_PREFLIGHT_STALE") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
    except TaskRunnerClosed as error:
        raise registered_api_error("TASK_RUNNER_UNAVAILABLE") from error
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.post(
    "/projects/{project_id}/shots/{shot_id}/generation/resume",
    response_model=TaskRecord,
    status_code=202,
    responses=_accepted_task_response(TaskOperation.SHOT_RESUME),
)
def resume_generation(
    project_id: str,
    shot_id: str,
    request: Request,
    response: Response,
    service: Annotated[
        ShotGenerationActionService,
        Depends(get_shot_generation_action_service),
    ],
) -> TaskRecord:
    try:
        task = service.submit_resume(
            project_id,
            shot_id,
            correlation_id=getattr(request.state, "correlation_id", None),
        )
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except GenerationNotResumable as error:
        raise registered_api_error("GENERATION_NOT_RESUMABLE") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
    except TaskRunnerClosed as error:
        raise registered_api_error("TASK_RUNNER_UNAVAILABLE") from error
    response.headers["Location"] = f"/api/tasks/{task.task_id}"
    return task


@router.get(
    "/projects/{project_id}/shots/{shot_id}/generation/status",
    response_model=ShotGenerationStatusResponse,
)
def generation_status(
    project_id: str,
    shot_id: str,
    service: Annotated[
        ShotGenerationActionService,
        Depends(get_shot_generation_action_service),
    ],
) -> ShotGenerationStatusResponse:
    try:
        return service.status(project_id, shot_id)
    except ProjectRepositoryError as error:
        _raise_mapped(error)


@router.post(
    "/projects/{project_id}/shots/{shot_id}/approve",
    response_model=ShotDetail,
    status_code=200,
)
def approve_shot(
    project_id: str,
    shot_id: str,
    service: Annotated[ShotApprovalService, Depends(get_shot_approval_service)],
) -> ShotDetail:
    try:
        return service.approve(project_id, shot_id)
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except ShotApprovalNotAllowed as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error


@router.post(
    "/projects/{project_id}/shots/{shot_id}/versions/{video_version}/set-official",
    response_model=ShotDetail,
    status_code=200,
    responses={
        200: {
            "description": "Existing historical video selected as the official version.",
            "content": {
                "application/json": {
                    "example": {
                        "project_id": "0123456789abcdef0123456789abcdef",
                        "shot_id": "shot_01",
                        "status": "APPROVED",
                        "official_version": 1,
                        "pending_review_version": None,
                        "version_count": 3,
                        "generation_count": 3,
                        "versions": [],
                    }
                }
            },
        }
    },
)
def set_official_shot_version(
    project_id: str,
    shot_id: str,
    video_version: str,
    service: Annotated[ShotVersionService, Depends(get_shot_version_service)],
) -> ShotDetail:
    try:
        return service.set_official(project_id, shot_id, video_version)
    except ProjectRepositoryError as error:
        _raise_mapped(error)
    except PendingVersionRequiresReview as error:
        raise registered_api_error("PENDING_VERSION_REQUIRES_REVIEW") from error
    except HistoricalVersionSelectionNotAllowed as error:
        raise registered_api_error("ACTION_NOT_ALLOWED") from error
    except ProjectBusy as error:
        raise registered_api_error("PROJECT_BUSY") from error
