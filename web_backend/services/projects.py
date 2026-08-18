"""Application service for safe, local-only Agent project creation."""

from __future__ import annotations

import json
import logging
import re
import shutil
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import ValidationError

from web_backend.locking.project_lock import ProjectLockBusy, ProjectLockManager
from web_backend.models.projects import (
    ProjectCreateRequest,
    ProjectCreateResponse,
)
from web_backend.services.workflow import ProjectManifests, derive_workflow


_WINDOWS_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_PATH_LIKE_NAME = re.compile(
    r"(?i)(?:^[a-z]:[\\/]|^\\\\|^file://|(?:^|[\\/])\.\.(?:[\\/]|$))"
)
_SENSITIVE_NAME = re.compile(r"(?i)(?:api[_-]?key|authorization|bearer|secret)")
_RESERVED_WINDOWS_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}
_MAX_DIRECTORY_NAME_LENGTH = 80
_PROJECT_ID = re.compile(r"^[0-9a-f]{32}$")
_STAGING_PREFIX = ".web-create-"
_CREATE_LOCK_TIMEOUT_SECONDS = 0.25
service_logger = logging.getLogger("uvicorn.error.web_projects")


class ProjectServiceError(RuntimeError):
    """Base class for expected, safely mapped create failures."""


class InvalidProjectRequest(ProjectServiceError):
    pass


class InvalidVideoDuration(ProjectServiceError):
    pass


class InvalidProjectName(ProjectServiceError):
    pass


class ProjectBusy(ProjectServiceError):
    pass


class ProjectCreateFailed(ProjectServiceError):
    pass


def safe_project_directory_name(product_name: str) -> str:
    """Preserve readable Unicode while producing one safe Windows component."""

    normalized = unicodedata.normalize("NFKC", str(product_name)).strip()
    if not normalized or _PATH_LIKE_NAME.search(normalized):
        raise InvalidProjectName("project name is empty or path-like")
    if _SENSITIVE_NAME.search(normalized):
        raise InvalidProjectName("project name contains a sensitive marker")
    safe = _WINDOWS_INVALID.sub("_", normalized)
    safe = re.sub(r"\s+", " ", safe).strip().strip(".").rstrip(" .")
    if not safe or safe in {".", ".."}:
        raise InvalidProjectName("project name has no safe characters")
    safe = safe[:_MAX_DIRECTORY_NAME_LENGTH].rstrip(" .")
    if not safe:
        raise InvalidProjectName("project name is empty after truncation")
    reserved_stem = safe.split(".", 1)[0].upper()
    if reserved_stem in _RESERVED_WINDOWS_NAMES:
        safe = f"项目_{safe}"
    return safe


