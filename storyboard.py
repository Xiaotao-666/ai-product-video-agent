"""Generate creative brief, storyboard, and per-shot video prompts with DeepSeek."""

from __future__ import annotations

import json
import hashlib
import re
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from prompt_generator import (
    ProductVideoRequest,
    StructuredOutputError,
    deepseek_json_request,
)
from task_logger import TaskLogger
from timeline_scheduler import (
    TimelineScheduleError,
    estimate_narration_duration,
    estimate_subtitle_duration,
    schedule_av_timeline,
)


class StoryboardError(RuntimeError):
    """Raised when a planning artifact is invalid."""


class VideoPromptStructureError(StructuredOutputError):
    """Raised when Video Prompt JSON does not match the confirmed Storyboard."""


SubtitlePosition = Literal[
    "bottom_center",
    "bottom_left",
    "bottom_right",
    "top_center",
    "top_left",
    "top_right",
    "none",
]


class StrictPlanningModel(BaseModel):
    """Strict output model that still permits explicit compatibility defaults."""

    model_config = ConfigDict(extra="forbid", strict=True)


class NarrationPlan(StrictPlanningModel):
    enabled: bool
    tone: str
    full_script: str
    target_duration_seconds: float = Field(ge=0)

    @model_validator(mode="after")
    def validate_enabled_plan(self) -> "NarrationPlan":
        if self.enabled:
            if not self.tone.strip():
                raise ValueError("启用旁白时 tone 不能为空")
            if not self.full_script.strip():
                raise ValueError("启用旁白时 full_script 不能为空")
            if self.target_duration_seconds <= 0:
                raise ValueError("启用旁白时 target_duration_seconds 必须大于 0")
        return self


class SubtitleStrategy(StrictPlanningModel):
    enabled: bool
    tone: str
    density: Literal["low", "medium", "high"]
    max_lines: int = Field(ge=1, le=3)
    preferred_position: SubtitlePosition
    principles: list[str]

    @model_validator(mode="after")
    def validate_enabled_strategy(self) -> "SubtitleStrategy":
        if self.enabled and not self.tone.strip():
            raise ValueError("启用字幕策略时 tone 不能为空")
        if self.enabled and self.preferred_position == "none":
            raise ValueError("启用字幕策略时 preferred_position 不能为 none")
        if any(not principle.strip() for principle in self.principles):
            raise ValueError("字幕策略 principles 不能包含空字符串")
        return self


class GlobalConstraints(StrictPlanningModel):
    """Canonical hard constraints extracted only from explicit user wording."""

    must: list[str]
    must_not: list[str]

    @model_validator(mode="after")
    def validate_unique_values(self) -> "GlobalConstraints":
        if any(not value.strip() for value in self.must + self.must_not):
            raise ValueError("global_constraints 不能包含空字符串")
        if len(set(self.must)) != len(self.must):
            raise ValueError("global_constraints.must 不能重复")
        if len(set(self.must_not)) != len(self.must_not):
            raise ValueError("global_constraints.must_not 不能重复")
        return self


AVTrack = Literal["voiceover", "subtitle"]


class AVForbiddenWindow(StrictPlanningModel):
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    tracks: list[AVTrack] = Field(min_length=1)

    @field_validator("start", "end", mode="before")
    @classmethod
    def require_numeric_time(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("forbidden window 时间必须是数值")
        return value

    @model_validator(mode="after")
    def validate_window(self) -> "AVForbiddenWindow":
        if self.start >= self.end:
            raise ValueError("forbidden window 必须满足 start < end")
        if len(set(self.tracks)) != len(self.tracks):
            raise ValueError("forbidden window tracks 不能重复")
        return self


class AVTimelineConstraints(StrictPlanningModel):
    forbidden_windows: list[AVForbiddenWindow]


def _disabled_narration_plan() -> NarrationPlan:
    return NarrationPlan(
        enabled=False,
        tone="",
        full_script="",
        target_duration_seconds=0,
    )


def _disabled_subtitle_strategy() -> SubtitleStrategy:
    return SubtitleStrategy(
        enabled=False,
        tone="",
        density="low",
        max_lines=1,
        preferred_position="none",
        principles=[],
    )


def _empty_global_constraints() -> GlobalConstraints:
    return GlobalConstraints(must=[], must_not=[])


def _empty_av_timeline_constraints() -> AVTimelineConstraints:
    return AVTimelineConstraints(forbidden_windows=[])


class CreativeBrief(StrictPlanningModel):
    creative_concept: str = Field(min_length=1)
    target_audience: str = Field(min_length=1)
    key_message: str = Field(min_length=1)
    visual_direction: str = Field(min_length=1)
    narrative_arc: str = Field(min_length=1)
    narration_plan: NarrationPlan = Field(default_factory=_disabled_narration_plan)
    subtitle_strategy: SubtitleStrategy = Field(
        default_factory=_disabled_subtitle_strategy
    )
    global_constraints: GlobalConstraints = Field(
        default_factory=_empty_global_constraints
    )
    av_timeline_constraints: AVTimelineConstraints = Field(
        default_factory=_empty_av_timeline_constraints
    )

    def to_review_text(self) -> str:
        narration = self.narration_plan
        subtitles = self.subtitle_strategy
        lines = [
            "创意方向：",
            self.creative_concept,
            "",
            "目标受众：",
            self.target_audience,
            "",
            "核心信息：",
            self.key_message,
            "",
            "视觉方向：",
            self.visual_direction,
            "",
            "叙事结构：",
            self.narrative_arc,
            "",
            f"旁白策略：{'启用' if narration.enabled else '不启用'}",
        ]
        if narration.enabled:
            lines.extend(
                [
                    f"旁白语气：{narration.tone}",
                    f"预计旁白时长：{narration.target_duration_seconds:g} 秒",
                    "旁白初稿：",
                    narration.full_script,
                ]
            )
        lines.extend(
            [
                "",
                f"字幕策略：{'启用' if subtitles.enabled else '不启用'}",
            ]
        )
        if subtitles.enabled:
            lines.extend(
                [
                    f"字幕语气：{subtitles.tone}",
                    f"字幕密度：{subtitles.density}",
                    f"最多行数：{subtitles.max_lines}",
                    f"首选位置：{subtitles.preferred_position}",
                    "字幕原则："
                    + ("；".join(subtitles.principles) if subtitles.principles else "无"),
                ]
            )
        constraints = self.global_constraints
        lines.extend(
            [
                "",
                "全局硬约束：",
                "必须：" + ("；".join(constraints.must) if constraints.must else "无"),
                "禁止："
                + ("；".join(constraints.must_not) if constraints.must_not else "无"),
            ]
        )
        lines.extend(["", "AV 时间硬约束："])
        if self.av_timeline_constraints.forbidden_windows:
            for window in self.av_timeline_constraints.forbidden_windows:
                lines.append(
                    f"{window.start:g}s - {window.end:g}s 禁止："
                    + "、".join(window.tracks)
                )
        else:
            lines.append("无")
        return "\n".join(lines)


class VoiceoverCue(StrictPlanningModel):
    text: str = Field(min_length=1)
    start_offset: float = Field(ge=0)
    end_offset: float = Field(gt=0)

    @field_validator("start_offset", "end_offset", mode="before")
    @classmethod
    def require_numeric_offset(cls, value: Any) -> Any:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("offset 必须是数值")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> "VoiceoverCue":
        if self.start_offset >= self.end_offset:
            raise ValueError("start_offset 必须小于 end_offset")
        return self


class SubtitleCue(VoiceoverCue):
    position: SubtitlePosition

    @model_validator(mode="after")
    def validate_position(self) -> "SubtitleCue":
        if self.position == "none":
            raise ValueError("有字幕内容时 position 不能为 none")
        return self


class VideoConstraints(StrictPlanningModel):
    reserve_subtitle_space: bool
    subtitle_safe_area: SubtitlePosition

    @model_validator(mode="after")
    def validate_safe_area(self) -> "VideoConstraints":
        if self.reserve_subtitle_space and self.subtitle_safe_area == "none":
            raise ValueError("保留字幕空间时 subtitle_safe_area 不能为 none")
        if not self.reserve_subtitle_space and self.subtitle_safe_area != "none":
            raise ValueError("不保留字幕空间时 subtitle_safe_area 必须为 none")
        return self


def _no_video_constraints() -> VideoConstraints:
    return VideoConstraints(
        reserve_subtitle_space=False,
        subtitle_safe_area="none",
    )


CuePlacement = Literal["auto", "start", "middle", "end"]


class PlanningVoiceoverCue(StrictPlanningModel):
    text: str = Field(min_length=1)
    placement: CuePlacement

    @field_validator("text")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Cue text 不能为空")
        return value


class PlanningSubtitleCue(PlanningVoiceoverCue):
    position: SubtitlePosition

    @model_validator(mode="after")
    def validate_position(self) -> "PlanningSubtitleCue":
        if self.position == "none":
            raise ValueError("有字幕内容时 position 不能为 none")
        return self


class PlanningStoryboardShot(StrictPlanningModel):
    shot_id: int = Field(gt=0)
    duration: int
    purpose: str = Field(min_length=1)
    visual: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    voiceover_cues: list[PlanningVoiceoverCue]
    subtitle_cues: list[PlanningSubtitleCue]
    video_constraints: VideoConstraints

    @model_validator(mode="after")
    def validate_subtitle_space(self) -> "PlanningStoryboardShot":
        if self.subtitle_cues and not self.video_constraints.reserve_subtitle_space:
            raise ValueError("存在 subtitle_cues 时必须保留字幕安全区域")
        return self


class StoryboardPlanning(StrictPlanningModel):
    total_duration: int = Field(gt=0)
    shots: list[PlanningStoryboardShot] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timeline(self) -> "StoryboardPlanning":
        if [shot.shot_id for shot in self.shots] != list(range(1, len(self.shots) + 1)):
            raise ValueError("shot_id 必须从 1 开始连续递增")
        if any(shot.duration not in {6, 10} for shot in self.shots):
            raise ValueError("每个镜头时长必须为 6 秒或 10 秒")
        if sum(shot.duration for shot in self.shots) != self.total_duration:
            raise ValueError("镜头时长之和必须等于 total_duration")
        return self


class StoryboardShot(StrictPlanningModel):
    shot_id: int = Field(gt=0)
    duration: int
    purpose: str = Field(min_length=1)
    visual: str = Field(min_length=1)
    camera: str = Field(min_length=1)
    voiceover_cues: list[VoiceoverCue] = Field(default_factory=list)
    subtitle_cues: list[SubtitleCue] = Field(default_factory=list)
    video_constraints: VideoConstraints = Field(default_factory=_no_video_constraints)

    @model_validator(mode="after")
    def validate_av_cues(self) -> "StoryboardShot":
        for label, cues in (
            ("voiceover_cues", self.voiceover_cues),
            ("subtitle_cues", self.subtitle_cues),
        ):
            previous_start = -1.0
            for cue in cues:
                if cue.end_offset > self.duration:
                    raise ValueError(f"{label} 的 end_offset 不得超过镜头时长")
                if cue.start_offset < previous_start:
                    raise ValueError(f"{label} 必须按 start_offset 递增排列")
                previous_start = cue.start_offset
        previous_end = 0.0
        for cue in self.subtitle_cues:
            if cue.start_offset < previous_end:
                raise ValueError("subtitle_cues 不允许时间重叠")
            previous_end = cue.end_offset
        if self.subtitle_cues and not self.video_constraints.reserve_subtitle_space:
            raise ValueError("存在 subtitle_cues 时必须保留字幕安全区域")
        return self


class Storyboard(StrictPlanningModel):
    total_duration: int = Field(gt=0)
    shots: list[StoryboardShot] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_timeline(self) -> "Storyboard":
        if [shot.shot_id for shot in self.shots] != list(range(1, len(self.shots) + 1)):
            raise ValueError("shot_id 必须从 1 开始连续递增")
        if any(shot.duration not in {6, 10} for shot in self.shots):
            raise ValueError("每个镜头时长必须为 6 秒或 10 秒")
        if sum(shot.duration for shot in self.shots) != self.total_duration:
            raise ValueError("镜头时长之和必须等于 total_duration")
        return self

    def to_review_text(self) -> str:
        lines = [f"总时长：{self.total_duration} 秒"]
        for shot in self.shots:
            lines.extend(
                [
                    "",
                    f"---------- Shot {shot.shot_id:02d} ----------",
                    f"Duration：{shot.duration}s",
                    f"Purpose：{shot.purpose}",
                    "Visual：",
                    shot.visual,
                    "Camera：",
                    shot.camera,
                    "Voiceover：",
                ]
            )
            if shot.voiceover_cues:
                for cue in shot.voiceover_cues:
                    lines.append(
                        f'{cue.start_offset:g}s - {cue.end_offset:g}s\n“{cue.text}”'
                    )
            else:
                lines.append("无")
            lines.append("Subtitle：")
            if shot.subtitle_cues:
                for cue in shot.subtitle_cues:
                    lines.append(
                        f'{cue.start_offset:g}s - {cue.end_offset:g}s '
                        f'[{cue.position}]\n“{cue.text}”'
                    )
            else:
                lines.append("无")
            lines.append(
                "Subtitle Safe Area："
                + shot.video_constraints.subtitle_safe_area
            )
        return "\n".join(lines)


class ShotVideoPrompt(StrictPlanningModel):
    shot_id: int = Field(gt=0)
    # Schema v1 artifacts contain only video_prompt.  New VIDEO_PROMPT results
    # retain the model-owned core separately from deterministic control blocks.
    visual_prompt_core: str | None = None
    video_prompt: str = Field(min_length=1)


class VideoPromptPlan(StrictPlanningModel):
    shots: list[ShotVideoPrompt] = Field(min_length=1)


class ShotVisualPromptCore(StrictPlanningModel):
    visual_prompt_core: str = Field(min_length=1)


def _planning_context(
    request: ProductVideoRequest,
    visual_analysis_result: list[dict[str, Any]] | None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> str:
    # Kept in the signature for old callers; automatic visual analysis is disabled.
    del visual_analysis_result, visual_constraints
    reference = reference_asset_context or {}
    asset_count = int(reference.get("asset_count") or 0)
    reference_note = (
        f"项目已提供 {asset_count} 张参考素材；仅记录素材存在，"
        "不要推断或描述图片内容。"
        if asset_count > 0
        else "项目未提供参考素材。"
    )
    return (
        f"用户备注：{request.user_notes or '无'}\n"
        f"参考素材：{reference_note}"
    )


_HARD_CONSTRAINT_INSTRUCTIONS = {
    "people": (
        "No people, human figures, hands, faces, or human body parts may appear "
        "anywhere in the generated footage."
    ),
    "artificial written text": (
        "No artificial written text, captions, title cards, or text overlays may "
        "appear in the generated footage."
    ),
    "product color changes": "The product's original color must not change.",
    "packaging changes": "The product packaging design, shape, and structure must not change.",
    "logo deformation": "The real brand logo must remain intact and must not be deformed.",
}


def extract_global_constraints(user_notes: str) -> GlobalConstraints:
    """Extract a conservative allow-list of explicit user hard constraints."""

    notes = (user_notes or "").strip()
    lowered = notes.lower()
    must: list[str] = []
    must_not: list[str] = []

    people_patterns = (
        r"不要(?:出现|有)?(?:任何)?(?:人物|人类|真人|模特|演员|人手|手部|面部|脸)",
        r"禁止(?:出现|使用)?(?:任何)?(?:人物|人类|真人|模特|演员|人手|手部|面部|脸)",
        r"不(?:要)?出现(?:人物|人类|真人|模特|演员|人手|手部|面部|脸)",
        r"(?:^|[，,。；;、\s])无人(?:[，,。；;、\s]|$)",
        r"\b(?:no|without)\s+(?:people|persons?|humans?|models?|actors?|hands?|faces?)\b",
    )
    if any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in people_patterns):
        must_not.append("people")

    if re.search(
        r"(?:不要|禁止|不出现|无)(?:任何)?(?:文字|字幕|标题|文案)|"
        r"\b(?:no|without)\s+(?:text|captions?|subtitles?|titles?)\b",
        lowered,
        flags=re.IGNORECASE,
    ):
        must_not.append("artificial written text")
    if re.search(
        r"(?:不要|禁止)(?:改变|更改|修改)(?:产品)?颜色|"
        r"(?:产品)?颜色(?:不能|不得)(?:改变|更改)|"
        r"\b(?:do not|don't|must not)\s+change\s+(?:the\s+)?product\s+colou?r\b",
        lowered,
        flags=re.IGNORECASE,
    ):
        must_not.append("product color changes")
    if re.search(
        r"(?:不要|禁止)(?:改变|更改|修改)(?:产品)?包装|"
        r"(?:产品)?包装(?:不能|不得)(?:改变|更改|变形)|"
        r"\b(?:do not|don't|must not)\s+change\s+(?:the\s+)?packaging\b",
        lowered,
        flags=re.IGNORECASE,
    ):
        must_not.append("packaging changes")
    if re.search(
        r"(?:不要|禁止)\s*(?:logo|标志|徽标)(?:变形|扭曲)|"
        r"(?:logo|标志|徽标)(?:不能|不得|不要)(?:变形|扭曲)|"
        r"\b(?:do not|don't|must not)\s+(?:deform|distort)\s+(?:the\s+)?logo\b",
        lowered,
        flags=re.IGNORECASE,
    ):
        must_not.append("logo deformation")

    if re.search(r"必须(?:保留|保持)(?:真实)?(?:logo|标志|徽标)", lowered):
        must.append("preserve real logo")
    if re.search(r"必须(?:保留|保持)(?:产品)?包装", lowered):
        must.append("preserve product packaging")
    if re.search(r"必须(?:保留|保持)(?:产品)?颜色", lowered):
        must.append("preserve product color")
    return GlobalConstraints(must=must, must_not=must_not)


_TIME_NUMBER_TOKEN = r"(?:\d+(?:\.\d+)?|[零〇一二两三四五六七八九十半]+)"
_CHINESE_DIGITS = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}


