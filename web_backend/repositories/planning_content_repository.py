"""Pure read-only projection of durable planning artifacts."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from web_backend.models.planning import (
    CreativeAVTimelineConstraints,
    CreativeContentResponse,
    CreativeForbiddenWindow,
    CreativeGlobalConstraints,
    CreativeNarrationPlan,
    CreativePlanningContent,
    CreativeSubtitleStrategy,
    PlanningCue,
    StoryboardContentResponse,
    StoryboardPlanningContent,
    StoryboardShotContent,
    StoryboardVideoConstraints,
    VideoPromptPlanningContent,
    VideoPromptShotContent,
    VideoPromptsContentResponse,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
)


_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|file://)")
_SECRET_MARKER = re.compile(
    r"(?i)(?:api[_ -]?key|credential(?:_env_name)?|authorization|"
    r"provider secret|bearer\s+\S+|sk-[A-Za-z0-9_-]{12,})"
)
_HIDDEN_TEXT = "[敏感内容已隐藏]"
_MAX_CONTENT_LENGTH = 50000


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if _WINDOWS_ABSOLUTE.search(value) or _SECRET_MARKER.search(value):
        return _HIDDEN_TEXT
    return value[:_MAX_CONTENT_LENGTH]


def _safe_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _safe_text(item)) is not None]


def _safe_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _safe_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    return number if number > 0 and number == value else None


def _safe_optional_bool(value: Any, *, default: bool = False) -> bool:
    return value if isinstance(value, bool) else default


class PlanningContentRepository:
    """Read fixed planning files without Core managers, migration, or writes."""

    def __init__(self, project_repository: ProjectRepository) -> None:
        self.project_repository = project_repository

    def get_creative(self, project_id: str) -> CreativeContentResponse:
        workflow, project_dir = self._project_context(project_id)
        payload = self._read_optional_object(
            project_dir, ("concepts", "creative_brief.json")
        )
        content = self._creative_content(payload) if payload is not None else None
        return CreativeContentResponse(
            project_id=workflow.project_id,
            status=workflow.stages.creative.status,
            content=content,
        )

    def get_storyboard(self, project_id: str) -> StoryboardContentResponse:
        workflow, project_dir = self._project_context(project_id)
        payload = self._read_optional_object(
            project_dir, ("storyboard", "storyboard.json")
        )
        content = self._storyboard_content(payload) if payload is not None else None
        return StoryboardContentResponse(
            project_id=workflow.project_id,
            status=workflow.stages.storyboard.status,
            content=content,
        )

    def get_video_prompts(self, project_id: str) -> VideoPromptsContentResponse:
        workflow, project_dir = self._project_context(project_id)
        payload = self._read_optional_object(
            project_dir, ("storyboard", "video_prompts.json")
        )
        if payload is None:
            content = None
        else:
            project_data = self._read_required_object(project_dir, ("project.json",))
            content = self._video_prompt_content(payload, project_data)
        return VideoPromptsContentResponse(
            project_id=workflow.project_id,
            status=workflow.stages.video_prompt.status,
            content=content,
        )

    def _project_context(self, project_id: str):
        workflow = self.project_repository.get_workflow(project_id)
        project_dir = self.project_repository.resolve_project_dir(project_id)
        return workflow, project_dir

    @staticmethod
    def _fixed_path(project_dir: Path, parts: tuple[str, ...]) -> Path:
        project_root = project_dir.resolve()
        target = project_dir.joinpath(*parts).resolve()
        try:
            target.relative_to(project_root)
        except ValueError as exc:
            raise ProjectDataCorrupt("planning content escaped project") from exc
        return target

    def _read_optional_object(
        self, project_dir: Path, parts: tuple[str, ...]
    ) -> Mapping[str, Any] | None:
        path = self._fixed_path(project_dir, parts)
        if not path.exists():
            return None
        if not path.is_file():
            raise ProjectDataCorrupt("planning content path is not a file")
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ProjectDataCorrupt("planning content is unreadable") from exc
        if not isinstance(payload, Mapping):
            raise ProjectDataCorrupt("planning content is not an object")
        return payload

    def _read_required_object(
        self, project_dir: Path, parts: tuple[str, ...]
    ) -> Mapping[str, Any]:
        payload = self._read_optional_object(project_dir, parts)
        if payload is None:
            raise ProjectDataCorrupt("required project data is missing")
        return payload

    @staticmethod
    def _creative_content(payload: Mapping[str, Any]) -> CreativePlanningContent:
        narration = _mapping(payload.get("narration_plan"))
        subtitles = _mapping(payload.get("subtitle_strategy"))
        constraints = _mapping(payload.get("global_constraints"))
        timeline = _mapping(payload.get("av_timeline_constraints"))
        windows: list[CreativeForbiddenWindow] = []
        raw_windows = timeline.get("forbidden_windows")
        if isinstance(raw_windows, list):
            for raw_window in raw_windows:
                window = _mapping(raw_window)
                windows.append(
                    CreativeForbiddenWindow(
                        start=_safe_number(window.get("start")),
                        end=_safe_number(window.get("end")),
                        tracks=_safe_text_list(window.get("tracks")),
                    )
                )
        return CreativePlanningContent(
            creative_concept=_safe_text(payload.get("creative_concept")),
            target_audience=_safe_text(payload.get("target_audience")),
            key_message=_safe_text(payload.get("key_message")),
            visual_direction=_safe_text(payload.get("visual_direction")),
            narrative_arc=_safe_text(payload.get("narrative_arc")),
            narration_plan=CreativeNarrationPlan(
                enabled=_safe_optional_bool(narration.get("enabled")),
                tone=_safe_text(narration.get("tone")),
                full_script=_safe_text(narration.get("full_script")),
                target_duration_seconds=_safe_number(
                    narration.get("target_duration_seconds")
                ),
            ),
            subtitle_strategy=CreativeSubtitleStrategy(
                enabled=_safe_optional_bool(subtitles.get("enabled")),
                tone=_safe_text(subtitles.get("tone")),
                density=_safe_text(subtitles.get("density")),
                max_lines=_safe_positive_int(subtitles.get("max_lines")),
                preferred_position=_safe_text(subtitles.get("preferred_position")),
                principles=_safe_text_list(subtitles.get("principles")),
            ),
            global_constraints=CreativeGlobalConstraints(
                must=_safe_text_list(constraints.get("must")),
                must_not=_safe_text_list(constraints.get("must_not")),
            ),
            av_timeline_constraints=CreativeAVTimelineConstraints(
                forbidden_windows=windows
            ),
        )

    @staticmethod
    def _cues(value: Any) -> list[PlanningCue]:
        if not isinstance(value, list):
            return []
        cues: list[PlanningCue] = []
        for raw_cue in value:
            cue = _mapping(raw_cue)
            cues.append(
                PlanningCue(
                    text=_safe_text(cue.get("text")),
                    start_offset=_safe_number(cue.get("start_offset")),
                    end_offset=_safe_number(cue.get("end_offset")),
                    position=_safe_text(cue.get("position")),
                )
            )
        return cues

    @classmethod
    def _storyboard_content(
        cls, payload: Mapping[str, Any]
    ) -> StoryboardPlanningContent:
        shots: list[StoryboardShotContent] = []
        raw_shots = payload.get("shots")
        if isinstance(raw_shots, list):
            for raw_shot in raw_shots:
                shot = _mapping(raw_shot)
                constraints = _mapping(shot.get("video_constraints"))
                shots.append(
                    StoryboardShotContent(
                        shot_id=_safe_positive_int(shot.get("shot_id")),
                        duration_seconds=_safe_number(shot.get("duration")),
                        purpose=_safe_text(shot.get("purpose")),
                        visual=_safe_text(shot.get("visual")),
                        camera=_safe_text(shot.get("camera")),
                        voiceover_cues=cls._cues(shot.get("voiceover_cues")),
                        subtitle_cues=cls._cues(shot.get("subtitle_cues")),
                        video_constraints=StoryboardVideoConstraints(
                            reserve_subtitle_space=_safe_optional_bool(
                                constraints.get("reserve_subtitle_space")
                            ),
                            subtitle_safe_area=_safe_text(
                                constraints.get("subtitle_safe_area")
                            ),
                        ),
                    )
                )
        return StoryboardPlanningContent(
            total_duration_seconds=_safe_number(payload.get("total_duration")),
            shots=shots,
        )

    @staticmethod
    def _shot_checkpoints(project_data: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
        generation = _mapping(project_data.get("video_generation"))
        raw_shots = generation.get("shots")
        checkpoints: dict[int, Mapping[str, Any]] = {}
        values = raw_shots.values() if isinstance(raw_shots, Mapping) else raw_shots
        if isinstance(values, (list, tuple)) or hasattr(values, "__iter__"):
            for raw_shot in values:
                shot = _mapping(raw_shot)
                shot_id = _safe_positive_int(shot.get("shot_id"))
                if shot_id is not None:
                    checkpoints[shot_id] = shot
        return checkpoints

    @staticmethod
    def _official_prompt(
        canonical: Mapping[str, Any], checkpoint: Mapping[str, Any]
    ) -> tuple[int | None, str | None, str | None]:
        version = _safe_positive_int(checkpoint.get("approved_prompt_version"))
        if version is None:
            version = _safe_positive_int(checkpoint.get("active_prompt_version"))
        raw_versions = checkpoint.get("prompt_versions")
        if version is not None and isinstance(raw_versions, list):
            for raw_version in raw_versions:
                entry = _mapping(raw_version)
                if _safe_positive_int(entry.get("version")) == version:
                    prompt = _safe_text(entry.get("prompt"))
                    if prompt is not None:
                        return version, _safe_text(entry.get("source")), prompt
        return version, None, _safe_text(canonical.get("video_prompt"))

    @classmethod
    def _video_prompt_content(
        cls,
        payload: Mapping[str, Any],
        project_data: Mapping[str, Any],
    ) -> VideoPromptPlanningContent:
        checkpoints = cls._shot_checkpoints(project_data)
        shots: list[VideoPromptShotContent] = []
        raw_shots = payload.get("shots")
        if isinstance(raw_shots, list):
            for raw_shot in raw_shots:
                shot = _mapping(raw_shot)
                shot_id = _safe_positive_int(shot.get("shot_id"))
                version, source, prompt = cls._official_prompt(
                    shot, checkpoints.get(shot_id or -1, {})
                )
                shots.append(
                    VideoPromptShotContent(
                        shot_id=shot_id,
                        prompt_version=version,
                        prompt_source=source,
                        visual_prompt_core=_safe_text(
                            shot.get("visual_prompt_core")
                        ),
                        prompt_text=prompt,
                    )
                )
        return VideoPromptPlanningContent(shots=shots)
