"""Checkpoint-backed post-production state, isolated from video generation.

This module owns state only. It never calls a VideoProvider, VoiceProvider, or
FFmpeg. Assembly, voice generation, and final export keep their own media
implementations and report durable results back through this pipeline.
"""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime
from enum import StrEnum
from typing import Any


class PostProductionStage(StrEnum):
    VIDEO_ASSEMBLY = "VIDEO_ASSEMBLY"
    AUDIO_PROCESSING = "AUDIO_PROCESSING"
    FINAL_EXPORT = "FINAL_EXPORT"


class PostProductionStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class ProjectCompletionStatus(StrEnum):
    """User-facing state above the legacy ProjectStage compatibility layer."""

    NOT_STARTED = "NOT_STARTED"
    VIDEO_GENERATION_COMPLETED = "VIDEO_GENERATION_COMPLETED"
    VIDEO_ASSEMBLY_COMPLETED = "VIDEO_ASSEMBLY_COMPLETED"
    POST_PRODUCTION = "POST_PRODUCTION"
    FINAL_COMPLETED = "FINAL_COMPLETED"


POST_PRODUCTION_STAGE_ORDER = tuple(PostProductionStage)
POST_PRODUCTION_COMPONENTS = ("voice", "subtitle", "music", "final_export")


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _new_component() -> dict[str, Any]:
    return {
        "status": PostProductionStatus.NOT_STARTED.value,
        "active_version": None,
        "path": None,
        "updated_at": None,
        "last_error": None,
    }


def new_post_production_state() -> dict[str, Any]:
    return {
        "status": PostProductionStatus.NOT_STARTED.value,
        "video_status": PostProductionStatus.NOT_STARTED.value,
        "current_stage": PostProductionStage.VIDEO_ASSEMBLY.value,
        "stages": {
            stage.value: {
                "status": PostProductionStatus.NOT_STARTED.value,
                "started_at": None,
                "completed_at": None,
                "updated_at": None,
                "last_error": None,
            }
            for stage in POST_PRODUCTION_STAGE_ORDER
        },
        "components": {
            name: _new_component() for name in POST_PRODUCTION_COMPONENTS
        },
    }


def normalize_post_production_state(value: Any) -> tuple[dict[str, Any], bool]:
    """Add fields to old projects without changing recorded media state."""
    changed = False
    if not isinstance(value, dict):
        value = {}
        changed = True
    normalized = deepcopy(value)
    defaults = new_post_production_state()
    for key in ("status", "video_status", "current_stage"):
        if key not in normalized:
            normalized[key] = defaults[key]
            changed = True
    stages = normalized.get("stages")
    if not isinstance(stages, dict):
        stages = {}
        normalized["stages"] = stages
        changed = True
    for stage, default in defaults["stages"].items():
        if not isinstance(stages.get(stage), dict):
            stages[stage] = deepcopy(default)
            changed = True
            continue
        for key, value_default in default.items():
            if key not in stages[stage]:
                stages[stage][key] = value_default
                changed = True

    components = normalized.get("components")
    if not isinstance(components, dict):
        components = {}
        normalized["components"] = components
        changed = True
    for name, default in defaults["components"].items():
        if not isinstance(components.get(name), dict):
            components[name] = deepcopy(default)
            changed = True
            continue
        for key, value_default in default.items():
            if key not in components[name]:
                components[name][key] = value_default
                changed = True
    return normalized, changed


