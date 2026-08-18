"""Deterministic Storyboard-to-voice-script planning with no provider calls."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from project_manager import ProjectPaths
from storyboard import Storyboard, build_global_av_timeline
from timeline_scheduler import estimate_narration_duration


class VoiceScriptBuilderError(RuntimeError):
    """Raised when a stored Storyboard cannot provide a safe voice plan."""


@dataclass(frozen=True)
class StoryboardVoiceScript:
    script: str
    cue_count: int
    planned_narration_duration: float
    planned_first_voice_start: float
    planned_last_voice_end: float
    planned_voice_span: float
    total_video_duration: float
    source_storyboard_path: str | None = None

    def request_settings(self) -> dict[str, Any]:
        return {
            "script_source": "compiled_storyboard",
            "source_storyboard_path": self.source_storyboard_path,
            "planned_narration_duration": self.planned_narration_duration,
            "planned_first_voice_start": self.planned_first_voice_start,
            "planned_last_voice_end": self.planned_last_voice_end,
            "planned_voice_span": self.planned_voice_span,
            "total_video_duration": self.total_video_duration,
            "cue_count": self.cue_count,
        }


def build_voice_script_from_storyboard(
    board: Storyboard,
    *,
    planned_narration_duration: float | None = None,
) -> StoryboardVoiceScript | None:
    """Combine globally ordered voice cues without rewriting their text."""

    timeline = build_global_av_timeline(board)
    indexed_cues = list(enumerate(timeline["voiceover_cues"]))
    indexed_cues.sort(
        key=lambda item: (
            float(item[1]["start"]),
            float(item[1]["end"]),
            int(item[1]["shot_id"]),
            item[0],
        )
    )
    cues = [cue for _, cue in indexed_cues]
    if not cues:
        return None

    texts = [str(cue.get("text") or "").strip() for cue in cues]
    if any(not text for text in texts):
        raise VoiceScriptBuilderError("Storyboard Voice Cue text 不能为空。")
    script = "\n".join(texts)
    if not script.strip():
        return None

    starts = [float(cue["start"]) for cue in cues]
    ends = [float(cue["end"]) for cue in cues]
    if any(
        not math.isfinite(value)
        for value in (*starts, *ends)
    ):
        raise VoiceScriptBuilderError("Storyboard Voice Cue 时间必须是有限数字。")
    first_start = min(starts)
    last_end = max(ends)
    if first_start < 0 or last_end <= first_start:
        raise VoiceScriptBuilderError("Storyboard Voice Cue 全局时间无效。")

    planned_duration = planned_narration_duration
    if planned_duration is None or planned_duration <= 0:
        planned_duration = sum(
            estimate_narration_duration(text) for text in texts
        )
    if not math.isfinite(float(planned_duration)) or planned_duration <= 0:
        raise VoiceScriptBuilderError("Planned narration duration 必须大于 0。")

    return StoryboardVoiceScript(
        script=script,
        cue_count=len(cues),
        planned_narration_duration=round(float(planned_duration), 2),
        planned_first_voice_start=round(first_start, 3),
        planned_last_voice_end=round(last_end, 3),
        planned_voice_span=round(last_end - first_start, 3),
        total_video_duration=round(float(board.total_duration), 3),
    )


def load_storyboard_voice_script(
    paths: ProjectPaths,
) -> StoryboardVoiceScript | None:
    """Load the approved compiled Storyboard; old/no-narration projects return None."""

    storyboard_path = paths.storyboard_file_path()
    if not storyboard_path.is_file():
        return None
    try:
        payload = json.loads(storyboard_path.read_text(encoding="utf-8"))
        board = Storyboard.model_validate(payload)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise VoiceScriptBuilderError(
            f"Compiled Storyboard 无法读取：{exc}"
        ) from exc

    planned_duration: float | None = None
    creative_path = paths.creative_brief_path()
    if creative_path.is_file():
        try:
            creative = json.loads(creative_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VoiceScriptBuilderError(f"Creative Brief 无法读取：{exc}") from exc
        if not isinstance(creative, Mapping):
            raise VoiceScriptBuilderError("Creative Brief 必须是 JSON 对象。")
        narration = creative.get("narration_plan")
        if isinstance(narration, Mapping):
            if narration.get("enabled") is False:
                return None
            raw_target = narration.get("target_duration_seconds")
            if raw_target is not None:
                if isinstance(raw_target, bool):
                    raise VoiceScriptBuilderError(
                        "target_duration_seconds 必须是有效数字。"
                    )
                try:
                    planned_duration = float(raw_target)
                except (TypeError, ValueError) as exc:
                    raise VoiceScriptBuilderError(
                        "target_duration_seconds 必须是有效数字。"
                    ) from exc

    plan = build_voice_script_from_storyboard(
        board,
        planned_narration_duration=planned_duration,
    )
    if plan is None:
        return None
    relative_storyboard = paths.ensure_within_project(storyboard_path).relative_to(
        paths.project_path.resolve()
    )
    return replace(plan, source_storyboard_path=relative_storyboard.as_posix())
