"""Durable project checkpoint, resume routing, and non-destructive stage reset."""

from __future__ import annotations

import json
import copy
import shutil
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import uuid4

from project_manager import ProjectDirectoryError, ProjectPaths
from post_production import (
    PostProductionPipeline,
    ProjectCompletionStatus,
    new_post_production_state,
    normalize_post_production_state,
)
from voice_assets import default_voice_config, normalize_voice_config
from visual_input import none_visual_input, normalize_visual_input, visual_input_snapshot
from video_provider import ProviderTask, ProviderTaskStatus


class ProjectStage(StrEnum):
    CREATIVE = "CREATIVE"
    CREATIVE_REVIEW = "CREATIVE_REVIEW"
    STORYBOARD = "STORYBOARD"
    STORYBOARD_REVIEW = "STORYBOARD_REVIEW"
    VIDEO_PROMPT = "VIDEO_PROMPT"
    PROMPT_REVIEW = "PROMPT_REVIEW"
    VIDEO_GENERATION = "VIDEO_GENERATION"
    COMPLETED = "COMPLETED"


class StageStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    WAITING_REVIEW = "WAITING_REVIEW"
    APPROVED = "APPROVED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


class ShotStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    GENERATING = "GENERATING"
    WAITING_REVIEW = "WAITING_REVIEW"
    APPROVED = "APPROVED"
    FAILED = "FAILED"


class CandidateStatus(StrEnum):
    NONE = "NONE"
    EDITING = "EDITING"
    GENERATING = "GENERATING"
    WAITING_REVIEW = "WAITING_REVIEW"
    FAILED = "FAILED"


class AssemblyStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


STAGE_ORDER = tuple(ProjectStage)
REVIEW_STAGES = {
    ProjectStage.CREATIVE_REVIEW,
    ProjectStage.STORYBOARD_REVIEW,
    ProjectStage.PROMPT_REVIEW,
}
RUNNING_STAGES = {
    ProjectStage.CREATIVE,
    ProjectStage.STORYBOARD,
    ProjectStage.VIDEO_PROMPT,
    ProjectStage.VIDEO_GENERATION,
}


class ProjectStateError(RuntimeError):
    """Raised when project.json is missing, corrupt, or inconsistent."""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _new_assembly_entry() -> dict[str, Any]:
    return {
        "status": AssemblyStatus.NOT_STARTED.value,
        "needs_update": False,
        "changed_shot_id": None,
        "old_approved_video_version": None,
        "new_approved_video_version": None,
        "final_video_path": None,
        "final_video_version": None,
        "assembled_at": None,
        "total_duration": None,
        "shot_versions": [],
        "last_error": None,
        "started_at": None,
        "pending_final_video_path": None,
        "pending_final_video_version": None,
        "pending_shot_versions": [],
        "changes": [],
    }