def _parse_time_number(value: str) -> float:
    token = value.strip()
    try:
        return float(token)
    except ValueError:
        pass
    if token == "半":
        return 0.5
    if "十" in token:
        left, right = token.split("十", 1)
        tens = _CHINESE_DIGITS.get(left, 1) if left else 1
        ones = _CHINESE_DIGITS.get(right, 0) if right else 0
        return float(tens * 10 + ones)
    if len(token) == 1 and token in _CHINESE_DIGITS:
        return float(_CHINESE_DIGITS[token])
    raise ValueError(f"无法识别时间数字：{value}")


def _tracks_from_timeline_clause(clause: str) -> list[AVTrack]:
    lowered = clause.lower()
    if not re.search(r"不要|禁止|不得|不出现|没有|无|保持纯画面|纯画面|静音", lowered):
        return []
    if "纯画面" in lowered:
        return ["voiceover", "subtitle"]
    tracks: list[AVTrack] = []
    if re.search(r"旁白|解说|口播|人声|voice[ -]?over|narration", lowered):
        tracks.append("voiceover")
    if re.search(r"字幕|caption|subtitle", lowered):
        tracks.append("subtitle")
    return tracks


def merge_av_timeline_constraints(
    *constraints: AVTimelineConstraints,
) -> AVTimelineConstraints:
    merged: dict[tuple[float, float], set[AVTrack]] = {}
    for constraint in constraints:
        for window in constraint.forbidden_windows:
            key = (round(window.start, 6), round(window.end, 6))
            merged.setdefault(key, set()).update(window.tracks)
    windows = [
        AVForbiddenWindow(
            start=start,
            end=end,
            tracks=[track for track in ("voiceover", "subtitle") if track in tracks],
        )
        for (start, end), tracks in sorted(merged.items())
    ]
    return AVTimelineConstraints(forbidden_windows=windows)


def extract_av_timeline_constraints(
    user_text: str, total_duration: float
) -> AVTimelineConstraints:
    """Extract explicit half-open [start, end) AV track prohibition windows."""

    windows: list[AVForbiddenWindow] = []
    clauses = re.split(r"[。！？!?；;\n]+", user_text or "")
    for clause in clauses:
        if not clause.strip():
            continue
        tracks = _tracks_from_timeline_clause(clause)
        if not tracks:
            continue
        beginning = re.search(
            rf"(?:前|开头|开始|起始)\s*({_TIME_NUMBER_TOKEN})\s*秒",
            clause,
            flags=re.IGNORECASE,
        )
        ending = re.search(
            rf"最后\s*({_TIME_NUMBER_TOKEN})\s*秒",
            clause,
            flags=re.IGNORECASE,
        )
        try:
            if beginning:
                duration = min(_parse_time_number(beginning.group(1)), total_duration)
                if duration > 0:
                    windows.append(
                        AVForbiddenWindow(start=0, end=duration, tracks=tracks)
                    )
            elif ending:
                duration = min(_parse_time_number(ending.group(1)), total_duration)
                if duration > 0:
                    windows.append(
                        AVForbiddenWindow(
                            start=max(0.0, total_duration - duration),
                            end=total_duration,
                            tracks=tracks,
                        )
                    )
        except ValueError:
            continue
    return merge_av_timeline_constraints(
        AVTimelineConstraints(forbidden_windows=windows)
    )


def narration_duration_is_consistent(
    script: str,
    target_duration_seconds: float,
    *,
    tolerance_ratio: float = 0.25,
) -> bool:
    if target_duration_seconds <= 0:
        return False
    estimated = estimate_narration_duration(script)
    tolerance = max(0.75, target_duration_seconds * tolerance_ratio)
    return abs(estimated - target_duration_seconds) <= tolerance


