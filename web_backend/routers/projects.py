"""Read-only project discovery, detail, and workflow endpoints."""

from __future__ import annotations

from typing import Annotated, NoReturn

from fastapi import APIRouter, Depends, Response
from fastapi.responses import FileResponse

from web_backend.dependencies import (
    get_assembly_planning_service,
    get_planning_content_repository,
    get_postproduction_repository,
    get_project_repository,
    get_project_service,
    get_shot_repository,
    get_task_service,
)
from web_backend.models.assembly_planning import AssemblyPlan, AssemblyReadiness
from web_backend.models.planning import (
    CreativeContentResponse,
    StoryboardContentResponse,
    VideoPromptsContentResponse,
)
from web_backend.errors import registered_api_error
from web_backend.models.postproduction import (
    AssemblyDetail,
    ExportDetail,
    MusicDetail,
    SubtitleDetail,
    VoiceDetail,
)
from web_backend.models.projects import (
    AvailableAction,
    ProjectDetail,
    ProjectCreateRequest,
    ProjectCreateResponse,
    ProjectListResponse,
    ProjectWorkflowResponse,
)
from web_backend.services.tasks import TaskService
from web_backend.services.assembly_planning import (
    AssemblyPlanNotReady,
    AssemblyPlanningBusy,
    AssemblyPlanningService,
)
from web_backend.models.shots import ShotDetail, ShotListResponse
from web_backend.services.projects import (
    InvalidProjectName,
    InvalidProjectRequest,
    InvalidVideoDuration,
    ProjectBusy,
    ProjectCreateFailed,
    ProjectService,
)
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    ProjectDataCorrupt,
    ProjectDataUnsupported,
    ProjectNotFound,
    ProjectRepository,
    ProjectRepositoryError,
)
from web_backend.repositories.planning_content_repository import (
    PlanningContentRepository,
)
from web_backend.repositories.postproduction_repository import (
    AssemblyDataCorrupt,
    AssemblyMediaNotFound,
    ExportDataCorrupt,
    ExportMediaNotFound,
    MusicDataCorrupt,
    MusicMediaNotFound,
    PostProductionRepository,
    SubtitleDataCorrupt,
    VoiceDataCorrupt,
    VoiceMediaNotFound,
)
from web_backend.repositories.shot_repository import (
    InvalidShotId,
    InvalidShotVersion,
    ShotDataCorrupt,
    ShotNotFound,
    ShotRepository,
    VideoNotFound,
)


router = APIRouter(tags=["projects"])


_ERROR_CODE_BY_EXCEPTION: dict[type[Exception], str] = {
    InvalidProjectId: "INVALID_PROJECT_ID",
    ProjectNotFound: "PROJECT_NOT_FOUND",
    ProjectDataCorrupt: "PROJECT_DATA_CORRUPT",
    ProjectDataUnsupported: "PROJECT_DATA_UNSUPPORTED",
    InvalidProjectName: "INVALID_PROJECT_NAME",
    InvalidVideoDuration: "INVALID_VIDEO_DURATION",
    InvalidProjectRequest: "INVALID_PROJECT_REQUEST",
    ProjectBusy: "PROJECT_BUSY",
    ProjectCreateFailed: "PROJECT_CREATE_FAILED",
    InvalidShotId: "INVALID_SHOT_ID",
    ShotNotFound: "SHOT_NOT_FOUND",
    ShotDataCorrupt: "SHOT_DATA_CORRUPT",
    InvalidShotVersion: "INVALID_SHOT_VERSION",
    VideoNotFound: "VIDEO_NOT_FOUND",
    AssemblyDataCorrupt: "ASSEMBLY_DATA_CORRUPT",
    AssemblyPlanNotReady: "ASSEMBLY_NOT_READY",
    AssemblyPlanningBusy: "PROJECT_BUSY",
    AssemblyMediaNotFound: "ASSEMBLY_MEDIA_NOT_FOUND",
    VoiceDataCorrupt: "VOICE_DATA_CORRUPT",
    VoiceMediaNotFound: "VOICE_MEDIA_NOT_FOUND",
    SubtitleDataCorrupt: "SUBTITLE_DATA_CORRUPT",
    MusicDataCorrupt: "MUSIC_DATA_CORRUPT",
    MusicMediaNotFound: "MUSIC_MEDIA_NOT_FOUND",
    ExportDataCorrupt: "EXPORT_DATA_CORRUPT",
    ExportMediaNotFound: "EXPORT_MEDIA_NOT_FOUND",
}


def _raise_mapped_error(error: Exception) -> NoReturn:
    code = _ERROR_CODE_BY_EXCEPTION.get(type(error))
    if code is None:
        raise error
    raise registered_api_error(code) from error


def _media_response(path, media_type: str) -> FileResponse:
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
    )


def _hide_submit_actions_while_task_active(
    workflow,
    project_id: str,
    task_service: TaskService,
):
    submit_actions = {
        AvailableAction.RETRY_GENERATE_CREATIVE,
        AvailableAction.GENERATE_VIDEO_PROMPTS,
    }
    if not any(action in submit_actions for action in workflow.available_actions):
        return workflow
    if task_service.active_for_project(project_id) is None:
        return workflow
    return workflow.model_copy(
        update={
            "available_actions": [
                action
                for action in workflow.available_actions
                if action not in submit_actions
            ]
        }
    )


@router.post(
    "/projects",
    response_model=ProjectCreateResponse,
    status_code=201,
)
def create_project(
    payload: ProjectCreateRequest,
    response: Response,
    service: Annotated[ProjectService, Depends(get_project_service)],
) -> ProjectCreateResponse:
    try:
        created = service.create_project(payload)
    except (
        InvalidProjectName,
        InvalidProjectRequest,
        InvalidVideoDuration,
        ProjectBusy,
        ProjectCreateFailed,
    ) as error:
        _raise_mapped_error(error)
    response.headers["Location"] = f"/api/projects/{created.project_id}"
    return created