class ProjectCheckpoint:
    def __init__(self, project: ProjectPaths, data: dict[str, Any]) -> None:
        self.project = project
        self.path = project.project_state_path()
        self.data = data

    @classmethod
    def exists(cls, project: ProjectPaths) -> bool:
        return project.project_state_path().is_file()

    @classmethod
    def create(
        cls,
        project: ProjectPaths,
        project_name: str,
        request_data: dict[str, Any],
    ) -> "ProjectCheckpoint":
        timestamp = now_iso()
        data: dict[str, Any] = {
            "project_schema_version": 2,
            "project_id": uuid4().hex,
            "project_name": project_name,
            "created_at": timestamp,
            "updated_at": timestamp,
            "status": StageStatus.NOT_STARTED.value,
            "completion_status": ProjectCompletionStatus.NOT_STARTED.value,
            "current_stage": ProjectStage.CREATIVE.value,
            "cancel_stage": "",
            "cancelled_at": None,
            "last_error": None,
            "request": request_data,
            "stages": {
                stage.value: {
                    "status": StageStatus.NOT_STARTED.value,
                    "started_at": None,
                    "completed_at": None,
                    "approved_at": None,
                    "updated_at": timestamp,
                    "attempts": 0,
                }
                for stage in STAGE_ORDER
            },
            "video_generation": {
                "shot_review_schema_version": 2,
                "completed_shots": [],
                "shots": {},
            },
            "assembly": _new_assembly_entry(),
            "voice_config": default_voice_config(),
            "post_production": new_post_production_state(),
            "revision_history": [],
        }
        checkpoint = cls(project, data)
        checkpoint.save()
        return checkpoint

    @classmethod
    def load(cls, project: ProjectPaths) -> "ProjectCheckpoint":
        path = project.project_state_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise ProjectStateError("当前项目不存在 project.json。") from exc
        except (OSError, json.JSONDecodeError) as exc:
            raise ProjectStateError(f"project.json 无法读取：{exc}") from exc
        checkpoint = cls(project, data)
        changed = False
        request_data = data.get("request")
        if isinstance(request_data, dict) and "user_notes" not in request_data:
            request_data["user_notes"] = ""
            changed = True
        voice_config, voice_changed = normalize_voice_config(data.get("voice_config"))
        data["voice_config"] = voice_config
        changed = voice_changed or changed
        post_production, post_changed = normalize_post_production_state(
            data.get("post_production")
        )
        data["post_production"] = post_production
        changed = post_changed or changed
        if "completion_status" not in data:
            data["completion_status"] = checkpoint._infer_completion_status()
            changed = True
        checkpoint._validate()
        changed = checkpoint._migrate_legacy_shot_reviews() or changed
        changed = checkpoint._hydrate_resume_from_bundles() or changed
        if changed:
            checkpoint.save()
        PostProductionPipeline(checkpoint).sync_from_existing_assets()
        checkpoint._sync_completion_status_from_assets()
        return checkpoint

    def _infer_completion_status(self) -> str:
        assembly = self.data.get("assembly") or {}
        if (
            str(assembly.get("status")) == AssemblyStatus.COMPLETED.value
            and not bool(assembly.get("needs_update"))
            and assembly.get("final_video_path")
        ):
            return ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
        stages = self.data.get("stages") or {}
        completed = stages.get(ProjectStage.COMPLETED.value) or {}
        if str(completed.get("status")) == StageStatus.COMPLETED.value:
            return ProjectCompletionStatus.VIDEO_GENERATION_COMPLETED.value
        return ProjectCompletionStatus.NOT_STARTED.value

    def _sync_completion_status_from_assets(self) -> None:
        post_status = str(
            (self.data.get("post_production") or {}).get("status") or ""
        )
        if post_status == ProjectCompletionStatus.FINAL_COMPLETED.value:
            desired = ProjectCompletionStatus.FINAL_COMPLETED.value
        elif post_status == ProjectCompletionStatus.POST_PRODUCTION.value:
            desired = ProjectCompletionStatus.POST_PRODUCTION.value
        elif post_status == ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value:
            desired = ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
        else:
            desired = self._infer_completion_status()
        if self.data.get("completion_status") != desired:
            self.data["completion_status"] = desired
            self.save()

    def _hydrate_resume_from_bundles(self) -> bool:
        """Recover provider ids from immutable Bundle metadata after interruption."""
        from shot_storage import ShotStorageError, read_bundle_json

        changed = False
        shots = self.data.setdefault("video_generation", {}).setdefault("shots", {})
        for raw_id, entry in shots.items():
            shot_id = int(raw_id)
            candidate = entry.get("candidate") or {}
            candidates = []
            current = entry.get("current_generation_version")
            if str(entry.get("status")) == ShotStatus.GENERATING.value and current:
                candidates.append((int(current), entry))
            candidate_version = candidate.get("video_version")
            if (
                str(candidate.get("status")) == CandidateStatus.GENERATING.value
                and candidate_version
            ):
                candidates.append((int(candidate_version), candidate))
            for version, target in candidates:
                try:
                    generation = read_bundle_json(
                        self.project, shot_id, version, "generation.json"
                    )
                except ShotStorageError:
                    continue
                for field in (
                    "provider_task_id",
                    "file_id",
                    "provider",
                    "generation_mode",
                    "provider_model",
                    "provider_api_version",
                    "selection_mode",
                    "credential_env_name",
                    "submitted_at",
                    "file_ready_at",
                ):
                    if not target.get(field) and generation.get(field):
                        target[field] = generation[field]
                        changed = True
                mirror = self._generation_for_version(entry, version)
                if mirror is not None:
                    for field in (
                        "provider_task_id",
                        "file_id",
                        "provider",
                        "generation_mode",
                        "provider_model",
                        "provider_api_version",
                        "selection_mode",
                        "credential_env_name",
                    ):
                        if not mirror.get(field) and generation.get(field):
                            mirror[field] = generation[field]
                            changed = True
        return changed

    def _validate(self) -> None:
        required = {
            "project_id",
            "project_name",
            "created_at",
            "updated_at",
            "status",
            "current_stage",
            "stages",
            "request",
        }
        if not required.issubset(self.data):
            raise ProjectStateError("project.json 缺少必要字段。")
        if self.data.get("project_schema_version") != 2:
            raise ProjectStateError("当前项目尚未迁移到 Shot Storage Schema v2。")
        missing_stages = {stage.value for stage in STAGE_ORDER} - set(self.data["stages"])
        if missing_stages:
            raise ProjectStateError(
                f"project.json 缺少阶段：{', '.join(sorted(missing_stages))}"
            )
        ProjectStage(self.data["current_stage"])

    @property
    def current_stage(self) -> ProjectStage:
        return ProjectStage(self.data["current_stage"])

    @property
    def status(self) -> str:
        return str(self.data["status"])

    def stage_status(self, stage: ProjectStage) -> StageStatus:
        return StageStatus(self.data["stages"][stage.value]["status"])

    def save(self) -> None:
        self.data["project_schema_version"] = 2
        self.data.pop("schema_version", None)
        self.data["updated_at"] = now_iso()
        self._sync_shot_storage()
        self.project.save_json(self.path, self.data)

    def save_checkpoint_metadata(self) -> None:
        """Persist project.json without rewriting immutable Shot Bundle snapshots."""

        self.data["project_schema_version"] = 2
        self.data.pop("schema_version", None)
        self.data["updated_at"] = now_iso()
        self.project.save_json(self.path, self.data)

    def _save_generation_state(
        self,
        shot_id: int,
        video_version: int | None = None,
        *,
        review_result: str | None = None,
    ) -> None:
        """Persist one mutable generation attempt without rewriting old Bundles."""

        from shot_storage import (
            sync_shot_manifest_from_checkpoint,
            write_generation_snapshot,
            write_prompt_snapshot,
            write_review_snapshot,
            write_safety_snapshot,
        )

        entry = self.shot_checkpoint(shot_id)
        self.data["project_schema_version"] = 2
        self.data.pop("schema_version", None)
        self.data["updated_at"] = now_iso()
        sync_shot_manifest_from_checkpoint(self.project, shot_id, entry)
        if video_version is not None:
            version = int(video_version)
            generation = self._generation_for_version(entry, version)
            if generation is not None:
                prompt_snapshot = generation.get("prompt_snapshot")
                prompt_path = self.project.shot_version_prompt_path(shot_id, version)
                safety_path = self.project.shot_version_safety_path(shot_id, version)
                if isinstance(prompt_snapshot, dict) and not prompt_path.is_file():
                    write_prompt_snapshot(self.project, shot_id, version, prompt_snapshot)
                if isinstance(prompt_snapshot, dict) and not safety_path.is_file():
                    write_safety_snapshot(
                        self.project,
                        shot_id,
                        version,
                        input_prompt=str(prompt_snapshot.get("prompt") or ""),
                        safety_payload={
                            "is_safe": prompt_snapshot.get("safety_is_safe", True),
                            "risk_notes": prompt_snapshot.get("safety_risk_notes") or [],
                            "reviewed_video_prompt": prompt_snapshot.get("safety_prompt")
                            or prompt_snapshot.get("prompt")
                            or "",
                            "checked_at": prompt_snapshot.get("safety_checked_at"),
                        },
                    )
                candidate = self.candidate_checkpoint(shot_id)
                generation_payload = dict(generation)
                generation_payload.update(
                    {
                        "generation_count": entry.get("generation_count", 0),
                        "generation_phase": (
                            candidate.get("generation_phase")
                            if generation.get("candidate")
                            else entry.get("generation_phase")
                        ),
                        "submission_unknown": (
                            candidate.get("submission_unknown")
                            if generation.get("candidate")
                            else entry.get("submission_unknown")
                        ),
                        "generation_intent": generation.get("generation_intent"),
                    }
                )
                write_generation_snapshot(
                    self.project, shot_id, version, generation_payload
                )
                review_path = self.project.shot_version_review_path(shot_id, version)
                if review_result is not None or not review_path.is_file():
                    write_review_snapshot(
                        self.project,
                        shot_id,
                        version,
                        review_result=review_result
                        or str(generation.get("review_result") or "NOT_STARTED"),
                    )
        self.project.save_json(self.path, self.data)

    def _sync_shot_storage(self) -> None:
        """Synchronize Schema v2 shot.json and existing Bundle metadata."""
        from shot_storage import (
            sync_shot_manifest_from_checkpoint,
            write_generation_snapshot,
            write_prompt_snapshot,
            write_review_snapshot,
            write_safety_snapshot,
        )

        shots = self.data.setdefault("video_generation", {}).setdefault("shots", {})
        for raw_id, entry in shots.items():
            shot_id = int(raw_id)
            sync_shot_manifest_from_checkpoint(self.project, shot_id, entry)
            for generation in entry.setdefault("generation_versions", []):
                raw_version = generation.get("video_version")
                if raw_version is None:
                    continue
                version = int(raw_version)
                if not self.project.shot_version_dir(shot_id, version).exists():
                    continue
                prompt_snapshot = generation.get("prompt_snapshot")
                if isinstance(prompt_snapshot, dict):
                    write_prompt_snapshot(
                        self.project, shot_id, version, prompt_snapshot
                    )
                    write_safety_snapshot(
                        self.project,
                        shot_id,
                        version,
                        input_prompt=str(prompt_snapshot.get("prompt") or ""),
                        safety_payload={
                            "is_safe": prompt_snapshot.get("safety_is_safe", True),
                            "risk_notes": prompt_snapshot.get("safety_risk_notes") or [],
                            "reviewed_video_prompt": prompt_snapshot.get("safety_prompt")
                            or prompt_snapshot.get("prompt")
                            or "",
                            "checked_at": prompt_snapshot.get("safety_checked_at"),
                        },
                    )
                generation_payload = dict(generation)
                generation_payload["generation_count"] = entry.get(
                    "generation_count", 0
                )
                write_generation_snapshot(
                    self.project, shot_id, version, generation_payload
                )
                write_review_snapshot(
                    self.project,
                    shot_id,
                    version,
                    review_result=str(
                        generation.get("review_result")
                        or generation.get("status")
                        or "NOT_STARTED"
                    ),
                    review_time=(
                        generation.get("reviewed_at")
                        or generation.get("approved_at")
                        or generation.get("completed_at")
                        or generation.get("updated_at")
                    ),
                )
    def update_stage(self, stage: ProjectStage, status: StageStatus) -> None:
        timestamp = now_iso()
        entry = self.data["stages"][stage.value]
        entry["status"] = status.value
        entry["updated_at"] = timestamp
        if status == StageStatus.RUNNING:
            entry["started_at"] = timestamp
            entry["attempts"] = int(entry.get("attempts", 0)) + 1
        if status == StageStatus.COMPLETED:
            entry["completed_at"] = timestamp
        if status == StageStatus.APPROVED:
            entry["approved_at"] = timestamp
        self.data["current_stage"] = stage.value
        self.data["status"] = status.value
        if status != StageStatus.CANCELLED:
            self.data["cancel_stage"] = ""
            self.data["cancelled_at"] = None
            self.data.pop("cancel_shot_id", None)
        self.save()

    def advance_to(self, stage: ProjectStage, status: StageStatus) -> None:
        self.update_stage(stage, status)

    def cancel(self, stage: ProjectStage, shot_id: int | None = None) -> None:
        timestamp = now_iso()
        self.data["stages"][stage.value]["status"] = StageStatus.CANCELLED.value
        self.data["stages"][stage.value]["updated_at"] = timestamp
        self.data["current_stage"] = stage.value
        self.data["status"] = StageStatus.CANCELLED.value
        self.data["cancel_stage"] = stage.value
        self.data["cancelled_at"] = timestamp
        if shot_id is not None:
            self.data["cancel_shot_id"] = int(shot_id)
            shot_entry = self.shot_checkpoint(shot_id)
            shot_entry["cancelled_at"] = timestamp
            shot_entry["updated_at"] = timestamp
        self.save()

    def fail(self, error: BaseException | str) -> None:
        stage = self.current_stage
        self.data["stages"][stage.value]["status"] = StageStatus.FAILED.value
        self.data["status"] = StageStatus.FAILED.value
        self.data["last_error"] = {
            "stage": stage.value,
            "type": type(error).__name__ if isinstance(error, BaseException) else "Error",
            "message": str(error),
            "timestamp": now_iso(),
        }
        self.save()

    def mark_video_generation_completed(self) -> None:
        self.update_stage(ProjectStage.COMPLETED, StageStatus.COMPLETED)
        self.data["status"] = StageStatus.COMPLETED.value
        self.data["completion_status"] = (
            ProjectCompletionStatus.VIDEO_GENERATION_COMPLETED.value
        )
        self.save()

    def mark_completed(self) -> None:
        """Legacy alias: COMPLETED means the video-generation workflow only."""
        self.mark_video_generation_completed()

    def completed_steps(self) -> list[str]:
        completed = []
        for stage in STAGE_ORDER:
            if self.stage_status(stage) in {StageStatus.COMPLETED, StageStatus.APPROVED}:
                completed.append(stage.value)
        return completed

    def next_stage(self) -> ProjectStage:
        for stage in STAGE_ORDER:
            status = self.stage_status(stage)
            if stage in REVIEW_STAGES:
                if status != StageStatus.APPROVED:
                    return stage
            elif status != StageStatus.COMPLETED:
                return stage
        return ProjectStage.COMPLETED

    def interrupted_stage(self) -> ProjectStage | None:
        stage = self.current_stage
        if stage in RUNNING_STAGES and self.stage_status(stage) == StageStatus.RUNNING:
            return stage
        return None

    def completed_shots(self) -> set[int]:
        return {
            int(shot_id)
            for shot_id in self.data.setdefault("video_generation", {}).setdefault(
                "completed_shots", []
            )
        }

    def _relative_project_path(self, path: Path) -> str:
        return path.resolve().relative_to(self.project.project_path.resolve()).as_posix()

    def _new_shot_entry(self, shot_id: int) -> dict[str, Any]:
        timestamp = now_iso()
        return {
            "shot_id": int(shot_id),
            "review_schema_version": 2,
            "status": ShotStatus.NOT_STARTED.value,
            "provider_task_id": None,
            "file_id": None,
            "provider": None,
            "generation_mode": None,
            "provider_model": None,
            "provider_api_version": None,
            "selection_mode": None,
            "credential_env_name": None,
            "last_provider_route": None,
            "submitted_at": None,
            "file_ready_at": None,
            "completed_at": None,
            "approved_at": None,
            "cancelled_at": None,
            "updated_at": timestamp,
            "generation_count": 0,
            "active_prompt_version": None,
            "active_video_version": None,
            "approved_prompt_version": None,
            "approved_video_version": None,
            "video_path": None,
            "prompt_version_count": 0,
            "prompt_versions": [],
            "last_error": None,
            "generation_versions": [],
            "generation_attempt_pending": False,
            "pending_video_version": None,
            "current_generation_version": None,
            "generation_phase": "NOT_STARTED",
            "generation_intent": None,
            "submission_unknown": False,
            "candidate": self._new_candidate_entry(),
            "candidate_history": [],
            "visual_input": none_visual_input(),
            "visual_input_selected": False,
        }

    @staticmethod
    def _new_candidate_entry() -> dict[str, Any]:
        return {
            "status": CandidateStatus.NONE.value,
            "base_approved_prompt_version": None,
            "base_approved_video_version": None,
            "prompt_version": None,
            "video_version": None,
            "video_path": None,
            "provider_task_id": None,
            "file_id": None,
            "provider": None,
            "generation_mode": None,
            "provider_model": None,
            "provider_api_version": None,
            "selection_mode": None,
            "credential_env_name": None,
            "last_provider_route": None,
            "editing_path": None,
            "editing_original_prompt": None,
            "created_at": None,
            "updated_at": None,
            "submitted_at": None,
            "file_ready_at": None,
            "completed_at": None,
            "generation_count": 0,
            "generation_attempt_pending": False,
            "generation_phase": "NOT_STARTED",
            "generation_intent": None,
            "submission_unknown": False,
            "source": None,
            "last_error": None,
            "visual_input": none_visual_input(),
        }

    def _fill_shot_defaults(self, shot_id: int, entry: dict[str, Any]) -> bool:
        changed = False
        defaults = self._new_shot_entry(shot_id)
        for key, value in defaults.items():
            if key not in entry:
                entry[key] = value
                changed = True
        if entry.get("review_schema_version") != 2:
            entry["review_schema_version"] = 2
            changed = True
        if entry.get("shot_id") != int(shot_id):
            entry["shot_id"] = int(shot_id)
            changed = True
        if str(entry.get("status")) == ShotStatus.APPROVED.value:
            if entry.get("approved_prompt_version") is None and entry.get(
                "active_prompt_version"
            ) is not None:
                entry["approved_prompt_version"] = entry.get(
                    "active_prompt_version"
                )
                changed = True
            if entry.get("approved_video_version") is None and entry.get(
                "active_video_version"
            ) is not None:
                entry["approved_video_version"] = entry.get("active_video_version")
                changed = True
        candidate = entry.setdefault("candidate", self._new_candidate_entry())
        for key, value in self._new_candidate_entry().items():
            if key not in candidate:
                candidate[key] = value
                changed = True
        normalized_shot_visual = normalize_visual_input(entry.get("visual_input"))
        if entry.get("visual_input") != normalized_shot_visual:
            entry["visual_input"] = normalized_shot_visual
            changed = True
        normalized_candidate_visual = normalize_visual_input(candidate.get("visual_input"))
        if candidate.get("visual_input") != normalized_candidate_visual:
            candidate["visual_input"] = normalized_candidate_visual
            changed = True
        if "candidate_history" not in entry:
            entry["candidate_history"] = []
            changed = True
        for generation in entry.setdefault("generation_versions", []):
            generation_defaults = {
                "created_at": generation.get("submitted_at"),
                "video_path": generation.get("archived_path")
                or generation.get("candidate_path"),
                "prompt_source": None,
                "review_result": generation.get("status"),
                "is_active": generation.get("video_version")
                == entry.get("active_video_version"),
                "is_approved": generation.get("video_version")
                == entry.get("approved_video_version"),
                "visual_input": none_visual_input(),
                "provider": None,
                "generation_mode": None,
                "provider_model": None,
                "provider_api_version": None,
                "selection_mode": None,
                "credential_env_name": None,
            }
            for key, value in generation_defaults.items():
                if key not in generation:
                    generation[key] = value
                    changed = True
            normalized_generation_visual = normalize_visual_input(
                generation.get("visual_input")
            )
            if generation.get("visual_input") != normalized_generation_visual:
                generation["visual_input"] = normalized_generation_visual
                changed = True
            raw_version = generation.get("video_version")
            if raw_version is not None:
                canonical = self._relative_project_path(
                    self.video_path_for_version(shot_id, int(raw_version))
                )
                if generation.get("video_path") != canonical:
                    generation["video_path"] = canonical
                    changed = True
        active = entry.get("active_video_version")
        if active is not None:
            canonical = self._relative_project_path(
                self.video_path_for_version(shot_id, int(active))
            )
            if entry.get("video_path") != canonical:
                entry["video_path"] = canonical
                changed = True
        candidate = entry.get("candidate") or {}
        candidate_version = candidate.get("video_version")
        if candidate_version is not None:
            canonical = self._relative_project_path(
                self.video_path_for_version(shot_id, int(candidate_version))
            )
            if candidate.get("video_path") != canonical:
                candidate["video_path"] = canonical
                changed = True
        return changed

    def _migrate_legacy_shot_reviews(self) -> bool:
        """Fill non-storage review defaults inside an already migrated v2 project."""
        video_generation = self.data.setdefault(
            "video_generation", {"completed_shots": [], "shots": {}}
        )
        changed = False
        for raw_id, entry in video_generation.setdefault("shots", {}).items():
            changed = self._fill_shot_defaults(int(raw_id), entry) or changed
        if video_generation.get("shot_review_schema_version") != 2:
            video_generation["shot_review_schema_version"] = 2
            changed = True
        assembly = self.data.setdefault("assembly", _new_assembly_entry())
        for key, value in _new_assembly_entry().items():
            if key not in assembly:
                assembly[key] = value
                changed = True
        return changed

    def ensure_shots(self, shot_ids: list[int]) -> None:
        video_generation = self.data.setdefault("video_generation", {})
        video_generation["shot_review_schema_version"] = 2
        shots = video_generation.setdefault("shots", {})
        for shot_id in shot_ids:
            entry = shots.setdefault(str(int(shot_id)), self._new_shot_entry(shot_id))
            self._fill_shot_defaults(shot_id, entry)
            entry["updated_at"] = now_iso()
        self.save()

    def video_path_for_version(self, shot_id: int, version: int) -> Path:
        return self.project.shot_version_video_path(int(shot_id), int(version))

    def active_video_path(self, shot_id: int) -> Path | None:
        version = self.shot_checkpoint(shot_id).get("active_video_version")
        return self.video_path_for_version(shot_id, int(version)) if version else None

    def approved_video_path(self, shot_id: int) -> Path | None:
        version = self.shot_checkpoint(shot_id).get("approved_video_version")
        return self.video_path_for_version(shot_id, int(version)) if version else None

    def candidate_video_path(self, shot_id: int) -> Path | None:
        version = self.candidate_checkpoint(shot_id).get("video_version")
        return self.video_path_for_version(shot_id, int(version)) if version else None

    def shot_status(self, shot_id: int) -> ShotStatus:
        entry = self.shot_checkpoint(shot_id)
        raw = str(entry.get("status", ShotStatus.NOT_STARTED.value))
        if raw == StageStatus.RUNNING.value:
            raw = ShotStatus.GENERATING.value
        if raw == StageStatus.COMPLETED.value:
            raw = ShotStatus.WAITING_REVIEW.value
        return ShotStatus(raw)

    def set_active_prompt_version(self, shot_id: int, version: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        entry["active_prompt_version"] = int(version)
        entry["updated_at"] = now_iso()
        self.save()

    def shot_visual_input(self, shot_id: int) -> dict[str, Any]:
        return visual_input_snapshot(self.shot_checkpoint(shot_id).get("visual_input"))

    def set_shot_visual_input(
        self, shot_id: int, visual_input: dict[str, Any], *, selected: bool = True
    ) -> None:
        entry = self.shot_checkpoint(shot_id)
        entry["visual_input"] = visual_input_snapshot(visual_input)
        entry["visual_input_selected"] = bool(selected)
        entry["updated_at"] = now_iso()
        self.save()

    def generation_visual_input(
        self, shot_id: int, video_version: int | None
    ) -> dict[str, Any]:
        if video_version is None:
            return self.shot_visual_input(shot_id)
        entry = self.shot_checkpoint(shot_id)
        generation = self._generation_for_version(entry, int(video_version))
        if generation is not None:
            return visual_input_snapshot(generation.get("visual_input"))
        try:
            from shot_storage import read_bundle_json

            payload = read_bundle_json(
                self.project, shot_id, int(video_version), "generation.json"
            )
            return visual_input_snapshot(payload.get("visual_input"))
        except Exception:
            return none_visual_input()

    def generation_provider_metadata(
        self, shot_id: int, video_version: int | None
    ) -> dict[str, str] | None:
        if video_version is None:
            return None
        entry = self.shot_checkpoint(shot_id)
        generation = self._generation_for_version(entry, int(video_version))
        payload: dict[str, Any] = generation or {}
        fields = (
            "provider",
            "generation_mode",
            "provider_model",
            "provider_api_version",
            "selection_mode",
            "credential_env_name",
        )
        if not any(payload.get(field) for field in fields):
            try:
                from shot_storage import read_bundle_json

                payload = read_bundle_json(
                    self.project, shot_id, int(video_version), "generation.json"
                )
            except Exception:
                return None
        result = {
            field: str(payload[field])
            for field in fields
            if payload.get(field) is not None
        }
        return result or None

    def generation_provider_task(
        self, shot_id: int, video_version: int | None
    ) -> ProviderTask | None:
        if video_version is None:
            return None
        entry = self.shot_checkpoint(shot_id)
        generation = self._generation_for_version(entry, int(video_version))
        payload: dict[str, Any] = dict(generation or {})
        identity_fields = ("provider", "provider_model", "provider_api_version")
        if (
            (not payload.get("provider_task_id") and not payload.get("file_id"))
            or not all(payload.get(field) for field in identity_fields)
        ):
            try:
                from shot_storage import read_bundle_json

                bundle = read_bundle_json(
                    self.project, shot_id, int(video_version), "generation.json"
                )
                for key, value in bundle.items():
                    if not payload.get(key) and value is not None:
                        payload[key] = value
            except Exception:
                pass
        task_id = payload.get("provider_task_id")
        file_id = payload.get("file_id")
        if not task_id and not file_id:
            return None
        return ProviderTask(
            provider=payload.get("provider"),
            model=payload.get("provider_model"),
            api_version=payload.get("provider_api_version"),
            generation_mode=payload.get("generation_mode"),
            provider_task_id=str(task_id) if task_id else None,
            provider_file_id=str(file_id) if file_id else None,
            status=(
                ProviderTaskStatus.COMPLETED
                if file_id
                else ProviderTaskStatus.SUBMITTED
            ),
            selection_mode=payload.get("selection_mode"),
            credential_env_name=payload.get("credential_env_name"),
        )

    def approved_visual_input(self, shot_id: int) -> dict[str, Any]:
        version = self.shot_checkpoint(shot_id).get("approved_video_version")
        return self.generation_visual_input(shot_id, int(version)) if version else none_visual_input()

    def set_candidate_visual_input(
        self, shot_id: int, visual_input: dict[str, Any]
    ) -> None:
        candidate = self.candidate_checkpoint(shot_id)
        candidate["visual_input"] = visual_input_snapshot(visual_input)
        candidate["updated_at"] = now_iso()
        self.save()

    def prompt_versions(self, shot_id: int) -> list[dict[str, Any]]:
        return self.shot_checkpoint(shot_id).setdefault("prompt_versions", [])

    def prompt_version(self, shot_id: int, version: int) -> dict[str, Any] | None:
        for payload in reversed(self.prompt_versions(shot_id)):
            if int(payload.get("version") or 0) == int(version):
                return payload
        return None

    def save_prompt_version(self, shot_id: int, payload: dict[str, Any]) -> None:
        self._apply_prompt_version(shot_id, payload)
        self.save()

    def save_prompt_version_metadata(
        self, shot_id: int, payload: dict[str, Any]
    ) -> None:
        """Persist Prompt pointer/review metadata without rewriting version Bundles."""

        self._apply_prompt_version(shot_id, payload)
        self.save_checkpoint_metadata()

    def save_prompt_versions(
        self, versions_by_shot: list[tuple[int, dict[str, Any]]]
    ) -> None:
        """Persist a complete Prompt-set version transition in one checkpoint write."""

        for shot_id, payload in versions_by_shot:
            self._apply_prompt_version(shot_id, payload)
        self.save()

    def _apply_prompt_version(
        self, shot_id: int, payload: dict[str, Any]
    ) -> None:
        version = int(payload["version"])
        versions = self.prompt_versions(shot_id)
        versions[:] = [
            item for item in versions if int(item.get("version") or 0) != version
        ]
        versions.append(copy.deepcopy(payload))
        versions.sort(key=lambda item: int(item.get("version") or 0))
        entry = self.shot_checkpoint(shot_id)
        entry["prompt_version_count"] = max(
            int(entry.get("prompt_version_count") or 0), version
        )
        entry["active_prompt_version"] = version
        entry["updated_at"] = now_iso()

    def prepare_shot_generation(
        self,
        shot_id: int,
        *,
        generation_intent: str = "INITIAL_GENERATION",
    ) -> None:
        entry = self.shot_checkpoint(shot_id)
        versions = [
            int(item.get("video_version") or 0)
            for item in entry.setdefault("generation_versions", [])
        ]
        next_version = max(
            int(entry.get("active_video_version") or 0),
            int(entry.get("approved_video_version") or 0),
            int(entry.get("generation_count") or 0),
            *versions,
        ) + 1
        entry.update(
            {
                "status": ShotStatus.GENERATING.value,
                "provider_task_id": None,
                "file_id": None,
                "provider": None,
                "generation_mode": None,
                "provider_model": None,
                "provider_api_version": None,
                "selection_mode": None,
                "credential_env_name": None,
                "submitted_at": None,
                "file_ready_at": None,
                "completed_at": None,
                "approved_at": None,
                "generation_attempt_pending": True,
                "pending_video_version": next_version,
                "current_generation_version": None,
                "generation_phase": "PREPARING",
                "generation_intent": str(generation_intent),
                "submission_unknown": False,
                "last_error": None,
                "updated_at": now_iso(),
            }
        )
        self.data["video_generation"]["completed_shots"] = sorted(
            self.completed_shots() - {int(shot_id)}
        )
        self._save_generation_state(shot_id)

    def mark_shot_preflight(
        self, shot_id: int, metadata: dict[str, Any]
    ) -> None:
        entry = self.shot_checkpoint(shot_id)
        safe = {
            key: metadata.get(key)
            for key in (
                "provider",
                "provider_model",
                "provider_api_version",
                "generation_mode",
                "selection_mode",
                "credential_env_name",
            )
        }
        entry.update(safe)
        entry["last_provider_route"] = copy.deepcopy(safe)
        entry["updated_at"] = now_iso()
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def mark_shot_submission_started(
        self,
        shot_id: int,
        metadata: dict[str, Any],
        *,
        duration: int,
        resolution: str,
        visual_input: dict[str, Any],
    ) -> None:
        """Allocate one immutable Bundle before the single billable submit call."""

        entry = self.shot_checkpoint(shot_id)
        if entry.get("generation_attempt_pending"):
            version = int(
                entry.get("pending_video_version")
                or int(entry.get("generation_count", 0)) + 1
            )
            entry["generation_count"] = int(entry.get("generation_count", 0)) + 1
            entry["current_generation_version"] = version
            self.project.shot_version_dir(shot_id, version).mkdir(
                parents=True, exist_ok=False
            )
            prompt_payload = self.prompt_version(
                shot_id, int(entry.get("active_prompt_version") or 0)
            )
            safe_metadata = {
                key: metadata.get(key)
                for key in (
                    "provider",
                    "provider_model",
                    "provider_api_version",
                    "generation_mode",
                    "selection_mode",
                    "credential_env_name",
                )
            }
            entry.setdefault("generation_versions", []).append(
                {
                    "video_version": version,
                    "prompt_version": entry.get("active_prompt_version"),
                    "status": "SUBMITTING",
                    "created_at": now_iso(),
                    "video_path": self._relative_project_path(
                        self.video_path_for_version(shot_id, version)
                    ),
                    "prompt_source": (
                        prompt_payload.get("source") if prompt_payload else None
                    ),
                    "prompt_snapshot": copy.deepcopy(prompt_payload),
                    "review_result": None,
                    "generation_intent": entry.get("generation_intent"),
                    "is_active": False,
                    "is_approved": False,
                    **safe_metadata,
                    "provider_task_id": None,
                    "file_id": None,
                    "submitted_at": None,
                    "file_ready_at": None,
                    "completed_at": None,
                    "duration": int(duration),
                    "resolution": str(resolution),
                    "visual_input": visual_input_snapshot(visual_input),
                    "updated_at": now_iso(),
                }
            )
            entry["generation_attempt_pending"] = False
            entry["pending_video_version"] = None
        entry["generation_phase"] = "SUBMITTING"
        entry["submission_unknown"] = False
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry,
            entry.get("current_generation_version"),
            status="SUBMITTING",
        )
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def mark_shot_completed(self, shot_id: int) -> None:
        self.mark_shot_ready_for_review(shot_id)

    def mark_shot_ready_for_review(self, shot_id: int) -> None:
        shots = self.completed_shots()
        shots.add(int(shot_id))
        self.data["video_generation"]["completed_shots"] = sorted(shots)
        entry = self.shot_checkpoint(shot_id)
        entry["status"] = ShotStatus.WAITING_REVIEW.value
        entry["generation_phase"] = "WAITING_REVIEW"
        entry["submission_unknown"] = False
        entry["completed_at"] = now_iso()
        current_version = entry.get("current_generation_version")
        if current_version is not None:
            entry["active_video_version"] = int(current_version)
            entry["video_path"] = self._relative_project_path(
                self.video_path_for_version(shot_id, int(current_version))
            )
        entry["generation_attempt_pending"] = False
        entry["pending_video_version"] = None
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry,
            current_version or entry.get("active_video_version"),
            status=ShotStatus.WAITING_REVIEW.value,
            completed_at=entry["completed_at"],
            video_path=entry.get("video_path"),
            review_result=ShotStatus.WAITING_REVIEW.value,
            is_active=True,
            is_approved=False,
        )
        entry["current_generation_version"] = None
        self._save_generation_state(
            shot_id,
            int(current_version) if current_version is not None else None,
            review_result=ShotStatus.WAITING_REVIEW.value,
        )

    def shot_checkpoint(self, shot_id: int) -> dict[str, Any]:
        shots = self.data.setdefault("video_generation", {}).setdefault("shots", {})
        entry = shots.setdefault(str(int(shot_id)), self._new_shot_entry(shot_id))
        self._fill_shot_defaults(int(shot_id), entry)
        return entry

    @staticmethod
    def _generation_for_version(
        entry: dict[str, Any], version: int | None
    ) -> dict[str, Any] | None:
        for generation in reversed(entry.setdefault("generation_versions", [])):
            if generation.get("video_version") == version:
                return generation
        return None

    def _update_generation(
        self, entry: dict[str, Any], version: int | None, **fields: Any
    ) -> None:
        generation = self._generation_for_version(entry, version)
        if generation is not None:
            generation.update(fields)
            generation["updated_at"] = now_iso()

    @staticmethod
    def _provider_task_values(
        task: ProviderTask | str,
        *,
        provider: str | None = None,
        generation_mode: str | None = None,
        provider_model: str | None = None,
        provider_api_version: str | None = None,
    ) -> dict[str, Any]:
        if isinstance(task, ProviderTask):
            return task.bundle_metadata()
        return {
            "provider": provider,
            "provider_model": provider_model,
            "provider_api_version": provider_api_version,
            "generation_mode": generation_mode,
            "provider_task_id": str(task),
            "file_id": None,
            "selection_mode": None,
            "credential_env_name": None,
        }

    def mark_shot_submitted(
        self,
        shot_id: int,
        provider_task_id: ProviderTask | str,
        *,
        provider: str | None = None,
        generation_mode: str | None = None,
        provider_model: str | None = None,
        provider_api_version: str | None = None,
    ) -> None:
        task_values = self._provider_task_values(
            provider_task_id,
            provider=provider,
            generation_mode=generation_mode,
            provider_model=provider_model,
            provider_api_version=provider_api_version,
        )
        task_id = task_values.get("provider_task_id")
        if not task_id:
            raise ProjectStateError("ProviderTask 缺少 provider_task_id。")
        entry = self.shot_checkpoint(shot_id)
        if entry.get("generation_attempt_pending"):
            version = int(
                entry.get("pending_video_version")
                or int(entry.get("generation_count", 0)) + 1
            )
            entry["generation_count"] = int(entry.get("generation_count", 0)) + 1
            entry["current_generation_version"] = version
            self.project.shot_version_dir(shot_id, version).mkdir(
                parents=True, exist_ok=False
            )
            bundle_video = self.video_path_for_version(shot_id, version)
            prompt_payload = self.prompt_version(
                shot_id, int(entry.get("active_prompt_version") or 0)
            )
            entry.setdefault("generation_versions", []).append(
                {
                    "video_version": version,
                    "prompt_version": entry.get("active_prompt_version"),
                    "status": ShotStatus.GENERATING.value,
                    "created_at": now_iso(),
                    "video_path": self._relative_project_path(bundle_video),
                    "prompt_source": (
                        prompt_payload.get("source") if prompt_payload else None
                    ),
                    "prompt_snapshot": copy.deepcopy(prompt_payload),
                    "review_result": None,
                    "generation_intent": entry.get("generation_intent"),
                    "is_active": False,
                    "is_approved": False,
                    **task_values,
                    "submitted_at": now_iso(),
                    "file_ready_at": None,
                    "completed_at": None,
                    "visual_input": visual_input_snapshot(entry.get("visual_input")),
                    "updated_at": now_iso(),
                }
            )
            entry["generation_attempt_pending"] = False
            entry["pending_video_version"] = None
        entry["status"] = ShotStatus.GENERATING.value
        entry["generation_phase"] = "PROVIDER_RUNNING"
        entry["submission_unknown"] = False
        entry.update(task_values)
        entry["submitted_at"] = now_iso()
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry,
            entry.get("current_generation_version"),
            status=ShotStatus.GENERATING.value,
            **task_values,
            submitted_at=entry["submitted_at"],
        )
        entry["video_path"] = self._relative_project_path(
            self.video_path_for_version(
                shot_id, int(entry.get("current_generation_version"))
            )
        )
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def mark_shot_file_ready(self, shot_id: int, file_id: str) -> None:
        entry = self.shot_checkpoint(shot_id)
        entry["status"] = ShotStatus.GENERATING.value
        entry["file_id"] = str(file_id)
        entry["file_ready_at"] = now_iso()
        entry["generation_phase"] = "READY_TO_DOWNLOAD"
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry,
            entry.get("current_generation_version"),
            status=ShotStatus.GENERATING.value,
            file_id=str(file_id),
            file_ready_at=entry["file_ready_at"],
        )
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def mark_shot_task_updated(
        self, shot_id: int, task: ProviderTask | str
    ) -> None:
        """Persist provider progress; string input is retained for legacy test doubles."""
        if isinstance(task, str):
            self.mark_shot_file_ready(shot_id, task)
            return
        entry = self.shot_checkpoint(shot_id)
        values = task.bundle_metadata()
        entry.update(values)
        if task.provider_file_id:
            entry["file_ready_at"] = now_iso()
            values["file_ready_at"] = entry["file_ready_at"]
            entry["generation_phase"] = "READY_TO_DOWNLOAD"
        else:
            entry["generation_phase"] = "PROVIDER_RUNNING"
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry, entry.get("current_generation_version"), **values
        )
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def mark_shot_downloading(self, shot_id: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        entry["generation_phase"] = "DOWNLOADING"
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry, entry.get("current_generation_version"), status="DOWNLOADING"
        )
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def mark_shot_local_finalizing(self, shot_id: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        entry["generation_phase"] = "LOCAL_FINALIZING"
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry,
            entry.get("current_generation_version"),
            status="LOCAL_FINALIZING",
        )
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def mark_shot_submission_unknown(self, shot_id: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        entry["status"] = ShotStatus.FAILED.value
        entry["generation_phase"] = "SUBMISSION_UNKNOWN"
        entry["submission_unknown"] = True
        entry["last_error"] = {
            "type": "SubmissionUnknown",
            "message": "Remote submission outcome is unknown.",
            "timestamp": now_iso(),
        }
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry,
            entry.get("current_generation_version"),
            status="SUBMISSION_UNKNOWN",
            submission_unknown=True,
            error=entry["last_error"],
        )
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def approve_shot(self, shot_id: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        timestamp = now_iso()
        entry["status"] = ShotStatus.APPROVED.value
        entry["generation_phase"] = ShotStatus.APPROVED.value
        entry["submission_unknown"] = False
        entry["approved_at"] = timestamp
        entry["approved_prompt_version"] = entry.get("active_prompt_version")
        entry["approved_video_version"] = entry.get("active_video_version")
        entry["updated_at"] = timestamp
        for generation in entry.setdefault("generation_versions", []):
            generation["is_active"] = generation.get("video_version") == entry.get(
                "active_video_version"
            )
            generation["is_approved"] = generation.get(
                "video_version"
            ) == entry.get("approved_video_version")
        self._update_generation(
            entry,
            entry.get("active_video_version"),
            status=ShotStatus.APPROVED.value,
            approved_at=timestamp,
            review_result=ShotStatus.APPROVED.value,
            review_user_action="approve",
        )
        shots = self.completed_shots()
        shots.add(int(shot_id))
        self.data["video_generation"]["completed_shots"] = sorted(shots)
        self.data["project_schema_version"] = 2
        self.data.pop("schema_version", None)
        self.data["updated_at"] = timestamp

        # Approval is a narrow metadata transaction. Do not rewrite immutable
        # prompt/safety/generation snapshots or touch video.mp4.
        from shot_storage import (
            sync_shot_manifest_from_checkpoint,
            write_review_snapshot,
        )

        sync_shot_manifest_from_checkpoint(self.project, shot_id, entry)
        write_review_snapshot(
            self.project,
            shot_id,
            int(entry["approved_video_version"]),
            review_result=ShotStatus.APPROVED.value,
            user_action="approve",
            review_time=timestamp,
        )
        self.project.save_json(self.path, self.data)

    def mark_shot_failed(self, shot_id: int, error: BaseException | str) -> None:
        entry = self.shot_checkpoint(shot_id)
        entry["status"] = ShotStatus.FAILED.value
        if entry.get("generation_phase") != "SUBMISSION_UNKNOWN":
            entry["generation_phase"] = "FAILED"
        entry["last_error"] = {
            "type": type(error).__name__ if isinstance(error, BaseException) else "Error",
            "message": str(error),
            "timestamp": now_iso(),
        }
        entry["updated_at"] = now_iso()
        self._update_generation(
            entry,
            entry.get("current_generation_version"),
            status=ShotStatus.FAILED.value,
            error=entry["last_error"],
        )
        self._save_generation_state(
            shot_id, entry.get("current_generation_version")
        )

    def mark_shot_video_archived(self, shot_id: int, version: int, path: Path) -> None:
        entry = self.shot_checkpoint(shot_id)
        relative_path = self._relative_project_path(
            self.project.ensure_within_project(path)
        )
        self._update_generation(
            entry,
            int(version),
            archived_path=relative_path,
            video_path=relative_path,
            archived_at=now_iso(),
            is_active=False,
        )
        entry["updated_at"] = now_iso()
        self.save()

    def select_waiting_review_video_version(
        self,
        shot_id: int,
        video_version: int,
        prompt_version: int | None,
        previous_video_version: int,
        previous_archive_path: Path,
        *,
        provider_task_id: str | None = None,
        file_id: str | None = None,
    ) -> None:
        """Persist a local history switch without counting a new generation."""
        if self.shot_status(shot_id) != ShotStatus.WAITING_REVIEW:
            raise ProjectStateError("只有 WAITING_REVIEW Shot 可以直接切换历史视频。")
        entry = self.shot_checkpoint(shot_id)
        if self._generation_for_version(entry, int(video_version)) is None:
            raise ProjectStateError(
                f"Shot {shot_id:02d} 不存在 Video v{int(video_version)} 元数据。"
            )
        del previous_video_version, previous_archive_path
        target_path = self.video_path_for_version(shot_id, int(video_version))
        if not target_path.is_file() or target_path.stat().st_size <= 0:
            raise ProjectStateError(f"历史 Video v{video_version} 文件不存在或为空。")
        timestamp = now_iso()
        selected_generation: dict[str, Any] | None = None
        for generation in entry.setdefault("generation_versions", []):
            version = generation.get("video_version")
            generation["is_active"] = version == int(video_version)
            generation["is_approved"] = version == entry.get("approved_video_version")
            if version == int(video_version):
                selected_generation = generation
                generation.update(
                    {
                        "status": ShotStatus.WAITING_REVIEW.value,
                        "review_result": ShotStatus.WAITING_REVIEW.value,
                        "video_path": self._relative_project_path(target_path),
                        "provider_task_id": provider_task_id,
                        "file_id": file_id,
                        "selected_at": timestamp,
                    }
                )
            generation["updated_at"] = timestamp
        entry.update(
            {
                "status": ShotStatus.WAITING_REVIEW.value,
                "active_video_version": int(video_version),
                "active_prompt_version": (
                    int(prompt_version)
                    if prompt_version is not None
                    else entry.get("active_prompt_version")
                ),
                "provider_task_id": provider_task_id,
                "file_id": file_id,
                "provider": (selected_generation or {}).get("provider"),
                "generation_mode": (selected_generation or {}).get("generation_mode"),
                "provider_model": (selected_generation or {}).get("provider_model"),
                "provider_api_version": (selected_generation or {}).get("provider_api_version"),
                "selection_mode": (selected_generation or {}).get("selection_mode"),
                "credential_env_name": (selected_generation or {}).get("credential_env_name"),
                "video_path": self._relative_project_path(target_path),
                "updated_at": timestamp,
            }
        )
        self.save()

    def create_historical_video_candidate(
        self,
        shot_id: int,
        video_version: int,
        prompt_version: int,
        candidate_path: Path,
        *,
        provider_task_id: str | None = None,
        file_id: str | None = None,
    ) -> None:
        """Expose an existing generated video as a reviewable Candidate."""
        if self.shot_status(shot_id) != ShotStatus.APPROVED:
            raise ProjectStateError("只有 APPROVED Shot 可以选择历史 Candidate。")
        if self.candidate_status(shot_id) != CandidateStatus.NONE:
            raise ProjectStateError("当前 Shot 已存在尚未处理的 Candidate。")
        entry = self.shot_checkpoint(shot_id)
        generation = self._generation_for_version(entry, int(video_version))
        if generation is None:
            raise ProjectStateError(
                f"Shot {shot_id:02d} 不存在 Video v{int(video_version)} 元数据。"
            )
        del candidate_path
        timestamp = now_iso()
        bundle_path = self.video_path_for_version(shot_id, int(video_version))
        if not bundle_path.is_file() or bundle_path.stat().st_size <= 0:
            raise ProjectStateError(f"历史 Video v{video_version} 文件不存在或为空。")
        candidate = self.candidate_checkpoint(shot_id)
        candidate.update(
            {
                **self._new_candidate_entry(),
                "status": CandidateStatus.WAITING_REVIEW.value,
                "base_approved_prompt_version": entry.get(
                    "approved_prompt_version"
                ),
                "base_approved_video_version": entry.get("approved_video_version"),
                "prompt_version": int(prompt_version),
                "video_version": int(video_version),
                "video_path": self._relative_project_path(bundle_path),
                "provider_task_id": provider_task_id,
                "file_id": file_id,
                "provider": generation.get("provider"),
                "generation_mode": generation.get("generation_mode"),
                "provider_model": generation.get("provider_model"),
                "provider_api_version": generation.get("provider_api_version"),
                "selection_mode": generation.get("selection_mode"),
                "credential_env_name": generation.get("credential_env_name"),
                "created_at": timestamp,
                "completed_at": timestamp,
                "updated_at": timestamp,
                "generation_count": 0,
                "generation_attempt_pending": False,
                "source": "historical_video",
                "visual_input": self.generation_visual_input(
                    shot_id, int(video_version)
                ),
            }
        )
        generation["historical_candidate_selected_at"] = timestamp
        generation["updated_at"] = timestamp
        entry["updated_at"] = timestamp
        from shot_storage import sync_shot_manifest_from_checkpoint

        sync_shot_manifest_from_checkpoint(self.project, shot_id, entry)
        self.save_checkpoint_metadata()

    def all_shots_approved(self, shot_ids: list[int]) -> bool:
        return all(self.shot_status(shot_id) == ShotStatus.APPROVED for shot_id in shot_ids)

    def candidate_checkpoint(self, shot_id: int) -> dict[str, Any]:
        entry = self.shot_checkpoint(shot_id)
        candidate = entry.setdefault("candidate", self._new_candidate_entry())
        for key, value in self._new_candidate_entry().items():
            candidate.setdefault(key, value)
        return candidate

    def candidate_status(self, shot_id: int) -> CandidateStatus:
        return CandidateStatus(
            str(self.candidate_checkpoint(shot_id).get("status", CandidateStatus.NONE))
        )

    def begin_candidate_editing(self, shot_id: int, editing_path: Path | None) -> None:
        if self.shot_status(shot_id) != ShotStatus.APPROVED:
            raise ProjectStateError("只有 APPROVED Shot 可以创建 Candidate。")
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        if self.candidate_status(shot_id) == CandidateStatus.NONE:
            candidate.update(self._new_candidate_entry())
            candidate["base_approved_prompt_version"] = entry.get(
                "approved_prompt_version"
            )
            candidate["base_approved_video_version"] = entry.get(
                "approved_video_version"
            )
            candidate["created_at"] = now_iso()
            candidate["visual_input"] = self.approved_visual_input(shot_id)
        candidate["status"] = CandidateStatus.EDITING.value
        if editing_path is not None:
            candidate["editing_path"] = self._relative_project_path(
                self.project.ensure_within_project(editing_path)
            )
        candidate["updated_at"] = now_iso()
        entry["updated_at"] = now_iso()
        self.save()

    def set_candidate_prompt(self, shot_id: int, prompt_version: int) -> None:
        candidate = self.candidate_checkpoint(shot_id)
        candidate["prompt_version"] = int(prompt_version)
        candidate["editing_path"] = None
        candidate["editing_original_prompt"] = None
        candidate["updated_at"] = now_iso()
        self.save()

    def next_candidate_video_version(self, shot_id: int) -> int:
        entry = self.shot_checkpoint(shot_id)
        versions = [
            int(entry.get("active_video_version") or 0),
            int(entry.get("approved_video_version") or 0),
            int(self.candidate_checkpoint(shot_id).get("video_version") or 0),
        ]
        versions.extend(
            int(item.get("video_version") or 0)
            for item in entry.setdefault("generation_versions", [])
        )
        return max(versions) + 1

    def prepare_candidate_generation(
        self,
        shot_id: int,
        *,
        generation_intent: str = "CANDIDATE_GENERATION",
    ) -> int:
        if self.shot_status(shot_id) != ShotStatus.APPROVED:
            raise ProjectStateError("Candidate 生成不能改变非 APPROVED Shot。")
        candidate = self.candidate_checkpoint(shot_id)
        if candidate.get("prompt_version") is None:
            raise ProjectStateError("Candidate 尚未创建 Prompt version。")
        version = self.next_candidate_video_version(shot_id)
        candidate.update(
            {
                "status": CandidateStatus.GENERATING.value,
                "source": "generated_video",
                "video_version": version,
                "video_path": self._relative_project_path(
                    self.video_path_for_version(shot_id, version)
                ),
                "provider_task_id": None,
                "file_id": None,
                "provider": None,
                "generation_mode": None,
                "provider_model": None,
                "provider_api_version": None,
                "selection_mode": None,
                "credential_env_name": None,
                "submitted_at": None,
                "file_ready_at": None,
                "completed_at": None,
                "generation_attempt_pending": True,
                "generation_phase": "PREPARING",
                "generation_intent": str(generation_intent),
                "submission_unknown": False,
                "last_error": None,
                "updated_at": now_iso(),
            }
        )
        self._save_generation_state(shot_id)
        return version

    def mark_candidate_submission_started(
        self,
        shot_id: int,
        metadata: dict[str, Any],
        *,
        duration: int,
        resolution: str,
        visual_input: dict[str, Any],
    ) -> None:
        """Allocate the immutable Candidate Bundle before the billable submit."""

        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        if candidate.get("generation_attempt_pending"):
            version = int(candidate.get("video_version") or 0)
            if version <= 0:
                raise ProjectStateError("Candidate 缺少待生成 Video version。")
            entry["generation_count"] = int(entry.get("generation_count", 0)) + 1
            candidate["generation_count"] = int(
                candidate.get("generation_count", 0)
            ) + 1
            self.project.shot_version_dir(shot_id, version).mkdir(
                parents=True, exist_ok=False
            )
            prompt_payload = self.prompt_version(
                shot_id, int(candidate.get("prompt_version") or 0)
            )
            safe_metadata = {
                key: metadata.get(key)
                for key in (
                    "provider",
                    "provider_model",
                    "provider_api_version",
                    "generation_mode",
                    "selection_mode",
                    "credential_env_name",
                )
            }
            entry.setdefault("generation_versions", []).append(
                {
                    "video_version": version,
                    "prompt_version": candidate.get("prompt_version"),
                    "status": "SUBMITTING",
                    "candidate": True,
                    "generation_intent": candidate.get("generation_intent"),
                    "created_at": now_iso(),
                    "video_path": candidate.get("video_path"),
                    "prompt_source": (
                        prompt_payload.get("source") if prompt_payload else None
                    ),
                    "prompt_snapshot": copy.deepcopy(prompt_payload),
                    "review_result": None,
                    "is_active": False,
                    "is_approved": False,
                    **safe_metadata,
                    "provider_task_id": None,
                    "file_id": None,
                    "submitted_at": None,
                    "file_ready_at": None,
                    "completed_at": None,
                    "duration": int(duration),
                    "resolution": str(resolution),
                    "visual_input": visual_input_snapshot(visual_input),
                    "updated_at": now_iso(),
                }
            )
            candidate["generation_attempt_pending"] = False
        candidate["generation_phase"] = "SUBMITTING"
        candidate["submission_unknown"] = False
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry,
            candidate.get("video_version"),
            status="SUBMITTING",
        )
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def mark_candidate_preflight(
        self, shot_id: int, metadata: dict[str, Any]
    ) -> None:
        candidate = self.candidate_checkpoint(shot_id)
        safe = {
            key: metadata.get(key)
            for key in (
                "provider",
                "provider_model",
                "provider_api_version",
                "generation_mode",
                "selection_mode",
                "credential_env_name",
            )
        }
        candidate.update(safe)
        candidate["last_provider_route"] = copy.deepcopy(safe)
        candidate["updated_at"] = now_iso()
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def defer_candidate_generation(self, shot_id: int) -> None:
        """Return an unsubmitted Candidate to editable state without creating a version."""
        candidate = self.candidate_checkpoint(shot_id)
        if candidate.get("provider_task_id") or candidate.get("file_id"):
            raise ProjectStateError("已提交的 Candidate 不能作为未提交任务撤回。")
        candidate.update(
            {
                "status": CandidateStatus.EDITING.value,
                "video_version": None,
                "video_path": None,
                "generation_attempt_pending": False,
                "provider": None,
                "generation_mode": None,
                "provider_model": None,
                "provider_api_version": None,
                "selection_mode": None,
                "credential_env_name": None,
                "last_error": None,
                "updated_at": now_iso(),
            }
        )
        self.save()

    def mark_candidate_submitted(
        self,
        shot_id: int,
        provider_task_id: ProviderTask | str,
        *,
        provider: str | None = None,
        generation_mode: str | None = None,
        provider_model: str | None = None,
        provider_api_version: str | None = None,
    ) -> None:
        task_values = self._provider_task_values(
            provider_task_id,
            provider=provider,
            generation_mode=generation_mode,
            provider_model=provider_model,
            provider_api_version=provider_api_version,
        )
        task_id = task_values.get("provider_task_id")
        if not task_id:
            raise ProjectStateError("Candidate ProviderTask 缺少 provider_task_id。")
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        if candidate.get("generation_attempt_pending"):
            entry["generation_count"] = int(entry.get("generation_count", 0)) + 1
            candidate["generation_count"] = int(
                candidate.get("generation_count", 0)
            ) + 1
            self.project.shot_version_dir(
                shot_id, int(candidate["video_version"])
            ).mkdir(parents=True, exist_ok=False)
            prompt_payload = self.prompt_version(
                shot_id, int(candidate.get("prompt_version") or 0)
            )
            entry.setdefault("generation_versions", []).append(
                {
                    "video_version": candidate.get("video_version"),
                    "prompt_version": candidate.get("prompt_version"),
                    "status": CandidateStatus.GENERATING.value,
                    "candidate": True,
                    "created_at": now_iso(),
                    "video_path": candidate.get("video_path"),
                    "prompt_source": (
                        prompt_payload.get("source") if prompt_payload else None
                    ),
                    "prompt_snapshot": copy.deepcopy(prompt_payload),
                    "review_result": None,
                    "generation_intent": candidate.get("generation_intent"),
                    "is_active": False,
                    "is_approved": False,
                    **task_values,
                    "submitted_at": now_iso(),
                    "file_ready_at": None,
                    "completed_at": None,
                    "visual_input": visual_input_snapshot(
                        candidate.get("visual_input")
                    ),
                    "updated_at": now_iso(),
                }
            )
            candidate["generation_attempt_pending"] = False
        candidate.update(task_values)
        candidate["generation_phase"] = "PROVIDER_RUNNING"
        candidate["submission_unknown"] = False
        candidate["submitted_at"] = now_iso()
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry,
            candidate.get("video_version"),
            **task_values,
            submitted_at=candidate["submitted_at"],
        )
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def mark_candidate_file_ready(self, shot_id: int, file_id: str) -> None:
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        candidate["file_id"] = str(file_id)
        candidate["file_ready_at"] = now_iso()
        candidate["generation_phase"] = "READY_TO_DOWNLOAD"
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry,
            candidate.get("video_version"),
            file_id=str(file_id),
            file_ready_at=candidate["file_ready_at"],
        )
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def mark_candidate_task_updated(
        self, shot_id: int, task: ProviderTask | str
    ) -> None:
        """Persist candidate provider progress; accepts legacy file-id test doubles."""
        if isinstance(task, str):
            self.mark_candidate_file_ready(shot_id, task)
            return
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        values = task.bundle_metadata()
        candidate.update(values)
        if task.provider_file_id:
            candidate["file_ready_at"] = now_iso()
            values["file_ready_at"] = candidate["file_ready_at"]
            candidate["generation_phase"] = "READY_TO_DOWNLOAD"
        else:
            candidate["generation_phase"] = "PROVIDER_RUNNING"
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry, candidate.get("video_version"), **values
        )
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def mark_candidate_downloading(self, shot_id: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        candidate["generation_phase"] = "DOWNLOADING"
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry, candidate.get("video_version"), status="DOWNLOADING"
        )
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def mark_candidate_local_finalizing(self, shot_id: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        candidate["generation_phase"] = "LOCAL_FINALIZING"
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry, candidate.get("video_version"), status="LOCAL_FINALIZING"
        )
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def mark_candidate_submission_unknown(self, shot_id: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        candidate["status"] = CandidateStatus.FAILED.value
        candidate["generation_phase"] = "SUBMISSION_UNKNOWN"
        candidate["submission_unknown"] = True
        candidate["generation_attempt_pending"] = False
        candidate["last_error"] = {
            "type": "SubmissionUnknown",
            "message": "Remote submission outcome is unknown.",
            "timestamp": now_iso(),
        }
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry,
            candidate.get("video_version"),
            status="SUBMISSION_UNKNOWN",
            submission_unknown=True,
            error=candidate["last_error"],
        )
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def mark_candidate_ready(self, shot_id: int) -> None:
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        candidate["status"] = CandidateStatus.WAITING_REVIEW.value
        candidate["generation_phase"] = "WAITING_REVIEW"
        candidate["submission_unknown"] = False
        candidate["completed_at"] = now_iso()
        candidate["generation_attempt_pending"] = False
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry,
            candidate.get("video_version"),
            status=CandidateStatus.WAITING_REVIEW.value,
            completed_at=candidate["completed_at"],
            candidate_path=candidate.get("video_path"),
            review_result=ShotStatus.WAITING_REVIEW.value,
            is_active=False,
            is_approved=False,
        )
        self._save_generation_state(
            shot_id,
            candidate.get("video_version"),
            review_result=ShotStatus.WAITING_REVIEW.value,
        )

    def mark_candidate_failed(self, shot_id: int, error: BaseException | str) -> None:
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        candidate["status"] = CandidateStatus.FAILED.value
        if candidate.get("generation_phase") != "SUBMISSION_UNKNOWN":
            candidate["generation_phase"] = "FAILED"
        candidate["submission_unknown"] = False
        candidate["generation_attempt_pending"] = False
        candidate["last_error"] = {
            "type": type(error).__name__ if isinstance(error, BaseException) else "Error",
            "message": str(error),
            "timestamp": now_iso(),
        }
        candidate["updated_at"] = now_iso()
        self._update_generation(
            entry,
            candidate.get("video_version"),
            status=CandidateStatus.FAILED.value,
            error=candidate["last_error"],
        )
        self._save_generation_state(shot_id, candidate.get("video_version"))

    def finish_candidate(
        self,
        shot_id: int,
        action: str,
        *,
        archived_video_path: Path | None = None,
    ) -> dict[str, Any]:
        entry = self.shot_checkpoint(shot_id)
        candidate = dict(self.candidate_checkpoint(shot_id))
        candidate["result"] = action
        candidate["finished_at"] = now_iso()
        self._update_generation(
            entry,
            candidate.get("video_version"),
            status=action,
            review_result=action,
            reviewed_at=candidate["finished_at"],
        )
        if archived_video_path is not None:
            candidate["archived_video_path"] = self._relative_project_path(
                self.project.ensure_within_project(archived_video_path)
            )
        entry.setdefault("candidate_history", []).append(candidate)
        entry["candidate"] = self._new_candidate_entry()
        entry["updated_at"] = now_iso()
        self.save()
        return candidate

    def approve_candidate(self, shot_id: int) -> tuple[int | None, int | None, int, int]:
        if self.shot_status(shot_id) != ShotStatus.APPROVED:
            raise ProjectStateError("Candidate 批准前原 Shot 必须保持 APPROVED。")
        entry = self.shot_checkpoint(shot_id)
        candidate = self.candidate_checkpoint(shot_id)
        if self.candidate_status(shot_id) != CandidateStatus.WAITING_REVIEW:
            raise ProjectStateError("Candidate 尚未进入 WAITING_REVIEW。")
        old_prompt = entry.get("approved_prompt_version")
        old_video = entry.get("approved_video_version")
        new_prompt = int(candidate["prompt_version"])
        new_video = int(candidate["video_version"])
        timestamp = now_iso()
        entry.update(
            {
                "status": ShotStatus.APPROVED.value,
                "active_prompt_version": new_prompt,
                "active_video_version": new_video,
                "approved_prompt_version": new_prompt,
                "approved_video_version": new_video,
                "approved_at": timestamp,
                "provider_task_id": candidate.get("provider_task_id"),
                "file_id": candidate.get("file_id"),
                "provider": candidate.get("provider"),
                "generation_mode": candidate.get("generation_mode"),
                "provider_model": candidate.get("provider_model"),
                "provider_api_version": candidate.get("provider_api_version"),
                "selection_mode": candidate.get("selection_mode"),
                "credential_env_name": candidate.get("credential_env_name"),
                "video_path": self._relative_project_path(
                    self.video_path_for_version(shot_id, new_video)
                ),
                "visual_input": visual_input_snapshot(
                    candidate.get("visual_input")
                ),
                "visual_input_selected": True,
                "updated_at": timestamp,
            }
        )
        for generation in entry.setdefault("generation_versions", []):
            generation["is_active"] = generation.get("video_version") == new_video
            generation["is_approved"] = generation.get("video_version") == new_video
        self._update_generation(
            entry,
            new_video,
            status=ShotStatus.APPROVED.value,
            approved_at=timestamp,
            candidate=False,
            video_path=entry.get("video_path"),
            review_result=ShotStatus.APPROVED.value,
            is_active=True,
            is_approved=True,
        )
        for generation in entry.setdefault("generation_versions", []):
            if generation.get("video_version") != new_video:
                generation["is_active"] = False
                generation["is_approved"] = False
        completed_candidate = dict(candidate)
        completed_candidate["video_path"] = entry.get("video_path")
        completed_candidate["result"] = "APPROVED"
        completed_candidate["finished_at"] = timestamp
        entry.setdefault("candidate_history", []).append(completed_candidate)
        entry["candidate"] = self._new_candidate_entry()
        entry["generation_phase"] = ShotStatus.APPROVED.value

        from shot_storage import (
            sync_shot_manifest_from_checkpoint,
            write_review_snapshot,
        )

        sync_shot_manifest_from_checkpoint(self.project, shot_id, entry)
        write_review_snapshot(
            self.project,
            shot_id,
            new_video,
            review_result=ShotStatus.APPROVED.value,
            user_action="approve",
            review_time=timestamp,
        )
        self.data["project_schema_version"] = 2
        self.data.pop("schema_version", None)
        self.data["updated_at"] = timestamp
        self.project.save_json(self.path, self.data)
        return old_prompt, old_video, new_prompt, new_video

    def mark_assembly_needs_update(
        self,
        shot_id: int,
        old_video_version: int | None,
        new_video_version: int,
    ) -> None:
        assembly = self.data.setdefault("assembly", {})
        change = {
            "changed_shot_id": int(shot_id),
            "old_approved_video_version": old_video_version,
            "new_approved_video_version": int(new_video_version),
            "timestamp": now_iso(),
        }
        assembly.update(
            {
                "needs_update": True,
                **{key: value for key, value in change.items() if key != "timestamp"},
            }
        )
        assembly.setdefault("changes", []).append(change)
        self.data["project_schema_version"] = 2
        self.data.pop("schema_version", None)
        self.data["updated_at"] = now_iso()
        self.project.save_json(self.path, self.data)

    def assembly_checkpoint(self) -> dict[str, Any]:
        assembly = self.data.setdefault("assembly", _new_assembly_entry())
        for key, value in _new_assembly_entry().items():
            assembly.setdefault(key, value)
        return assembly

    def start_assembly(
        self,
        final_video_path: Path,
        final_video_version: int,
        shot_versions: list[dict[str, Any]],
    ) -> None:
        assembly = self.assembly_checkpoint()
        assembly.update(
            {
                "status": AssemblyStatus.RUNNING.value,
                "started_at": now_iso(),
                "last_error": None,
                "pending_final_video_path": self._relative_project_path(
                    self.project.ensure_within_project(final_video_path)
                ),
                "pending_final_video_version": int(final_video_version),
                "pending_shot_versions": shot_versions,
            }
        )
        self.save()

    def complete_assembly(
        self,
        final_video_path: Path,
        final_video_version: int,
        total_duration: float,
        shot_versions: list[dict[str, Any]],
    ) -> None:
        assembly = self.assembly_checkpoint()
        assembly.update(
            {
                "status": AssemblyStatus.COMPLETED.value,
                "needs_update": False,
                "final_video_path": self._relative_project_path(
                    self.project.ensure_within_project(final_video_path)
                ),
                "final_video_version": int(final_video_version),
                "assembled_at": now_iso(),
                "total_duration": float(total_duration),
                "shot_versions": shot_versions,
                "last_error": None,
                "pending_final_video_path": None,
                "pending_final_video_version": None,
                "pending_shot_versions": [],
            }
        )
        self.save()
        PostProductionPipeline(self).mark_video_assembly_completed()

    def fail_assembly(self, error: BaseException | str) -> None:
        assembly = self.assembly_checkpoint()
        assembly["status"] = AssemblyStatus.FAILED.value
        assembly["last_error"] = {
            "type": type(error).__name__ if isinstance(error, BaseException) else "Error",
            "message": str(error),
            "timestamp": now_iso(),
        }
        self.save()

    def reset_from(self, stage: ProjectStage) -> list[Path]:
        index = STAGE_ORDER.index(stage)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archived: list[Path] = []
        for path in self._affected_artifacts(stage):
            path = self.project.ensure_within_project(path)
            if not path.is_file():
                continue
            revision_dir = self.project.ensure_within_project(path.parent / "revisions")
            revision_dir.mkdir(parents=True, exist_ok=True)
            target = self.project.ensure_within_project(
                revision_dir / f"{path.stem}_{timestamp}{path.suffix}"
            )
            shutil.move(str(path), str(target))
            archived.append(target)
        for affected_stage in STAGE_ORDER[index:]:
            self.data["stages"][affected_stage.value] = {
                "status": StageStatus.NOT_STARTED.value,
                "started_at": None,
                "completed_at": None,
                "approved_at": None,
                "updated_at": now_iso(),
                "attempts": 0,
            }
        if index <= STAGE_ORDER.index(ProjectStage.VIDEO_GENERATION):
            self.data["video_generation"]["completed_shots"] = []
            self.data["video_generation"]["shots"] = {}
            assembly = self.assembly_checkpoint()
            if assembly.get("final_video_path"):
                # The last assembled file remains a valid historical artifact, but it
                # no longer represents the workflow that is about to be regenerated.
                # Clear per-Shot/pending details so a project-wide reset cannot be
                # mistaken for a specific approved Shot replacement.
                assembly.update(
                    {
                        "status": AssemblyStatus.COMPLETED.value,
                        "needs_update": True,
                        "changed_shot_id": None,
                        "old_approved_video_version": None,
                        "new_approved_video_version": None,
                        "last_error": None,
                        "started_at": None,
                        "pending_final_video_path": None,
                        "pending_final_video_version": None,
                        "pending_shot_versions": [],
                    }
                )
                assembly.setdefault("changes", []).append(
                    {
                        "reason": "PROJECT_STAGE_RESET",
                        "reset_from": stage.value,
                        "timestamp": now_iso(),
                    }
                )
            else:
                changes = list(assembly.get("changes") or [])
                self.data["assembly"] = _new_assembly_entry()
                self.data["assembly"]["changes"] = changes
        self.data["current_stage"] = stage.value
        self.data["status"] = StageStatus.NOT_STARTED.value
        self.data["completion_status"] = ProjectCompletionStatus.NOT_STARTED.value
        self.data["post_production"] = new_post_production_state()
        self.data["cancel_stage"] = ""
        self.data["cancelled_at"] = None
        self.data.pop("cancel_shot_id", None)
        self.data["last_error"] = None
        self.data["revision_history"].append(
            {
                "reset_from": stage.value,
                "timestamp": now_iso(),
                "archived_files": [str(path) for path in archived],
            }
        )
        self.save()
        return archived

    def _affected_artifacts(self, stage: ProjectStage) -> list[Path]:
        paths: list[Path] = []
        if STAGE_ORDER.index(stage) <= STAGE_ORDER.index(ProjectStage.CREATIVE):
            paths.append(self.project.creative_brief_path())
        if STAGE_ORDER.index(stage) <= STAGE_ORDER.index(ProjectStage.STORYBOARD):
            paths.append(self.project.storyboard_file_path())
        if STAGE_ORDER.index(stage) <= STAGE_ORDER.index(ProjectStage.VIDEO_PROMPT):
            paths.append(self.project.video_prompts_path())
            paths.append(self.project.video_prompt_generation_progress_path())
        return paths


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def display_project_status(checkpoint: ProjectCheckpoint) -> None:
    print("\n========== 项目状态 ==========")
    print(f"项目名称：{checkpoint.data['project_name']}")
    print(f"当前阶段：{checkpoint.current_stage.value}")
    print(f"项目状态：{checkpoint.status}")
    print(
        "项目完成状态："
        f"{checkpoint.data.get('completion_status', ProjectCompletionStatus.NOT_STARTED.value)}"
    )
    completed = checkpoint.completed_steps()
    print(f"已经完成：{', '.join(completed) if completed else '无'}")
    print(f"下一步：{checkpoint.next_stage().value}")
    if checkpoint.data.get("cancel_stage"):
        print(f"取消位置：{checkpoint.data['cancel_stage']}")
    assembly = checkpoint.data.get("assembly") or {}
    assembly_status = str(
        assembly.get("status") or AssemblyStatus.NOT_STARTED.value
    )
    changed_shot_id = _optional_int(assembly.get("changed_shot_id"))
    old_version = _optional_int(assembly.get("old_approved_video_version"))
    new_version = _optional_int(assembly.get("new_approved_video_version"))
    final_version = _optional_int(assembly.get("final_video_version"))
    pending_version = _optional_int(assembly.get("pending_final_video_version"))
    total_duration = _optional_float(assembly.get("total_duration"))
    final_path = assembly.get("final_video_path")
    pending_path = assembly.get("pending_final_video_path")
    assembled_at = assembly.get("assembled_at")

    if assembly_status != AssemblyStatus.NOT_STARTED.value or final_path:
        print(f"完整视频状态：{assembly_status}")
    if final_path:
        version_label = f"（v{final_version}）" if final_version is not None else ""
        print(f"历史完整视频：{final_path}{version_label}")
    if assembled_at:
        print(f"上次合片时间：{assembled_at}")
    if total_duration is not None:
        print(f"上次成片时长：{total_duration:g} 秒")
    if pending_path or pending_version is not None:
        pending_label = f"v{pending_version}" if pending_version is not None else "未编号"
        print(f"待完成合片：{pending_path or '路径未生成'}（{pending_label}）")
    if assembly.get("needs_update"):
        if changed_shot_id is not None:
            version_change = ""
            if old_version is not None or new_version is not None:
                old_label = f"v{old_version}" if old_version is not None else "未知版本"
                new_label = f"v{new_version}" if new_version is not None else "未知版本"
                version_change = f"，{old_label} → {new_label}"
            print(
                "完整视频：需要重新合片（Shot "
                f"{changed_shot_id:02d} 已更新{version_change}）"
            )
        else:
            reset_change = any(
                str(change.get("reason")) == "PROJECT_STAGE_RESET"
                for change in (assembly.get("changes") or [])
                if isinstance(change, dict)
            )
            if reset_change:
                print("完整视频：历史成片已保留；工作流重置后需要重新合片。")
            else:
                print("完整视频：需要重新合片（暂无变更镜头）。")
        final_export = (
            ((checkpoint.data.get("post_production") or {}).get("components") or {})
            .get("final_export")
            or {}
        )
        if final_export.get("status") == "COMPLETED":
            version = _optional_int(final_export.get("active_version"))
            label = f" v{version:03d}" if version is not None else ""
            print(
                f"最终导出：历史 Export{label} 已保留；"
                "重新合片后需要再次导出。"
            )
    print("==============================")


