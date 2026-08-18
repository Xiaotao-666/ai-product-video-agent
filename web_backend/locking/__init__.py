"""Process-local locking primitives for Web write operations."""

from web_backend.locking.project_lock import (
    DEFAULT_PROJECT_LOCK_MANAGER,
    ProjectLockBusy,
    ProjectLockManager,
)

__all__ = [
    "DEFAULT_PROJECT_LOCK_MANAGER",
    "ProjectLockBusy",
    "ProjectLockManager",
]
