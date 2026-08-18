"""Deterministic AV cue scheduling for Storyboard planning output.

This module is deliberately provider-free: it performs no network or LLM calls and
returns the same compiled timeline for the same input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal


Placement = Literal["auto", "start", "middle", "end"]

DEFAULT_SHOT_EDGE_PADDING_SECONDS = 0.2
DEFAULT_CUE_GAP_SECONDS = 0.15
MIN_SUBTITLE_DURATION_SECONDS = 1.2
MAX_SUBTITLE_DURATION_SECONDS = 4.5


class TimelineScheduleError(RuntimeError):
    """Raised when semantic cue planning cannot fit a legal deterministic timeline."""

    code = "SCHEDULE_UNSATISFIABLE"

    def __init__(self, shot_id: int, track: str, reason: str) -> None:
        self.shot_id = shot_id
        self.track = track
        self.reason = reason
        super().__init__(
            f"{self.code}: Shot {shot_id} {track} content {reason}. "
            "Shorten the content or redistribute it across shots. "
            "Do not provide start/end timestamps."
        )


def estimate_narration_duration(script: str) -> float:
    """Estimate natural ad-read duration from content and punctuation."""

    text = script or ""
    chinese_chars = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", text))
    english_words = len(re.findall(r"\b[A-Za-z]+(?:['’-][A-Za-z]+)?\b", text))
    light_pauses = len(re.findall(r"[，,、：:]", text))
    strong_pauses = len(re.findall(r"[。！？!?；;]", text))
    ellipses = len(re.findall(r"(?:……|\.\.\.)", text))
    duration = (
        chinese_chars / 3.5
        + english_words / 2.5
        + light_pauses * 0.18
        + strong_pauses * 0.35
        + ellipses * 0.45
    )
    return round(duration, 2)


def estimate_subtitle_duration(text: str) -> float:
    """Estimate a readable subtitle duration with stable first-version bounds."""

    value = text or ""
    chinese_chars = len(re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]", value))
    words = len(re.findall(r"\b[A-Za-z0-9]+(?:['’-][A-Za-z0-9]+)?\b", value))
    punctuation = len(re.findall(r"[，,。！？!?；;：:]", value))
    estimated = chinese_chars / 4.0 + words / 2.8 + punctuation * 0.12 + 0.35
    return round(
        min(MAX_SUBTITLE_DURATION_SECONDS, max(MIN_SUBTITLE_DURATION_SECONDS, estimated)),
        2,
    )


@dataclass(frozen=True)
class _ScheduledCue:
    start: float
    end: float


def _subtract_intervals(
    segments: list[tuple[float, float]],
    blocked: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    result = segments
    for block_start, block_end in sorted(blocked):
        next_result: list[tuple[float, float]] = []
        for start, end in result:
            if block_end <= start or block_start >= end:
                next_result.append((start, end))
                continue
            if block_start > start:
                next_result.append((start, min(block_start, end)))
            if block_end < end:
                next_result.append((max(block_end, start), end))
        result = next_result
    return [(start, end) for start, end in result if end - start > 1e-6]


def _available_segments(
    *,
    shot_start: float,
    shot_end: float,
    track: str,
    forbidden_windows: list[dict[str, Any]],
    edge_padding: float,
) -> list[tuple[float, float]]:
    base_start = shot_start + edge_padding
    base_end = shot_end - edge_padding
    if base_start >= base_end:
        return []
    blocked = [
        (max(base_start, float(window["start"])), min(base_end, float(window["end"])))
        for window in forbidden_windows
        if track in window.get("tracks", [])
        and float(window["start"]) < base_end
        and float(window["end"]) > base_start
    ]
    return _subtract_intervals([(base_start, base_end)], blocked)


def _free_segments(
    available: list[tuple[float, float]],
    occupied: list[_ScheduledCue],
    cue_gap: float,
) -> list[tuple[float, float]]:
    blocked = [(cue.start - cue_gap, cue.end + cue_gap) for cue in occupied]
    return _subtract_intervals(available, blocked)


def _choose_slot(
    *,
    free_segments: list[tuple[float, float]],
    duration: float,
    placement: Placement,
    shot_start: float,
    shot_end: float,
    cue_index: int,
    cue_count: int,
) -> _ScheduledCue | None:
    candidates: list[tuple[float, float]] = []
    if placement == "start":
        target_center = shot_start
    elif placement == "end":
        target_center = shot_end
    elif placement == "middle":
        target_center = (shot_start + shot_end) / 2
    else:
        target_center = shot_start + (shot_end - shot_start) * (
            (cue_index + 1) / (cue_count + 1)
        )

    for segment_start, segment_end in free_segments:
        if segment_end - segment_start + 1e-6 < duration:
            continue
        if placement == "start":
            start = segment_start
        elif placement == "end":
            start = segment_end - duration
        else:
            start = min(
                max(target_center - duration / 2, segment_start),
                segment_end - duration,
            )
        candidates.append((start, start + duration))
    if not candidates:
        return None
    if placement == "start":
        start, end = min(candidates, key=lambda item: item[0])
    elif placement == "end":
        start, end = max(candidates, key=lambda item: item[1])
    else:
        start, end = min(
            candidates,
            key=lambda item: (abs(((item[0] + item[1]) / 2) - target_center), item[0]),
        )
    return _ScheduledCue(start=round(start, 3), end=round(end, 3))


def _schedule_track(
    *,
    cues: list[dict[str, Any]],
    shot_id: int,
    shot_start: float,
    shot_end: float,
    track: Literal["voiceover", "subtitle"],
    forbidden_windows: list[dict[str, Any]],
    edge_padding: float,
    cue_gap: float,
) -> list[dict[str, Any]]:
    available = _available_segments(
        shot_start=shot_start,
        shot_end=shot_end,
        track=track,
        forbidden_windows=forbidden_windows,
        edge_padding=edge_padding,
    )
    occupied: list[_ScheduledCue] = []
    compiled: list[dict[str, Any]] = []
    for index, cue in enumerate(cues):
        duration = (
            estimate_narration_duration(cue["text"])
            if track == "voiceover"
            else estimate_subtitle_duration(cue["text"])
        )
        if duration <= 0:
            raise TimelineScheduleError(shot_id, track, "has no readable duration")
        slot = _choose_slot(
            free_segments=_free_segments(available, occupied, cue_gap),
            duration=duration,
            placement=cue["placement"],
            shot_start=shot_start,
            shot_end=shot_end,
            cue_index=index,
            cue_count=len(cues),
        )
        if slot is None and edge_padding > 0:
            # Edge padding is a preferred breathing space, not a reason to reject
            # otherwise legal content. Forbidden windows and Shot boundaries remain hard.
            fallback_available = _available_segments(
                shot_start=shot_start,
                shot_end=shot_end,
                track=track,
                forbidden_windows=forbidden_windows,
                edge_padding=0.0,
            )
            slot = _choose_slot(
                free_segments=_free_segments(fallback_available, occupied, cue_gap),
                duration=duration,
                placement=cue["placement"],
                shot_start=shot_start,
                shot_end=shot_end,
                cue_index=index,
                cue_count=len(cues),
            )
        if slot is None:
            raise TimelineScheduleError(
                shot_id,
                track,
                "is too long for the available window",
            )
        occupied.append(slot)
        output = {
            "text": cue["text"],
            "start_offset": round(slot.start - shot_start, 3),
            "end_offset": round(slot.end - shot_start, 3),
        }
        if track == "subtitle":
            output["position"] = cue["position"]
        compiled.append(output)
    return sorted(compiled, key=lambda item: (item["start_offset"], item["end_offset"]))


def schedule_av_timeline(
    planning: dict[str, Any],
    av_timeline_constraints: dict[str, Any] | None = None,
    *,
    shot_edge_padding: float = DEFAULT_SHOT_EDGE_PADDING_SECONDS,
    cue_gap: float = DEFAULT_CUE_GAP_SECONDS,
) -> dict[str, Any]:
    """Compile semantic cue placement into exact Shot-local offsets."""

    if shot_edge_padding < 0 or cue_gap < 0:
        raise ValueError("Scheduler padding and cue gap must be non-negative.")
    forbidden_windows = (av_timeline_constraints or {}).get("forbidden_windows", [])
    compiled_shots: list[dict[str, Any]] = []
    shot_start = 0.0
    for shot in planning["shots"]:
        shot_id = int(shot["shot_id"])
        shot_end = shot_start + float(shot["duration"])
        compiled_shots.append(
            {
                "shot_id": shot_id,
                "duration": shot["duration"],
                "purpose": shot["purpose"],
                "visual": shot["visual"],
                "camera": shot["camera"],
                "voiceover_cues": _schedule_track(
                    cues=shot["voiceover_cues"],
                    shot_id=shot_id,
                    shot_start=shot_start,
                    shot_end=shot_end,
                    track="voiceover",
                    forbidden_windows=forbidden_windows,
                    edge_padding=shot_edge_padding,
                    cue_gap=cue_gap,
                ),
                "subtitle_cues": _schedule_track(
                    cues=shot["subtitle_cues"],
                    shot_id=shot_id,
                    shot_start=shot_start,
                    shot_end=shot_end,
                    track="subtitle",
                    forbidden_windows=forbidden_windows,
                    edge_padding=shot_edge_padding,
                    cue_gap=cue_gap,
                ),
                "video_constraints": shot["video_constraints"],
            }
        )
        shot_start = shot_end
    return {
        "total_duration": planning["total_duration"],
        "shots": compiled_shots,
    }
