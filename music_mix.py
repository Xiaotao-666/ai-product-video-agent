"""Deterministic post-production music mix settings and render planning.

The music asset remains immutable.  This module stores user-controlled mix
settings in ``project.json`` and derives one render-only plan from already
calibrated Voice timing plus probed media durations.
"""

from __future__ import annotations

import math
from copy import deepcopy
from typing import Any


DEFAULT_MUSIC_MIX_SETTINGS: dict[str, Any] = {
    "base_volume": 0.25,
    "ducking_enabled": True,
    "ducking_ratio": 0.40,
    "duck_attack_seconds": 0.25,
    "duck_release_seconds": 0.35,
    "fade_in_seconds": 0.8,
    "fade_out_seconds": 1.2,
    "loop_music": False,
}


class MusicMixError(ValueError):
    """Raised when mix settings or media timing cannot be rendered safely."""


def _finite_number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise MusicMixError(f"{field} 必须是有限数字。")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise MusicMixError(f"{field} 必须是有限数字。") from exc
    if not math.isfinite(number):
        raise MusicMixError(f"{field} 必须是有限数字。")
    return number


def normalize_music_mix_settings(
    value: Any,
    *,
    legacy_base_volume: float | None = None,
) -> dict[str, Any]:
    """Return validated settings while preserving a legacy asset volume."""

    raw = dict(value) if isinstance(value, dict) else {}
    defaults = deepcopy(DEFAULT_MUSIC_MIX_SETTINGS)
    if legacy_base_volume is not None and "base_volume" not in raw:
        defaults["base_volume"] = legacy_base_volume
    normalized = {**defaults, **raw}

    base = _finite_number(normalized["base_volume"], "base_volume")
    ratio = _finite_number(normalized["ducking_ratio"], "ducking_ratio")
    if not 0.0 <= base <= 1.0:
        raise MusicMixError("base_volume 必须位于 0.0 到 1.0 之间。")
    if not 0.0 <= ratio <= 1.0:
        raise MusicMixError("ducking_ratio 必须位于 0.0 到 1.0 之间。")

    nonnegative_fields = (
        "duck_attack_seconds",
        "duck_release_seconds",
        "fade_in_seconds",
        "fade_out_seconds",
    )
    for field in nonnegative_fields:
        number = _finite_number(normalized[field], field)
        if number < 0:
            raise MusicMixError(f"{field} 不能小于 0。")
        normalized[field] = number

    normalized["base_volume"] = base
    normalized["ducking_ratio"] = ratio
    normalized["ducking_enabled"] = bool(normalized["ducking_enabled"])
    normalized["loop_music"] = bool(normalized["loop_music"])
    if normalized["loop_music"]:
        raise MusicMixError("Phase 2.5 不支持 Music Loop，请保持 loop_music=false。")
    return normalized


class MusicMixSettingsManager:
    """Persist render settings under ``post_production.music_mix``."""

    def __init__(self, checkpoint: Any) -> None:
        self.checkpoint = checkpoint

    def current(self, *, legacy_base_volume: float = 0.25) -> dict[str, Any]:
        post = self.checkpoint.data.get("post_production")
        raw = post.get("music_mix") if isinstance(post, dict) else None
        return normalize_music_mix_settings(
            raw,
            legacy_base_volume=legacy_base_volume,
        )

    def update(
        self,
        *,
        legacy_base_volume: float = 0.25,
        **updates: Any,
    ) -> dict[str, Any]:
        settings = self.current(legacy_base_volume=legacy_base_volume)
        settings.update(updates)
        normalized = normalize_music_mix_settings(settings)
        self._save(normalized)
        return deepcopy(normalized)

    def reset(self, *, base_volume: float = 0.25) -> dict[str, Any]:
        settings = normalize_music_mix_settings(
            DEFAULT_MUSIC_MIX_SETTINGS,
            legacy_base_volume=base_volume,
        )
        settings["base_volume"] = _finite_number(base_volume, "base_volume")
        settings = normalize_music_mix_settings(settings)
        self._save(settings)
        return deepcopy(settings)

    def _save(self, settings: dict[str, Any]) -> None:
        post = self.checkpoint.data.setdefault("post_production", {})
        post["music_mix"] = deepcopy(settings)
        self.checkpoint.save()


def _clamp_fades(
    fade_in: float,
    fade_out: float,
    effective_duration: float,
) -> tuple[float, float]:
    """Scale overlapping fades proportionally for deterministic valid filters."""

    if effective_duration <= 0:
        raise MusicMixError("有效 Music 时长必须大于 0。")
    total = fade_in + fade_out
    if total <= effective_duration or total <= 0:
        return fade_in, fade_out
    scale = effective_duration / total
    return fade_in * scale, fade_out * scale


