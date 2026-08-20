"""Synchronous, provider-free Assembly planning over approved Shot bundles."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from project_manager import ProjectDirectoryError, ProjectPaths, create_project_paths
from shot_storage import ShotStorageError, validate_bundle
from web_backend.locking import ProjectLockBusy, ProjectLockManager
from web_backend.models.assembly_planning import (
    AssemblyPlan,
    AssemblyPlanningStatus,
    AssemblyPlanShot,
    AssemblyReadiness,
    AssemblyReadinessIssue,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
    ProjectRepositoryError,
)
from web_backend.repositories.shot_repository import ShotRepository, normalize_shot_id


class AssemblyPlanNotReady(ProjectRepositoryError):
    """The current approved Shot collection cannot form a safe plan."""


class AssemblyPlanningBusy(ProjectRepositoryError):
    """The project changed under another local Web write."""


class AssemblyPlanNotFound(ProjectRepositoryError):
    """The requested immutable Assembly plan does not exist."""


class AssemblyPlanOutdated(ProjectRepositoryError):
    """The requested plan no longer matches approved Shot state."""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _positive_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or str(value).strip() not in {str(number), f"{float(number)}"}:
        return None
    return number


def _positive_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _read_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProjectDataCorrupt(f"{label} is unreadable") from exc
    if not isinstance(payload, dict):
        raise ProjectDataCorrupt(f"{label} is not an object")
    return payload


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class AssemblyPlanningService:
    """Create immutable plan snapshots without executing an Assembly job."""

    def __init__(
        self,
        project_repository: ProjectRepository,
        shot_repository: ShotRepository,
        lock_manager: ProjectLockManager,
    ) -> None:
        self.project_repository = project_repository
        self.shot_repository = shot_repository
        self.lock_manager = lock_manager

    def readiness(self, project_id: str) -> AssemblyReadiness:
        return self._readiness(project_id)

    def get_plan(self, project_id: str, assembly_version: int) -> AssemblyPlan:
        """Return one exact plan and derive its status from current Shot state."""

        readiness = self._readiness(project_id)
        project_dir = self.project_repository.resolve_project_dir(project_id)
        paths = create_project_paths(project_dir, ensure_directories=False)
        manifest = self._load_manifest(paths)
        record = next(
            (
                _mapping(item)
                for item in manifest["plans"]
                if _positive_int(_mapping(item).get("assembly_version"))
                == assembly_version
            ),
            None,
        )
        if record is None:
            raise AssemblyPlanNotFound("Assembly plan does not exist")
        stored = self._plan_from_record(record, AssemblyPlanningStatus.READY)
        status = (
            AssemblyPlanningStatus.READY
            if readiness.ready
            and self._snapshot_key(stored.shots)
            == self._snapshot_key(readiness.shots)
            else AssemblyPlanningStatus.OUTDATED
        )
        return stored.model_copy(
            update={"project_id": readiness.project_id, "status": status}
        )

    def require_current_plan(
        self, project_id: str, assembly_version: int
    ) -> AssemblyPlan:
        plan = self.get_plan(project_id, assembly_version)
        if plan.status is not AssemblyPlanningStatus.READY:
            raise AssemblyPlanOutdated("Assembly plan is outdated")
        return plan

    def create_plan(self, project_id: str) -> AssemblyPlan:
        try:
            with self.lock_manager.project_write(project_id):
                readiness = self._readiness(project_id)
                if not readiness.ready:
                    raise AssemblyPlanNotReady("Assembly is not ready")
                project_dir = self.project_repository.resolve_project_dir(project_id)
                paths = create_project_paths(project_dir, ensure_directories=False)
                manifest = self._load_manifest(paths)
                plans = manifest["plans"]
                current = self._current_plan(
                    readiness.project_id,
                    plans,
                    readiness.shots,
                    readiness.ready,
                )
                if current is not None and current.status == AssemblyPlanningStatus.READY:
                    return current
                version = self._next_version(manifest)
                record = {
                    "assembly_version": version,
                    "status": AssemblyPlanningStatus.READY.value,
                    "created_at": _now_iso(),
                    "project_id": readiness.project_id,
                    "total_duration": readiness.total_duration,
                    "shots": [shot.model_dump() for shot in readiness.shots],
                }
                plans.append(record)
                plans.sort(key=lambda item: int(_mapping(item).get("assembly_version") or 0))
                manifest.update(
                    {
                        "manifest_version": 1,
                        "planning_schema_version": 1,
                        "latest_plan_version": version,
                        "plans": plans,
                    }
                )
                paths.videos_dir.mkdir(parents=True, exist_ok=True)
                paths.save_json(paths.assembly_manifest_path(), manifest)
                return self._plan_from_record(record, AssemblyPlanningStatus.READY)
        except ProjectLockBusy as exc:
            raise AssemblyPlanningBusy("Assembly planning lock is busy") from exc
        except ProjectDirectoryError as exc:
            raise ProjectDataCorrupt("Assembly plan could not be persisted") from exc

    def _readiness(self, project_id: str) -> AssemblyReadiness:
        collection = self.shot_repository.list_shots(project_id)
        project_dir = self.project_repository.resolve_project_dir(project_id)
        paths = create_project_paths(project_dir, ensure_directories=False)
        project_data = _read_object(paths.project_state_path(), label="project.json")
        checkpoints = _mapping(_mapping(project_data.get("video_generation")).get("shots"))
        shots: list[AssemblyPlanShot] = []
        issues: list[AssemblyReadinessIssue] = []
        seen_orders: set[int] = set()
        for summary in collection.shots:
            _, shot_number = normalize_shot_id(summary.shot_id)
            if summary.order in seen_orders:
                issues.append(
                    AssemblyReadinessIssue(
                        shot_id=shot_number,
                        order=summary.order,
                        reason="INVALID_ORDER",
                    )
                )
                continue
            seen_orders.add(summary.order)
            checkpoint = _mapping(
                checkpoints.get(str(shot_number), checkpoints.get(shot_number))
            )
            approved_version = _positive_int(checkpoint.get("approved_video_version"))
            if approved_version is None:
                issues.append(
                    AssemblyReadinessIssue(
                        shot_id=shot_number,
                        order=summary.order,
                        reason=summary.status if summary.status != "APPROVED" else "APPROVED_VERSION_MISSING",
                    )
                )
                continue
            manifest_path = paths.shot_manifest_path(shot_number)
            if not manifest_path.is_file():
                issues.append(
                    AssemblyReadinessIssue(
                        shot_id=shot_number,
                        order=summary.order,
                        reason="BUNDLE_INCOMPLETE",
                    )
                )
                continue
            shot_manifest = _read_object(manifest_path, label="shot.json")
            if _positive_int(shot_manifest.get("approved_version")) != approved_version:
                issues.append(
                    AssemblyReadinessIssue(
                        shot_id=shot_number,
                        order=summary.order,
                        reason="APPROVED_INDEX_MISMATCH",
                    )
                )
                continue
            video_path = paths.shot_version_video_path(shot_number, approved_version)
            if not video_path.is_file() or video_path.stat().st_size <= 0:
                issues.append(
                    AssemblyReadinessIssue(
                        shot_id=shot_number,
                        order=summary.order,
                        reason="VIDEO_MISSING",
                    )
                )
                continue
            try:
                bundle = validate_bundle(paths, shot_number, approved_version)
            except (ShotStorageError, OSError, ValueError):
                issues.append(
                    AssemblyReadinessIssue(
                        shot_id=shot_number,
                        order=summary.order,
                        reason="BUNDLE_INCOMPLETE",
                    )
                )
                continue
            prompt_version = _positive_int(_mapping(bundle.get("prompt")).get("prompt_version"))
            generation = _mapping(bundle.get("generation"))
            duration = _positive_number(generation.get("duration"))
            resolution = str(generation.get("resolution") or "").strip()
            review_status = str(_mapping(bundle.get("review")).get("review_result") or "").upper()
            if prompt_version is None or duration is None or not resolution or review_status != "APPROVED":
                issues.append(
                    AssemblyReadinessIssue(
                        shot_id=shot_number,
                        order=summary.order,
                        reason="BUNDLE_INCOMPLETE",
                    )
                )
                continue
            shots.append(
                AssemblyPlanShot(
                    shot_id=shot_number,
                    order=summary.order,
                    approved_video_version=approved_version,
                    prompt_version=prompt_version,
                    duration=duration,
                    resolution=resolution,
                )
            )
        if not collection.shots:
            issues.append(AssemblyReadinessIssue(reason="NO_SHOTS"))
        ready = bool(collection.shots) and not issues and len(shots) == len(collection.shots)
        shots.sort(key=lambda shot: shot.order)
        manifest = self._load_manifest(paths)
        current_plan = self._current_plan(
            collection.project_id,
            manifest["plans"],
            shots,
            ready,
        )
        total_duration = sum(shot.duration for shot in shots) if ready else None
        return AssemblyReadiness(
            project_id=collection.project_id,
            status=(AssemblyPlanningStatus.READY if ready else AssemblyPlanningStatus.NOT_READY),
            ready=ready,
            shot_count=len(collection.shots),
            ready_count=len(shots),
            total_duration=total_duration,
            shots=shots,
            issues=issues,
            current_plan=current_plan,
        )

    @staticmethod
    def _load_manifest(paths: ProjectPaths) -> dict[str, Any]:
        path = paths.assembly_manifest_path()
        if not path.is_file():
            return {"manifest_version": 1, "assemblies": [], "plans": []}
        payload = _read_object(path, label="assembly_manifest.json")
        assemblies = payload.get("assemblies", [])
        plans = payload.get("plans", [])
        if not isinstance(assemblies, list) or not isinstance(plans, list):
            raise ProjectDataCorrupt("Assembly manifest history is invalid")
        payload["assemblies"] = assemblies
        payload["plans"] = plans
        return payload

    @classmethod
    def _current_plan(
        cls,
        project_id: str,
        plans: list[Any],
        current_shots: list[AssemblyPlanShot],
        ready: bool,
    ) -> AssemblyPlan | None:
        if not plans:
            return None
        records = sorted(
            (_mapping(item) for item in plans),
            key=lambda item: _positive_int(item.get("assembly_version")) or 0,
        )
        latest = records[-1]
        stored = cls._plan_from_record(latest, AssemblyPlanningStatus.READY)
        status = (
            AssemblyPlanningStatus.READY
            if ready and cls._snapshot_key(stored.shots) == cls._snapshot_key(current_shots)
            else AssemblyPlanningStatus.OUTDATED
        )
        return stored.model_copy(update={"project_id": project_id, "status": status})

    @staticmethod
    def _snapshot_key(shots: list[AssemblyPlanShot]) -> tuple[tuple[Any, ...], ...]:
        return tuple(
            (
                shot.shot_id,
                shot.order,
                shot.approved_video_version,
                shot.prompt_version,
                shot.duration,
                shot.resolution,
            )
            for shot in shots
        )

    @staticmethod
    def _plan_from_record(
        record: Mapping[str, Any], status: AssemblyPlanningStatus
    ) -> AssemblyPlan:
        try:
            return AssemblyPlan.model_validate({**record, "status": status.value})
        except Exception as exc:
            raise ProjectDataCorrupt("Assembly plan is invalid") from exc

    @staticmethod
    def _next_version(manifest: Mapping[str, Any]) -> int:
        versions = [
            _positive_int(_mapping(item).get("assembly_version")) or 0
            for key in ("assemblies", "plans")
            for item in (manifest.get(key) if isinstance(manifest.get(key), list) else [])
        ]
        return max(versions or [0]) + 1
