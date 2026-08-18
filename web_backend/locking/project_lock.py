"""In-memory per-project and project-root locks for one Web process."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import Lock, RLock


class ProjectLockBusy(RuntimeError):
    """Raised when a Web write transaction cannot acquire its lock promptly."""


class ProjectLockManager:
    """Own independent re-entrant locks without creating project lock files."""

    def __init__(self) -> None:
        self._registry_guard = Lock()
        self._project_locks: dict[str, RLock] = {}
        self._creation_locks: dict[str, RLock] = {}

    def _project_lock_for(self, project_id: str) -> RLock:
        key = str(project_id)
        with self._registry_guard:
            return self._project_locks.setdefault(key, RLock())

    def _creation_lock_for(self, projects_root: Path) -> RLock:
        key = str(Path(projects_root).expanduser().resolve(strict=False)).casefold()
        with self._registry_guard:
            return self._creation_locks.setdefault(key, RLock())

    @staticmethod
    @contextmanager
    def _acquire(lock: RLock, *, timeout_seconds: float) -> Iterator[None]:
        timeout = max(0.0, float(timeout_seconds))
        acquired = (
            lock.acquire(blocking=False)
            if timeout == 0
            else lock.acquire(timeout=timeout)
        )
        if not acquired:
            raise ProjectLockBusy("Web project write lock is busy")
        try:
            yield
        finally:
            lock.release()

    @contextmanager
    def project_write(
        self,
        project_id: str,
        *,
        timeout_seconds: float = 0.0,
    ) -> Iterator[None]:
        """Acquire the write lock for one stable project ID."""

        with self._acquire(
            self._project_lock_for(project_id),
            timeout_seconds=timeout_seconds,
        ):
            yield

    @contextmanager
    def project_creation(
        self,
        projects_root: Path,
        *,
        timeout_seconds: float = 0.25,
    ) -> Iterator[None]:
        """Serialize directory selection and project creation for one root."""

        with self._acquire(
            self._creation_lock_for(projects_root),
            timeout_seconds=timeout_seconds,
        ):
            yield


DEFAULT_PROJECT_LOCK_MANAGER = ProjectLockManager()
