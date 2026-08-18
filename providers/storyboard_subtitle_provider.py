"""Generate SRT subtitles from the compiled Storyboard global timeline."""

from __future__ import annotations

import math
from typing import Any, Mapping

from subtitle_provider import (
    SubtitleCue,
    SubtitleGenerationRequest,
    SubtitleGenerationResult,
    SubtitleProvider,
    SubtitleProviderCapabilities,
    SubtitleProviderError,
)


class StoryboardSubtitleProvider(SubtitleProvider):
    """Render already-scheduled Storyboard cues without changing their timing."""

    provider_name = "storyboard_subtitle"
    api_version = "local-v1"
    capabilities = SubtitleProviderCapabilities(
        supported_formats=frozenset({"srt"}),
    )

    def __init__(self, *, model: str = "compiled-storyboard-v1") -> None:
        self.model_name = str(model or "compiled-storyboard-v1")

    @classmethod
    def from_config(
        cls, settings: Mapping[str, Any] | None = None
    ) -> "StoryboardSubtitleProvider":
        config = dict(settings or {})
        return cls(model=str(config.get("model") or "compiled-storyboard-v1"))

    def get_metadata(self) -> dict[str, Any]:
        metadata = super().get_metadata()
        metadata.update(
            {
                "timing_strategy": "compiled-storyboard-global-timeline",
                "external_api": False,
            }
        )
        return metadata

    def supports(self, request: SubtitleGenerationRequest) -> bool:
        settings = request.settings
        timeline = settings.get("global_timeline")
        cues = timeline.get("subtitle_cues") if isinstance(timeline, Mapping) else None
        return (
            super().supports(request)
            and settings.get("source") == "compiled_storyboard"
            and isinstance(settings.get("compiled_storyboard"), Mapping)
            and isinstance(cues, list)
            and bool(cues)
        )

    def preflight(self, request: SubtitleGenerationRequest) -> None:
        super().preflight(request)
        settings = request.settings
        storyboard = settings.get("compiled_storyboard")
        timeline = settings.get("global_timeline")
        if not isinstance(storyboard, Mapping) or not isinstance(timeline, Mapping):
            raise SubtitleProviderError("缺少有效的 Compiled Storyboard 或 Global Timeline。")

        try:
            total_duration = float(storyboard.get("total_duration"))
        except (TypeError, ValueError) as exc:
            raise SubtitleProviderError("Compiled Storyboard 总时长无效。") from exc
        if not math.isfinite(total_duration) or total_duration <= 0:
            raise SubtitleProviderError("Compiled Storyboard 总时长必须大于 0。")

        cues = timeline.get("subtitle_cues")
        if not isinstance(cues, list) or not cues:
            raise SubtitleProviderError("Compiled Storyboard 中没有可生成的字幕规划。")

        expected_cues: list[tuple[int, str]] = []
        shots = storyboard.get("shots")
        if not isinstance(shots, list):
            raise SubtitleProviderError("Compiled Storyboard shots 结构无效。")
        valid_shot_ids: set[int] = set()
        for shot in shots:
            if not isinstance(shot, Mapping):
                raise SubtitleProviderError("Compiled Storyboard Shot 结构无效。")
            shot_id = self._positive_int(shot.get("shot_id"), "shot_id")
            valid_shot_ids.add(shot_id)
            shot_cues = shot.get("subtitle_cues", [])
            if not isinstance(shot_cues, list):
                raise SubtitleProviderError(
                    f"Shot {shot_id:02d} subtitle_cues 必须是数组。"
                )
            for shot_cue in shot_cues:
                if not isinstance(shot_cue, Mapping):
                    raise SubtitleProviderError(
                        f"Shot {shot_id:02d} Subtitle Cue 结构无效。"
                    )
                expected_cues.append(
                    (shot_id, str(shot_cue.get("text") or "").strip())
                )
        if len(expected_cues) != len(cues):
            raise SubtitleProviderError(
                "Global Timeline 字幕数量与 Compiled Storyboard 不一致。"
            )

        previous_end = 0.0
        for index, cue in enumerate(cues, start=1):
            if not isinstance(cue, Mapping):
                raise SubtitleProviderError(f"Subtitle Cue {index} 结构无效。")
            text = str(cue.get("text") or "").strip()
            if not text:
                raise SubtitleProviderError(f"Subtitle Cue {index} text 不能为空。")
            shot_id = self._positive_int(cue.get("shot_id"), "shot_id")
            if shot_id not in valid_shot_ids:
                raise SubtitleProviderError(
                    f"Subtitle Cue {index} 引用了不存在的 Shot {shot_id:02d}。"
                )
            expected_shot_id, expected_text = expected_cues[index - 1]
            if shot_id != expected_shot_id or text != expected_text:
                raise SubtitleProviderError(
                    "Global Timeline 字幕顺序或内容与 Compiled Storyboard 不一致。"
                )
            start = self._finite_seconds(cue.get("start"), index, "start")
            end = self._finite_seconds(cue.get("end"), index, "end")
            if start < 0 or end <= start:
                raise SubtitleProviderError(
                    f"Subtitle Cue {index} 必须满足 0 <= start < end。"
                )
            if end > total_duration:
                raise SubtitleProviderError(
                    f"Subtitle Cue {index} 超过视频总时长 {total_duration:g}s。"
                )
            if start < previous_end:
                raise SubtitleProviderError("Storyboard 字幕时间存在重叠或顺序错误。")
            previous_end = end

        self._validate_forbidden_windows(
            cues,
            settings.get("av_timeline_constraints"),
        )

    def generate_subtitle(
        self, request: SubtitleGenerationRequest
    ) -> SubtitleGenerationResult:
        self.preflight(request)
        storyboard = request.settings["compiled_storyboard"]
        timeline = request.settings["global_timeline"]
        raw_cues = timeline["subtitle_cues"]
        cues = tuple(
            SubtitleCue(
                index=index,
                start_seconds=float(raw["start"]),
                end_seconds=float(raw["end"]),
                text=str(raw["text"]).strip(),
            )
            for index, raw in enumerate(raw_cues, start=1)
        )
        subtitle_text = "\n".join(self._format_cue(cue) for cue in cues)
        return SubtitleGenerationResult(
            subtitle_text=subtitle_text,
            cues=cues,
            duration_seconds=float(storyboard["total_duration"]),
            metadata={
                "source": "compiled_storyboard",
                "timing_source": "compiled_storyboard_global_timeline",
                "cue_count": len(cues),
            },
        )

    @staticmethod
    def _positive_int(value: Any, label: str) -> int:
        if isinstance(value, bool):
            raise SubtitleProviderError(f"{label} 必须是正整数。")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise SubtitleProviderError(f"{label} 必须是正整数。") from exc
        if parsed <= 0:
            raise SubtitleProviderError(f"{label} 必须是正整数。")
        return parsed

    @staticmethod
    def _finite_seconds(value: Any, index: int, label: str) -> float:
        if isinstance(value, bool):
            raise SubtitleProviderError(
                f"Subtitle Cue {index} {label} 必须是有效数字。"
            )
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise SubtitleProviderError(
                f"Subtitle Cue {index} {label} 必须是有效数字。"
            ) from exc
        if not math.isfinite(parsed):
            raise SubtitleProviderError(
                f"Subtitle Cue {index} {label} 必须是有限数字。"
            )
        return parsed

    @staticmethod
    def _validate_forbidden_windows(
        cues: list[Mapping[str, Any]],
        constraints: Any,
    ) -> None:
        if constraints is None:
            return
        if not isinstance(constraints, Mapping):
            raise SubtitleProviderError("AV Timeline Constraints 结构无效。")
        windows = constraints.get("forbidden_windows", [])
        if not isinstance(windows, list):
            raise SubtitleProviderError("forbidden_windows 必须是数组。")
        for window_index, window in enumerate(windows, start=1):
            if not isinstance(window, Mapping):
                raise SubtitleProviderError(
                    f"Forbidden Window {window_index} 结构无效。"
                )
            tracks = window.get("tracks", [])
            if not isinstance(tracks, list):
                raise SubtitleProviderError(
                    f"Forbidden Window {window_index} tracks 必须是数组。"
                )
            if "subtitle" not in tracks:
                continue
            start = StoryboardSubtitleProvider._finite_seconds(
                window.get("start"), window_index, "forbidden start"
            )
            end = StoryboardSubtitleProvider._finite_seconds(
                window.get("end"), window_index, "forbidden end"
            )
            if start < 0 or end <= start:
                raise SubtitleProviderError(
                    f"Forbidden Window {window_index} 时间无效。"
                )
            for cue_index, cue in enumerate(cues, start=1):
                cue_start = float(cue["start"])
                cue_end = float(cue["end"])
                if cue_start < end and cue_end > start:
                    raise SubtitleProviderError(
                        f"Subtitle Cue {cue_index} 与字幕禁用窗口 "
                        f"[{start:g}, {end:g}) 重叠。"
                    )

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
