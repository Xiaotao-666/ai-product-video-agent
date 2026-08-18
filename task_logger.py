"""Project-scoped, task-correlated and secret-safe business logging."""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from project_manager import ProjectPaths


fallback_logger = logging.getLogger(__name__)


def create_task_id() -> str:
    return f"{datetime.now():%Y%m%d_%H%M%S}_{uuid4().hex[:4]}"


class TaskLogger:
    """Write every business log below the current ProjectPaths.logs_dir."""

    def __init__(self, project: ProjectPaths, task_id: str | None = None) -> None:
        self.project = project
        self.task_id = task_id or create_task_id()
        self.current_stage = "initializing"
        self.task_log_path = project.task_log_file_path(self.task_id)
        self.error_log_path = project.error_log_file_path(self.task_id)
        self.api_log_path = project.api_log_file_path(self.task_id)
        self._secrets: set[str] = set()
        self._raw_counts: dict[str, int] = {}

    def register_secret(self, secret: str | None) -> None:
        if secret and secret.strip():
            self._secrets.add(secret.strip())

    def set_stage(self, stage: str) -> None:
        self.current_stage = stage

    def sanitize(self, value: Any) -> str:
        text = str(value)
        for secret in sorted(self._secrets, key=len, reverse=True):
            text = text.replace(secret, "***REDACTED***")
        patterns = (
            (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s,;]+", r"\1***REDACTED***"),
            (r"(?i)(bearer\s+)[A-Za-z0-9._-]+", r"\1***REDACTED***"),
            (r"(?i)((?:api[_ -]?key|token|secret)\s*[:=]\s*)[^\s,;]+", r"\1***REDACTED***"),
            (r"\bsk-[A-Za-z0-9_-]{8,}\b", "***REDACTED***"),
        )
        for pattern, replacement in patterns:
            text = re.sub(pattern, replacement, text)
        return text

    def _append(self, path: Path, content: str) -> None:
        try:
            path = self.project.ensure_within_project(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(content)
        except OSError as exc:
            # Logging must never change or stop the video workflow.
            fallback_logger.warning("项目日志写入失败（%s）：%s", path, exc)

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def event(self, event: str, message: str = "", **fields: Any) -> None:
        lines = [
            f"[{self._timestamp()}] [{event}]\n",
            f"task_id={self.task_id}\n",
        ]
        if message:
            lines.append(f"{self.sanitize(message)}\n")
        for key, value in fields.items():
            if value is not None and value != "":
                lines.append(f"{key}={self.sanitize(value)}\n")
        lines.append("\n")
        self._append(self.task_log_path, "".join(lines))

    def review_action(
        self, stage: str, action: str, feedback: str | None = None
    ) -> None:
        self.event(
            "REVIEW_ACTION", stage=stage, action=action, feedback=feedback
        )

    def api(self, event: str, provider: str, **fields: Any) -> None:
        lines = [
            f"[{self._timestamp()}] [{event}]\n",
            f"task_id={self.task_id}\n",
            f"provider={self.sanitize(provider)}\n",
        ]
        for key, value in fields.items():
            if value is not None and value != "":
                lines.append(f"{key}={self.sanitize(value)}\n")
        lines.append("\n")
        self._append(self.api_log_path, "".join(lines))

    def save_llm_raw(self, stage: str, content: str) -> Path:
        count = self._raw_counts.get(stage, 0) + 1
        self._raw_counts[stage] = count
        path = self.project.llm_raw_file_path(stage, self.task_id, count)
        try:
            path = self.project.ensure_within_project(path)
            path.write_text(self.sanitize(content), encoding="utf-8", errors="replace")
        except OSError as exc:
            fallback_logger.warning("LLM 原始响应写入失败（%s）：%s", path, exc)
        return path

    def error(
        self,
        error: BaseException | str,
        *,
        stage: str | None = None,
        raw_response: str | None = None,
    ) -> None:
        error_type = type(error).__name__ if isinstance(error, BaseException) else "Error"
        lines = [
            f"[{self._timestamp()}] [ERROR]\n",
            f"task_id={self.task_id}\n",
            f"stage={self.sanitize(stage or self.current_stage)}\n",
            f"error_type={error_type}\n",
            f"error_message={self.sanitize(error)}\n",
        ]
        if raw_response:
            lines.append(f"raw_response={self.sanitize(raw_response)}\n")
        lines.append("\n")
        self._append(self.error_log_path, "".join(lines))
