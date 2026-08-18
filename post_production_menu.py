"""Command-line Resume and PostProduction menus.

The menu orchestrates existing Voice and Assembly boundaries; it contains no
VideoProvider or TTS adapter implementation.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from export_pipeline import (
    ExportAlreadyExistsError,
    ExportPipeline,
    ExportPipelineError,
)
from export_assets import ExportAssetError, ExportAssetManager
from music_assets import MusicAssetError, MusicAssetManager
from music_generation import add_local_music
from music_mix import MusicMixError, MusicMixSettingsManager
from music_provider import MusicProviderError
from music_provider_registry import build_music_provider_registry
from post_production import (
    PostProductionPipeline,
    PostProductionStatus,
    ProjectCompletionStatus,
)
from project_manager import ProjectPaths
from task_logger import TaskLogger
from subtitle_assets import SubtitleAssetError, SubtitleAssetManager
from subtitle_generation import (
    generate_subtitle_for_project,
    subtitle_source_label,
)
from subtitle_provider import SubtitleProviderError
from subtitle_provider_registry import build_subtitle_provider_registry
from voice_assets import VoiceAssetError, VoiceAssetManager
from voice_generation import generate_confirmed_voice
from voice_provider import VoiceGenerationRequest, VoiceProviderError
from voice_provider_registry import VoiceProviderRegistry, build_voice_provider_registry
from voice_script_builder import (
    VoiceScriptBuilderError,
    load_storyboard_voice_script,
)


InputFn = Callable[[str], str]
OutputFn = Callable[[str], None]


def _status_label(status: str) -> str:
    return "已完成" if status == PostProductionStatus.COMPLETED.value else "未生成"


def _export_status_label(
    component: dict[str, Any], *, assembly_needs_update: bool = False
) -> str:
    if (
        component.get("status") == PostProductionStatus.COMPLETED.value
        and component.get("active_version") is not None
    ):
        suffix = "，需要更新" if assembly_needs_update else ""
        return (
            f"已完成（Export v{int(component['active_version']):03d}{suffix}）"
        )
    return "未生成"


def _assembly_video_path(paths: ProjectPaths, checkpoint: Any) -> Path | None:
    assembly = checkpoint.assembly_checkpoint()
    relative = assembly.get("final_video_path")
    if not relative:
        return None
    try:
        path = paths.ensure_within_project(paths.project_path / str(relative))
    except Exception:
        return None
    return path if path.is_file() and path.stat().st_size > 0 else None


def has_completed_assembly(paths: ProjectPaths, checkpoint: Any) -> bool:
    assembly = checkpoint.assembly_checkpoint()
    return (
        str(assembly.get("status")) == "COMPLETED"
        and not bool(assembly.get("needs_update"))
        and _assembly_video_path(paths, checkpoint) is not None
    )


def _open_path(path: Path) -> None:
    os.startfile(path)  # type: ignore[attr-defined]


def _read_multiline_script(input_fn: InputFn, output_fn: OutputFn) -> str:
    output_fn("请输入配音文本，可以输入多行；单独输入 END 表示完成：")
    lines: list[str] = []
    while True:
        line = input_fn("")
        if line.strip() == "END":
            return "\n".join(lines).strip()
        lines.append(line)


def _voice_source_label(value: Any) -> str:
    return {
        "compiled_storyboard": "Storyboard Planned",
        "storyboard_edited": "Storyboard Planned (Edited)",
        "manual": "Manual Script",
    }.get(str(value or "manual"), str(value or "Manual Script"))


def _seconds(value: Any) -> str:
    return "N/A" if value is None else f"{float(value):g}s"


def _display_voice_calibration(
    entry: dict[str, Any], output_fn: OutputFn
) -> str | None:
    calibration = entry.get("timing_calibration")
    if not isinstance(calibration, dict):
        output_fn("\nTiming Calibration:\nNot available (legacy Voice Version)")
        return None
    status = str(calibration.get("status") or "UNKNOWN")
    difference = calibration.get("duration_difference_seconds")
    ratio = calibration.get("duration_difference_ratio")
    difference_label = "N/A"
    if difference is not None and ratio is not None:
        difference_label = f"{float(difference):+g}s ({float(ratio):+.1%})"
    output_fn("\n========== Voice Timing Calibration ==========")
    output_fn(f"\nSource:\n{_voice_source_label(entry.get('script_source'))}")
    output_fn("\nTiming Mode:\nWhole Track")
    output_fn(
        "\nPlanned Narration:\n"
        f"{_seconds(calibration.get('planned_narration_duration'))}"
    )
    output_fn(
        "\nActual Audio:\n"
        f"{_seconds(calibration.get('actual_audio_duration'))}"
    )
    output_fn(
        "\nVoice Track Start:\n"
        f"{_seconds(calibration.get('voice_track_start'))}"
    )
    output_fn(
        "\nActual Voice End:\n"
        f"{_seconds(calibration.get('actual_voice_end'))}"
    )
    output_fn(f"\nDifference:\n{difference_label}")
    output_fn(f"\nCalibration:\n{status}")
    if calibration.get("script_matches_storyboard") is False:
        output_fn("\n提示：本次配音文本已与 Storyboard 原旁白内容不同。")
    return status


def _review_voice_calibration(
    entry: dict[str, Any],
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> str:
    """Return accept/regenerate/edit/return without calling any provider."""

    status = _display_voice_calibration(entry, output_fn)
    if status in {None, "PASS", "WARNING", "NOT_APPLICABLE"}:
        if status == "WARNING":
            output_fn("\n配音时长存在偏差，但系统不会自动重新生成。")
        return "accept"
    if status == "OUT_OF_TOLERANCE":
        output_fn("\n========== Voice Timing Warning ==========")
        output_fn("\n当前配音时长与前期规划差异较大。")
        output_fn(
            "\n请选择：\n1. 接受当前配音\n2. 重新生成配音"
            "\n3. 编辑配音文本后重新生成\n4. 返回"
        )
        choices = {
            "1": "accept",
            "2": "regenerate",
            "3": "edit",
            "4": "return",
        }
        prompt = "请输入 1-4: "
    elif status == "OUT_OF_BOUNDS":
        output_fn("\n当前旁白超出视频总时长，未静默截断音频。")
        output_fn(
            "\n请选择：\n1. 重新生成配音"
            "\n2. 编辑配音文本后重新生成\n3. 返回"
        )
        choices = {"1": "regenerate", "2": "edit", "3": "return"}
        prompt = "请输入 1-3: "
    else:
        output_fn(f"\n未知 Calibration 状态：{status}")
        return "return"
    while True:
        choice = input_fn(prompt).strip()
        if choice in choices:
            return choices[choice]
        output_fn("输入无效，请重新选择。")


def _request_from_voice_entry(
    paths: ProjectPaths,
    entry: dict[str, Any],
    fallback: VoiceGenerationRequest,
) -> VoiceGenerationRequest:
    script_path = paths.ensure_within_project(
        paths.project_path / str(entry["script_path"])
    )
    settings = dict(fallback.settings)
    for key in (
        "script_source",
        "source_storyboard_path",
        "planned_narration_duration",
        "planned_first_voice_start",
        "planned_last_voice_end",
        "planned_voice_span",
        "total_video_duration",
        "cue_count",
    ):
        if key in entry:
            settings[key] = entry.get(key)
    return VoiceGenerationRequest(
        script=script_path.read_text(encoding="utf-8"),
        voice=str(entry.get("voice") or fallback.voice),
        language=str(entry.get("language") or fallback.language),
        output_format=fallback.output_format,
        settings=settings,
    )


def _voice_status_details(paths: ProjectPaths) -> str | None:
    try:
        active = VoiceAssetManager(paths).active_version()
    except VoiceAssetError:
        return None
    if not active:
        return None
    calibration = active.get("timing_calibration")
    if not isinstance(calibration, dict):
        return (
            f"已生成 v{int(active['version']):03d}\n"
            f"Source：{_voice_source_label(active.get('script_source'))}\n"
            "Timing：Not available (legacy)"
        )
    return (
        f"已生成 v{int(active['version']):03d}\n"
        f"Source：{_voice_source_label(active.get('script_source'))}\n"
        f"Timing：{calibration.get('status', 'UNKNOWN')}\n"
        f"Start：{_seconds(calibration.get('voice_track_start'))}\n"
        "Planned / Actual："
        f"{_seconds(calibration.get('planned_narration_duration'))} / "
        f"{_seconds(calibration.get('actual_audio_duration'))}"
    )


def _display_export_voice_timing(
    summary: dict[str, Any], output_fn: OutputFn
) -> str | None:
    timing = summary.get("voice_timing")
    if not isinstance(timing, dict):
        return None
    status = str(timing.get("status") or "LEGACY_NO_CALIBRATION")
    output_fn(f"\nVoice Timing:\n{status}")
    output_fn(
        f"\nVoice Start:\n{_seconds(timing.get('voice_track_start', 0.0))}"
    )
    output_fn(
        f"\nVoice Actual End:\n{_seconds(timing.get('actual_voice_end'))}"
    )
    output_fn(
        "\nPlanned / Actual:\n"
        f"{_seconds(timing.get('planned_narration_duration'))} / "
        f"{_seconds(timing.get('actual_audio_duration'))}"
    )
    acceptance = timing.get("timing_acceptance")
    if status == "OUT_OF_TOLERANCE" and isinstance(acceptance, dict):
        if acceptance.get("accepted"):
            output_fn("\nTiming Acceptance:\nUser Accepted")
    return status


def _active_export_voice_details(paths: ProjectPaths) -> str | None:
    try:
        active = ExportAssetManager(paths).active_version()
    except ExportAssetError:
        return None
    if not active or not isinstance(active.get("voice"), dict):
        return None
    voice = active["voice"]
    return (
        f"Voice：v{int(voice['version']):03d}\n"
        f"Voice Start：{_seconds(voice.get('voice_track_start'))}\n"
        f"Calibration：{voice.get('calibration_status', 'UNKNOWN')}"
    )


def _display_music_mix(
    music_mix: dict[str, Any] | None,
    output_fn: OutputFn,
) -> None:
    if not isinstance(music_mix, dict):
        output_fn("\nMusic Mix:\n未选择背景音乐")
        return
    base = float(music_mix.get("base_volume", 0.25))
    ducked = float(music_mix.get("ducked_volume", base))
    enabled = bool(music_mix.get("ducking_enabled"))
    output_fn(f"\nBase Volume:\n{base:.0%}")
    output_fn(f"\nMusic Ducking:\n{'Enabled' if enabled else 'Disabled'}")
    if enabled:
        output_fn(f"\nDucked Volume:\n{ducked:.0%}")
        output_fn(
            "\nDucking Window:\n"
            f"{_seconds(music_mix.get('ducking_start'))} - "
            f"{_seconds(music_mix.get('ducking_end'))}"
        )
        output_fn(
            "\nAttack / Release:\n"
            f"{_seconds(music_mix.get('duck_attack_seconds'))} / "
            f"{_seconds(music_mix.get('duck_release_seconds'))}"
        )
    elif music_mix.get("ducking_status") == "UNAVAILABLE_LEGACY_VOICE_TIMING":
        output_fn("\nMusic Ducking:\nUnavailable for legacy voice timing")
    output_fn(
        "\nFade In / Out:\n"
        f"{_seconds(music_mix.get('fade_in_seconds'))} / "
        f"{_seconds(music_mix.get('fade_out_seconds'))}"
    )


def _active_export_music_details(paths: ProjectPaths) -> str | None:
    try:
        active = ExportAssetManager(paths).active_version()
    except ExportAssetError:
        return None
    if not active or not isinstance(active.get("music_mix"), dict):
        return None
    mix = active["music_mix"]
    music_version = active.get("music_version") or mix.get("music_version")
    if music_version is None:
        return None
    base = float(mix.get("base_volume", 0.25))
    ducked = float(mix.get("ducked_volume", base))
    return (
        f"Music：v{int(music_version):03d}\n"
        f"Ducking：{'Enabled' if mix.get('ducking_enabled') else 'Disabled'}\n"
        f"Base / Ducked：{base:.0%} / {ducked:.0%}\n"
        "Fade In / Out："
        f"{_seconds(mix.get('fade_in_seconds'))} / "
        f"{_seconds(mix.get('fade_out_seconds'))}"
    )


def _voice_generation_menu(
    paths: ProjectPaths,
    checkpoint: Any,
    task_logger: TaskLogger,
    *,
    registry: VoiceProviderRegistry | None,
    input_fn: InputFn,
    output_fn: OutputFn,
    generate_voice_fn: Callable[..., dict | None],
) -> None:
    manager = VoiceAssetManager(paths)
    active = manager.active_version()
    if active:
        output_fn("\n当前已有配音：")
        output_fn(
            f"Voice v{int(active['version']):03d}\n"
            f"{paths.project_path / str(active['audio_path'])}\n"
            f"Source:\n{_voice_source_label(active.get('script_source'))}"
        )
        planned = active.get("planned_narration_duration")
        actual = active.get("actual_audio_duration", active.get("duration_seconds"))
        if planned is not None:
            output_fn(f"\nPlanned:\n{float(planned):g}s")
        if actual is not None:
            output_fn(f"\nActual:\n{float(actual):g}s")
        if planned is not None and actual is not None:
            _display_voice_calibration(active, output_fn)
        output_fn("\nResume 不会自动重复生成配音。")
        output_fn("\n1. 保留当前配音并返回\n2. 生成新的配音版本")
        while True:
            choice = input_fn("请输入 1 或 2: ").strip()
            if choice == "1":
                return
            if choice == "2":
                break
            output_fn("输入无效，请重新选择。")

    try:
        storyboard_voice = load_storyboard_voice_script(paths)
    except VoiceScriptBuilderError as exc:
        task_logger.error(exc, stage="post_production_voice_source")
        output_fn(f"\nStoryboard 旁白规划无法读取：{exc}")
        storyboard_voice = None
    if storyboard_voice is not None:
        output_fn("\nVoice Source:\nStoryboard Planned")
        script = storyboard_voice.script
        request_settings = storyboard_voice.request_settings()
    else:
        output_fn("\n当前 Storyboard 未规划旁白。")
        output_fn("\n1. 手动添加配音\n2. 返回")
        while True:
            source_choice = input_fn("请输入 1 或 2: ").strip()
            if source_choice == "2":
                return
            if source_choice == "1":
                break
            output_fn("输入无效，请重新选择。")
        script = _read_multiline_script(input_fn, output_fn)
        if not script:
            output_fn("配音文本为空，未调用 TTS。")
            return
        request_settings = {"script_source": "manual"}
    config = checkpoint.data["voice_config"]
    selected_registry = registry or build_voice_provider_registry()
    selected_name = str(
        config.get("provider")
        or selected_registry.config.get("default_provider")
        or ""
    ).strip()
    provider_config = (
        (selected_registry.config.get("providers") or {}).get(selected_name) or {}
    )
    default_voice = str(
        config.get("voice") or provider_config.get("default_voice") or "xiaoyun"
    )
    voice = input_fn(f"Voice 名称（默认 {default_voice}）: ").strip() or default_voice
    language = str(config.get("language") or "zh-CN")
    request = VoiceGenerationRequest(
        script=script,
        voice=voice,
        language=language,
        output_format="wav",
        settings=request_settings,
    )
    current_request = request
    while True:
        task_logger.set_stage("post_production_voice")
        task_logger.event(
            "VOICE_GENERATION_READY",
            voice=current_request.voice,
            language=current_request.language,
            script_source=current_request.settings.get("script_source"),
            cue_count=current_request.settings.get("cue_count"),
        )
        try:
            entry = generate_voice_fn(
                manager,
                selected_registry,
                current_request,
                provider_name=config.get("provider"),
                input_fn=input_fn,
                output_fn=output_fn,
            )
        except (VoiceProviderError, VoiceAssetError, ValueError) as exc:
            task_logger.error(exc, stage="post_production_voice")
            output_fn(f"\n配音生成失败：{exc}")
            return
        if entry is None:
            return
        config.update(
            {
                "enabled": True,
                "provider": entry.get("provider"),
                "voice": entry.get("voice") or current_request.voice,
                "language": entry.get("language") or current_request.language,
            }
        )
        PostProductionPipeline(checkpoint).mark_component_completed(
            "voice",
            version=int(entry["version"]),
            path=str(entry["audio_path"]),
            created_at=entry.get("created_at"),
        )
        calibration = entry.get("timing_calibration") or {}
        task_logger.event(
            "VOICE_GENERATION_COMPLETED",
            voice_version=entry["version"],
            audio_path=entry["audio_path"],
            script_source=entry.get("script_source"),
            planned_narration_duration=entry.get("planned_narration_duration"),
            actual_audio_duration=entry.get("actual_audio_duration"),
            calibration_status=calibration.get("status"),
        )
        output_fn(
            f"\n配音生成完成：\n{paths.project_path / str(entry['audio_path'])}"
        )
        action = _review_voice_calibration(
            entry, input_fn=input_fn, output_fn=output_fn
        )
        if action == "accept":
            if calibration.get("status") == "OUT_OF_TOLERANCE":
                entry = manager.set_timing_acceptance(
                    int(entry["version"]), accepted=True
                )
            task_logger.event(
                "VOICE_TIMING_ACCEPTED",
                voice_version=entry["version"],
                calibration_status=calibration.get("status"),
            )
            return
        if action == "return":
            return
        current_request = _request_from_voice_entry(paths, entry, current_request)
        if action == "edit":
            edited = _read_multiline_script(input_fn, output_fn)
            if not edited:
                output_fn("配音文本为空，未重新生成。")
                return
            updated_settings = dict(current_request.settings)
            if updated_settings.get("script_source") in {
                "compiled_storyboard",
                "storyboard_edited",
            }:
                updated_settings["script_source"] = "storyboard_edited"
            else:
                updated_settings["script_source"] = "manual"
            current_request = VoiceGenerationRequest(
                script=edited,
                voice=current_request.voice,
                language=current_request.language,
                output_format=current_request.output_format,
                settings=updated_settings,
            )
        task_logger.event(
            "VOICE_TIMING_REGENERATION_SELECTED",
            previous_voice_version=entry["version"],
            action=action,
        )


def _subtitle_generation_menu(
    paths: ProjectPaths,
    checkpoint: Any,
    task_logger: TaskLogger,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> None:
    manager = SubtitleAssetManager(paths)
    active = manager.active_version()
    if active:
        subtitle_path = paths.ensure_within_project(
            paths.project_path / str(active["subtitle_path"])
        )
        output_fn(
            "\n当前已有字幕：\n"
            f"Subtitle v{int(active['version']):03d}\n{subtitle_path}\n"
            "Subtitle Source:\n"
            + (
                "Storyboard Planned"
                if active.get("provider") == "storyboard_subtitle"
                else "Script Fallback"
            )
        )
        output_fn("\nResume 不会自动重复生成字幕。")
        output_fn("\n1. 保留当前字幕并返回\n2. 生成新的字幕版本")
        while True:
            choice = input_fn("请输入 1 或 2: ").strip()
            if choice == "1":
                PostProductionPipeline(checkpoint).mark_component_completed(
                    "subtitle",
                    version=int(active["version"]),
                    path=str(active["subtitle_path"]),
                    created_at=active.get("created_at"),
                )
                return
            if choice == "2":
                break
            output_fn("输入无效，请重新选择。")
    task_logger.set_stage("post_production_subtitle")
    task_logger.event("SUBTITLE_GENERATION_STARTED")
    try:
        source_label = subtitle_source_label(paths)
        output_fn(f"\nSubtitle Source:\n{source_label}")
        entry = generate_subtitle_for_project(
            manager,
            build_subtitle_provider_registry(),
        )
    except (SubtitleProviderError, SubtitleAssetError, ValueError) as exc:
        task_logger.error(exc, stage="post_production_subtitle")
        output_fn(f"\n字幕生成失败：{exc}")
        return
    PostProductionPipeline(checkpoint).mark_component_completed(
        "subtitle",
        version=int(entry["version"]),
        path=str(entry["subtitle_path"]),
        created_at=entry.get("created_at"),
    )
    task_logger.event(
        "SUBTITLE_GENERATION_COMPLETED",
        subtitle_version=entry["version"],
        subtitle_path=entry["subtitle_path"],
        source_voice_version=entry.get("source_voice_version"),
        subtitle_source=entry.get("source"),
        subtitle_provider=entry.get("provider"),
    )
    output_fn(
        "\n字幕生成完成：\n"
        f"{paths.project_path / str(entry['subtitle_path'])}"
    )


def _read_music_volume(
    default: float,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> float:
    while True:
        raw = input_fn(f"背景音乐音量（0.0-1.0，默认 {default:g}）: ").strip()
        if not raw:
            return default
        try:
            volume = float(raw)
        except ValueError:
            output_fn("音量必须是 0.0 到 1.0 之间的数字。")
            continue
        if 0.0 <= volume <= 1.0:
            return volume
        output_fn("音量必须是 0.0 到 1.0 之间的数字。")


def _read_nonnegative_seconds(
    label: str,
    default: float,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> float:
    while True:
        raw = input_fn(f"{label}（秒，默认 {default:g}）: ").strip()
        if not raw:
            return default
        try:
            value = float(raw)
        except ValueError:
            output_fn(f"{label} 必须是大于等于 0 的数字。")
            continue
        if value >= 0:
            return value
        output_fn(f"{label} 必须是大于等于 0 的数字。")


def _music_menu(
    paths: ProjectPaths,
    checkpoint: Any,
    task_logger: TaskLogger,
    *,
    input_fn: InputFn,
    output_fn: OutputFn,
) -> None:
    manager = MusicAssetManager(paths)
    mix_manager = MusicMixSettingsManager(checkpoint)
    while True:
        active = manager.active_version()
        output_fn("\n========== Background Music ==========")
        if active:
            music_path = paths.ensure_within_project(
                paths.project_path / str(active["music_path"])
            )
            settings = mix_manager.current(
                legacy_base_volume=float(active.get("music_volume", 0.25))
            )
            base = float(settings["base_volume"])
            ducked = base * float(settings["ducking_ratio"])
            output_fn(
                f"\n当前音乐：Music v{int(active['version']):03d}"
                f"\n文件：{music_path}"
                f"\n\nBase Volume：{base:.0%}"
                f"\nDucking：{'Enabled' if settings['ducking_enabled'] else 'Disabled'}"
                f"\nDuring Voice：{float(settings['ducking_ratio']):.0%} of Base"
                f"\nEffective：{ducked:.0%}"
                f"\nAttack / Release："
                f"{float(settings['duck_attack_seconds']):g}s / "
                f"{float(settings['duck_release_seconds']):g}s"
                f"\nFade In / Out：{float(settings['fade_in_seconds']):g}s / "
                f"{float(settings['fade_out_seconds']):g}s"
            )
            PostProductionPipeline(checkpoint).mark_component_completed(
                "music",
                version=int(active["version"]),
                path=str(active["music_path"]),
                created_at=active.get("created_at"),
            )
            output_fn(
                "\n1. 查看 / 更换背景音乐"
                "\n2. 调整基础音量"
                "\n3. 调整 Voice Ducking"
                "\n4. 调整 Fade In / Fade Out"
                "\n5. 恢复默认混音设置"
                "\n6. 返回"
            )
            choice = input_fn("请输入 1-6: ").strip()
            if choice == "1":
                output_fn(
                    "\n当前背景音乐信息："
                    f"\n原文件名：{active['original_filename']}"
                    f"\n格式：{active['extension']}"
                    f"\n大小：{active['size_bytes']} bytes"
                    f"\nSHA-256：{active['sha256']}"
                    f"\n路径：{music_path}"
                )
                output_fn("\n1. 更换背景音乐\n2. 返回")
                replace_choice = input_fn("请输入 1 或 2: ").strip()
                if replace_choice == "1":
                    default_volume = base
                    break
                if replace_choice != "2":
                    output_fn("输入无效，已返回 Music 菜单。")
                continue
            if choice == "2":
                value = _read_music_volume(base, input_fn, output_fn)
                mix_manager.update(
                    legacy_base_volume=float(active.get("music_volume", 0.25)),
                    base_volume=value,
                )
                task_logger.event(
                    "MUSIC_MIX_SETTINGS_UPDATED",
                    setting="base_volume",
                    value=value,
                    music_version=active["version"],
                )
                output_fn("基础音量已更新；Music Asset Version 未改变。")
                continue
            if choice == "3":
                output_fn("\n1. 启用 Ducking\n2. 关闭 Ducking\n3. 返回")
                duck_choice = input_fn("请输入 1-3: ").strip()
                if duck_choice == "3":
                    continue
                if duck_choice not in {"1", "2"}:
                    output_fn("输入无效，请重新选择。")
                    continue
                updates: dict[str, Any] = {
                    "ducking_enabled": duck_choice == "1"
                }
                if duck_choice == "1":
                    ratio = _read_music_volume(
                        float(settings["ducking_ratio"]), input_fn, output_fn
                    )
                    attack = _read_nonnegative_seconds(
                        "Attack",
                        float(settings["duck_attack_seconds"]),
                        input_fn,
                        output_fn,
                    )
                    release = _read_nonnegative_seconds(
                        "Release",
                        float(settings["duck_release_seconds"]),
                        input_fn,
                        output_fn,
                    )
                    updates.update(
                        {
                            "ducking_ratio": ratio,
                            "duck_attack_seconds": attack,
                            "duck_release_seconds": release,
                        }
                    )
                mix_manager.update(
                    legacy_base_volume=float(active.get("music_volume", 0.25)),
                    **updates,
                )
                task_logger.event(
                    "MUSIC_MIX_SETTINGS_UPDATED",
                    setting="voice_ducking",
                    music_version=active["version"],
                    **updates,
                )
                output_fn("Voice Ducking 设置已更新；Music Asset Version 未改变。")
                continue
            if choice == "4":
                fade_in = _read_nonnegative_seconds(
                    "Fade In",
                    float(settings["fade_in_seconds"]),
                    input_fn,
                    output_fn,
                )
                fade_out = _read_nonnegative_seconds(
                    "Fade Out",
                    float(settings["fade_out_seconds"]),
                    input_fn,
                    output_fn,
                )
                mix_manager.update(
                    legacy_base_volume=float(active.get("music_volume", 0.25)),
                    fade_in_seconds=fade_in,
                    fade_out_seconds=fade_out,
                )
                task_logger.event(
                    "MUSIC_MIX_SETTINGS_UPDATED",
                    setting="fade",
                    fade_in_seconds=fade_in,
                    fade_out_seconds=fade_out,
                    music_version=active["version"],
                )
                output_fn("Fade 设置已更新；Music Asset Version 未改变。")
                continue
            if choice == "5":
                mix_manager.reset(base_volume=0.25)
                task_logger.event(
                    "MUSIC_MIX_SETTINGS_RESET",
                    music_version=active["version"],
                )
                output_fn("Music Mix 已恢复 Phase 2.5 默认设置。")
                continue
            if choice == "6":
                return
            output_fn("输入无效，请重新选择。")
            continue
        output_fn("\n当前没有背景音乐。")
        output_fn("\n1. 上传音乐\n2. 返回")
        choice = input_fn("请输入 1 或 2: ").strip()
        if choice == "1":
            default_volume = 0.25
            break
        if choice == "2":
            return
        output_fn("输入无效，请重新选择。")

    source_path = input_fn("请输入本地背景音乐文件路径：").strip().strip('"')
    volume = _read_music_volume(default_volume, input_fn, output_fn)
    task_logger.set_stage("post_production_music")
    task_logger.event(
        "MUSIC_IMPORT_STARTED",
        source_filename=Path(source_path).name,
        music_volume=volume,
    )
    try:
        entry = add_local_music(
            manager,
            build_music_provider_registry(),
            source_path,
            music_volume=volume,
        )
    except (MusicProviderError, MusicAssetError, ValueError, OSError) as exc:
        task_logger.error(exc, stage="post_production_music")
        output_fn(f"\n背景音乐导入失败：{exc}")
        return
    try:
        mix_manager.update(base_volume=volume)
    except MusicMixError as exc:
        task_logger.error(exc, stage="post_production_music_mix")
        output_fn(f"\n背景音乐已导入，但 Mix 设置保存失败：{exc}")
        return
    PostProductionPipeline(checkpoint).mark_component_completed(
        "music",
        version=int(entry["version"]),
        path=str(entry["music_path"]),
        created_at=entry.get("created_at"),
    )
    task_logger.event(
        "MUSIC_IMPORT_COMPLETED",
        music_version=entry["version"],
        music_path=entry["music_path"],
        music_volume=entry["music_volume"],
    )
    output_fn(
        "\n背景音乐导入完成：\n"
        f"{paths.project_path / str(entry['music_path'])}"
    )


def post_production_menu(
    paths: ProjectPaths,
    checkpoint: Any,
    task_logger: TaskLogger,
    *,
    voice_registry: VoiceProviderRegistry | None = None,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
    generate_voice_fn: Callable[..., dict | None] = generate_confirmed_voice,
    export_pipeline_factory: Callable[..., Any] = ExportPipeline,
    open_path_fn: Callable[[Path], None] = _open_path,
) -> None:
    pipeline = PostProductionPipeline(checkpoint)
    pipeline.sync_from_existing_assets()
    pipeline.enter_post_production()
    task_logger.event("POST_PRODUCTION_ENTERED")
    while True:
        pipeline.sync_from_existing_assets()
        final_video = _assembly_video_path(paths, checkpoint)
        components = pipeline.data["components"]
        output_fn("\n========== Post Production ==========")
        output_fn(f"\n当前视频：\n{final_video or '完整视频不存在'}")
        output_fn("\n状态：")
        output_fn("视频：\n已完成")
        voice_details = _voice_status_details(paths)
        output_fn(
            "配音：\n"
            + (voice_details or _status_label(components["voice"]["status"]))
        )
        output_fn(f"字幕：\n{_status_label(components['subtitle']['status'])}")
        output_fn(f"音乐：\n{_status_label(components['music']['status'])}")
        output_fn(
            "最终导出：\n"
            + _export_status_label(
                components["final_export"],
                assembly_needs_update=bool(
                    checkpoint.assembly_checkpoint().get("needs_update")
                ),
            )
        )
        output_fn(
            "\n请选择：\n\n1. 配音制作\n\n2. 字幕制作\n\n3. 背景音乐"
            "\n\n4. 导出最终视频\n\n5. 返回"
        )
        choice = input_fn("请输入 1-5: ").strip()
        if choice == "1":
            _voice_generation_menu(
                paths,
                checkpoint,
                task_logger,
                registry=voice_registry,
                input_fn=input_fn,
                output_fn=output_fn,
                generate_voice_fn=generate_voice_fn,
            )
            continue
        if choice == "2":
            _subtitle_generation_menu(
                paths,
                checkpoint,
                task_logger,
                input_fn=input_fn,
                output_fn=output_fn,
            )
            continue
        if choice == "3":
            _music_menu(
                paths,
                checkpoint,
                task_logger,
                input_fn=input_fn,
                output_fn=output_fn,
            )
            continue
        if choice == "4":
            exporter = export_pipeline_factory(paths, checkpoint, task_logger)
            try:
                export_inputs = exporter.collect_inputs()
                prepare = getattr(exporter, "prepare_inputs_for_confirmation", None)
                if callable(prepare):
                    export_inputs = prepare(export_inputs)
            except ExportPipelineError as exc:
                output_fn(f"Final Export 失败：{exc}")
                continue
            summary = export_inputs.summary()
            voice_timing_status = str(
                (summary.get("voice_timing") or {}).get("status") or ""
            )
            if voice_timing_status == "OUT_OF_BOUNDS":
                output_fn("\n========== Voice Timing Blocked ==========")
                _display_export_voice_timing(summary, output_fn)
                output_fn("\nVoice Audio exceeds final video duration.")
                output_fn("\n1. 返回配音制作\n2. 返回后期制作")
                while True:
                    blocked_choice = input_fn("请输入 1 或 2: ").strip()
                    if blocked_choice in {"1", "2"}:
                        break
                    output_fn("输入无效，请重新选择。")
                if blocked_choice == "1":
                    _voice_generation_menu(
                        paths,
                        checkpoint,
                        task_logger,
                        registry=voice_registry,
                        input_fn=input_fn,
                        output_fn=output_fn,
                        generate_voice_fn=generate_voice_fn,
                    )
                continue
            existing = exporter.find_existing_export(export_inputs)
            force_export = False
            if existing:
                existing_entry = existing["entry"]
                existing_path = Path(existing["final_video_path"])
                output_fn("\n========== Export Already Exists ==========")
                output_fn("\n当前素材组合已经成功导出。")
                output_fn(
                    f"\nExisting Export:\nv{int(existing_entry['version']):03d}"
                    f"\n\nVideo:\n{summary['video_path']}"
                    "\n\nVoice:\n"
                    + (
                        f"v{int(summary['voice_version']):03d}"
                        if summary.get("voice_version") is not None
                        else "未选择"
                    )
                    + "\n\nSubtitle:\n"
                    + (
                        f"v{int(summary['subtitle_version']):03d}"
                        if summary.get("subtitle_version") is not None
                        else "未选择"
                    )
                    + "\n\nMusic:\n"
                    + (
                        f"v{int(summary['music_version']):03d}"
                        if summary.get("music_version") is not None
                        else "未选择"
                    )
                )
                _display_export_voice_timing(summary, output_fn)
                _display_music_mix(summary.get("music_mix"), output_fn)
                while True:
                    output_fn(
                        "\n请选择：\n\n1. 查看已有最终视频"
                        "\n2. 强制重新导出为新版本\n3. 返回"
                    )
                    duplicate_choice = input_fn("请输入 1-3: ").strip()
                    if duplicate_choice == "1":
                        try:
                            open_path_fn(existing_path)
                        except OSError as exc:
                            output_fn(f"无法打开最终视频：{exc}")
                        continue
                    if duplicate_choice == "2":
                        force_export = True
                        break
                    if duplicate_choice == "3":
                        break
                    output_fn("输入无效，请重新选择。")
                if duplicate_choice == "3":
                    continue
            else:
                output_fn("\n========== Final Export Confirmation ==========")
                output_fn(f"\n当前素材：\n\nVideo:\n{summary['video_path']}")
                output_fn(
                    "\nVoice:\n"
                    + (
                        f"v{int(summary['voice_version']):03d}"
                        if summary.get("voice_version") is not None
                        else "未选择"
                    )
                )
                output_fn(
                    "\nVoice Source:\n"
                    f"{_voice_source_label(summary.get('voice_source'))}"
                    if summary.get("voice_version") is not None
                    else "\nVoice:\n未选择"
                )
                timing_status = _display_export_voice_timing(summary, output_fn)
                output_fn(
                    "\nSubtitle:\n"
                    + (
                        f"v{int(summary['subtitle_version']):03d}"
                        if summary.get("subtitle_version") is not None
                        else "未选择"
                    )
                )
                output_fn(
                    "\nMusic:\n"
                    + (
                        f"v{int(summary['music_version']):03d}"
                        f"（音量 {float(summary['music_volume']):g}）"
                        if summary.get("music_version") is not None
                        else "未选择"
                    )
                )
                _display_music_mix(summary.get("music_mix"), output_fn)
                if timing_status == "OUT_OF_TOLERANCE" and not bool(
                    ((summary.get("voice_timing") or {}).get("timing_acceptance") or {}).get(
                        "accepted"
                    )
                ):
                    output_fn(
                        "\n当前 Voice Timing 为 OUT_OF_TOLERANCE，尚未接受。"
                    )
                    output_fn(
                        "\n1. 明确接受当前配音并导出"
                        "\n2. 修改 Music Mix"
                        "\n3. 查看 Voice Timing 详情\n4. 返回"
                    )
                    requires_timing_acceptance = True
                else:
                    output_fn(
                        "\n1. 确认导出\n2. 修改 Music Mix"
                        "\n3. 查看 Voice Timing 详情\n4. 返回"
                    )
                    requires_timing_acceptance = False
                return_to_post_production = False
                while True:
                    confirmation = input_fn("请输入 1-4: ").strip()
                    if confirmation == "2":
                        _music_menu(
                            paths,
                            checkpoint,
                            task_logger,
                            input_fn=input_fn,
                            output_fn=output_fn,
                        )
                        return_to_post_production = True
                        break
                    if confirmation == "3":
                        _display_export_voice_timing(summary, output_fn)
                        continue
                    if confirmation in {"1", "4"}:
                        break
                    output_fn("输入无效，请重新选择。")
                if return_to_post_production:
                    continue
                if confirmation == "4":
                    output_fn("已取消 Final Export。")
                    continue
                if requires_timing_acceptance:
                    voice_version = summary.get("voice_version")
                    if voice_version is None:
                        output_fn("Voice Version 不存在，无法保存 Timing Acceptance。")
                        continue
                    VoiceAssetManager(paths).set_timing_acceptance(
                        int(voice_version), accepted=True
                    )
                    export_inputs = exporter.collect_inputs()
                    summary = export_inputs.summary()
                    task_logger.event(
                        "VOICE_TIMING_ACCEPTED_FOR_EXPORT",
                        voice_version=voice_version,
                    )
            task_logger.set_stage("final_export")
            task_logger.event("FINAL_EXPORT_CONFIRMED")
            try:
                entry = exporter.export_current(force=force_export)
            except ExportAlreadyExistsError:
                output_fn("当前素材组合已经成功导出，未创建重复版本。")
                continue
            except ExportPipelineError as exc:
                task_logger.error(exc, stage="final_export")
                output_fn(f"Final Export 失败：{exc}")
                continue
            output_fn("\n========== Final Export 完成 ==========")
            output_fn(f"Export v{int(entry['version']):03d}")
            final_relative = entry.get("final_video_path") or entry.get("video_path")
            final_path = paths.ensure_within_project(paths.project_path / str(final_relative))
            output_fn(f"最终视频：\n{final_path}")
            output_fn("\n使用素材：")
            output_fn(f"Video: v{summary['video_version'] or '未知'}")
            output_fn(
                "Voice: "
                + (
                    f"v{int(summary['voice_version']):03d}"
                    if summary.get("voice_version") is not None
                    else "未选择"
                )
            )
            output_fn(
                "Subtitle: "
                + (
                    f"v{int(summary['subtitle_version']):03d}"
                    if summary.get("subtitle_version") is not None
                    else "未选择"
                )
            )
            output_fn(
                "Music: "
                + (
                    f"v{int(summary['music_version']):03d}"
                    if summary.get("music_version") is not None
                    else "未选择"
                )
            )
            _display_music_mix(summary.get("music_mix"), output_fn)
            while True:
                output_fn(
                    "\n请选择：\n\n1. 打开最终视频"
                    "\n2. 打开最终视频所在文件夹"
                    "\n3. 返回后期制作\n4. 返回项目主页"
                )
                success_choice = input_fn("请输入 1-4: ").strip()
                if success_choice == "1":
                    try:
                        open_path_fn(final_path)
                    except OSError as exc:
                        output_fn(f"无法打开最终视频：{exc}")
                    continue
                if success_choice == "2":
                    try:
                        open_path_fn(final_path.parent)
                    except OSError as exc:
                        output_fn(f"无法打开最终视频所在文件夹：{exc}")
                    continue
                if success_choice == "3":
                    break
                if success_choice == "4":
                    return
                output_fn("输入无效，请重新选择。")
            continue
        if choice == "5":
            return
        output_fn("输入无效，请重新选择。")


def project_resume_menu(
    paths: ProjectPaths,
    checkpoint: Any,
    task_logger: TaskLogger,
    *,
    regenerate_assembly: Callable[[], Path | None],
    open_shot_management: Callable[[], None],
    voice_registry: VoiceProviderRegistry | None = None,
    input_fn: InputFn = input,
    output_fn: OutputFn = print,
    open_path_fn: Callable[[Path], None] = _open_path,
    generate_voice_fn: Callable[..., dict | None] = generate_confirmed_voice,
    export_pipeline_factory: Callable[..., Any] = ExportPipeline,
) -> None:
    pipeline = PostProductionPipeline(checkpoint)
    pipeline.sync_from_existing_assets()
    while True:
        final_video = _assembly_video_path(paths, checkpoint)
        components = pipeline.data["components"]
        output_fn("\n========== Project Resume ==========")
        output_fn(f"\n项目已有完整视频：\n\n路径：\n{final_video}")
        output_fn("\n视频状态：\nVideo Assembly Completed")
        output_fn("\n后期状态：")
        voice_details = _voice_status_details(paths)
        output_fn(
            "\n配音：\n"
            + (voice_details or _status_label(components["voice"]["status"]))
        )
        output_fn(
            f"\n字幕：\n{_status_label(components['subtitle']['status'])}"
        )
        output_fn(f"\n音乐：\n{_status_label(components['music']['status'])}")
        export = components["final_export"]
        if export.get("status") == PostProductionStatus.COMPLETED.value:
            update_label = (
                "，需要更新"
                if checkpoint.assembly_checkpoint().get("needs_update")
                else ""
            )
            output_fn(
                f"\n最终导出：\n已完成（当前 v{int(export['active_version']):03d}"
                f"{update_label}）\n"
                f"{paths.project_path / str(export['path'])}"
            )
            export_voice = _active_export_voice_details(paths)
            if export_voice:
                output_fn(f"\n{export_voice}")
            export_music = _active_export_music_details(paths)
            if export_music:
                output_fn(f"\n{export_music}")
        else:
            output_fn("\n最终导出：\n未生成")
        output_fn(
            "\n请选择：\n\n1. 查看完整视频\n2. 进入后期制作"
            "\n3. 重新生成完整视频\n4. Shot 管理\n5. 返回"
        )
        choice = input_fn("请输入 1-5: ").strip()
        if choice == "1":
            if final_video is None:
                output_fn("记录的完整视频不存在，请重新生成完整视频。")
            else:
                try:
                    open_path_fn(final_video)
                except OSError as exc:
                    output_fn(f"无法打开完整视频：{exc}")
            continue
        if choice == "2":
            post_production_menu(
                paths,
                checkpoint,
                task_logger,
                voice_registry=voice_registry,
                input_fn=input_fn,
                output_fn=output_fn,
                generate_voice_fn=generate_voice_fn,
                export_pipeline_factory=export_pipeline_factory,
                open_path_fn=open_path_fn,
            )
            pipeline.sync_from_existing_assets()
            continue
        if choice == "3":
            regenerate_assembly()
            pipeline.sync_from_existing_assets()
            continue
        if choice == "4":
            open_shot_management()
            pipeline.sync_from_existing_assets()
            continue
        if choice == "5":
            return
        output_fn("输入无效，请重新选择。")
