"""Locked, local-only Web adapter for selecting an existing official Shot version."""

from __future__ import annotations

from pydantic import ValidationError

from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from shot_manager import ShotManagerError, set_historical_video_as_official
from storyboard import VideoPromptPlan
from video_history import VideoHistoryError
from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.shots import ShotDetail, ShotVersionRole
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
)
from web_backend.repositories.shot_repository import (
    InvalidShotVersion,
    ShotRepository,
    normalize_shot_id,
    normalize_shot_version,
)
from web_backend.services.projects import ProjectBusy
from web_backend.services.tasks import TaskService


class HistoricalVersionSelectionNotAllowed(RuntimeError):
    pass


class PendingVersionRequiresReview(HistoricalVersionSelectionNotAllowed):
    pass


class ShotVersionService:
    def __init__(
        self,
        project_repository: ProjectRepository,
        shot_repository: ShotRepository,
        task_service: TaskService,
        project_lock_manager: ProjectLockManager,
    ) -> None:
        self._project_repository = project_repository
        self._shot_repository = shot_repository
        self._task_service = task_service
        self._project_lock_manager = project_lock_manager

    def set_official(
        self,
        project_id: str,
        shot_id: str,
        video_version: str | int,
    ) -> ShotDetail:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        canonical_shot_id, shot_number = normalize_shot_id(shot_id)
        normalized_version = normalize_shot_version(video_version)

        with self._task_service.prevent_task_submission():
            self._require_no_active_task(canonical_project_id)
            self._require_selection_allowed(
                canonical_project_id, canonical_shot_id, normalized_version
            )
            try:
                with self._project_lock_manager.project_write(canonical_project_id):
                    self._require_no_active_task(canonical_project_id)
                    self._require_selection_allowed(
                        canonical_project_id, canonical_shot_id, normalized_version
                    )
                    paths = create_project_paths(
                        self._project_repository.resolve_project_dir(
                            canonical_project_id
                        ),
                        ensure_directories=False,
                    )
                    try:
                        checkpoint = ProjectCheckpoint.load(paths)
                        plan = VideoPromptPlan.model_validate_json(
                            paths.video_prompts_path().read_text(encoding="utf-8")
                        )
                        set_historical_video_as_official(
                            paths=paths,
                            checkpoint=checkpoint,
                            plan=plan,
                            shot_id=shot_number,
                            target_version=normalized_version,
                        )
                    except (OSError, ValidationError, ProjectStateError) as error:
                        raise ProjectDataCorrupt(
                            "project state is unreadable"
                        ) from error
                    except (ShotManagerError, VideoHistoryError) as error:
                        raise HistoricalVersionSelectionNotAllowed(
                            "historical version cannot become official"
                        ) from error
            except ProjectLockBusy as error:
                raise ProjectBusy("project write lock is busy") from error

        return self._shot_repository.get_shot(
            canonical_project_id, canonical_shot_id
        )

    def _require_selection_allowed(
        self, project_id: str, shot_id: str, video_version: int
    ) -> None:
        detail = self._shot_repository.get_shot(project_id, shot_id)
        if detail.pending_review_version is not None:
            raise PendingVersionRequiresReview(
                "pending version must be reviewed before history selection"
            )
        target = next(
            (item for item in detail.versions if item.version == video_version),
            None,
        )
        if target is None:
            raise InvalidShotVersion("shot version was not found")
        if (
            detail.status != "APPROVED"
            or target.role is not ShotVersionRole.HISTORY
            or not target.video_available
        ):
            raise HistoricalVersionSelectionNotAllowed(
                "only a complete historical version can become official"
            )

    def _require_no_active_task(self, project_id: str) -> None:
        if self._task_service.active_for_project(project_id) is not None:
            raise ProjectBusy("project already has an active Web task")
