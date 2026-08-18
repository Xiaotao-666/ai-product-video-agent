"""Deterministic whole-track voice timing calibration.

This module only compares stored planning metadata with a measured WAV
duration.  It never calls a provider and never mutates audio.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any


CALIBRATION_PASS = "PASS"
CALIBRATION_WARNING = "WARNING"
CALIBRATION_OUT_OF_TOLERANCE = "OUT_OF_TOLERANCE"
CALIBRATION_OUT_OF_BOUNDS = "OUT_OF_BOUNDS"
CALIBRATION_NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True)
class CalibrationThresholds:
    """Absolute duration-difference ratios used for status classification."""

    pass_ratio: float = 0.10
    warning_ratio: float = 0.20

    def __post_init__(self) -> None:
        if not (0 <= self.pass_ratio <= self.warning_ratio):
            raise ValueError("Audio Calibration 阈值配置无效。")


def _finite_number(value: Any, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是有限数字。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是有限数字。") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} 必须是有限数字。")
    return number


def _optional_number(value: Any, *, field: str) -> float | None:
    if value is None:
        return None
    return _finite_number(value, field=field)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 6)


def _source_fingerprint(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return {
        **payload,
        "fingerprint_sha256": hashlib.sha256(canonical).hexdigest(),
    }


def calibrate_voice_timeline(
    *,
    script_source: str,
    actual_audio_duration: float,
    planned_narration_duration: float | None = None,
    planned_voice_span: float | None = None,
    planned_first_voice_start: float | None = None,
    total_video_duration: float | None = None,
    source_storyboard_path: str | None = None,
    storyboard_revision: Any = None,
    voice_version: int | None = None,
    audio_sha256: str | None = None,
    calibrated_at: str | None = None,
    thresholds: CalibrationThresholds = CalibrationThresholds(),
) -> dict[str, Any]:
    """Return a stable calibration snapshot for one immutable Voice Version."""

    source = str(script_source or "manual")
    actual = _finite_number(
        actual_audio_duration, field="actual_audio_duration"
    )
    if actual < 0:
        raise ValueError("actual_audio_duration 不能小于 0。")
    planned = _optional_number(
        planned_narration_duration, field="planned_narration_duration"
    )
    span = _optional_number(planned_voice_span, field="planned_voice_span")
    start = _optional_number(
        planned_first_voice_start, field="planned_first_voice_start"
    )
    total = _optional_number(total_video_duration, field="total_video_duration")

    has_storyboard_plan = source in {
        "compiled_storyboard",
        "storyboard_edited",
    } and planned is not None
    voice_track_start = start if has_storyboard_plan and start is not None else 0.0
    if voice_track_start < 0:
        raise ValueError("planned_first_voice_start 不能小于 0。")
    actual_voice_end = voice_track_start + actual

    difference: float | None = None
    ratio: float | None = None
    if not has_storyboard_plan:
        status = CALIBRATION_NOT_APPLICABLE
    else:
        if planned is None or planned <= 0:
            raise ValueError("planned_narration_duration 必须大于 0。")
        difference = actual - planned
        ratio = difference / planned
        if total is not None and actual_voice_end > total + 1e-9:
            status = CALIBRATION_OUT_OF_BOUNDS
        elif abs(ratio) <= thresholds.pass_ratio + 1e-12:
            status = CALIBRATION_PASS
        elif abs(ratio) <= thresholds.warning_ratio + 1e-12:
            status = CALIBRATION_WARNING
        else:
            status = CALIBRATION_OUT_OF_TOLERANCE

    fingerprint_input = {
        "source_storyboard_path": source_storyboard_path,
        "storyboard_revision": storyboard_revision,
        "voice_version": voice_version,
        "audio_sha256": audio_sha256,
        "planned_first_voice_start": _rounded(start),
        "planned_narration_duration": _rounded(planned),
    }
    return {
        "timing_mode": "whole_track",
        "status": status,
        "planned_narration_duration": _rounded(planned),
        "planned_voice_span": _rounded(span),
        "actual_audio_duration": _rounded(actual),
        "voice_track_start": _rounded(voice_track_start),
        "actual_voice_end": _rounded(actual_voice_end),
        "total_video_duration": _rounded(total),
        "duration_difference_seconds": _rounded(difference),
        "duration_difference_ratio": _rounded(ratio),
        "cue_level_alignment": False,
        "script_matches_storyboard": (
            True
            if source == "compiled_storyboard"
            else False
            if source == "storyboard_edited"
            else None
        ),
        "calibrated_at": calibrated_at,
        "source_fingerprint": _source_fingerprint(fingerprint_input),
    }
