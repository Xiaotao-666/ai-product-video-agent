"""Bounded local executor for durable Web task records."""

from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import nullcontext
from datetime import datetime, timezone
from threading import Lock
from time import perf_counter

from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.tasks import (
    TaskError,
    TaskRecord,
    TaskResultReference,
    TaskStatus,
)
from web_backend.repositories.task_repository import (
    TaskNotFound,
    TaskRepository,
)


task_logger = logging.getLogger("uvicorn.error.web_tasks")
TaskCallable = Callable[[], TaskResultReference | None]


class TaskRunnerClosed(RuntimeError):
    pass


class TaskExecutionFailure(RuntimeError):
    """An explicitly safe business failure for a future task adapter."""

    def __init__(self, error: TaskError) -> None:
        super().__init__(error.code)
        self.error = error


def _updated(record: TaskRecord, **updates: object) -> TaskRecord:
    payload = record.model_dump()
    payload.update(updates)
    return TaskRecord.model_validate(payload)


class TaskRunner:
    """Execute each callable once, under the existing per-project write lock."""

    def __init__(
        self,
        repository: TaskRepository,
        project_lock_manager: ProjectLockManager,
        *,
        max_workers: int = 2,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._repository = repository
        self._project_lock_manager = project_lock_manager
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="web-task",
        )
        self._state_guard = Lock()
        self._futures: dict[str, Future[None]] = {}
        self._closed = False

    @property
    def is_shutdown(self) -> bool:
        with self._state_guard:
            return self._closed

    def submit(
        self,
        task_id: str,
        operation: TaskCallable,
        *,
        acquire_project_lock: bool = True,
    ) -> Future[None]:
        with self._state_guard:
            if self._closed:
                raise TaskRunnerClosed("task runner is shut down")
            future = self._executor.submit(
                self._execute,
                task_id,
                operation,
                acquire_project_lock,
            )
            self._futures[task_id] = future
        future.add_done_callback(
            lambda completed, current_task_id=task_id: self._task_done(
                current_task_id,
                completed,
            )
        )
        return future

    def shutdown(self) -> None:
        with self._state_guard:
            if self._closed:
                return
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)

    def _execute(
        self,
        task_id: str,
        operation: TaskCallable,
        acquire_project_lock: bool,
    ) -> None:
        try:
            queued = self._repository.get(task_id)
        except TaskNotFound:
            return
        if queued.status is not TaskStatus.QUEUED:
            return

        running = _updated(
            queued,
            status=TaskStatus.RUNNING,
            started_at=self._clock(),
        )
        self._repository.update(running)
        started = perf_counter()
        self._log_status(running)

        try:
            lock_context = (
                self._project_lock_manager.project_write(running.project_id)
                if acquire_project_lock
                else nullcontext()
            )
            with lock_context:
                result = operation()
                if result is not None and not isinstance(result, TaskResultReference):
                    raise TypeError("task callable returned an unsupported result")
            succeeded = _updated(
                running,
                status=TaskStatus.SUCCEEDED,
                finished_at=self._clock(),
                result=result,
            )
            self._repository.update(succeeded)
            self._log_status(succeeded, duration_seconds=perf_counter() - started)
        except ProjectLockBusy:
            self._fail(
                running,
                TaskError(
                    code="PROJECT_BUSY",
                    message="项目当前正在执行其他操作，请稍后重试。",
                    retryable=True,
                ),
                started,
            )
        except TaskExecutionFailure as error:
            self._fail(running, error.error, started)
        except Exception:
            # Never persist or log repr/traceback from an unknown operation error.
            self._fail(
                running,
                TaskError(
                    code="TASK_EXECUTION_FAILED",
                    message="任务执行失败。",
                    retryable=False,
                ),
                started,
            )

    def _fail(
        self,
        running: TaskRecord,
        error: TaskError,
        started: float,
    ) -> None:
        failed = _updated(
            running,
            status=TaskStatus.FAILED,
            finished_at=self._clock(),
            error=error,
            result=None,
        )
        self._repository.update(failed)
        self._log_status(failed, duration_seconds=perf_counter() - started)

    def _task_done(self, task_id: str, future: Future[None]) -> None:
        with self._state_guard:
            self._futures.pop(task_id, None)
        if not future.cancelled():
            return
        try:
            record = self._repository.get(task_id)
            if record.status is not TaskStatus.QUEUED:
                return
            interrupted = _updated(
                record,
                status=TaskStatus.INTERRUPTED,
                finished_at=self._clock(),
                error=TaskError(
                    code="TASK_INTERRUPTED",
                    message="任务在服务关闭前未开始执行。",
                    retryable=False,
                ),
            )
            self._repository.update(interrupted)
            self._log_status(interrupted)
        except Exception:
            task_logger.error("task_status_update_failed task_id=%s", task_id)

    @staticmethod
    def _log_status(
        record: TaskRecord,
        *,
        duration_seconds: float | None = None,
    ) -> None:
        task_logger.info(
            "task_id=%s project_id=%s operation=%s status=%s "
            "duration_seconds=%s correlation_id=%s",
            record.task_id,
            record.project_id,
            record.operation.value,
            record.status.value,
            "n/a" if duration_seconds is None else f"{duration_seconds:.3f}",
            record.correlation_id,
        )