class PostProductionPipeline:
    """Durable transitions with no provider or FFmpeg side effects."""

    def __init__(self, checkpoint: Any) -> None:
        self.checkpoint = checkpoint
        normalized, changed = normalize_post_production_state(
            checkpoint.data.get("post_production")
        )
        checkpoint.data["post_production"] = normalized
        if changed:
            checkpoint.save()

    @property
    def data(self) -> dict[str, Any]:
        return self.checkpoint.data["post_production"]

    def component(self, name: str) -> dict[str, Any]:
        if name not in POST_PRODUCTION_COMPONENTS:
            raise ValueError(f"未知 PostProduction component：{name}")
        return self.data["components"][name]

    def sync_from_existing_assets(self) -> bool:
        """Recover assembly/voice/export state without generating any media."""
        changed = False
        assembly = self.checkpoint.assembly_checkpoint()
        final_relative = assembly.get("final_video_path")
        final_path = None
        if final_relative:
            try:
                final_path = self.checkpoint.project.ensure_within_project(
                    self.checkpoint.project.project_path / str(final_relative)
                )
            except Exception:
                final_path = None
        assembly_ready = (
            str(assembly.get("status")) == "COMPLETED"
            and not bool(assembly.get("needs_update"))
            and final_path is not None
            and final_path.is_file()
            and final_path.stat().st_size > 0
        )
        if assembly_ready:
            stage = self.data["stages"][PostProductionStage.VIDEO_ASSEMBLY.value]
            if stage.get("status") != PostProductionStatus.COMPLETED.value:
                timestamp = str(assembly.get("assembled_at") or _now_iso())
                stage.update(
                    {
                        "status": PostProductionStatus.COMPLETED.value,
                        "completed_at": timestamp,
                        "updated_at": timestamp,
                        "last_error": None,
                    }
                )
                changed = True
            if self.data.get("video_status") != ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value:
                self.data["video_status"] = ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
                changed = True
            if self.data.get("status") == PostProductionStatus.NOT_STARTED.value:
                self.data["status"] = ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
                self.data["current_stage"] = PostProductionStage.AUDIO_PROCESSING.value
                changed = True

        try:
            from voice_assets import VoiceAssetManager

            active_voice = VoiceAssetManager(self.checkpoint.project).active_version()
        except Exception:
            active_voice = None
        if active_voice:
            voice = self.component("voice")
            desired = {
                "status": PostProductionStatus.COMPLETED.value,
                "active_version": active_voice.get("version"),
                "path": active_voice.get("audio_path"),
                "updated_at": active_voice.get("created_at"),
                "last_error": None,
            }
            if any(voice.get(key) != value for key, value in desired.items()):
                voice.update(desired)
                changed = True

        manifest_path = self.checkpoint.project.export_manifest_path()
        export_entry = None
        if manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                active_version = manifest.get("active_version")
                export_entry = next(
                    (
                        item
                        for item in manifest.get("versions", [])
                        if item.get("version") == active_version
                    ),
                    None,
                )
            except (OSError, json.JSONDecodeError, TypeError):
                export_entry = None
        if (
            export_entry
            and assembly_ready
            and export_entry.get("assembly_version")
            == assembly.get("final_video_version")
        ):
            export_video = self.checkpoint.project.project_path / str(
                export_entry.get("video_path") or ""
            )
            if export_video.is_file() and export_video.stat().st_size > 0:
                final_export = self.component("final_export")
                desired = {
                    "status": PostProductionStatus.COMPLETED.value,
                    "active_version": export_entry.get("version"),
                    "path": export_entry.get("video_path"),
                    "updated_at": export_entry.get("created_at"),
                    "last_error": None,
                }
                if any(final_export.get(key) != value for key, value in desired.items()):
                    final_export.update(desired)
                    changed = True
                export_stage = self.data["stages"][PostProductionStage.FINAL_EXPORT.value]
                if export_stage.get("status") != PostProductionStatus.COMPLETED.value:
                    export_stage.update(
                        {
                            "status": PostProductionStatus.COMPLETED.value,
                            "completed_at": export_entry.get("created_at"),
                            "updated_at": export_entry.get("created_at"),
                            "last_error": None,
                        }
                    )
                    changed = True
                if self.data.get("status") != ProjectCompletionStatus.FINAL_COMPLETED.value:
                    self.data["status"] = ProjectCompletionStatus.FINAL_COMPLETED.value
                    self.data["current_stage"] = PostProductionStage.FINAL_EXPORT.value
                    changed = True
        if changed:
            self.checkpoint.save()
        return changed

    def mark_video_assembly_completed(self) -> None:
        """Start a fresh post-production cycle after an Assembly succeeds."""
        timestamp = str(
            self.checkpoint.assembly_checkpoint().get("assembled_at") or _now_iso()
        )
        stage = self.data["stages"][PostProductionStage.VIDEO_ASSEMBLY.value]
        stage.update(
            {
                "status": PostProductionStatus.COMPLETED.value,
                "completed_at": timestamp,
                "updated_at": timestamp,
                "last_error": None,
            }
        )
        self.data["video_status"] = (
            ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
        )
        self.data["status"] = (
            ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
        )
        self.data["current_stage"] = PostProductionStage.AUDIO_PROCESSING.value
        self.data["stages"][PostProductionStage.FINAL_EXPORT.value] = deepcopy(
            new_post_production_state()["stages"][
                PostProductionStage.FINAL_EXPORT.value
            ]
        )
        self.data["components"]["final_export"] = _new_component()
        self.checkpoint.data["completion_status"] = (
            ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value
        )
        self.checkpoint.save()

    def enter_post_production(self) -> None:
        if self.data.get("video_status") != ProjectCompletionStatus.VIDEO_ASSEMBLY_COMPLETED.value:
            raise ValueError("完整视频尚未完成，不能进入 PostProduction。")
        self.data["status"] = ProjectCompletionStatus.POST_PRODUCTION.value
        self.data["current_stage"] = PostProductionStage.AUDIO_PROCESSING.value
        self.checkpoint.data["completion_status"] = ProjectCompletionStatus.POST_PRODUCTION.value
        self.checkpoint.save()

    def mark_component_completed(
        self, name: str, *, version: int, path: str, created_at: str | None = None
    ) -> None:
        timestamp = created_at or _now_iso()
        self.component(name).update(
            {
                "status": PostProductionStatus.COMPLETED.value,
                "active_version": int(version),
                "path": str(path),
                "updated_at": timestamp,
                "last_error": None,
            }
        )
        self.data["status"] = ProjectCompletionStatus.POST_PRODUCTION.value
        self.data["current_stage"] = PostProductionStage.AUDIO_PROCESSING.value
        self.checkpoint.data["completion_status"] = ProjectCompletionStatus.POST_PRODUCTION.value
        self.checkpoint.save()

    def mark_final_export_completed(
        self, *, version: int, path: str, created_at: str | None = None
    ) -> None:
        timestamp = created_at or _now_iso()
        self.component("final_export").update(
            {
                "status": PostProductionStatus.COMPLETED.value,
                "active_version": int(version),
                "path": str(path),
                "updated_at": timestamp,
                "last_error": None,
            }
        )
        stage = self.data["stages"][PostProductionStage.FINAL_EXPORT.value]
        stage.update(
            {
                "status": PostProductionStatus.COMPLETED.value,
                "completed_at": timestamp,
                "updated_at": timestamp,
                "last_error": None,
            }
        )
        self.data["status"] = ProjectCompletionStatus.FINAL_COMPLETED.value
        self.data["current_stage"] = PostProductionStage.FINAL_EXPORT.value
        self.checkpoint.data["completion_status"] = ProjectCompletionStatus.FINAL_COMPLETED.value
        self.checkpoint.save()

    def mark_running(self, stage: PostProductionStage) -> None:
        timestamp = _now_iso()
        entry = self.data["stages"][stage.value]
        entry.update(
            {
                "status": PostProductionStatus.RUNNING.value,
                "started_at": timestamp,
                "updated_at": timestamp,
                "last_error": None,
            }
        )
        self.data["current_stage"] = stage.value
        self.data["status"] = PostProductionStatus.RUNNING.value
        self.checkpoint.save()

    def mark_completed(self, stage: PostProductionStage) -> None:
        timestamp = _now_iso()
        entry = self.data["stages"][stage.value]
        entry.update(
            {
                "status": PostProductionStatus.COMPLETED.value,
                "completed_at": timestamp,
                "updated_at": timestamp,
                "last_error": None,
            }
        )
        index = POST_PRODUCTION_STAGE_ORDER.index(stage)
        if index + 1 < len(POST_PRODUCTION_STAGE_ORDER):
            self.data["current_stage"] = POST_PRODUCTION_STAGE_ORDER[index + 1].value
            self.data["status"] = PostProductionStatus.NOT_STARTED.value
        else:
            self.data["status"] = PostProductionStatus.COMPLETED.value
        self.checkpoint.save()

    def mark_failed(self, stage: PostProductionStage, error: str) -> None:
        timestamp = _now_iso()
        entry = self.data["stages"][stage.value]
        entry.update(
            {
                "status": PostProductionStatus.FAILED.value,
                "updated_at": timestamp,
                "last_error": str(error),
            }
        )
        self.data["current_stage"] = stage.value
        self.data["status"] = PostProductionStatus.FAILED.value
        self.checkpoint.save()