def build_music_mix_plan(
    settings: dict[str, Any],
    *,
    original_music_duration: float,
    video_duration: float,
    voice_timing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build the exact timeline envelope consumed by Final Export."""

    config = normalize_music_mix_settings(settings)
    original_duration = _finite_number(
        original_music_duration, "original_music_duration"
    )
    final_duration = _finite_number(video_duration, "video_duration")
    if original_duration <= 0 or final_duration <= 0:
        raise MusicMixError("Music 与 Video 时长必须大于 0。")
    effective_end = min(original_duration, final_duration)
    fade_in, fade_out = _clamp_fades(
        config["fade_in_seconds"],
        config["fade_out_seconds"],
        effective_end,
    )

    base = config["base_volume"]
    ratio = config["ducking_ratio"]
    plan: dict[str, Any] = {
        "settings": deepcopy(config),
        "original_duration": original_duration,
        "video_duration": final_duration,
        "effective_music_end": effective_end,
        "base_volume": base,
        "ducking_requested": config["ducking_enabled"],
        "ducking_enabled": False,
        "ducking_ratio": ratio,
        "ducked_volume": base * ratio,
        "ducking_start": None,
        "ducking_end": None,
        "duck_attack_seconds": 0.0,
        "duck_release_seconds": 0.0,
        "configured_duck_attack_seconds": config["duck_attack_seconds"],
        "configured_duck_release_seconds": config["duck_release_seconds"],
        "attack_start": None,
        "release_end": None,
        "fade_in_seconds": fade_in,
        "fade_out_seconds": fade_out,
        "configured_fade_in_seconds": config["fade_in_seconds"],
        "configured_fade_out_seconds": config["fade_out_seconds"],
        "loop_music": False,
        "padded_with_silence": original_duration < final_duration,
        "ducking_status": "DISABLED_BY_USER",
        "media_timing_resolved": True,
    }

    if not config["ducking_enabled"]:
        return plan
    if not isinstance(voice_timing, dict):
        plan["ducking_status"] = "NO_VOICE"
        return plan
    timing_status = str(
        voice_timing.get("status") or "LEGACY_NO_CALIBRATION"
    )
    if timing_status == "LEGACY_NO_CALIBRATION":
        plan["ducking_status"] = "UNAVAILABLE_LEGACY_VOICE_TIMING"
        return plan
    end_value = voice_timing.get("actual_voice_end")
    if end_value is None:
        plan["ducking_status"] = "UNAVAILABLE_VOICE_END"
        return plan
    start = _finite_number(
        voice_timing.get("voice_track_start", 0.0), "voice_track_start"
    )
    end = _finite_number(end_value, "actual_voice_end")
    if start < 0 or end <= start:
        plan["ducking_status"] = "INVALID_VOICE_TIMING"
        return plan

    duck_start = max(0.0, start)
    duck_end = min(end, effective_end)
    if duck_start >= effective_end or duck_end <= duck_start:
        plan["ducking_status"] = "VOICE_OUTSIDE_MUSIC"
        return plan

    attack = min(config["duck_attack_seconds"], duck_start)
    release = min(
        config["duck_release_seconds"],
        max(0.0, effective_end - duck_end),
    )
    plan.update(
        {
            "ducking_enabled": True,
            "ducking_start": duck_start,
            "ducking_end": duck_end,
            "duck_attack_seconds": attack,
            "duck_release_seconds": release,
            "attack_start": duck_start - attack,
            "release_end": duck_end + release,
            "ducking_status": "ENABLED",
        }
    )
    return plan


def music_ducking_expression(plan: dict[str, Any]) -> str | None:
    """Return a continuous 1.0→ratio→1.0 FFmpeg volume multiplier."""

    if not bool(plan.get("ducking_enabled")):
        return None
    ratio = float(plan["ducking_ratio"])
    start = float(plan["ducking_start"])
    end = float(plan["ducking_end"])
    attack = float(plan["duck_attack_seconds"])
    release = float(plan["duck_release_seconds"])
    attack_start = float(plan["attack_start"])
    release_end = float(plan["release_end"])

    low = f"{ratio:.12g}"
    parts: list[tuple[str, str]] = []
    if attack > 0:
        ramp_down = (
            f"1-(1-{low})*(t-{attack_start:.12g})/{attack:.12g}"
        )
        parts.append((f"lt(t,{attack_start:.12g})", "1"))
        parts.append((f"lt(t,{start:.12g})", ramp_down))
    else:
        parts.append((f"lt(t,{start:.12g})", "1"))
    parts.append((f"lt(t,{end:.12g})", low))
    if release > 0:
        ramp_up = (
            f"{low}+(1-{low})*(t-{end:.12g})/{release:.12g}"
        )
        parts.append((f"lt(t,{release_end:.12g})", ramp_up))

    expression = "1"
    for condition, value in reversed(parts):
        expression = f"if({condition},{value},{expression})"
    return expression


def music_mix_fingerprint(plan: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only deterministic fields that can alter rendered bytes."""

    if not isinstance(plan, dict):
        return None
    fields = (
        "original_duration",
        "effective_music_end",
        "base_volume",
        "ducking_enabled",
        "ducking_ratio",
        "ducked_volume",
        "ducking_start",
        "ducking_end",
        "duck_attack_seconds",
        "duck_release_seconds",
        "attack_start",
        "release_end",
        "fade_in_seconds",
        "fade_out_seconds",
        "loop_music",
        "padded_with_silence",
        "ducking_status",
    )
    return {field: plan.get(field) for field in fields}