def _requested_narration_range(text: str) -> tuple[float, float] | None:
    match = re.search(
        rf"(?:旁白|解说|口播)\s*(?:时长)?\s*({_TIME_NUMBER_TOKEN})\s*"
        rf"(?:[-~—–到至]\s*({_TIME_NUMBER_TOKEN}))?\s*秒",
        text or "",
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    start = _parse_time_number(match.group(1))
    end = _parse_time_number(match.group(2)) if match.group(2) else start
    return (min(start, end), max(start, end))


def _normalized_narration_text(text: str) -> str:
    return "".join(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fffA-Za-z0-9]", text)).lower()


def normalize_camera_language(prompt: str) -> str:
    """Replace conflicting or low-value camera numbers with visual-effect language."""

    normalized = re.sub(
        r"轻微俯角\s*(?:为|约|达到)?\s*90\s*度",
        "90-degree overhead shot",
        prompt,
        flags=re.IGNORECASE,
    )
    normalized = re.sub(
        r"(?:每秒\s*\d{3,}\s*帧|\d{3,}\s*(?:fps|帧/秒))",
        "high-speed macro capture aesthetic with extreme slow-motion commercial motion",
        normalized,
        flags=re.IGNORECASE,
    )
    return normalized


def plan_shot_durations(total_duration: int) -> list[int]:
    if total_duration < 6 or total_duration > 120:
        raise StoryboardError("总时长必须在 6 到 120 秒之间。")
    for six_count in range(total_duration // 6, -1, -1):
        remainder = total_duration - six_count * 6
        if remainder >= 0 and remainder % 10 == 0:
            return [6] * six_count + [10] * (remainder // 10)
    raise StoryboardError("总时长必须能由 6 秒和 10 秒镜头精确组成，例如 18、20、24、30 秒。")


def _voiceover_occupied_duration(board: Storyboard) -> float:
    intervals = sorted(
        (float(cue["start"]), float(cue["end"]))
        for cue in build_global_av_timeline(board)["voiceover_cues"]
    )
    if not intervals:
        return 0.0
    total = 0.0
    current_start, current_end = intervals[0]
    for start, end in intervals[1:]:
        if start <= current_end:
            current_end = max(current_end, end)
        else:
            total += current_end - current_start
            current_start, current_end = start, end
    return round(total + current_end - current_start, 2)


def _validate_storyboard_hard_constraints(
    board: Storyboard, constraints: GlobalConstraints
) -> None:
    for shot in board.shots:
        visual_text = " ".join((shot.purpose, shot.visual, shot.camera)).lower()
        violations = _hard_constraint_violations(visual_text, constraints)
        if violations:
            raise StoryboardError(
                f"Storyboard Shot {shot.shot_id} 违反用户硬约束：{violations}。"
            )


def _validate_storyboard_av_timeline_constraints(
    board: Storyboard, constraints: AVTimelineConstraints
) -> None:
    """Reject overlap using half-open intervals: [start, end)."""

    timeline = build_global_av_timeline(board)
    track_cues = {
        "voiceover": timeline["voiceover_cues"],
        "subtitle": timeline["subtitle_cues"],
    }
    for window in constraints.forbidden_windows:
        for track in window.tracks:
            for cue in track_cues[track]:
                cue_start = float(cue["start"])
                cue_end = float(cue["end"])
                if cue_start < window.end and cue_end > window.start:
                    raise StoryboardError(
                        f"Storyboard Shot {cue['shot_id']} 的 {track} Cue "
                        f"[{cue_start:g}, {cue_end:g}) 与禁止窗口 "
                        f"[{window.start:g}, {window.end:g}) 重叠。"
                    )


_OVERLAY_ACTION_PATTERN = re.compile(
    r"(?:品牌名|品牌文字|字幕|文字|标题|标语|文案|slogan)"
    r".{0,12}(?:浮现|出现|显示|打出|淡入|闪现|叠加)|"
    r"(?:画面|屏幕|中央|顶部|底部).{0,10}"
    r"(?:显示|出现|打出|浮现|淡入|闪现).{0,18}"
    r"(?:[“\"].+?[”\"]|品牌名|文字|字幕|标题|标语|文案|slogan)|"
    r"\b(?:caption|title\s*card|text\s*overlay|slogan)\b"
    r".{0,20}\b(?:appears?|displays?|fades?|overlays?)\b|"
    r"\b(?:displays?|shows?|fade(?:s)?\s*in|overlays?)\b.{0,20}"
    r"\b(?:caption|title\s*card|text|slogan)\b",
    flags=re.IGNORECASE,
)


def _validate_storyboard_text_overlays(board: Storyboard) -> None:
    for shot in board.shots:
        for field_name, value in (
            ("purpose", shot.purpose),
            ("visual", shot.visual),
            ("camera", shot.camera),
        ):
            if _OVERLAY_ACTION_PATTERN.search(value):
                raise StoryboardError(
                    f"Storyboard Shot {shot.shot_id}.{field_name} 包含后期人工文字 "
                    "Overlay 指令；请移入 subtitle_cues，视觉字段只描述纯画面。"
                )


def _hard_constraint_violations(
    visual_text: str, constraints: GlobalConstraints
) -> list[str]:
    # Constraint enforcement language is not visual content.  The primary
    # protection is structural (only the LLM core reaches this validator), and
    # these small guards also keep natural negative wording in a core from being
    # mistaken for a request to depict a person.
    people_scan_text = re.sub(
        r"\b(?:no|without|excluding?|avoid(?:ing)?|free\s+of)\s+(?:any\s+)?"
        r"(?:people|persons?|humans?|human\s+figures?|women|men|girls?|boys?|"
        r"models?|actors?|hands?|faces?|body\s+parts?)"
        r"(?:\s*,\s*(?:people|persons?|humans?|human\s+figures?|women|men|girls?|"
        r"boys?|models?|actors?|hands?|faces?|body\s+parts?))*",
        " ",
        visual_text,
        flags=re.IGNORECASE,
    )
    people_scan_text = re.sub(
        r"\b(?:do\s+not|don't|never)\s+(?:show|include|depict|feature|display)\s+"
        r"(?:any\s+)?(?:people|persons?|humans?|human\s+figures?|women|men|girls?|"
        r"boys?|models?|actors?|hands?|faces?|body\s+parts?)",
        " ",
        people_scan_text,
        flags=re.IGNORECASE,
    )
    people_scan_text = re.sub(
        r"(?:不要|不得|禁止|避免|不应|没有|无)(?:出现|展示|包含|描绘|呈现|加入|任何)?"
        r"(?:人物|真人|人像|女性|男性|女人|男人|女孩|男孩|模特|演员|手部|人手|"
        r"双手|单手|手指|面部|脸部|面孔|人体|身体部位)",
        " ",
        people_scan_text,
    )
    violations: list[str] = []
    if "people" in constraints.must_not and re.search(
            r"人物|真人|人像|女性|男性|女人|男人|女孩|男孩|模特|演员|"
            r"手部|人手|双手|单手|手指|面部|脸部|面孔|人体|身体部位|拟人|"
            r"\b(?:people|person|human|woman|man|girl|boy|model|actor|hand|face|body)\b",
            people_scan_text,
            flags=re.IGNORECASE,
        ):
        violations.append("people")
    if "artificial written text" in constraints.must_not and re.search(
            r"字幕|标题卡|文字叠加|文案叠加|屏幕文字|人工文字|"
            r"\b(?:subtitle|caption|title card|text overlay|ui text)\b",
            visual_text,
            flags=re.IGNORECASE,
        ):
        violations.append("artificial written text")
    if "product color changes" in constraints.must_not and re.search(
            r"改变产品颜色|产品变色|包装变色|change product colou?r",
            visual_text,
            flags=re.IGNORECASE,
        ):
        violations.append("product color changes")
    if "packaging changes" in constraints.must_not and re.search(
            r"改变包装|包装变形|重新设计包装|change packaging|redesign packaging",
            visual_text,
            flags=re.IGNORECASE,
        ):
        violations.append("packaging changes")
    if "logo deformation" in constraints.must_not and re.search(
            r"logo变形|扭曲logo|标志变形|deform(?:ed)? logo|distort(?:ed)? logo",
            visual_text,
            flags=re.IGNORECASE,
        ):
        violations.append("logo deformation")
    return violations


def _validate_storyboard_narration(
    board: Storyboard, brief: CreativeBrief
) -> None:
    narration = brief.narration_plan
    cues = [cue for shot in board.shots for cue in shot.voiceover_cues]
    if not narration.enabled:
        if cues:
            raise StoryboardError("Creative 已关闭旁白，Storyboard 不应生成 voiceover_cues。")
        return
    if not cues:
        raise StoryboardError("Creative 已启用旁白，Storyboard 必须规划 voiceover_cues。")

    source = _normalized_narration_text(narration.full_script)
    planned = _normalized_narration_text("".join(cue.text for cue in cues))
    length_coverage = len(planned) / max(1, len(source))
    similarity = SequenceMatcher(None, source, planned).ratio()
    if length_coverage < 0.65 or similarity < 0.65:
        raise StoryboardError(
            "Storyboard voiceover_cues 明显遗漏或偏离 Creative Narration 的主要内容。"
        )

    estimated_spoken_duration = round(
        sum(estimate_narration_duration(cue.text) for cue in cues), 2
    )
    target = narration.target_duration_seconds
    tolerance = max(1.0, target * 0.30)
    if abs(estimated_spoken_duration - target) > tolerance:
        raise StoryboardError(
            f"Storyboard Voiceover 文本预计朗读约 {estimated_spoken_duration:g} 秒，"
            f"与旁白目标 {target:g} 秒明显不一致。"
        )


def _validate_storyboard(
    board: Storyboard,
    request: ProductVideoRequest,
    brief: CreativeBrief | None = None,
) -> Storyboard:
    durations = plan_shot_durations(request.duration_seconds)
    if board.total_duration != request.duration_seconds:
        raise StoryboardError("Storyboard 总时长与用户输入不一致。")
    if [shot.duration for shot in board.shots] != durations:
        raise StoryboardError("Storyboard 改变了规定的镜头时长。")
    if brief is not None:
        _validate_storyboard_narration(board, brief)
        _validate_storyboard_hard_constraints(board, brief.global_constraints)
        _validate_storyboard_av_timeline_constraints(
            board, brief.av_timeline_constraints
        )
        _validate_storyboard_text_overlays(board)
    return board


def _infer_placement(start: float, end: float, duration: float) -> CuePlacement:
    center_ratio = ((start + end) / 2) / max(duration, 1e-6)
    if center_ratio <= 0.35:
        return "start"
    if center_ratio >= 0.65:
        return "end"
    return "middle"


def storyboard_to_planning(board: Storyboard) -> StoryboardPlanning:
    """Create semantic placement data for revising an existing compiled Storyboard."""

    return StoryboardPlanning(
        total_duration=board.total_duration,
        shots=[
            PlanningStoryboardShot(
                shot_id=shot.shot_id,
                duration=shot.duration,
                purpose=shot.purpose,
                visual=shot.visual,
                camera=shot.camera,
                voiceover_cues=[
                    PlanningVoiceoverCue(
                        text=cue.text,
                        placement=_infer_placement(
                            cue.start_offset, cue.end_offset, shot.duration
                        ),
                    )
                    for cue in shot.voiceover_cues
                ],
                subtitle_cues=[
                    PlanningSubtitleCue(
                        text=cue.text,
                        placement=_infer_placement(
                            cue.start_offset, cue.end_offset, shot.duration
                        ),
                        position=cue.position,
                    )
                    for cue in shot.subtitle_cues
                ],
                video_constraints=shot.video_constraints,
            )
            for shot in board.shots
        ],
    )


def compile_storyboard_planning(
    planning: StoryboardPlanning,
    request: ProductVideoRequest,
    brief: CreativeBrief,
) -> Storyboard:
    """Compile semantic LLM output into the existing downstream Storyboard schema."""

    if planning.total_duration != request.duration_seconds:
        raise StoryboardError("Storyboard Planning 总时长与用户输入不一致。")
    expected_durations = plan_shot_durations(request.duration_seconds)
    if [shot.duration for shot in planning.shots] != expected_durations:
        raise StoryboardError("Storyboard Planning 改变了规定的镜头时长。")
    try:
        compiled_data = schedule_av_timeline(
            planning.model_dump(),
            brief.av_timeline_constraints.model_dump(),
        )
        board = Storyboard.model_validate(compiled_data)
    except TimelineScheduleError as exc:
        raise StoryboardError(str(exc)) from exc
    except ValidationError as exc:
        raise StoryboardError(f"编译后的 Storyboard 结构无效：{exc}") from exc
    return _validate_storyboard(board, request, brief)


def _validate_creative_brief(
    brief: CreativeBrief,
    request: ProductVideoRequest,
    expected_av_constraints: AVTimelineConstraints | None = None,
) -> CreativeBrief:
    narration = brief.narration_plan
    if narration.target_duration_seconds > request.duration_seconds:
        raise StoryboardError("旁白预计时长不得超过视频总时长。")
    requested_range = _requested_narration_range(request.user_notes)
    if requested_range is not None:
        if not narration.enabled:
            raise StoryboardError("用户明确要求旁白时长时 narration_plan 必须启用。")
        if not requested_range[0] <= narration.target_duration_seconds <= requested_range[1]:
            raise StoryboardError(
                f"旁白目标时长必须位于用户要求的 {requested_range[0]:g}-"
                f"{requested_range[1]:g} 秒范围内。"
            )
    if narration.enabled and not narration_duration_is_consistent(
        narration.full_script, narration.target_duration_seconds
    ):
        estimated = estimate_narration_duration(narration.full_script)
        raise StoryboardError(
            f"旁白文本预计朗读约 {estimated:g} 秒，与声明的 "
            f"{narration.target_duration_seconds:g} 秒明显不一致；必须同步调整文案。"
        )
    expected_constraints = extract_global_constraints(request.user_notes)
    if brief.global_constraints != expected_constraints:
        raise StoryboardError(
            "global_constraints 必须与程序从用户明确输入中提取的硬约束完全一致。"
        )
    expected_av = expected_av_constraints or extract_av_timeline_constraints(
        request.user_notes, request.duration_seconds
    )
    for window in brief.av_timeline_constraints.forbidden_windows:
        if window.end > request.duration_seconds:
            raise StoryboardError("AV Timeline 禁止窗口不得超过视频总时长。")
    if brief.av_timeline_constraints != expected_av:
        raise StoryboardError(
            "av_timeline_constraints 必须与用户明确提出并已保留的时间硬约束完全一致。"
        )
    return brief


def _validate_creative_revision(
    brief: CreativeBrief,
    request: ProductVideoRequest,
    current: CreativeBrief,
    comment: str,
) -> CreativeBrief:
    expected_av = merge_av_timeline_constraints(
        extract_av_timeline_constraints(
            request.user_notes, request.duration_seconds
        ),
        current.av_timeline_constraints,
        extract_av_timeline_constraints(comment, request.duration_seconds),
    )
    _validate_creative_brief(brief, request, expected_av)
    requested_range = _requested_narration_range(comment)
    if requested_range is not None:
        narration = brief.narration_plan
        if not narration.enabled:
            raise StoryboardError("用户要求修改旁白时长后 narration_plan 必须启用。")
        if not requested_range[0] <= narration.target_duration_seconds <= requested_range[1]:
            raise StoryboardError(
                f"修改后的旁白时长必须位于 {requested_range[0]:g}-"
                f"{requested_range[1]:g} 秒范围内。"
            )
        if narration.full_script.strip() == current.narration_plan.full_script.strip():
            raise StoryboardError(
                "用户修改旁白时长时必须同步改写 full_script，不能只修改时长字段。"
            )
    return brief


def _structured_model_validator(
    model_type: type[BaseModel],
    label: str,
    after_validate: Any | None = None,
):
    """Return a validator compatible with DeepSeek's structured-output retry."""

    def validate(data: dict[str, Any]) -> None:
        try:
            value = model_type.model_validate(data)
            if after_validate is not None:
                after_validate(value)
        except (ValidationError, StoryboardError) as exc:
            raise StructuredOutputError(f"{label}结构无效：{exc}") from exc

    return validate


def _require_exact_fields(
    data: Any, required: set[str], label: str
) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise StructuredOutputError(f"{label}必须是 JSON 对象。")
    missing = sorted(required - set(data))
    extra = sorted(set(data) - required)
    if missing or extra:
        details = []
        if missing:
            details.append(f"缺少字段：{missing}")
        if extra:
            details.append(f"多出字段：{extra}")
        raise StructuredOutputError(f"{label}字段无效：{'；'.join(details)}")
    return data


def _creative_output_validator(
    request: ProductVideoRequest,
    *,
    current: CreativeBrief | None = None,
    comment: str = "",
):
    required = {
        "creative_concept",
        "target_audience",
        "key_message",
        "visual_direction",
        "narrative_arc",
        "narration_plan",
        "subtitle_strategy",
        "global_constraints",
        "av_timeline_constraints",
    }

    def validate(data: dict[str, Any]) -> None:
        _require_exact_fields(data, required, "Creative Brief")
        _require_exact_fields(
            data["narration_plan"],
            {"enabled", "tone", "full_script", "target_duration_seconds"},
            "narration_plan",
        )
        _require_exact_fields(
            data["subtitle_strategy"],
            {
                "enabled",
                "tone",
                "density",
                "max_lines",
                "preferred_position",
                "principles",
            },
            "subtitle_strategy",
        )
        _require_exact_fields(
            data["global_constraints"],
            {"must", "must_not"},
            "global_constraints",
        )
        _require_exact_fields(
            data["av_timeline_constraints"],
            {"forbidden_windows"},
            "av_timeline_constraints",
        )
        windows = data["av_timeline_constraints"]["forbidden_windows"]
        if not isinstance(windows, list):
            raise StructuredOutputError(
                "av_timeline_constraints.forbidden_windows 必须是数组。"
            )
        for index, window in enumerate(windows, start=1):
            _require_exact_fields(
                window,
                {"start", "end", "tracks"},
                f"forbidden_window {index}",
            )
        after_validate = (
            (lambda value: _validate_creative_revision(
                value, request, current, comment
            ))
            if current is not None
            else (lambda value: _validate_creative_brief(value, request))
        )
        _structured_model_validator(
            CreativeBrief,
            "创意方案",
            after_validate,
        )(data)

    return validate


def _storyboard_output_validator(
    request: ProductVideoRequest, brief: CreativeBrief
):
    shot_fields = {
        "shot_id",
        "duration",
        "purpose",
        "visual",
        "camera",
        "voiceover_cues",
        "subtitle_cues",
        "video_constraints",
    }

    def validate(data: dict[str, Any]) -> None:
        _require_exact_fields(data, {"total_duration", "shots"}, "Storyboard")
        shots = data["shots"]
        if not isinstance(shots, list):
            raise StructuredOutputError("Storyboard.shots 必须是数组。")
        for index, shot in enumerate(shots, start=1):
            _require_exact_fields(shot, shot_fields, f"Storyboard Shot {index}")
            voiceover_cues = shot["voiceover_cues"]
            subtitle_cues = shot["subtitle_cues"]
            if not isinstance(voiceover_cues, list):
                raise StructuredOutputError(
                    f"Storyboard Shot {index}.voiceover_cues 必须是数组。"
                )
            if not isinstance(subtitle_cues, list):
                raise StructuredOutputError(
                    f"Storyboard Shot {index}.subtitle_cues 必须是数组。"
                )
            for cue_index, cue in enumerate(voiceover_cues, start=1):
                _require_exact_fields(
                    cue,
                    {"text", "placement"},
                    f"Shot {index} voiceover_cue {cue_index}",
                )
            for cue_index, cue in enumerate(subtitle_cues, start=1):
                _require_exact_fields(
                    cue,
                    {"text", "placement", "position"},
                    f"Shot {index} subtitle_cue {cue_index}",
                )
            _require_exact_fields(
                shot["video_constraints"],
                {"reserve_subtitle_space", "subtitle_safe_area"},
                f"Shot {index} video_constraints",
            )
        _structured_model_validator(
            StoryboardPlanning,
            "Storyboard Planning",
            lambda value: compile_storyboard_planning(value, request, brief),
        )(data)

    return validate


def build_global_av_timeline(board: Storyboard) -> dict[str, list[dict[str, Any]]]:
    """Convert Shot-relative AV cues to a global timeline without side effects."""

    voiceover: list[dict[str, Any]] = []
    subtitles: list[dict[str, Any]] = []
    shot_start = 0.0
    for shot in board.shots:
        for cue in shot.voiceover_cues:
            voiceover.append(
                {
                    "shot_id": shot.shot_id,
                    "text": cue.text,
                    "start": shot_start + cue.start_offset,
                    "end": shot_start + cue.end_offset,
                }
            )
        for cue in shot.subtitle_cues:
            subtitles.append(
                {
                    "shot_id": shot.shot_id,
                    "text": cue.text,
                    "start": shot_start + cue.start_offset,
                    "end": shot_start + cue.end_offset,
                    "position": cue.position,
                }
            )
        shot_start += shot.duration
    return {"voiceover_cues": voiceover, "subtitle_cues": subtitles}


def _parse(model_type, data: dict, label: str):
    try:
        return model_type.model_validate(data)
    except ValidationError as exc:
        raise StoryboardError(f"DeepSeek 返回的{label}结构无效：{exc}") from exc


def generate_creative_brief(
    request: ProductVideoRequest,
    api_key: str,
    task_logger: TaskLogger | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> CreativeBrief:
    extracted_constraints = extract_global_constraints(request.user_notes)
    extracted_av_constraints = extract_av_timeline_constraints(
        request.user_notes, request.duration_seconds
    )
    system = """
你是品牌广告策略与创意总监。根据用户需求生成 Creative Brief，不生成分镜或视频 Prompt，不虚构产品信息。
同时规划整条广告的旁白初稿与字幕整体策略。允许旁白或字幕不启用；若启用旁白，文案应像精炼广告旁白，预计时长不得超过视频总时长。Creative 阶段不要生成逐镜头时间轴。
target_duration_seconds 不是标签，而是真实的自然朗读目标。如果目标是 8 秒，full_script 必须有足够内容在自然广告语速和合理停顿下占用约 8 秒；不得只修改 target_duration_seconds 而不改写 full_script。广告旁白允许停顿，不要求持续说满，但文本量必须与目标时长基本对应。
 global_constraints 与 av_timeline_constraints 均由程序从用户明确输入中提取。必须逐字复制用户消息中提供的规范 JSON，不得自行添加、删除或改写约束。
 av_timeline_constraints 使用全片绝对时间，forbidden_windows 采用半开区间 [start,end)，tracks 只允许 voiceover、subtitle。Creative 不得自行推测额外禁用窗口。
只输出以下严格 JSON，字段不得缺失或增加：
{
  "creative_concept":"",
  "target_audience":"",
  "key_message":"",
  "visual_direction":"",
  "narrative_arc":"",
  "narration_plan":{"enabled":true,"tone":"","full_script":"","target_duration_seconds":12},
  "subtitle_strategy":{"enabled":true,"tone":"","density":"low","max_lines":1,"preferred_position":"bottom_center","principles":[""]},
  "global_constraints":{"must":[],"must_not":[]},
  "av_timeline_constraints":{"forbidden_windows":[]}
}
density 只能是 low、medium、high；preferred_position 只能是 bottom_center、bottom_left、bottom_right、top_center、top_left、top_right、none。
不启用时仍必须输出完整对象：旁白使用空 tone、空 full_script、0 秒；字幕 preferred_position 使用 none。
""".strip()
    user = (
        f"产品名称：{request.product_name}\n产品信息：{request.product_description}\n"
        f"总时长：{request.duration_seconds} 秒\n视频风格：{request.video_style}\n"
        f"宣传目标：{request.video_purpose}\n"
        f"{_planning_context(request, visual_analysis_result, visual_constraints, reference_asset_context)}\n"
        f"必须原样复制的 global_constraints："
        f"{json.dumps(extracted_constraints.model_dump(), ensure_ascii=False)}\n"
        f"必须原样复制的 av_timeline_constraints："
        f"{json.dumps(extracted_av_constraints.model_dump(), ensure_ascii=False)}\n"
        "请生成 Creative Brief JSON。"
    )
    data = deepseek_json_request(
        api_key,
        system,
        user,
        task_logger=task_logger,
        raw_stage="creative",
        structure_validator=_creative_output_validator(request),
        retry_instruction=(
            "必须完整输出 narration_plan、subtitle_strategy、global_constraints、"
            "av_timeline_constraints；"
            "类型、枚举和字段必须符合模板。旁白文本量必须与 target_duration_seconds 匹配，"
            "且时长不得超过视频总时长。两个 constraints 对象都必须原样复制。"
        ),
    )
    brief = _parse(CreativeBrief, data, "创意方案").model_copy(
        update={
            "global_constraints": extracted_constraints,
            "av_timeline_constraints": extracted_av_constraints,
        }
    )
    return _validate_creative_brief(brief, request, extracted_av_constraints)


def revise_creative_brief(
    request: ProductVideoRequest,
    current: CreativeBrief,
    comment: str,
    api_key: str,
    task_logger: TaskLogger | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> CreativeBrief:
    extracted_constraints = extract_global_constraints(request.user_notes)
    extracted_av_constraints = merge_av_timeline_constraints(
        extract_av_timeline_constraints(request.user_notes, request.duration_seconds),
        current.av_timeline_constraints,
        extract_av_timeline_constraints(comment, request.duration_seconds),
    )
    system = (
        "根据用户意见修改 Creative Brief。保持产品事实不变，保留并正确更新 "
        "narration_plan、subtitle_strategy、global_constraints 与 av_timeline_constraints，"
        "只输出与原结构相同的严格 JSON。"
        "target_duration_seconds 是真实自然朗读目标；用户要求修改旁白时长时，必须同步改写 "
        "full_script，使文本量和合理停顿与新目标基本匹配，禁止只修改数字。"
        "global_constraints 与 av_timeline_constraints 必须原样复制程序提供的规范 JSON，"
        "不得自行增删。新增的 AV 时间限制来自人工修改意见，使用全片绝对半开区间 [start,end)。"
    )
    user = (
        f"原方案：{json.dumps(current.model_dump(), ensure_ascii=False)}\n"
        f"修改意见：{comment}\n产品需求：{request.model_dump_json()}\n"
        f"必须原样复制的 global_constraints："
        f"{json.dumps(extracted_constraints.model_dump(), ensure_ascii=False)}\n"
        f"必须原样复制的 av_timeline_constraints："
        f"{json.dumps(extracted_av_constraints.model_dump(), ensure_ascii=False)}\n"
        f"{_planning_context(request, visual_analysis_result, visual_constraints, reference_asset_context)}"
    )
    data = deepseek_json_request(
        api_key,
        system,
        user,
        task_logger=task_logger,
        raw_stage="creative_revision",
        structure_validator=_creative_output_validator(
            request, current=current, comment=comment
        ),
        retry_instruction=(
            "必须完整输出 narration_plan、subtitle_strategy、global_constraints、"
            "av_timeline_constraints；"
            "旁白预计时长不得超过视频总时长，文本量必须匹配目标时长。"
            "若用户修改旁白时长，full_script 必须同步改写。"
        ),
    )
    revised = _parse(CreativeBrief, data, "修改后创意方案").model_copy(
        update={
            "global_constraints": extracted_constraints,
            "av_timeline_constraints": extracted_av_constraints,
        }
    )
    return _validate_creative_revision(
        revised,
        request,
        current,
        comment,
    )


def generate_storyboard(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    api_key: str,
    task_logger: TaskLogger | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> Storyboard:
    durations = plan_shot_durations(request.duration_seconds)
    timeline = ", ".join(f"镜头{i}:{d}秒" for i, d in enumerate(durations, 1))
    system = """
你是品牌广告分镜师。根据已确认的 Creative Brief 生成 Storyboard，规划镜头目的、画面、运镜、旁白 Cue、字幕 Cue 和字幕安全区，不生成 video_prompt。
镜头必须可独立生成且保持统一视觉连续性，不依赖上一镜头最后一帧。你只负责 Cue 文本、所属 Shot 和 placement 意图，绝对不要输出、猜测或计算 start_offset、end_offset 或任何精确时间戳；Python Scheduler 会完成全部数值调度。
placement 只能是 auto、start、middle、end。它表示镜头内的语义位置意图，不是时间。字幕是精炼广告视觉文案，不要默认逐字复制旁白。不要求每个 Shot 都有旁白或字幕；不需要字幕时必须输出 subtitle_cues=[]，禁止空 text Cue。
如果 Creative narration_plan.enabled=true，所有 voiceover_cues 组合后必须覆盖 full_script 的主要内容，允许合理断句和少量自然调整；各 Cue 文本的真实预计朗读总时长必须与 target_duration_seconds 基本匹配。如果旁白关闭，voiceover_cues 必须为空。
 严格遵守 Creative.global_constraints 与 Creative.av_timeline_constraints。根据禁用窗口把内容合理分配到有足够可用时间的 Shot，但不要给出数字时间；单条 Cue 不得依赖跨 Shot 播放。
 purpose、visual、camera 只能描述真实画面，不得要求品牌名、字幕、Slogan、标题卡或其他人工文字浮现、显示、淡入、叠加。需要后期出现的文字只能写入 subtitle_cues；产品包装上真实存在的 Logo、品牌名和标签可以作为产品身份自然入镜。
Camera 优先使用视觉效果语言，避免无必要的摄影机技术数字。不要写“轻微俯角90度”这类矛盾表达；在轻微俯角和 90-degree overhead shot 中明确选择。不要写 1000fps，改写为 high-speed macro capture aesthetic 或 extreme slow-motion commercial motion。
只输出以下严格 JSON，字段不得缺失或增加：
{"total_duration":30,"shots":[{"shot_id":1,"duration":6,"purpose":"","visual":"","camera":"","voiceover_cues":[{"text":"旁白内容","placement":"middle"}],"subtitle_cues":[{"text":"字幕内容","placement":"middle","position":"bottom_center"}],"video_constraints":{"reserve_subtitle_space":true,"subtitle_safe_area":"bottom_center"}}]}
placement 只能是 auto、start、middle、end。严禁输出 start_offset、end_offset 或其他时间字段。
position/subtitle_safe_area 只能是 bottom_center、bottom_left、bottom_right、top_center、top_left、top_right、none。
无字幕的 Shot 使用 subtitle_cues=[]、reserve_subtitle_space=false、subtitle_safe_area="none"。
""".strip()
    user = (
        f"已确认 Creative Brief：{json.dumps(brief.model_dump(), ensure_ascii=False)}\n"
        f"产品需求：{request.model_dump_json()}\n"
        f"{_planning_context(request, visual_analysis_result, visual_constraints, reference_asset_context)}\n"
        f"必须严格执行的 AV Timeline Constraints："
        f"{json.dumps(brief.av_timeline_constraints.model_dump(), ensure_ascii=False)}\n"
        f"严格时长表：{timeline}。"
    )
    data = deepseek_json_request(
        api_key,
        system,
        user,
        task_logger=task_logger,
        raw_stage="storyboard",
        structure_validator=_storyboard_output_validator(request, brief),
        retry_instruction=(
            "每个 Shot 必须完整输出 voiceover_cues、subtitle_cues、video_constraints；"
            "Cue 只能包含文本、placement（字幕另含 position），不得输出 start/end 时间；"
            "若某 Shot 内容放不下，请缩短文字或重新分配到其他 Shot。"
            "人工文字出现动作必须移入 subtitle_cues。"
        ),
    )
    planning = _parse(StoryboardPlanning, data, "Storyboard Planning")
    return compile_storyboard_planning(planning, request, brief)


def revise_storyboard(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    current: Storyboard,
    comment: str,
    api_key: str,
    task_logger: TaskLogger | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
    persist_creative: Callable[[CreativeBrief], None] | None = None,
) -> Storyboard:
    durations = plan_shot_durations(request.duration_seconds)
    revised_av_constraints = merge_av_timeline_constraints(
        brief.av_timeline_constraints,
        extract_av_timeline_constraints(comment, request.duration_seconds),
    )
    constraints_changed = revised_av_constraints != brief.av_timeline_constraints
    effective_brief = (
        brief.model_copy(
            update={"av_timeline_constraints": revised_av_constraints}
        )
        if constraints_changed
        else brief
    )
    current_planning = storyboard_to_planning(current)
    system = (
        "根据用户意见修改 Storyboard。不得改变 shot_id、镜头数量和各镜头 duration；"
        "保留 voiceover_cues、subtitle_cues、video_constraints 的严格语义规划结构；"
        "只可修改 Cue 文本、所属 Shot 和 placement。placement 只能是 auto/start/middle/end；"
        "绝对不得输出或修改 start_offset、end_offset 等精确时间，Python Scheduler 会重新计算。"
        "voiceover_cues 必须覆盖 Creative 旁白主要内容，文本预计朗读总时长应匹配目标；"
        "严格遵守 Creative.global_constraints 与 Creative.av_timeline_constraints，并把内容分配到可用 Shot；"
        "Camera 使用明确的视觉效果语言，避免冲突角度和无必要的高 fps 数字；"
        "purpose、visual、camera 不得描述品牌名、字幕、Slogan、标题卡或其他人工文字浮现/显示/叠加，"
        "后期文字只能进入 subtitle_cues，包装上真实 Logo/品牌名可自然入镜；"
        "不生成 video_prompt。只输出原结构 JSON。"
    )
    user = (
        f"Creative Brief：{json.dumps(effective_brief.model_dump(), ensure_ascii=False)}\n"
        f"原 Storyboard Planning：{json.dumps(current_planning.model_dump(), ensure_ascii=False)}\n"
        f"修改意见：{comment}\n"
        f"必须严格执行的 AV Timeline Constraints："
        f"{json.dumps(effective_brief.av_timeline_constraints.model_dump(), ensure_ascii=False)}\n"
        f"{_planning_context(request, visual_analysis_result, visual_constraints, reference_asset_context)}\n"
        f"固定时长：{durations}"
    )
    data = deepseek_json_request(
        api_key,
        system,
        user,
        task_logger=task_logger,
        raw_stage="storyboard_revision",
        structure_validator=_storyboard_output_validator(request, effective_brief),
        retry_instruction=(
            "每个 Shot 必须完整输出 voiceover_cues、subtitle_cues、video_constraints；"
            "Cue 只能包含文本、placement（字幕另含 position），不得输出 start/end 时间；"
            "若某 Shot 内容放不下，请缩短文字或重新分配到其他 Shot。"
            "人工文字出现动作必须移入 subtitle_cues。"
        ),
    )
    planning = _parse(StoryboardPlanning, data, "修改后 Storyboard Planning")
    board = compile_storyboard_planning(planning, request, effective_brief)
    if constraints_changed:
        # Commit the reviewed constraint only after the revised Storyboard is valid.
        brief.av_timeline_constraints = revised_av_constraints
        if persist_creative is not None:
            persist_creative(brief)
    return board


def _extract_visual_prompt_core(video_prompt: str) -> str:
    markers = (
        "[Composition Constraint]",
        "[Global Hard Constraints]",
        "[Text Overlay Constraint]",
        "[Audio Constraint]",
        "Post-production overlay constraint:",
    )
    positions = [video_prompt.find(marker) for marker in markers if marker in video_prompt]
    return (
        video_prompt[: min(positions)].rstrip()
        if positions
        else video_prompt.strip()
    )


def extract_visual_prompt_core(video_prompt: str) -> str:
    """Return the user-editable visual core from one compiled video Prompt."""

    return _extract_visual_prompt_core(video_prompt)


def compile_manual_visual_prompt(
    visual_prompt_core: str,
    shot: StoryboardShot,
    global_constraints: GlobalConstraints | None = None,
    product_name: str | None = None,
) -> str:
    """Validate one manual core and rebuild every deterministic control block."""

    core = str(visual_prompt_core or "").strip()
    _validate_visual_prompt_core(core, shot, global_constraints, product_name)
    final_prompt = apply_video_overlay_constraints(
        core,
        shot,
        global_constraints or _empty_global_constraints(),
    )
    _validate_final_video_prompt(final_prompt, shot, product_name)
    return final_prompt


def _shot_cue_bodies(shot: StoryboardShot) -> list[str]:
    return [
        cue.text.strip()
        for cue in [*shot.subtitle_cues, *shot.voiceover_cues]
        if cue.text.strip()
    ]


def _contains_forbidden_cue_body(
    text: str, shot: StoryboardShot, product_name: str | None = None
) -> bool:
    for body in _shot_cue_bodies(shot):
        if body not in text:
            continue
        # A subtitle may equal the product name, but the same name can also be
        # a legitimate visible product identity. Overlay actions are rejected
        # separately by _OVERLAY_ACTION_PATTERN.
        if product_name and body.strip().casefold() == product_name.strip().casefold():
            continue
        return True
    return False


def _validate_visual_prompt_core(
    core: str,
    shot: StoryboardShot,
    global_constraints: GlobalConstraints | None = None,
    product_name: str | None = None,
) -> None:
    if not isinstance(core, str) or not core.strip():
        raise VideoPromptStructureError(
            "当前 Shot 的 visual_prompt_core 必须是非空字符串。",
            expected_ids=[shot.shot_id],
            actual_ids=[],
        )
    if len(core) > 12000:
        raise VideoPromptStructureError(
            "当前 Shot 的 visual_prompt_core 长度超过允许范围。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )
    if any(
        marker in core
        for marker in (
            "[Composition Constraint]",
            "[Global Hard Constraints]",
            "[Text Overlay Constraint]",
            "[Audio Constraint]",
        )
    ):
        raise VideoPromptStructureError(
            "visual_prompt_core 不得自行生成程序控制块。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )
    if _contains_forbidden_cue_body(core, shot, product_name):
        raise VideoPromptStructureError(
            "生成的核心视觉 Prompt 包含禁止进入视频模型的后期文字或旁白内容。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )
    if _OVERLAY_ACTION_PATTERN.search(core):
        raise VideoPromptStructureError(
            "生成的核心视觉 Prompt 包含人工字幕、标题卡或文字 Overlay 指令。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )
    violations = _hard_constraint_violations(
        core, global_constraints or _empty_global_constraints()
    )
    if violations:
        raise VideoPromptStructureError(
            f"Video Prompt 的 Shot {shot.shot_id} 违反用户硬约束：{violations}。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )


def _validate_single_shot_core_payload(
    data: dict,
    shot: StoryboardShot,
    global_constraints: GlobalConstraints | None = None,
    product_name: str | None = None,
) -> None:
    if set(data) != {"visual_prompt_core"}:
        raise VideoPromptStructureError(
            "单 Shot Video Prompt 顶层只能包含 visual_prompt_core 字段。",
            expected_ids=[shot.shot_id],
            actual_ids=[],
        )
    _validate_visual_prompt_core(
        data.get("visual_prompt_core"), shot, global_constraints, product_name
    )


def _validate_final_video_prompt(
    prompt: str, shot: StoryboardShot, product_name: str | None = None
) -> None:
    if not isinstance(prompt, str) or not prompt.strip():
        raise VideoPromptStructureError(
            "最终 Video Prompt 不能为空。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )
    required = (
        "[Composition Constraint]",
        "[Global Hard Constraints]",
        "[Text Overlay Constraint]",
        "[Audio Constraint]",
    )
    if any(prompt.count(marker) != 1 for marker in required):
        raise VideoPromptStructureError(
            "最终 Video Prompt 的程序控制块缺失或重复。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )
    if [prompt.index(marker) for marker in required] != sorted(
        prompt.index(marker) for marker in required
    ):
        raise VideoPromptStructureError(
            "最终 Video Prompt 的程序控制块顺序无效。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )
    if not _extract_visual_prompt_core(prompt):
        raise VideoPromptStructureError(
            "最终 Video Prompt 缺少核心视觉内容。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )
    if _contains_forbidden_cue_body(prompt, shot, product_name):
        raise VideoPromptStructureError(
            "最终 Video Prompt 不得包含字幕或旁白正文。",
            expected_ids=[shot.shot_id],
            actual_ids=[shot.shot_id],
        )


def _validate_video_prompt_payload(
    data: dict,
    board: Storyboard,
    global_constraints: GlobalConstraints | None = None,
    product_name: str | None = None,
) -> None:
    expected = [shot.shot_id for shot in board.shots]
    if set(data) != {"shots"}:
        raise VideoPromptStructureError(
            "Video Prompt 顶层只能包含 shots 字段。",
            expected_ids=expected,
            actual_ids=[],
        )
    shots = data.get("shots")
    if not isinstance(shots, list):
        raise VideoPromptStructureError(
            "Video Prompt 的 shots 必须是数组。",
            expected_ids=expected,
            actual_ids=[],
        )
    if not shots:
        raise VideoPromptStructureError(
            "Video Prompt 的 shots 不能为空。",
            expected_ids=expected,
            actual_ids=[],
        )

    actual: list[int] = []
    for index, shot in enumerate(shots, start=1):
        if not isinstance(shot, dict):
            raise VideoPromptStructureError(
                f"Video Prompt 第 {index} 个 Shot 必须是对象。",
                expected_ids=expected,
                actual_ids=actual,
            )
        if set(shot) not in (
            {"shot_id", "video_prompt"},
            {"shot_id", "visual_prompt_core", "video_prompt"},
        ):
            raise VideoPromptStructureError(
                f"Video Prompt 第 {index} 个 Shot 字段无效。",
                expected_ids=expected,
                actual_ids=actual,
            )
        shot_id = shot.get("shot_id")
        if type(shot_id) is not int or shot_id <= 0:
            raise VideoPromptStructureError(
                f"Video Prompt 第 {index} 个 shot_id 必须是正整数。",
                expected_ids=expected,
                actual_ids=actual + [shot_id],
            )
        actual.append(shot_id)
        video_prompt = shot.get("video_prompt")
        if not isinstance(video_prompt, str) or not video_prompt.strip():
            raise VideoPromptStructureError(
                f"Video Prompt 的 Shot {shot_id} 缺少非空 video_prompt。",
                expected_ids=expected,
                actual_ids=actual,
            )
        storyboard_shot = next(
            (item for item in board.shots if item.shot_id == shot_id), None
        )
        if storyboard_shot is not None:
            explicit_core = shot.get("visual_prompt_core")
            core = (
                explicit_core
                if isinstance(explicit_core, str) and explicit_core.strip()
                else _extract_visual_prompt_core(video_prompt)
            )
            _validate_visual_prompt_core(
                core, storyboard_shot, global_constraints, product_name
            )
            if "[Composition Constraint]" in video_prompt:
                _validate_final_video_prompt(
                    video_prompt, storyboard_shot, product_name
                )

    problems: list[str] = []
    if len(actual) != len(expected):
        problems.append(f"镜头数量应为 {len(expected)}，实际为 {len(actual)}")
    duplicate_ids = sorted({shot_id for shot_id in actual if actual.count(shot_id) > 1})
    if duplicate_ids:
        problems.append(f"shot_id 重复：{duplicate_ids}")
    missing_ids = [shot_id for shot_id in expected if shot_id not in actual]
    if missing_ids:
        problems.append(f"缺少 shot_id：{missing_ids}")
    extra_ids = [shot_id for shot_id in actual if shot_id not in expected]
    if extra_ids:
        problems.append(f"多出 shot_id：{extra_ids}")
    if not duplicate_ids and not missing_ids and not extra_ids and actual != expected:
        problems.append("shot_id 顺序与 Storyboard 不一致")
    if problems:
        raise VideoPromptStructureError(
            "Video Prompt 结构无效：" + "；".join(problems),
            expected_ids=expected,
            actual_ids=actual,
        )


def _video_prompt_retry_instruction(board: Storyboard) -> str:
    expected = [shot.shot_id for shot in board.shots]
    return (
        f"必须严格输出 Shot IDs：{expected}。"
        "每个 Shot 必须是 shots 数组中的独立对象；shot_id 必须唯一、顺序一致，"
        "不得遗漏或增加 Shot；每个 video_prompt 必须是非空字符串。"
    )


def _validate_prompt_plan(
    plan: VideoPromptPlan,
    board: Storyboard,
    global_constraints: GlobalConstraints | None = None,
    product_name: str | None = None,
) -> VideoPromptPlan:
    expected = [shot.shot_id for shot in board.shots]
    actual = [shot.shot_id for shot in plan.shots]
    try:
        _validate_video_prompt_payload(
            plan.model_dump(), board, global_constraints, product_name
        )
    except VideoPromptStructureError as exc:
        raise StoryboardError(str(exc)) from exc
    if actual != expected:  # Defensive guard if the raw validator changes later.
        raise StoryboardError(
            "Video Prompt 的 shot_id 必须与 Storyboard 完全一致。"
        )
    return plan


_LEGACY_OVERLAY_CONSTRAINT_MARKER = "Post-production overlay constraint:"
_DETERMINISTIC_CONTROL_MARKERS = (
    "[Composition Constraint]",
    "[Global Hard Constraints]",
    "[Text Overlay Constraint]",
    "[Audio Constraint]",
    _LEGACY_OVERLAY_CONSTRAINT_MARKER,
)
_SAFE_AREA_LABELS = {
    "bottom_center": "lower-center",
    "bottom_left": "lower-left",
    "bottom_right": "lower-right",
    "top_center": "upper-center",
    "top_left": "upper-left",
    "top_right": "upper-right",
}


def _video_planning_context(
    brief: CreativeBrief, board: Storyboard
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build visual-only LLM context; never expose cue text to video prompting."""

    brief_context = {
        "creative_concept": brief.creative_concept,
        "target_audience": brief.target_audience,
        "key_message": brief.key_message,
        "visual_direction": brief.visual_direction,
        "narrative_arc": brief.narrative_arc,
        "global_constraints": brief.global_constraints.model_dump(),
    }
    board_context = {
        "total_duration": board.total_duration,
        "shots": [
            {
                "shot_id": shot.shot_id,
                "duration": shot.duration,
                "purpose": shot.purpose,
                "visual": shot.visual,
                "camera": shot.camera,
                "video_constraints": shot.video_constraints.model_dump(),
            }
            for shot in board.shots
        ],
    }
    return brief_context, board_context


def apply_video_overlay_constraints(
    prompt: str,
    shot: StoryboardShot,
    global_constraints: GlobalConstraints | None = None,
) -> str:
    """Append deterministic control blocks after the LLM's core visual prompt."""

    cut_positions = [
        prompt.find(marker)
        for marker in _DETERMINISTIC_CONTROL_MARKERS
        if prompt.find(marker) >= 0
    ]
    base_prompt = normalize_camera_language(
        prompt[: min(cut_positions)].rstrip() if cut_positions else prompt.rstrip()
    )
    composition_lines = ["[Composition Constraint]"]
    if shot.video_constraints.reserve_subtitle_space:
        region = _SAFE_AREA_LABELS[shot.video_constraints.subtitle_safe_area]
        composition_lines.extend(
            [
                f"Reserve a clean, low-detail {region} region for a future "
                "post-production subtitle overlay.",
                "Keep the primary product, face, logo, and other important visual "
                "elements away from this subtitle-safe region.",
            ]
        )
    else:
        composition_lines.append(
            "No dedicated subtitle-safe region is required for this shot."
        )

    hard_constraints = global_constraints or _empty_global_constraints()
    hard_constraint_lines = ["[Global Hard Constraints]"]
    if hard_constraints.must or hard_constraints.must_not:
        must_instructions = {
            "preserve real logo": "Preserve the real brand logo exactly as provided.",
            "preserve product packaging": "Preserve the product packaging design and shape.",
            "preserve product color": "Preserve the product's original color.",
        }
        hard_constraint_lines.extend(
            must_instructions.get(item, f"Must preserve: {item}.")
            for item in hard_constraints.must
        )
        hard_constraint_lines.extend(
            _HARD_CONSTRAINT_INSTRUCTIONS.get(item, f"Must not include: {item}.")
            for item in hard_constraints.must_not
        )
    else:
        hard_constraint_lines.append(
            "No additional user-defined global hard constraints."
        )

    text_overlay_lines = [
        "[Text Overlay Constraint]",
        "Do not generate subtitles, captions, title cards, slogans, UI text, "
        "placeholder text, or artificial written overlays. Real logo, packaging "
        "text, and product labels that naturally exist on the referenced product "
        "should remain intact and must not be invented or deformed.",
    ]
    audio_lines = [
        "[Audio Constraint]",
        "Generate visual footage only; do not generate voice-over, speech, sound "
        "effects, soundtrack, or BGM.",
    ]
    sections = (
        composition_lines,
        hard_constraint_lines,
        text_overlay_lines,
        audio_lines,
    )
    return base_prompt + "\n\n" + "\n\n".join(
        "\n".join(section) for section in sections
    )


def _apply_video_prompt_constraints(
    plan: VideoPromptPlan,
    board: Storyboard,
    global_constraints: GlobalConstraints | None = None,
) -> VideoPromptPlan:
    shots_by_id = {shot.shot_id: shot for shot in board.shots}
    results: list[ShotVideoPrompt] = []
    for item in plan.shots:
        shot = shots_by_id[item.shot_id]
        core = (item.visual_prompt_core or _extract_visual_prompt_core(item.video_prompt)).strip()
        _validate_visual_prompt_core(core, shot, global_constraints)
        final_prompt = apply_video_overlay_constraints(core, shot, global_constraints)
        _validate_final_video_prompt(final_prompt, shot)
        results.append(
            ShotVideoPrompt(
                shot_id=item.shot_id,
                visual_prompt_core=core,
                video_prompt=final_prompt,
            )
        )
    return VideoPromptPlan(shots=results)


_VIDEO_PROMPT_SCHEMA_VERSION = 2


def _video_prompt_progress_fingerprint(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    board: Storyboard,
    *,
    operation: str = "generate",
    current: VideoPromptPlan | None = None,
    revision_comment: str | None = None,
) -> str:
    payload = {
        "request": {
            "product_name": request.product_name,
            "product_description": request.product_description,
            "user_notes": request.user_notes,
            "video_style": request.video_style,
            "video_purpose": request.video_purpose,
        },
        "visual_direction": brief.visual_direction,
        "global_constraints": brief.global_constraints.model_dump(),
        "storyboard": board.model_dump(),
    }
    if operation != "generate":
        payload["operation"] = operation
        # The current Prompt set identifies one review round without exposing
        # it in durable Web Task JSON. Regenerate never sends this content to
        # the provider; it is used only to distinguish/resume Core progress.
        payload["current_video_prompts"] = (
            current.model_dump() if current is not None else None
        )
    if operation == "revise":
        payload["revision_comment"] = str(revision_comment or "").strip()
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _new_video_prompt_progress(
    fingerprint: str, board: Storyboard, *, operation: str = "generate"
) -> dict[str, Any]:
    return {
        "video_prompt_schema_version": _VIDEO_PROMPT_SCHEMA_VERSION,
        "storyboard_fingerprint": fingerprint,
        "operation": operation,
        "status": "RUNNING",
        "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "shots": [_new_video_prompt_progress_entry(shot.shot_id) for shot in board.shots],
    }


def _new_video_prompt_progress_entry(shot_id: int) -> dict[str, Any]:
    return {
        "shot_id": shot_id,
        "status": "NOT_STARTED",
        "visual_prompt_core": None,
        "video_prompt": None,
        "generation_runs": 0,
        "last_error": None,
    }


def _save_video_prompt_progress(
    progress_path: Path | None, progress: dict[str, Any]
) -> None:
    if progress_path is None:
        return
    progress_path.parent.mkdir(parents=True, exist_ok=True)
    progress["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    temporary = progress_path.with_suffix(progress_path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(progress, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(progress_path)
    except OSError as exc:
        raise StoryboardError(f"Video Prompt 中间进度保存失败：{exc}") from exc


def _load_video_prompt_progress(
    progress_path: Path | None,
    fingerprint: str,
    board: Storyboard,
    *,
    force_regenerate: bool,
    operation: str = "generate",
) -> dict[str, Any]:
    if progress_path is None or force_regenerate or not progress_path.exists():
        return _new_video_prompt_progress(
            fingerprint, board, operation=operation
        )
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoryboardError(
            "Video Prompt 中间进度无法读取；为避免重复调用已停止恢复："
            f"{exc}"
        ) from exc
    if not isinstance(progress, dict):
        raise StoryboardError("Video Prompt 中间进度格式无效。")
    if (
        progress.get("video_prompt_schema_version") != _VIDEO_PROMPT_SCHEMA_VERSION
        or progress.get("storyboard_fingerprint") != fingerprint
    ):
        return _new_video_prompt_progress(
            fingerprint, board, operation=operation
        )
    stored_operation = str(progress.get("operation") or "generate")
    if stored_operation != operation:
        return _new_video_prompt_progress(
            fingerprint, board, operation=operation
        )
    # A published review action is terminal. A later explicit click is a new
    # paid operation even when its inputs happen to be textually identical.
    # COMPLETED is intentionally resumable: all Shots may be cached while the
    # atomic canonical/version commit still needs to be retried.
    if operation != "generate" and progress.get("status") == "PUBLISHED":
        return _new_video_prompt_progress(
            fingerprint, board, operation=operation
        )
    entries = progress.get("shots")
    if not isinstance(entries, list):
        raise StoryboardError("Video Prompt 中间进度缺少 shots。")
    indexed = {
        item.get("shot_id"): item
        for item in entries
        if isinstance(item, dict) and type(item.get("shot_id")) is int
    }
    progress["shots"] = [
        indexed.get(shot.shot_id)
        or _new_video_prompt_progress_entry(shot.shot_id)
        for shot in board.shots
    ]
    return progress


def _single_shot_prompt_context(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    shot: StoryboardShot,
    reference_asset_context: dict[str, Any] | None,
    *,
    current_core: str | None = None,
    revision_comment: str | None = None,
) -> str:
    reference = reference_asset_context or {}
    asset_count = int(reference.get("asset_count") or 0)
    reference_note = (
        "A project reference asset is available and will be provided to the "
        "video generation model separately. Do not claim to have seen it or "
        "invent its visual details."
        if asset_count > 0
        else "No project reference asset is available."
    )
    payload: dict[str, Any] = {
        "product": {
            "name": request.product_name,
            "description": request.product_description,
        },
        "project_visual_requirements": {
            "video_style": request.video_style,
            "video_purpose": request.video_purpose,
            "user_notes": request.user_notes,
            "visual_direction": brief.visual_direction,
            "global_constraints": brief.global_constraints.model_dump(),
            "reference_asset_note": reference_note,
        },
        "current_shot": {
            "duration": shot.duration,
            "purpose": shot.purpose,
            "visual": shot.visual,
            "camera": shot.camera,
            "video_constraints": shot.video_constraints.model_dump(),
        },
    }
    if current_core is not None:
        payload["current_visual_prompt_core"] = current_core
    if revision_comment is not None:
        payload["revision_request"] = revision_comment
    return json.dumps(payload, ensure_ascii=False)


def _request_single_shot_visual_core(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    shot: StoryboardShot,
    api_key: str,
    task_logger: TaskLogger | None,
    reference_asset_context: dict[str, Any] | None,
    *,
    current_core: str | None = None,
    revision_comment: str | None = None,
    raw_stage_prefix: str = "video_prompt",
) -> str:
    if revision_comment is None:
        task = "Generate one visual prompt core for the current Shot only."
    else:
        task = (
            "Revise only the current Shot visual prompt core according to the "
            "user's revision request."
        )
    system = f"""
You are a professional video-generation prompt engineer. {task}
Describe only visible subjects, environment, action, camera, lighting, color, material, and commercial visual quality. Preserve the product identity and do not invent product claims.
Do not output or infer subtitle text, captions, title cards, slogans, UI text, voice-over wording, dialogue, sound effects, soundtrack, or BGM. Real logos, packaging text, and product labels naturally belonging to the referenced product may remain intact.
Do not write Composition Constraint, Global Hard Constraints, Text Overlay Constraint, Audio Constraint, shot_id, or any other control field. Python appends all control blocks and binds shot_id deterministically.
Return exactly one JSON object with one field:
{{"visual_prompt_core":""}}
""".strip()
    validator = lambda data: _validate_single_shot_core_payload(
        data, shot, brief.global_constraints, request.product_name
    )
    data = deepseek_json_request(
        api_key,
        system,
        _single_shot_prompt_context(
            request,
            brief,
            shot,
            reference_asset_context,
            current_core=current_core,
            revision_comment=revision_comment,
        ),
        task_logger=task_logger,
        raw_stage=f"{raw_stage_prefix}_shot_{shot.shot_id:02d}",
        structure_validator=validator,
        retry_instruction=(
            "只输出单字段 visual_prompt_core；不要输出 shots、shot_id 或控制块。"
            "核心内容只描述可见画面，不得包含人工字幕、标题卡、旁白或音频指令，"
            "并必须遵守用户全局硬约束。"
        ),
        retry_preamble=(
            "严格遵守本次请求指定的单字段 JSON Schema；禁止重复 JSON key，"
            "不要添加未要求的字段。"
        ),
        log_fields={"pipeline_stage": "VIDEO_PROMPT", "shot_id": shot.shot_id},
    )
    result = _parse(ShotVisualPromptCore, data, f"Shot {shot.shot_id} Video Prompt")
    return result.visual_prompt_core.strip()


def generate_video_prompts(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    board: Storyboard,
    api_key: str,
    task_logger: TaskLogger | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
    progress_path: Path | None = None,
    force_regenerate: bool = False,
    operation: str = "generate",
    current: VideoPromptPlan | None = None,
    revision_comment: str | None = None,
) -> VideoPromptPlan:
    del visual_analysis_result, visual_constraints
    if operation not in {"generate", "revise", "regenerate"}:
        raise StoryboardError("未知 Video Prompt 生成操作。")
    normalized_comment = str(revision_comment or "").strip()
    if operation == "revise" and not normalized_comment:
        raise StoryboardError("Video Prompt 修改意见不能为空。")
    if operation in {"revise", "regenerate"}:
        if current is None:
            raise StoryboardError("Video Prompt 修改缺少当前正式方案。")
        _validate_prompt_plan(
            current,
            board,
            brief.global_constraints,
            request.product_name,
        )

    fingerprint = _video_prompt_progress_fingerprint(
        request,
        brief,
        board,
        operation=operation,
        current=current,
        revision_comment=normalized_comment if operation == "revise" else None,
    )
    progress = _load_video_prompt_progress(
        progress_path,
        fingerprint,
        board,
        force_regenerate=force_regenerate,
        operation=operation,
    )
    progress["status"] = "RUNNING"
    _save_video_prompt_progress(progress_path, progress)
    results: list[ShotVideoPrompt] = []
    current_by_id = (
        {item.shot_id: item for item in current.shots}
        if current is not None
        else {}
    )
    for shot, entry in zip(board.shots, progress["shots"], strict=True):
        cached_core = entry.get("visual_prompt_core")
        if entry.get("status") == "COMPLETED" and isinstance(cached_core, str):
            _validate_visual_prompt_core(
                cached_core, shot, brief.global_constraints, request.product_name
            )
            final_prompt = apply_video_overlay_constraints(
                cached_core, shot, brief.global_constraints
            )
            _validate_final_video_prompt(final_prompt, shot, request.product_name)
            results.append(
                ShotVideoPrompt(
                    shot_id=shot.shot_id,
                    visual_prompt_core=cached_core,
                    video_prompt=final_prompt,
                )
            )
            if task_logger:
                task_logger.event(
                    "VIDEO_PROMPT_SHOT_RESUMED",
                    stage="VIDEO_PROMPT",
                    shot_id=shot.shot_id,
                )
            continue

        entry["status"] = "RUNNING"
        entry["generation_runs"] = int(entry.get("generation_runs") or 0) + 1
        entry["last_error"] = None
        _save_video_prompt_progress(progress_path, progress)
        if task_logger:
            task_logger.event(
                "VIDEO_PROMPT_SHOT_STARTED",
                stage="VIDEO_PROMPT",
                shot_id=shot.shot_id,
            )
        try:
            core = _request_single_shot_visual_core(
                request,
                brief,
                shot,
                api_key,
                task_logger,
                reference_asset_context,
                current_core=(
                    (
                        current_by_id[shot.shot_id].visual_prompt_core
                        or _extract_visual_prompt_core(
                            current_by_id[shot.shot_id].video_prompt
                        )
                    )
                    if operation == "revise"
                    else None
                ),
                revision_comment=(
                    normalized_comment if operation == "revise" else None
                ),
                raw_stage_prefix=(
                    "video_prompt_revision"
                    if operation == "revise"
                    else (
                        "video_prompt_regeneration"
                        if operation == "regenerate"
                        else "video_prompt"
                    )
                ),
            )
            final_prompt = apply_video_overlay_constraints(
                core, shot, brief.global_constraints
            )
            _validate_final_video_prompt(final_prompt, shot, request.product_name)
        except Exception as exc:
            entry["status"] = "FAILED"
            entry["last_error"] = {
                "type": type(exc).__name__,
                "message": str(exc),
            }
            progress["status"] = "FAILED"
            _save_video_prompt_progress(progress_path, progress)
            if task_logger:
                task_logger.event(
                    "VIDEO_PROMPT_SHOT_FAILED",
                    stage="VIDEO_PROMPT",
                    shot_id=shot.shot_id,
                    error=exc,
                )
            raise
        entry.update(
            {
                "status": "COMPLETED",
                "visual_prompt_core": core,
                "video_prompt": final_prompt,
                "last_error": None,
            }
        )
        _save_video_prompt_progress(progress_path, progress)
        results.append(
            ShotVideoPrompt(
                shot_id=shot.shot_id,
                visual_prompt_core=core,
                video_prompt=final_prompt,
            )
        )
        if task_logger:
            task_logger.event(
                "VIDEO_PROMPT_SHOT_COMPLETED",
                stage="VIDEO_PROMPT",
                shot_id=shot.shot_id,
            )
    plan = VideoPromptPlan(shots=results)
    _validate_prompt_plan(
        plan, board, brief.global_constraints, request.product_name
    )
    progress["status"] = "COMPLETED"
    _save_video_prompt_progress(progress_path, progress)
    return plan


def revise_video_prompts(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    board: Storyboard,
    current: VideoPromptPlan,
    comment: str,
    api_key: str,
    task_logger: TaskLogger | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
    progress_path: Path | None = None,
) -> VideoPromptPlan:
    return generate_video_prompts(
        request,
        brief,
        board,
        api_key,
        task_logger,
        visual_analysis_result,
        visual_constraints,
        reference_asset_context,
        progress_path=progress_path,
        operation="revise",
        current=current,
        revision_comment=comment,
    )


def regenerate_video_prompts(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    board: Storyboard,
    current: VideoPromptPlan,
    api_key: str,
    task_logger: TaskLogger | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
    progress_path: Path | None = None,
) -> VideoPromptPlan:
    """Generate a clean per-Shot Prompt set with resumable operation cache."""

    return generate_video_prompts(
        request,
        brief,
        board,
        api_key,
        task_logger,
        visual_analysis_result,
        visual_constraints,
        reference_asset_context,
        progress_path=progress_path,
        operation="regenerate",
        current=current,
    )


def mark_video_prompt_progress_published(progress_path: Path | None) -> None:
    """Close one review operation so a later explicit action starts fresh."""

    if progress_path is None or not progress_path.exists():
        return
    try:
        progress = json.loads(progress_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise StoryboardError(
            "Video Prompt 中间进度无法标记为已发布。"
        ) from exc
    if not isinstance(progress, dict):
        raise StoryboardError("Video Prompt 中间进度格式无效。")
    progress["status"] = "PUBLISHED"
    _save_video_prompt_progress(progress_path, progress)


def revise_shot_video_prompt(
    request: ProductVideoRequest,
    brief: CreativeBrief,
    shot: StoryboardShot,
    current_prompt: str,
    comment: str,
    api_key: str,
    task_logger: TaskLogger | None = None,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> str:
    """Use DeepSeek to revise only one active Shot prompt."""
    del visual_analysis_result, visual_constraints
    core = _request_single_shot_visual_core(
        request,
        brief,
        shot,
        api_key,
        task_logger,
        reference_asset_context,
        current_core=_extract_visual_prompt_core(current_prompt),
        revision_comment=comment,
        raw_stage_prefix="shot_prompt_revision",
    )
    final_prompt = apply_video_overlay_constraints(
        core, shot, brief.global_constraints
    )
    _validate_final_video_prompt(final_prompt, shot, request.product_name)
    return final_prompt
