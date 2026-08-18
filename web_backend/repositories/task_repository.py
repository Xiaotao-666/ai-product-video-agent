"""Atomic JSON persistence for durable local Web task records."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from urllib.parse import unquote

from pydantic import ValidationError

from web_backend.models.tasks import (
    ACTIVE_TASK_STATUSES,
    TASK_ID_PATTERN,
    TaskError,
    TaskRecord,
    TaskStatus,
)
from web_backend.repositories.project_repository import (
    InvalidProjectId,
    normalize_project_id,
)


class TaskRepositoryError(RuntimeError):
    """Base class for safe task persistence failures."""


class InvalidTaskId(TaskRepositoryError):
    pass


class TaskNotFound(TaskRepositoryError):
    pass


class TaskDataCorrupt(TaskRepositoryError):
    pass


class TaskAlreadyExists(TaskRepositoryError):
    pass


def normalize_task_id(value: str) -> str:
    candidate = str(value or "").strip()
    decoded = candidate
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if not TASK_ID_PATTERN.fullmatch(decoded):
        raise InvalidTaskId("unsafe task id")
    return decoded


def _replace_record(record: TaskRecord, **updates: object) -> TaskRecord:
    payload = record.model_dump()
    payload.update(updates)
    return TaskRecord.model_validate(payload)


class TaskRepository:
    """Persist tasks outside Agent projects without side effects on reads."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root)
        self._guard = RLock()

    @property
    def tasks_root(self) -> Path:
        return self.runtime_root / "tasks"

    def create(self, task: TaskRecord) -> TaskRecord:
        self._validate_record_identity(task)
        with self._guard:
            tasks_root = self._ensure_tasks_root()
            path = self._task_path(task.task_id, tasks_root=tasks_root)
            if path.exists():
                raise TaskAlreadyExists("task already exists")
            self._atomic_write(path, task)
        return task

    def get(self, task_id: str) -> TaskRecord:
        normalized = normalize_task_id(task_id)
        with self._guard:
            tasks_root = self._existing_tasks_root()
            if tasks_root is None:
                raise TaskNotFound("task was not found")
            path = self._task_path(normalized, tasks_root=tasks_root)
            if not path.is_file():
                raise TaskNotFound("task was not found")
            return self._read(path)

    def update(self, task: TaskRecord) -> TaskRecord:
        self._validate_record_identity(task)
        with self._guard:
            tasks_root = self._existing_tasks_root()
            if tasks_root is None:
                raise TaskNotFound("task was not found")
            path = self._task_path(task.task_id, tasks_root=tasks_root)
            if not path.is_file():
                raise TaskNotFound("task was not found")
            self._atomic_write(path, task)
        return task

    def list_for_project(self, project_id: str) -> list[TaskRecord]:
        try:
            normalized_project_id = normalize_project_id(project_id)
        except InvalidProjectId:
            raise
        with self._guard:
            records = self._read_all()
        matching = [
            record for record in records if record.project_id == normalized_project_id
        ]
        matching.sort(key=lambda record: (record.created_at, record.task_id), reverse=True)
        return matching

    def find_active_for_project(self, project_id: str) -> TaskRecord | None:
        for record in self.list_for_project(project_id):
            if record.status in ACTIVE_TASK_STATUSES:
                return record
        return None

    def interrupt_active_tasks(self) -> list[TaskRecord]:
        """Mark abandoned queued/running records without executing any callable."""

        interrupted: list[TaskRecord] = []
        with self._guard:
            for record in self._read_all():
                if record.status not in ACTIVE_TASK_STATUSES:
                    continue
                replacement = _replace_record(
                    record,
                    status=TaskStatus.INTERRUPTED,
                    finished_at=datetime.now(timezone.utc),
                    error=TaskError(
                        code="TASK_INTERRUPTED",
                        message="上一次Web任务已中断，请根据当前项目状态继续。",
                        retryable=False,
                    ),
                    result=None,
                )
                path = self._task_path(
                    replacement.task_id,
                    tasks_root=self._existing_tasks_root_required(),
                )
                self._atomic_write(path, replacement)
                interrupted.append(replacement)
        return interrupted

    def _read_all(self) -> list[TaskRecord]:
        tasks_root = self._existing_tasks_root()
        if tasks_root is None:
            return []
        records: list[TaskRecord] = []
        for path in sorted(tasks_root.iterdir(), key=lambda item: item.name.casefold()):
            if not path.is_file() or not path.name.endswith(".json"):
                continue
            task_id = path.name[:-5]
            try:
                normalize_task_id(task_id)
            except InvalidTaskId as exc:
                raise TaskDataCorrupt("task filename is invalid") from exc
            safe_path = self._task_path(task_id, tasks_root=tasks_root)
            if safe_path != path:
                raise TaskDataCorrupt("task path is invalid")
            records.append(self._read(safe_path))
        return records

    def _read(self, path: Path) -> TaskRecord:
        try:
            resolved = path.resolve(strict=True)
            tasks_root = self._existing_tasks_root_required()
            if resolved.parent != tasks_root:
                raise TaskDataCorrupt("task file escaped runtime root")
            with resolved.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
            record = TaskRecord.model_validate(payload)
            self._validate_record_identity(record)
            if resolved.name != f"{record.task_id}.json":
                raise TaskDataCorrupt("task id does not match filename")
            return record
        except TaskDataCorrupt:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as exc:
            raise TaskDataCorrupt("task data is unreadable") from exc

    def _validate_record_identity(self, task: TaskRecord) -> None:
        normalize_task_id(task.task_id)
        try:
            normalized_project_id = normalize_project_id(task.project_id)
        except InvalidProjectId as exc:
            raise TaskDataCorrupt("task project id is invalid") from exc
        if normalized_project_id != task.project_id:
            raise TaskDataCorrupt("task project id is not canonical")

    def _existing_tasks_root(self) -> Path | None:
        if not self.runtime_root.exists():
            return None
        try:
            runtime_root = self.runtime_root.resolve(strict=True)
        except OSError as exc:
            raise TaskDataCorrupt("runtime root is unreadable") from exc
        tasks_root = runtime_root / "tasks"
        if not tasks_root.exists():
            return None
        if not tasks_root.is_dir():
            raise TaskDataCorrupt("task storage is not a directory")
        try:
            resolved = tasks_root.resolve(strict=True)
        except OSError as exc:
            raise TaskDataCorrupt("task storage is unreadable") from exc
        if resolved.parent != runtime_root:
            raise TaskDataCorrupt("task storage escaped runtime root")
        return resolved

    def _existing_tasks_root_required(self) -> Path:
        tasks_root = self._existing_tasks_root()
        if tasks_root is None:
            raise TaskNotFound("task storage does not exist")
        return tasks_root

    def _ensure_tasks_root(self) -> Path:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        runtime_root = self.runtime_root.resolve(strict=True)
        tasks_root = runtime_root / "tasks"
        tasks_root.mkdir(exist_ok=True)
        resolved = tasks_root.resolve(strict=True)
        if resolved.parent != runtime_root:
            raise TaskDataCorrupt("task storage escaped runtime root")
        return resolved

    @staticmethod
    def _task_path(task_id: str, *, tasks_root: Path) -> Path:
        normalized = normalize_task_id(task_id)
        path = tasks_root / f"{normalized}.json"
        if path.parent != tasks_root:
            raise InvalidTaskId("task path escaped runtime root")
        return path

    @staticmethod
    def _atomic_write(path: Path, task: TaskRecord) -> None:
        payload = task.model_dump(mode="json")
        rendered = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        temporary = path.parent / f".{task.task_id}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
