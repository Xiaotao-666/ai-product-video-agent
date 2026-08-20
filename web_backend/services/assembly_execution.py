"""Durable Assembly execution from immutable plan snapshots."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_manager import ProjectDirectoryError, ProjectPaths, create_project_paths
from project_state import ProjectCheckpoint
from shot_storage import ShotStorageError, validate_bundle
from task_logger import TaskLogger
from video_assembly import (
    AssemblyError,
    AssemblyExecutionResult,
    execute_assembly_snapshot,
)
from web_backend.models.assembly_planning import AssemblyPlan
from web_backend.models.tasks import (
    TaskOperation,
    TaskRecord,
    TaskResultReference,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
    ProjectRepositoryError,
)
from web_backend.services.assembly_planning import (
    AssemblyPlanOutdated,
    AssemblyPlanningService,
)
from web_backend.services.task_failures import raise_task_failure
from web_backend.services.tasks import TaskService
from web_backend.services.projects import ProjectBusy


class AssemblyAlreadyExecuted(ProjectRepositoryError):
    """The requested plan already has a completed Final Video bundle."""


class AssemblyNotResumable(ProjectRepositoryError):
    """No interrupted/failed execution exists for the requested plan."""


CoreExecutor = Callable[..., AssemblyExecutionResult]


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result > 0 else None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_json(path: Path, *, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectDataCorrupt(f"{label} is unreadable") from exc
    if not isinstance(payload, dict):
        raise ProjectDataCorrupt(f"{label} is invalid")
    return payload


class AssemblyExecutionService:
    """Submit and recover one project-scoped Assembly execution task."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        planning_service: AssemblyPlanningService,
        task_service: TaskService,
        *,
        core_executor: CoreExecutor = execute_assembly_snapshot,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._project_repository = project_repository
        self._planning_service = planning_service
        self._task_service = task_service
        self._core_executor = core_executor
        self._id_factory = id_factory or (lambda: f"assembly_{uuid.uuid4().hex}")

    def submit_execute(
        self,
        project_id: str,
        assembly_version: int,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        plan = self._planning_service.require_current_plan(
            project_id, assembly_version
        )
        paths = self._paths(plan.project_id)
        execution = self._find_execution(
            self._load_manifest(paths), assembly_version
        )
        if execution is not None:
            status = str(execution.get("status") or "").upper()
            if status == "COMPLETED":
                raise AssemblyAlreadyExecuted("Assembly plan was already executed")
            if status == "RUNNING":
                raise ProjectBusy("Assembly execution is running")
            raise AssemblyNotResumable("Assembly execution already exists; resume it")
        return self._submit(plan, correlation_id=correlation_id, resume=False)

    def submit_resume(
        self,
        project_id: str,
        assembly_version: int,
        *,
        correlation_id: str | None,
    ) -> TaskRecord:
        plan = self._planning_service.require_current_plan(
            project_id, assembly_version
        )
        paths = self._paths(plan.project_id)
        execution = self._find_execution(
            self._load_manifest(paths), assembly_version
        )
        if execution is None or str(execution.get("status") or "").upper() not in {
            "RUNNING",
            "FAILED",
        }:
            raise AssemblyNotResumable("Assembly execution cannot be resumed")
        return self._submit(plan, correlation_id=correlation_id, resume=True)

    def _submit(
        self,
        plan: AssemblyPlan,
        *,
        correlation_id: str | None,
        resume: bool,
    ) -> TaskRecord:
        return self._task_service.submit(
            project_id=plan.project_id,
            operation=TaskOperation.ASSEMBLY_EXECUTE,
            target_id=f"assembly_v{plan.assembly_version:03d}",
            correlation_id=correlation_id,
            callable_=lambda: self._execute(
                plan.project_id, plan.assembly_version, resume=resume
            ),
        )

    def _execute(
        self, project_id: str, assembly_version: int, *, resume: bool
    ) -> TaskResultReference:
        try:
            plan = self._planning_service.require_current_plan(
                project_id, assembly_version
            )
        except AssemblyPlanOutdated:
            raise_task_failure(
                "ASSEMBLY_PLAN_OUTDATED",
                "合片计划已失效，请重新创建计划。",
            )
        paths = self._paths(project_id, ensure_directories=True)
        checkpoint: ProjectCheckpoint | None = None
        execution: dict[str, Any] | None = None
        manifest: dict[str, Any] | None = None
        staging: Path | None = None
        try:
            checkpoint = ProjectCheckpoint.load(paths)
            manifest = self._load_manifest(paths)
            execution = self._find_execution(manifest, assembly_version)
            if resume:
                if execution is None or str(execution.get("status") or "").upper() not in {
                    "RUNNING",
                    "FAILED",
                }:
                    raise AssemblyNotResumable("Assembly execution cannot be resumed")
            elif execution is not None:
                raise AssemblyAlreadyExecuted("Assembly execution already exists")

            sources = self._validate_plan_sources(paths, plan)
            if execution is None:
                final_version = self._next_final_version(paths, manifest)
                execution = {
                    "assembly_version": assembly_version,
                    "final_video_version": final_version,
                    "execution_id": self._id_factory(),
                    "status": "RUNNING",
                    "created_at": _now_iso(),
                    "started_at": _now_iso(),
                    "finished_at": None,
                    "source_shots": self._source_manifest(plan),
                }
                manifest.setdefault("executions", []).append(execution)
            else:
                final_version = _positive_int(execution.get("final_video_version"))
                if final_version is None:
                    raise ProjectDataCorrupt("Assembly execution version is invalid")
                execution.update(
                    status="RUNNING",
                    started_at=execution.get("started_at") or _now_iso(),
                    finished_at=None,
                    error=None,
                )
            self._save_manifest(paths, manifest)

            final_dir = paths.assembly_output_version_dir(final_version)
            final_video = paths.assembly_output_video_path(final_version)
            snapshot = self._source_manifest(plan)
            checkpoint.start_assembly(final_video, final_version, snapshot)

            result = self._read_completed_bundle(paths, final_version)
            if result is None:
                execution_id = str(execution["execution_id"])
                staging = paths.assembly_output_staging_dir(
                    final_version, execution_id
                )
                shutil.rmtree(staging, ignore_errors=True)
                staging.mkdir(parents=True, exist_ok=False)
                staged_video = paths.ensure_within_project(
                    staging / "final_video.mp4"
                )
                logger = TaskLogger(paths, task_id=execution_id)
                result = self._core_executor(
                    paths,
                    task_id=execution_id,
                    sources=sources,
                    output=staged_video,
                    task_logger=logger,
                )
                self._write_staging_bundle(
                    paths,
                    staging,
                    plan,
                    final_version,
                    result,
                )
                self._validate_staging_bundle(staging)
                if final_dir.exists():
                    raise ProjectDataCorrupt("Final Video version already exists")
                os.rename(staging, final_dir)

            checkpoint.complete_assembly(
                final_video,
                final_version,
                result.total_duration,
                snapshot,
            )
            self._complete_manifest(
                paths,
                manifest,
                execution,
                plan,
                final_version,
                result,
            )
            shutil.rmtree(
                paths.assembly_run_dir(str(execution["execution_id"])),
                ignore_errors=True,
            )
            return TaskResultReference(
                resource_type="ASSEMBLY_OUTPUT",
                resource_id=f"assembly_v{assembly_version:03d}",
                version=final_version,
            )
        except (AssemblyAlreadyExecuted, AssemblyNotResumable):
            raise_task_failure("ACTION_NOT_ALLOWED", "当前合片状态不允许执行此操作。")
        except Exception:
            if staging is not None:
                shutil.rmtree(staging, ignore_errors=True)
            if manifest is not None and execution is not None:
                self._mark_failed(paths, manifest, execution)
            if checkpoint is not None:
                try:
                    checkpoint.fail_assembly("Assembly execution failed")
                except Exception:
                    pass
            raise_task_failure(
                "ASSEMBLY_EXECUTION_FAILED",
                "合片执行失败，可在确认计划仍有效后继续。",
                retryable=True,
            )

    def _paths(
        self, project_id: str, *, ensure_directories: bool = False
    ) -> ProjectPaths:
        return create_project_paths(
            self._project_repository.resolve_project_dir(project_id),
            ensure_directories=ensure_directories,
        )

    @staticmethod
    def _load_manifest(paths: ProjectPaths) -> dict[str, Any]:
        if not paths.assembly_manifest_path().is_file():
            raise ProjectDataCorrupt("Assembly manifest is missing")
        payload = _read_json(
            paths.assembly_manifest_path(), label="assembly_manifest.json"
        )
        for key in ("plans", "assemblies", "executions"):
            value = payload.get(key, [])
            if not isinstance(value, list):
                raise ProjectDataCorrupt("Assembly manifest history is invalid")
            payload[key] = value
        return payload

    @staticmethod
    def _save_manifest(paths: ProjectPaths, manifest: dict[str, Any]) -> None:
        manifest["manifest_version"] = 1
        manifest["execution_schema_version"] = 1
        paths.save_json(paths.assembly_manifest_path(), manifest)

    @staticmethod
    def _find_execution(
        manifest: Mapping[str, Any], assembly_version: int
    ) -> dict[str, Any] | None:
        executions = manifest.get("executions", [])
        if not isinstance(executions, list):
            raise ProjectDataCorrupt("Assembly execution history is invalid")
        matches = [
            item
            for item in executions
            if isinstance(item, dict)
            and _positive_int(item.get("assembly_version")) == assembly_version
        ]
        if len(matches) > 1:
            raise ProjectDataCorrupt("Assembly plan has duplicate executions")
        return matches[0] if matches else None

    @staticmethod
    def _source_manifest(plan: AssemblyPlan) -> list[dict[str, Any]]:
        return [
            {
                "shot_id": shot.shot_id,
                "order": shot.order,
                "approved_video_version": shot.approved_video_version,
                "prompt_version": shot.prompt_version,
                "duration": shot.duration,
                "resolution": shot.resolution,
            }
            for shot in plan.shots
        ]

    @staticmethod
    def _validate_plan_sources(
        paths: ProjectPaths, plan: AssemblyPlan
    ) -> list[dict[str, Any]]:
        sources: list[dict[str, Any]] = []
        for shot in plan.shots:
            try:
                bundle = validate_bundle(
                    paths, shot.shot_id, shot.approved_video_version
                )
            except (ShotStorageError, OSError, ValueError) as exc:
                raise ProjectDataCorrupt("Assembly source bundle is incomplete") from exc
            prompt_version = _positive_int(
                _mapping(bundle.get("prompt")).get("prompt_version")
            )
            generation = _mapping(bundle.get("generation"))
            if (
                prompt_version != shot.prompt_version
                or abs(float(generation.get("duration") or 0) - shot.duration) > 0.001
                or str(generation.get("resolution") or "").strip()
                != shot.resolution
            ):
                raise ProjectDataCorrupt("Assembly source bundle changed")
            video = paths.shot_version_video_path(
                shot.shot_id, shot.approved_video_version
            )
            if not video.is_file() or video.stat().st_size <= 0:
                raise ProjectDataCorrupt("Assembly source video is unavailable")
            sources.append({"shot_id": shot.shot_id, "path": video})
        return sources

    @staticmethod
    def _next_final_version(paths: ProjectPaths, manifest: Mapping[str, Any]) -> int:
        versions = [
            _positive_int(_mapping(item).get("final_video_version")) or 0
            for item in manifest.get("executions", [])
        ]
        versions.extend(
            _positive_int(_mapping(item).get("assembly_version")) or 0
            for item in manifest.get("assemblies", [])
        )
        if paths.assembly_outputs_dir.is_dir():
            versions.extend(
                int(item.name[1:])
                for item in paths.assembly_outputs_dir.glob("v[0-9][0-9][0-9]")
                if item.is_dir() and item.name[1:].isdigit()
            )
        return max(versions or [0]) + 1

    @staticmethod
    def _write_staging_bundle(
        paths: ProjectPaths,
        staging: Path,
        plan: AssemblyPlan,
        final_version: int,
        result: AssemblyExecutionResult,
    ) -> None:
        source_manifest = {
            "schema_version": 1,
            "assembly_version": plan.assembly_version,
            "final_video_version": final_version,
            "shots": AssemblyExecutionService._source_manifest(plan),
        }
        assembly = {
            "schema_version": 1,
            "assembly_version": plan.assembly_version,
            "final_video_version": final_version,
            "created_at": _now_iso(),
            "total_duration": result.total_duration,
            "silent_video": True,
            "mode": result.mode,
            "width": result.width,
            "height": result.height,
            "fps": result.fps,
            "codec": result.codec,
            "pixel_format": result.pixel_format,
        }
        paths.save_json(staging / "assembly.json", assembly)
        paths.save_json(staging / "source_manifest.json", source_manifest)
        paths.save_json(
            staging / "review.json",
            {
                "schema_version": 1,
                "final_video_version": final_version,
                "status": "NOT_STARTED",
            },
        )

    @staticmethod
    def _validate_staging_bundle(staging: Path) -> None:
        video = staging / "final_video.mp4"
        if not video.is_file() or video.stat().st_size <= 0:
            raise AssemblyError("Assembly output is empty")
        for filename in ("assembly.json", "source_manifest.json", "review.json"):
            _read_json(staging / filename, label=filename)

    @staticmethod
    def _read_completed_bundle(
        paths: ProjectPaths, final_version: int
    ) -> AssemblyExecutionResult | None:
        directory = paths.assembly_output_version_dir(final_version)
        if not directory.exists():
            return None
        AssemblyExecutionService._validate_staging_bundle(directory)
        metadata = _read_json(directory / "assembly.json", label="assembly.json")
        try:
            return AssemblyExecutionResult(
                mode=str(metadata["mode"]),
                total_duration=float(metadata["total_duration"]),
                width=int(metadata["width"]),
                height=int(metadata["height"]),
                fps=float(metadata["fps"]),
                codec=str(metadata["codec"]),
                pixel_format=str(metadata["pixel_format"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ProjectDataCorrupt("Assembly output metadata is invalid") from exc

    @staticmethod
    def _complete_manifest(
        paths: ProjectPaths,
        manifest: dict[str, Any],
        execution: dict[str, Any],
        plan: AssemblyPlan,
        final_version: int,
        result: AssemblyExecutionResult,
    ) -> None:
        record = {
            "assembly_version": final_version,
            "plan_version": plan.assembly_version,
            "created_at": _now_iso(),
            "total_duration": result.total_duration,
            "silent_video": True,
            "mode": result.mode,
            "shots": AssemblyExecutionService._source_manifest(plan),
        }
        assemblies = manifest.setdefault("assemblies", [])
        assemblies[:] = [
            item
            for item in assemblies
            if _positive_int(_mapping(item).get("assembly_version")) != final_version
        ]
        assemblies.append(record)
        assemblies.sort(
            key=lambda item: _positive_int(_mapping(item).get("assembly_version")) or 0
        )
        execution.update(status="COMPLETED", finished_at=_now_iso(), error=None)
        manifest.update(
            latest_assembly_version=final_version,
            latest_plan_version=plan.assembly_version,
        )
        AssemblyExecutionService._save_manifest(paths, manifest)

    @staticmethod
    def _mark_failed(
        paths: ProjectPaths,
        manifest: dict[str, Any],
        execution: dict[str, Any],
    ) -> None:
        execution.update(
            status="FAILED",
            finished_at=_now_iso(),
            error={
                "code": "ASSEMBLY_EXECUTION_FAILED",
                "message": "合片执行失败，可在确认计划仍有效后继续。",
            },
        )
        try:
            AssemblyExecutionService._save_manifest(paths, manifest)
        except (ProjectDirectoryError, OSError):
            pass
