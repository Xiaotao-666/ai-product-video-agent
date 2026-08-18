"""Project subtitle routing: compiled Storyboard first, Voice script fallback."""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from project_manager import ProjectPaths
from subtitle_assets import SubtitleAssetManager, SubtitleAssetError
from subtitle_provider import SubtitleGenerationRequest
from subtitle_provider_registry import SubtitleProviderRegistry
from storyboard import Storyboard, build_global_av_timeline
from voice_assets import VoiceAssetManager


@dataclass(frozen=True)
class ActiveVoiceSubtitleSource:
    request: SubtitleGenerationRequest
    voice_version: int
    script_path: Path
    audio_path: Path


@dataclass(frozen=True)
class StoryboardSubtitleSource:
    request: SubtitleGenerationRequest
    storyboard_path: Path


def load_storyboard_subtitle_source(
    paths: ProjectPaths,
) -> StoryboardSubtitleSource | None:
    """Load planned cues; return None only when the project has no plan."""

    storyboard_path = paths.storyboard_file_path()
    if not storyboard_path.is_file():
        return None
    try:
        raw_storyboard = json.loads(storyboard_path.read_text(encoding="utf-8"))
        board = Storyboard.model_validate(raw_storyboard)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SubtitleAssetError(f"Compiled Storyboard 无法读取：{exc}") from exc

    global_timeline = build_global_av_timeline(board)
    subtitle_cues = global_timeline["subtitle_cues"]
    if not subtitle_cues:
        return None

    constraints: Mapping[str, Any] = {"forbidden_windows": []}
    creative_path = paths.creative_brief_path()
    if creative_path.is_file():
        try:
            creative_payload = json.loads(creative_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SubtitleAssetError(f"Creative Brief 无法读取：{exc}") from exc
        if not isinstance(creative_payload, Mapping):
            raise SubtitleAssetError("Creative Brief 必须是 JSON 对象。")
        raw_constraints = creative_payload.get("av_timeline_constraints")
        if raw_constraints is not None:
            if not isinstance(raw_constraints, Mapping):
                raise SubtitleAssetError("AV Timeline Constraints 结构无效。")
            constraints = raw_constraints

    script = "\n".join(str(cue["text"]).strip() for cue in subtitle_cues)
    return StoryboardSubtitleSource(
        request=SubtitleGenerationRequest(
            script=script,
            audio_duration_seconds=float(board.total_duration),
            language="zh-CN",
            output_format="srt",
            settings={
                "source": "compiled_storyboard",
                "compiled_storyboard": board.model_dump(mode="json"),
                "global_timeline": global_timeline,
                "av_timeline_constraints": dict(constraints),
            },
        ),
        storyboard_path=storyboard_path,
    )


def load_active_voice_subtitle_source(
    paths: ProjectPaths,
) -> ActiveVoiceSubtitleSource:
    active = VoiceAssetManager(paths).active_version()
    if active is None:
        raise SubtitleAssetError("当前项目没有配音版本，请先完成配音制作。")
    script_path = paths.ensure_within_project(
        paths.project_path / str(active.get("script_path") or "")
    )
    audio_path = paths.ensure_within_project(
        paths.project_path / str(active.get("audio_path") or "")
    )
    if not script_path.is_file() or not script_path.read_text(encoding="utf-8").strip():
        raise SubtitleAssetError("当前配音版本缺少有效 script.txt。")
    if not audio_path.is_file() or audio_path.stat().st_size <= 0:
        raise SubtitleAssetError("当前配音版本缺少有效 audio.wav。")
    try:
        with wave.open(str(audio_path), "rb") as stream:
            frame_rate = stream.getframerate()
            if frame_rate <= 0:
                raise SubtitleAssetError("audio.wav 采样率无效。")
            duration = stream.getnframes() / frame_rate
    except (wave.Error, EOFError, OSError) as exc:
        raise SubtitleAssetError(f"无法读取 audio.wav 时长：{exc}") from exc
    return ActiveVoiceSubtitleSource(
        request=SubtitleGenerationRequest(
            script=script_path.read_text(encoding="utf-8").strip(),
            audio_duration_seconds=duration,
            language=str(active.get("language") or "zh-CN"),
            output_format="srt",
        ),
        voice_version=int(active["version"]),
        script_path=script_path,
        audio_path=audio_path,
    )


def generate_subtitle_from_active_voice(
    manager: SubtitleAssetManager,
    registry: SubtitleProviderRegistry,
    *,
    provider_name: str | None = None,
) -> dict:
    source = load_active_voice_subtitle_source(manager.project)
    provider = registry.resolve(source.request, provider_name)
    return manager.generate_and_save(
        source.request,
        provider,
        source_voice_version=source.voice_version,
        source_script_path=source.script_path,
        source_audio_path=source.audio_path,
    )


def subtitle_source_label(paths: ProjectPaths) -> str:
    """Return the source that a manual subtitle generation would use."""

    return (
        "Storyboard Planned"
        if load_storyboard_subtitle_source(paths) is not None
        else "Script Fallback"
    )


def generate_subtitle_for_project(
    manager: SubtitleAssetManager,
    registry: SubtitleProviderRegistry,
    *,
    provider_name: str | None = None,
) -> dict:
    """Prefer compiled Storyboard cues and fall back to the active Voice script."""

    storyboard_source = load_storyboard_subtitle_source(manager.project)
    if storyboard_source is None:
        return generate_subtitle_from_active_voice(
            manager,
            registry,
            provider_name=provider_name,
        )
    provider = registry.resolve(storyboard_source.request, provider_name)
    return manager.generate_and_save(
        storyboard_source.request,
        provider,
        source_voice_version=None,
        source_script_path=None,
        source_audio_path=None,
        source_storyboard_path=storyboard_source.storyboard_path,
    )