def ask_existing_project_action(checkpoint: ProjectCheckpoint) -> str:
    while True:
        display_project_status(checkpoint)
        print("\n请选择：")
        print("1. 继续当前项目")
        print("2. 查看项目状态")
        print("3. 从某个阶段重新开始")
        print("4. Shot 管理（主动编辑已 APPROVED 镜头）")
        print("5. 退出")
        choice = input("请输入 1、2、3、4 或 5: ").strip()
        if choice == "1":
            return "continue"
        if choice == "2":
            continue
        if choice == "3":
            return "restart"
        if choice == "4":
            return "shot_management"
        if choice == "5":
            return "exit"
        print("无效选择，请重新输入。")


def ask_restart_stage() -> ProjectStage:
    restartable = (
        ProjectStage.CREATIVE,
        ProjectStage.STORYBOARD,
        ProjectStage.VIDEO_PROMPT,
        ProjectStage.VIDEO_GENERATION,
    )
    while True:
        print("\n请选择重新开始阶段：")
        for index, stage in enumerate(restartable, 1):
            print(f"{index}. {stage.value}")
        raw = input(f"请输入 1-{len(restartable)}: ").strip()
        if raw.isdigit() and 1 <= int(raw) <= len(restartable):
            return restartable[int(raw) - 1]
        print("无效选择，请重新输入。")
