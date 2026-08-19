"""Synchronous, locked Web adapter for approving one initial Shot version."""

from __future__ import annotations

from project_manager import create_project_paths
from project_state import ProjectCheckpoint, ProjectStateError
from shot_approval_workflow import ShotApprovalError, approve_shot_stage
from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.shots import ShotDetail, ShotVersionRole
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
)
from web_backend.repositories.shot_repository import (
    ShotRepository,
    normalize_shot_id,
)
from web_backend.services.projects import ProjectBusy
from web_backend.services.tasks import TaskService


class ShotApprovalNotAllowed(RuntimeError):
    pass


class ShotApprovalService:
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

    def approve(self, project_id: str, shot_id: str) -> ShotDetail:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        canonical_shot_id, shot_number = normalize_shot_id(shot_id)

        with self._task_service.prevent_task_submission():
            self._require_no_active_task(canonical_project_id)
            self._require_approve_allowed(canonical_project_id, canonical_shot_id)
            try:
                with self._project_lock_manager.project_write(canonical_project_id):
                    self._require_no_active_task(canonical_project_id)
                    self._require_approve_allowed(
                        canonical_project_id, canonical_shot_id
                    )
                    try:
                        paths = create_project_paths(
                            self._project_repository.resolve_project_dir(
                                canonical_project_id
                            ),
                            ensure_directories=False,
                        )
                        checkpoint = ProjectCheckpoint.load(paths)
                        approve_shot_stage(
                            paths=paths,
                            checkpoint=checkpoint,
                            shot_id=shot_number,
                        )
                    except ShotApprovalError as error:
                        raise ShotApprovalNotAllowed(
                            "Shot approval is not allowed"
                        ) from error
                    except ProjectStateError as error:
                        raise ProjectDataCorrupt(
                            "project checkpoint is unreadable"
                        ) from error
            except ProjectLockBusy as error:
                raise ProjectBusy("project write lock is busy") from error

        return self._shot_repository.get_shot(
            canonical_project_id, canonical_shot_id
        )

    def _require_approve_allowed(self, project_id: str, shot_id: str) -> None:
        detail = self._shot_repository.get_shot(project_id, shot_id)
        pending = next(
            (
                version
                for version in detail.versions
                if version.role is ShotVersionRole.PENDING_REVIEW
            ),
            None,
        )
        initial_review = (
            detail.status == "WAITING_REVIEW"
            and detail.official_version is None
        )
        pending_official_replacement = (
            detail.status == "APPROVED"
            and detail.official_version is not None
        )
        if (
            not (initial_review or pending_official_replacement)
            or detail.pending_review_version is None
            or pending is None
            or pending.version != detail.pending_review_version
            or pending.review_status != "WAITING_REVIEW"
            or not pending.video_available
        ):
            raise ShotApprovalNotAllowed("Shot has no approvable pending version")

    def _require_no_active_task(self, project_id: str) -> None:
        if self._task_service.active_for_project(project_id) is not None:
            raise ProjectBusy("project already has an active Web task")