def _validate_core_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Use the exact Core request model and duration planner without importing at startup."""

    from prompt_generator import ProductVideoRequest
    from storyboard import StoryboardError, plan_shot_durations

    try:
        request = ProductVideoRequest.model_validate(dict(payload))
    except ValidationError as exc:
        fields = {str(item) for error in exc.errors() for item in error.get("loc", ())}
        if "product_name" in fields:
            raise InvalidProjectName("Core rejected the project name") from exc
        raise InvalidProjectRequest("Core rejected the product request") from exc
    try:
        plan_shot_durations(request.duration_seconds)
    except StoryboardError as exc:
        raise InvalidVideoDuration("Core rejected the video duration") from exc
    return request.model_dump()


def _create_core_checkpoint(
    staging_directory: Path,
    project_name: str,
    request_data: dict[str, Any],
) -> Mapping[str, Any]:
    """Create the standard CLI-compatible directory tree and Schema 2 checkpoint."""

    from project_manager import create_project_paths
    from project_state import ProjectCheckpoint

    paths = create_project_paths(staging_directory)
    checkpoint = ProjectCheckpoint.create(paths, project_name, request_data)
    return checkpoint.data


def _verify_checkpoint(staging_directory: Path, expected_name: str) -> dict[str, Any]:
    project_file = staging_directory / "project.json"
    try:
        with project_file.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectCreateFailed("Core did not create a readable project checkpoint") from exc
    if not isinstance(data, dict):
        raise ProjectCreateFailed("Core created an invalid project checkpoint")
    if data.get("project_schema_version") != 2:
        raise ProjectCreateFailed("Core created an unsupported project checkpoint")
    if data.get("project_name") != expected_name:
        raise ProjectCreateFailed("Core project name verification failed")
    if data.get("current_stage") != "CREATIVE" or data.get("status") != "NOT_STARTED":
        raise ProjectCreateFailed("Core project initial state verification failed")
    if not _PROJECT_ID.fullmatch(str(data.get("project_id") or "")):
        raise ProjectCreateFailed("Core project ID verification failed")
    return data


def _unique_directory(root: Path, base_name: str) -> Path:
    candidate = root / base_name
    suffix = 2
    while candidate.exists():
        candidate = root / f"{base_name}_{suffix}"
        suffix += 1
    return candidate


def _remove_owned_directory(directory: Path, root: Path, operation_id: str) -> None:
    try:
        resolved = directory.resolve(strict=False)
        if (
            resolved.parent != root
            or resolved.name != f"{_STAGING_PREFIX}{operation_id}"
        ):
            return
        if resolved.exists():
            shutil.rmtree(resolved)
    except OSError:
        service_logger.error(
            "Failed to clean Web project staging operation_id=%s",
            operation_id,
        )


class ProjectService:
    """Coordinate one atomic, Core-compatible project creation transaction."""

    def __init__(
        self,
        projects_root: Path,
        lock_manager: ProjectLockManager,
        *,
        create_lock_timeout_seconds: float = _CREATE_LOCK_TIMEOUT_SECONDS,
    ) -> None:
        self.projects_root = Path(projects_root)
        self.lock_manager = lock_manager
        self.create_lock_timeout_seconds = create_lock_timeout_seconds

    def create_project(self, request: ProjectCreateRequest) -> ProjectCreateResponse:
        request_data = _validate_core_request(request.model_dump())
        project_name = str(request_data["product_name"])
        directory_name = safe_project_directory_name(project_name)
        operation_id = uuid4().hex
        root = self.projects_root.expanduser().resolve(strict=False)
        staging = root / f"{_STAGING_PREFIX}{operation_id}"
        owned_staging = False

        try:
            with self.lock_manager.project_creation(
                root,
                timeout_seconds=self.create_lock_timeout_seconds,
            ):
                root.mkdir(parents=True, exist_ok=True)
                if not root.is_dir():
                    raise ProjectCreateFailed("Configured projects root is unavailable")
                final_directory = _unique_directory(root, directory_name)
                staging.mkdir(exist_ok=False)
                owned_staging = True
                _create_core_checkpoint(staging, project_name, request_data)
                checkpoint = _verify_checkpoint(staging, project_name)
                workflow = derive_workflow(checkpoint, ProjectManifests())
                if final_directory.exists():
                    final_directory = _unique_directory(root, directory_name)
                staging.rename(final_directory)
                owned_staging = False
        except ProjectLockBusy as exc:
            raise ProjectBusy("projects root creation lock is busy") from exc
        except (InvalidProjectName, InvalidProjectRequest, InvalidVideoDuration, ProjectBusy):
            raise
        except ProjectCreateFailed:
            raise
        except Exception as exc:
            raise ProjectCreateFailed("Core project creation failed") from exc
        finally:
            if owned_staging:
                _remove_owned_directory(staging, root, operation_id)

        return ProjectCreateResponse(
            project_id=str(checkpoint["project_id"]),
            name=project_name,
            workflow_phase=workflow.workflow_phase,
            status=str(checkpoint["status"]),
            created_at=str(checkpoint["created_at"]),
            updated_at=str(checkpoint["updated_at"]),
        )
