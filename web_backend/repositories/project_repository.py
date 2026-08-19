"""Strictly read-only discovery and projection of existing Agent projects."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from web_backend.models.projects import (
    PostProductionState,
    ProjectDetail,
    ProjectListResponse,
    ProjectRequest,
    ProjectSummary,
    ProjectWorkflowResponse,
    WorkflowPhase,
)
from web_backend.services.workflow import ProjectManifests, derive_workflow


_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|file://)")
_SECRET_MARKER = re.compile(
    r"(?i)(?:api[_-]?key|authorization|bearer\s+\S+|sk-[A-Za-z0-9_-]{12,})"
)
_DRIVE_PREFIX = re.compile(r"(?i)^[a-z]:")
_SKIPPED_DIRECTORY_NAMES = {"logs", "log", "staging", ".staging", "tmp", "temp"}
_SUPPORTED_PROJECT_SCHEMAS = {1, 2}
_MANIFEST_PATHS = {
    "assembly": ("videos", "assembly_manifest.json"),
    "voice": ("voice", "voice_manifest.json"),
    "subtitle": ("subtitles", "subtitle_manifest.json"),
    "music": ("music", "music_manifest.json"),
    "export": ("exports", "export_manifest.json"),
}
_PLANNING_ARTIFACT_PATHS = {
    "creative_exists": ("concepts", "creative_brief.json"),
    "storyboard_exists": ("storyboard", "storyboard.json"),
}


class ProjectRepositoryError(RuntimeError):
    """Base class for failures translated by the HTTP layer."""


class InvalidProjectId(ProjectRepositoryError):
    pass


class ProjectNotFound(ProjectRepositoryError):
    pass


class ProjectDataCorrupt(ProjectRepositoryError):
    pass


class ProjectDataUnsupported(ProjectRepositoryError):
    pass


@dataclass(frozen=True)
class _ProjectRecord:
    directory_name: str
    directory_path: Path
    project_file: Path
    data: Mapping[str, Any] | None
    data_error: type[ProjectRepositoryError] | None
    api_id: str = ""


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def normalize_project_id(value: str) -> str:
    candidate = str(value or "").strip()
    decoded = candidate
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    if (
        not decoded
        or decoded in {".", ".."}
        or "/" in decoded
        or "\\" in decoded
        or "\x00" in decoded
        or _DRIVE_PREFIX.match(decoded)
        or decoded.startswith("//")
        or decoded.startswith("\\\\")
        or Path(decoded).is_absolute()
        or len(decoded) > 255
    ):
        raise InvalidProjectId("unsafe project id")
    return decoded


def _public_project_id(value: Any) -> str | None:
    try:
        candidate = normalize_project_id(str(value))
    except InvalidProjectId:
        return None
    if _WINDOWS_ABSOLUTE.search(candidate) or _SECRET_MARKER.search(candidate):
        return None
    return candidate


def _legacy_project_id(directory_name: str) -> str:
    public_name = _public_project_id(directory_name)
    if public_name is not None:
        return public_name
    digest = hashlib.sha256(directory_name.encode("utf-8")).hexdigest()[:24]
    return f"legacy-{digest}"


def _safe_text(value: Any, *, fallback: str | None = None) -> str | None:
    if value is None:
        return fallback
    text = str(value)
    if _WINDOWS_ABSOLUTE.search(text):
        return "[本地路径已隐藏]"
    if _SECRET_MARKER.search(text):
        return "[敏感内容已隐藏]"
    return text[:10000]


def _safe_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _schema_version(data: Mapping[str, Any]) -> int | None:
    value = data.get("project_schema_version", data.get("schema_version"))
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_timestamp(data: Mapping[str, Any] | None, project_file: Path) -> tuple[str, float]:
    raw = data.get("updated_at") if data else None
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.isoformat(), parsed.timestamp()
        except ValueError:
            pass
    stat_result = project_file.stat()
    parsed = datetime.fromtimestamp(stat_result.st_mtime, tz=timezone.utc)
    return parsed.isoformat(), stat_result.st_mtime


class ProjectRepository:
    """Read JSON and metadata without importing or invoking mutable Core classes."""

    def __init__(self, projects_root: Path) -> None:
        self.projects_root = Path(projects_root)

    def list_projects(self) -> ProjectListResponse:
        summaries: list[tuple[float, ProjectSummary]] = []
        for record in self._discover_records():
            updated_at, sort_timestamp = _safe_timestamp(record.data, record.project_file)
            if record.data_error or record.data is None:
                summary = ProjectSummary(
                    project_id=record.api_id,
                    name=_safe_text(record.directory_name, fallback="项目") or "项目",
                    workflow_phase=WorkflowPhase.ERROR,
                    status="UNREADABLE",
                    updated_at=updated_at,
                    assembly={"status": "UNKNOWN", "needs_update": False, "version": None},
                    final_export={
                        "status": "UNKNOWN",
                        "version": None,
                        "created_at": None,
                        "stale": False,
                    },
                )
            else:
                try:
                    manifests = self._load_manifests(record)
                    workflow = derive_workflow(record.data, manifests)
                    summary = ProjectSummary(
                        project_id=record.api_id,
                        name=self._project_name(record),
                        workflow_phase=workflow.workflow_phase,
                        status=workflow.status,
                        updated_at=updated_at,
                        assembly=workflow.stages.assembly,
                        final_export=workflow.stages.export,
                    )
                except ProjectRepositoryError:
                    summary = ProjectSummary(
                        project_id=record.api_id,
                        name=self._project_name(record),
                        workflow_phase=WorkflowPhase.ERROR,
                        status="UNREADABLE",
                        updated_at=updated_at,
                        assembly={"status": "UNKNOWN", "needs_update": False, "version": None},
                        final_export={
                            "status": "UNKNOWN",
                            "version": None,
                            "created_at": None,
                            "stale": False,
                        },
                    )
            summaries.append((sort_timestamp, summary))
        summaries.sort(key=lambda item: (-item[0], item[1].project_id.casefold()))
        return ProjectListResponse(projects=[summary for _, summary in summaries])

    def get_project(self, project_id: str) -> ProjectDetail:
        record = self._resolve_record(project_id)
        data = self._require_data(record)
        manifests = self._load_manifests(record)
        workflow = derive_workflow(data, manifests)
        request = data.get("request") if isinstance(data.get("request"), Mapping) else {}
        updated_at, _ = _safe_timestamp(data, record.project_file)
        post = data.get("post_production")
        post_status = "NOT_STARTED"
        if isinstance(post, Mapping):
            raw_post_status = str(post.get("status") or "NOT_STARTED").upper()
            if raw_post_status in {"NOT_STARTED", "RUNNING", "COMPLETED", "FAILED", "FINAL_COMPLETED"}:
                post_status = raw_post_status
        return ProjectDetail(
            project_id=record.api_id,
            name=self._project_name(record),
            request=ProjectRequest(
                product_name=_safe_text(request.get("product_name")),
                product_description=_safe_text(request.get("product_description")),
                user_notes=_safe_text(request.get("user_notes")),
                duration_seconds=_safe_number(request.get("duration_seconds")),
                video_style=_safe_text(request.get("video_style")),
                video_purpose=_safe_text(request.get("video_purpose")),
            ),
            workflow=workflow,
            assembly=workflow.stages.assembly,
            post_production=PostProductionState(
                status=post_status,
                voice=workflow.stages.voice,
                subtitle=workflow.stages.subtitle,
                music=workflow.stages.music,
            ),
            final_export=workflow.stages.export,
            updated_at=updated_at,
        )

    def get_workflow(self, project_id: str) -> ProjectWorkflowResponse:
        record = self._resolve_record(project_id)
        data = self._require_data(record)
        workflow = derive_workflow(data, self._load_manifests(record))
        updated_at, _ = _safe_timestamp(data, record.project_file)
        return ProjectWorkflowResponse(
            project_id=record.api_id,
            workflow_phase=workflow.workflow_phase,
            status=workflow.status,
            stages=workflow.stages,
            available_actions=workflow.available_actions,
            updated_at=updated_at,
        )

    def resolve_project_dir(self, project_id: str) -> Path:
        return self._resolve_record(project_id).directory_path

    def _discover_records(self) -> list[_ProjectRecord]:
        if not self.projects_root.exists() or not self.projects_root.is_dir():
            return []
        root = self.projects_root.resolve()
        records: list[_ProjectRecord] = []
        for child in sorted(self.projects_root.iterdir(), key=lambda item: item.name.casefold()):
            if child.name.casefold() in _SKIPPED_DIRECTORY_NAMES or child.name.startswith("."):
                continue
            try:
                resolved_directory = child.resolve()
            except OSError:
                continue
            if not child.is_dir() or resolved_directory.parent != root:
                continue
            project_file = resolved_directory / "project.json"
            if not project_file.is_file():
                continue
            try:
                resolved_file = project_file.resolve()
            except OSError:
                continue
            if not _is_within(resolved_file, resolved_directory):
                continue
            data: Mapping[str, Any] | None
            error: type[ProjectRepositoryError] | None = None
            try:
                with resolved_file.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                if not isinstance(payload, Mapping):
                    raise ProjectDataCorrupt("project data is not an object")
                data = payload
                if _schema_version(data) not in _SUPPORTED_PROJECT_SCHEMAS:
                    error = ProjectDataUnsupported
            except (OSError, UnicodeError, json.JSONDecodeError, ProjectDataCorrupt):
                data = None
                error = ProjectDataCorrupt
            records.append(
                _ProjectRecord(
                    directory_name=child.name,
                    directory_path=resolved_directory,
                    project_file=resolved_file,
                    data=data,
                    data_error=error,
                )
            )
        return self._assign_api_ids(records)

    def _assign_api_ids(self, records: list[_ProjectRecord]) -> list[_ProjectRecord]:
        stable_ids: list[str | None] = []
        for record in records:
            value = record.data.get("project_id") if record.data else None
            stable_ids.append(_public_project_id(value) if value else None)
        counts = Counter(value for value in stable_ids if value)
        directory_names = {record.directory_name for record in records}
        assigned: list[_ProjectRecord] = []
        for record, stable_id in zip(records, stable_ids, strict=True):
            use_stable = bool(
                stable_id
                and counts[stable_id] == 1
                and (stable_id not in directory_names or stable_id == record.directory_name)
            )
            api_id = stable_id if use_stable else _legacy_project_id(record.directory_name)
            assigned.append(
                _ProjectRecord(
                    directory_name=record.directory_name,
                    directory_path=record.directory_path,
                    project_file=record.project_file,
                    data=record.data,
                    data_error=record.data_error,
                    api_id=api_id,
                )
            )
        return assigned

    def _resolve_record(self, project_id: str) -> _ProjectRecord:
        normalized = normalize_project_id(project_id)
        for record in self._discover_records():
            if record.api_id == normalized:
                root = self.projects_root.resolve()
                resolved = record.directory_path.resolve()
                if resolved.parent != root:
                    raise InvalidProjectId("project path escaped root")
                return record
        raise ProjectNotFound("project was not found")

    def _require_data(self, record: _ProjectRecord) -> Mapping[str, Any]:
        if record.data_error is ProjectDataUnsupported:
            raise ProjectDataUnsupported("project schema is unsupported")
        if record.data_error or record.data is None:
            raise ProjectDataCorrupt("project data is unreadable")
        return record.data

    def _load_manifests(self, record: _ProjectRecord) -> ProjectManifests:
        loaded: dict[str, Any] = {}
        for name, parts in _MANIFEST_PATHS.items():
            path = record.directory_path.joinpath(*parts)
            if not path.exists():
                loaded[name] = None
                continue
            if not path.is_file():
                raise ProjectDataCorrupt("manifest path is not a file")
            try:
                resolved = path.resolve()
            except OSError as exc:
                raise ProjectDataCorrupt("manifest path cannot be resolved") from exc
            if not _is_within(resolved, record.directory_path):
                raise ProjectDataCorrupt("manifest escaped project directory")
            try:
                with resolved.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise ProjectDataCorrupt("manifest is unreadable") from exc
            if not isinstance(payload, Mapping):
                raise ProjectDataCorrupt("manifest is not an object")
            loaded[name] = payload

        for name, parts in _PLANNING_ARTIFACT_PATHS.items():
            loaded[name] = self._managed_path_exists(record, *parts)
        loaded["video_prompts_exist"] = any(
            self._managed_path_exists(record, "storyboard", filename)
            for filename in (
                "video_prompts.json",
                "video_prompt_generation_progress.json",
            )
        )
        loaded["shot_artifacts_exist"] = self._shot_artifacts_exist(record)
        return ProjectManifests(**loaded)

    @staticmethod
    def _managed_path_exists(record: _ProjectRecord, *parts: str) -> bool:
        path = record.directory_path.joinpath(*parts)
        if not path.exists():
            return False
        try:
            resolved = path.resolve(strict=True)
        except OSError as exc:
            raise ProjectDataCorrupt("managed artifact cannot be resolved") from exc
        if not _is_within(resolved, record.directory_path):
            raise ProjectDataCorrupt("managed artifact escaped project directory")
        return True

    @staticmethod
    def _shot_artifacts_exist(record: _ProjectRecord) -> bool:
        shots = record.directory_path / "shots"
        if not shots.exists():
            return False
        try:
            resolved = shots.resolve(strict=True)
        except OSError as exc:
            raise ProjectDataCorrupt("Shot storage cannot be resolved") from exc
        if not _is_within(resolved, record.directory_path):
            raise ProjectDataCorrupt("Shot storage escaped project directory")
        if not resolved.is_dir():
            return True
        try:
            return any(path.is_file() or path.is_symlink() for path in resolved.rglob("*"))
        except OSError as exc:
            raise ProjectDataCorrupt("Shot storage is unreadable") from exc

    def _project_name(self, record: _ProjectRecord) -> str:
        assert record.data is not None
        value = record.data.get("project_name")
        if not value and isinstance(record.data.get("request"), Mapping):
            value = record.data["request"].get("product_name")
        return _safe_text(value, fallback=record.directory_name) or "项目"
