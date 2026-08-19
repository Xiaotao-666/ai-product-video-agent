"""Local-only Shot generation preparation endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from web_backend.dependencies import (
    get_reference_asset_repository,
    get_shot_generation_preflight_service,
)
from web_backend.errors import registered_api_error
from web_backend.models.generation import (
    GenerationOptionsResponse,
    GenerationPreflightRequest,
    GenerationPreflightResponse,
    ReferenceAssetListResponse,
)
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
from web_backend.repositories.shot_repository import InvalidShotId, ShotNotFound
from web_backend.services.shot_generation_preflight import (
    ShotGenerationPreflightService,
)


router = APIRouter(tags=["shot-generation"])


_ERROR_CODE_BY_EXCEPTION: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    InvalidShotId: "INVALID_SHOT_ID",
    ShotNotFound: "SHOT_NOT_FOUND",
    InvalidReferenceAssetId: "INVALID_REFERENCE_ASSET_ID",
    ReferenceAssetNotFound: "REFERENCE_ASSET_NOT_FOUND",
    ReferenceAssetDataCorrupt: "REFERENCE_ASSET_DATA_CORRUPT",
}


def _raise_mapped(error: Exception) -> NoReturn:
    code = _ERROR_CODE_BY_EXCEPTION.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


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
) -> GenerationOptionsResponse:
    try:
        return service.options(project_id, shot_id)
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

