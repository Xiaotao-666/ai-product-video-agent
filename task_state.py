"""Unified lifecycle state for a video generation task."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskState(StrEnum):
    PENDING = "PENDING"
    WAITING_REVIEW = "WAITING_REVIEW"
    APPROVED = "APPROVED"
    REVISING = "REVISING"
    CANCELLED = "CANCELLED"
    GENERATING = "GENERATING"
    COMPLETED = "COMPLETED"


ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.PENDING: {TaskState.WAITING_REVIEW},
    TaskState.WAITING_REVIEW: {
        TaskState.APPROVED,
        TaskState.REVISING,
        TaskState.CANCELLED,
    },
    TaskState.REVISING: {TaskState.WAITING_REVIEW},
    TaskState.APPROVED: {TaskState.WAITING_REVIEW, TaskState.GENERATING},
    TaskState.GENERATING: {TaskState.COMPLETED, TaskState.CANCELLED},
    TaskState.CANCELLED: set(),
    TaskState.COMPLETED: set(),
}


@dataclass
class TaskStateManager:
    state: TaskState = TaskState.PENDING

    def transition(self, new_state: TaskState) -> None:
        if new_state not in ALLOWED_TRANSITIONS[self.state]:
            raise RuntimeError(
                f"非法任务状态迁移：{self.state.value} -> {new_state.value}"
            )
        self.state = new_state
