"""Deterministic local subtitle generation from a voice script."""

from __future__ import annotations

import math
import re
from typing import Any, Mapping

from subtitle_provider import (
    SubtitleCue,
    SubtitleGenerationRequest,
    SubtitleGenerationResult,
    SubtitleProvider,
    SubtitleProviderCapabilities,
    SubtitleProviderError,
)


SENTENCE_BOUNDARY = re.compile(r"(?<=[，,。！？!?；;：:])\s*")


class ScriptSubtitleProvider(SubtitleProvider):
    """Allocate SRT cue timing by visible-text weight and audio duration."""

    provider_name = "script_subtitle"
    api_version = "local-v1"
    capabilities = SubtitleProviderCapabilities(
        supported_formats=frozenset({"srt"}),
    )

    def __init__(
        self,
        *,
        model: str = "length-weighted-v1",
        max_chars_per_cue: int = 18,
        estimated_chars_per_second: float = 4.0,
    ) -> None:
        self.model_name = str(model or "length-weighted-v1")
        self.max_chars_per_cue = int(max_chars_per_cue)
        self.estimated_chars_per_second = float(estimated_chars_per_second)

    @classmethod
    def from_config(
        cls, settings: Mapping[str, Any] | None = None
    ) -> "ScriptSubtitleProvider":
        config = dict(settings or {})
        return cls(
            model=str(config.get("model") or "length-weighted-v1"),
            max_chars_per_cue=int(config.get("max_chars_per_cue") or 18),
            estimated_chars_per_second=float(
                config.get("estimated_chars_per_second") or 4.0
            ),
        )

    def get_metadata(self) -> dict[str, Any]:
        metadata = super().get_metadata()
        metadata.update(
            {
                "timing_strategy": "visible-text-length-weighted",
                "max_chars_per_cue": self.max_chars_per_cue,
                "external_api": False,
            }
        )
        return metadata

    def preflight(self, request: SubtitleGenerationRequest) -> None:
        if self.max_chars_per_cue <= 0:
            raise SubtitleProviderError("max_chars_per_cue 必须大于 0。")
        if self.estimated_chars_per_second <= 0:
            raise SubtitleProviderError(
                "estimated_chars_per_second 必须大于 0。"
            )
        super().preflight(request)

    def generate_subtitle(
        self, request: SubtitleGenerationRequest
    ) -> SubtitleGenerationResult:
        self.preflight(request)
        chunks = self._split_script(request.script)
        weights = [self._text_weight(chunk) for chunk in chunks]
        total_weight = sum(weights)
        source = str(request.settings.get("source") or "voice_script")
        timing_source = (
            "voice_audio_duration" if source == "active_voice" else "audio.wav"
        )
        duration = request.audio_duration_seconds
        if duration is None:
            timing_source = "text-estimate"
            duration = max(1.0, total_weight / self.estimated_chars_per_second)
        total_ms = max(1, int(round(float(duration) * 1000)))
        voice_track_start = request.settings.get("voice_track_start", 0.0)
        if isinstance(voice_track_start, bool) or not isinstance(
            voice_track_start, (int, float)
        ):
            raise SubtitleProviderError("voice_track_start 必须是非负数字。")
        voice_track_start = float(voice_track_start)
        if not math.isfinite(voice_track_start) or voice_track_start < 0:
            raise SubtitleProviderError("voice_track_start 必须是非负数字。")
        actual_audio_duration = request.settings.get(
            "actual_audio_duration", duration
        )
        if isinstance(actual_audio_duration, bool) or not isinstance(
            actual_audio_duration, (int, float)
        ):
            raise SubtitleProviderError("actual_audio_duration 必须是非负数字。")
        actual_audio_duration = float(actual_audio_duration)
        if not math.isfinite(actual_audio_duration) or actual_audio_duration < 0:
            raise SubtitleProviderError("actual_audio_duration 必须是非负数字。")
        actual_voice_end = request.settings.get(
            "actual_voice_end", voice_track_start + actual_audio_duration
        )
        if isinstance(actual_voice_end, bool) or not isinstance(
            actual_voice_end, (int, float)
        ):
            raise SubtitleProviderError("actual_voice_end 必须是非负数字。")
        actual_voice_end = float(actual_voice_end)
        if not math.isfinite(actual_voice_end) or actual_voice_end < voice_track_start:
            raise SubtitleProviderError("actual_voice_end 必须不早于 voice_track_start。")
        start_offset_ms = int(round(voice_track_start * 1000))
        if len(chunks) > total_ms:
            chunks = [" ".join(chunks)]
            weights = [sum(weights)]
            total_weight = weights[0]

        cues: list[SubtitleCue] = []
        elapsed_weight = 0
        start_ms = 0
        for offset, (chunk, weight) in enumerate(zip(chunks, weights)):
            remaining = len(chunks) - offset - 1
            elapsed_weight += weight
            if remaining == 0:
                end_ms = total_ms
            else:
                weighted_end = int(round(total_ms * elapsed_weight / total_weight))
                end_ms = min(
                    total_ms - remaining,
                    max(start_ms + 1, weighted_end),
                )
            cues.append(
                SubtitleCue(
                    index=offset + 1,
                    start_seconds=(start_offset_ms + start_ms) / 1000,
                    end_seconds=(start_offset_ms + end_ms) / 1000,
                    text=chunk,
                )
            )
            start_ms = end_ms
        subtitle_text = "\n".join(self._format_cue(cue) for cue in cues)
        return SubtitleGenerationResult(
            subtitle_text=subtitle_text,
            cues=tuple(cues),
            duration_seconds=(start_offset_ms + total_ms) / 1000,
            metadata={
                "source": source,
                "semantic_type": request.settings.get("semantic_type"),
                "timing_source": timing_source,
                "cue_count": len(cues),
                "actual_audio_duration": actual_audio_duration,
                "voice_track_start": voice_track_start,
                "actual_voice_end": actual_voice_end,
                "cue_level_alignment": False,
            },
        )

    def _split_script(self, script: str) -> list[str]:
        chunks: list[str] = []
        for raw_line in script.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
            line = raw_line.strip()
            if not line:
                continue
            for sentence in SENTENCE_BOUNDARY.split(line):
                text = sentence.strip()
                if not text:
                    continue
                while len(text) > self.max_chars_per_cue:
                    chunks.append(text[: self.max_chars_per_cue].strip())
                    text = text[self.max_chars_per_cue :].strip()
                if text:
                    chunks.append(text)
        return chunks or [script.strip()]

    @staticmethod
    def _text_weight(text: str) -> int:
        return max(1, sum(1 for char in text if char.isalnum()))

    @classmethod
    def _format_cue(cls, cue: SubtitleCue) -> str:
        return (
            f"{cue.index}\n"
            f"{cls._timestamp(cue.start_seconds)} --> "
            f"{cls._timestamp(cue.end_seconds)}\n"
            f"{cue.text}\n"
        )

    @staticmethod
    def _timestamp(seconds: float) -> str:
        total_ms = max(0, int(round(seconds * 1000)))
        hours, remainder = divmod(total_ms, 3_600_000)
        minutes, remainder = divmod(remainder, 60_000)
        whole_seconds, milliseconds = divmod(remainder, 1000)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{milliseconds:03d}"
