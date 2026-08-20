"""Internal task submission and query service; no public submit API exists."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import contextmanager
from datetime import datetime, timezone
from threading import Lock
from typing import Iterator

from web_backend.middleware import select_correlation_id
from web_backend.models.tasks import (
    ProjectTaskListResponse,
    TaskError,
    TaskOperation,
    TaskRecord,
    TaskStatus,
)
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.repositories.task_repository import TaskRepository
from web_backend.services.projects import ProjectBusy
from web_backend.services.task_runner import (
    TaskCallable,
    TaskRunner,
    TaskRunnerClosed,
)


class TaskService:
    """Coordinate durable creation with same-project active-task protection."""

    def __init__(
        self,
        repository: TaskRepository,
        runner: TaskRunner,
        project_repository: ProjectRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._repository = repository
        self._runner = runner
        self._project_repository = project_repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._id_factory = id_factory or (lambda: f"task_{uuid.uuid4().hex}")
        self._submission_guard = Lock()

    def submit(
        self,
        *,
        project_id: str,
        operation: TaskOperation,
        target_id: str | None = None,
        correlation_id: str | None,
        callable_: TaskCallable,
        allow_parallel_targets: bool = False,
        acquire_project_lock: bool = True,
    ) -> TaskRecord:
        if (
            allow_parallel_targets or not acquire_project_lock
        ) and operation != TaskOperation.SHOT_PROMPT_REVISION_DRAFT:
            raise ValueError(
                "project-lock-free execution is reserved for Prompt draft tasks"
            )
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        with self._submission_guard:
            active_tasks = self._repository.list_active_for_project(
                canonical_project_id
            )
            parallel_target_is_safe = (
                allow_parallel_targets
                and not acquire_project_lock
                and target_id is not None
                and all(
                    active.operation == operation
                    and active.target_id is not None
                    and active.target_id != target_id
                    for active in active_tasks
                )
            )
            if active_tasks and not parallel_target_is_safe:
                raise ProjectBusy("project already has an active Web task")
            task = TaskRecord(
                task_id=self._id_factory(),
                project_id=canonical_project_id,
                operation=operation,
                target_id=target_id,
                status=TaskStatus.QUEUED,
                created_at=self._clock(),
                correlation_id=select_correlation_id(correlation_id),
            )
            self._repository.create(task)
            try:
                self._runner.submit(
                    task.task_id,
                    callable_,
                    acquire_project_lock=acquire_project_lock,
                )
            except TaskRunnerClosed:
                interrupted_payload = task.model_dump()
                interrupted_payload.update(
                    status=TaskStatus.INTERRUPTED,
                    finished_at=self._clock(),
                    error=TaskError(
                        code="TASK_RUNNER_UNAVAILABLE",
                        message="任务执行器当前不可用。",
                        retryable=False,
                    ),
                )
                self._repository.update(
                    TaskRecord.model_validate(interrupted_payload)
                )
                raise
            return task

    def get(self, task_id: str) -> TaskRecord:
        return self._repository.get(task_id)

    def list_for_project(self, project_id: str) -> ProjectTaskListResponse:
        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        return ProjectTaskListResponse(
            project_id=canonical_project_id,
            tasks=self._repository.list_for_project(canonical_project_id),
        )

    def active_for_project(self, project_id: str) -> TaskRecord | None:
        """Return an active task without creating runtime storage."""

        canonical_project_id = self._project_repository.get_project(
            project_id
        ).project_id
        return self._repository.find_active_for_project(canonical_project_id)

    @contextmanager
    def prevent_task_submission(self) -> Iterator[None]:
        """Keep a short synchronous action atomic with task submission."""

        with self._submission_guard:
            yield

    def recover_interrupted_tasks(self) -> list[TaskRecord]:
        return self._repository.interrupt_active_tasks()