@router.get("/projects", response_model=ProjectListResponse)
async def list_projects(
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
) -> ProjectListResponse:
    return repository.list_projects()


@router.get("/projects/{project_id}", response_model=ProjectDetail)
async def get_project(
    project_id: str,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> ProjectDetail:
    try:
        detail = repository.get_project(project_id)
        workflow = _hide_submit_actions_while_task_active(
            detail.workflow,
            detail.project_id,
            task_service,
        )
        return detail.model_copy(update={"workflow": workflow})
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/workflow",
    response_model=ProjectWorkflowResponse,
)
async def get_project_workflow(
    project_id: str,
    repository: Annotated[ProjectRepository, Depends(get_project_repository)],
    task_service: Annotated[TaskService, Depends(get_task_service)],
) -> ProjectWorkflowResponse:
    try:
        workflow = repository.get_workflow(project_id)
        return _hide_submit_actions_while_task_active(
            workflow,
            workflow.project_id,
            task_service,
        )
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/planning/creative",
    response_model=CreativeContentResponse,
)
async def get_project_creative_content(
    project_id: str,
    repository: Annotated[
        PlanningContentRepository, Depends(get_planning_content_repository)
    ],
) -> CreativeContentResponse:
    try:
        return repository.get_creative(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/planning/storyboard",
    response_model=StoryboardContentResponse,
)
async def get_project_storyboard_content(
    project_id: str,
    repository: Annotated[
        PlanningContentRepository, Depends(get_planning_content_repository)
    ],
) -> StoryboardContentResponse:
    try:
        return repository.get_storyboard(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/planning/video-prompts",
    response_model=VideoPromptsContentResponse,
)
async def get_project_video_prompts_content(
    project_id: str,
    repository: Annotated[
        PlanningContentRepository, Depends(get_planning_content_repository)
    ],
) -> VideoPromptsContentResponse:
    try:
        return repository.get_video_prompts(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/shots",
    response_model=ShotListResponse,
)
async def get_project_shots(
    project_id: str,
    repository: Annotated[ShotRepository, Depends(get_shot_repository)],
) -> ShotListResponse:
    try:
        return repository.list_shots(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/shots/{shot_id}",
    response_model=ShotDetail,
)
async def get_project_shot(
    project_id: str,
    shot_id: str,
    repository: Annotated[ShotRepository, Depends(get_shot_repository)],
) -> ShotDetail:
    try:
        return repository.get_shot(project_id, shot_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/shots/{shot_id}/versions/{version}/video",
    response_class=FileResponse,
)
async def get_project_shot_video(
    project_id: str,
    shot_id: str,
    version: str,
    repository: Annotated[ShotRepository, Depends(get_shot_repository)],
) -> FileResponse:
    try:
        path = repository.resolve_video(project_id, shot_id, version)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)
    return _media_response(path, "video/mp4")


@router.get(
    "/projects/{project_id}/assembly/readiness",
    response_model=AssemblyReadiness,
)
async def get_project_assembly_readiness(
    project_id: str,
    service: Annotated[
        AssemblyPlanningService, Depends(get_assembly_planning_service)
    ],
) -> AssemblyReadiness:
    try:
        return service.readiness(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.post(
    "/projects/{project_id}/assembly/plan",
    response_model=AssemblyPlan,
)
def create_project_assembly_plan(
    project_id: str,
    service: Annotated[
        AssemblyPlanningService, Depends(get_assembly_planning_service)
    ],
) -> AssemblyPlan:
    try:
        return service.create_plan(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/assembly",
    response_model=AssemblyDetail,
)
async def get_project_assembly(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> AssemblyDetail:
    try:
        return repository.get_assembly(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/assembly/video",
    response_class=FileResponse,
)
async def get_project_assembly_video(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> FileResponse:
    try:
        media = repository.resolve_assembly_video(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)
    return _media_response(media.path, media.media_type)


@router.get(
    "/projects/{project_id}/post-production/voice",
    response_model=VoiceDetail,
)
async def get_project_voice(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> VoiceDetail:
    try:
        return repository.get_voice(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/post-production/voice/audio",
    response_class=FileResponse,
)
async def get_project_voice_audio(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> FileResponse:
    try:
        media = repository.resolve_voice_audio(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)
    return _media_response(media.path, media.media_type)


@router.get(
    "/projects/{project_id}/post-production/subtitle",
    response_model=SubtitleDetail,
)
async def get_project_subtitle(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> SubtitleDetail:
    try:
        return repository.get_subtitle(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/post-production/music",
    response_model=MusicDetail,
)
async def get_project_music(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> MusicDetail:
    try:
        return repository.get_music(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/post-production/music/audio",
    response_class=FileResponse,
)
async def get_project_music_audio(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> FileResponse:
    try:
        media = repository.resolve_music_audio(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)
    return _media_response(media.path, media.media_type)


@router.get(
    "/projects/{project_id}/export",
    response_model=ExportDetail,
)
async def get_project_export(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> ExportDetail:
    try:
        return repository.get_export(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)


@router.get(
    "/projects/{project_id}/export/video",
    response_class=FileResponse,
)
async def get_project_export_video(
    project_id: str,
    repository: Annotated[
        PostProductionRepository, Depends(get_postproduction_repository)
    ],
) -> FileResponse:
    try:
        media = repository.resolve_export_video(project_id)
    except ProjectRepositoryError as error:
        _raise_mapped_error(error)
    return _media_response(media.path, media.media_type)
