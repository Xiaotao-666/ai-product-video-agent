"""FFmpeg-based Final Export orchestration for local post-production assets."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import subprocess
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from export_assets import ExportAssetError, ExportAssetManager
from music_assets import MusicAssetError, MusicAssetManager
from music_mix import (
    MusicMixError,
    MusicMixSettingsManager,
    build_music_mix_plan,
    music_ducking_expression,
    music_mix_fingerprint,
)
from post_production import PostProductionPipeline, PostProductionStage
from project_manager import ProjectDirectoryError, ProjectPaths
from subtitle_assets import SubtitleAssetError, SubtitleAssetManager
from voice_assets import VoiceAssetError, VoiceAssetManager


CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
WhichFn = Callable[[str], str | None]


class ExportPipelineError(RuntimeError):
    """Raised when Final Export preflight, encoding, or validation fails."""


class ExportAlreadyExistsError(ExportPipelineError):
    """Raised when the latest successful Export has the same exact inputs."""

    def __init__(self, existing: dict[str, Any]) -> None:
        super().__init__("当前素材组合已经成功导出。")
        self.existing = deepcopy(existing)


DEFAULT_SUBTITLE_STYLE: dict[str, Any] = {
    "position": "bottom_center",
    "alignment": 2,
    "font_name": "Arial",
    "font_size": 24,
    "font_color": "white",
    "outline_color": "black",
    "outline_width": 2,
    "shadow": 0,
    "margin_vertical": 36,
}

EXPORT_RENDER_CONFIG: dict[str, Any] = {
    "voice_volume": 1.0,
    "audio_codec": "aac",
    "audio_bitrate": "192k",
    "subtitle_video_codec": "libx264",
    "subtitle_video_preset": "medium",
    "subtitle_video_crf": 18,
    "subtitle_pixel_format": "yuv420p",
    "plain_video_codec": "copy",
    "movflags": "+faststart",
}


@dataclass(frozen=True)
class ExportInputs:
    video_path: Path
    assembly_version: int | None
    voice: dict[str, Any] | None
    voice_path: Path | None
    voice_timing: dict[str, Any] | None
    subtitle: dict[str, Any] | None
    subtitle_path: Path | None
    music: dict[str, Any] | None
    music_path: Path | None
    music_volume: float
    music_mix: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "video_path": str(self.video_path),
            "video_version": self.assembly_version,
            "voice_version": self.voice.get("version") if self.voice else None,
            "voice_path": str(self.voice_path) if self.voice_path else None,
            "voice_source": self.voice.get("script_source") if self.voice else None,
            "voice_timing": deepcopy(self.voice_timing),
            "subtitle_version": (
                self.subtitle.get("version") if self.subtitle else None
            ),
            "subtitle_path": str(self.subtitle_path) if self.subtitle_path else None,
            "music_version": self.music.get("version") if self.music else None,
            "music_path": str(self.music_path) if self.music_path else None,
            "music_volume": self.music_volume if self.music else None,
            "music_mix": deepcopy(self.music_mix) if self.music else None,
        }


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class ExportPipeline:
    """Select active assets, render one immutable Export version, and checkpoint it."""

    def __init__(
        self,
        project: ProjectPaths,
        checkpoint: Any,
        task_logger: Any | None = None,
        *,
        runner: CommandRunner = subprocess.run,
        which: WhichFn = shutil.which,
    ) -> None:
        self.project = project
        self.checkpoint = checkpoint
        self.task_logger = task_logger
        self.runner = runner
        self.which = which
        self.assets = ExportAssetManager(project)

    def active_version(self) -> dict[str, Any] | None:
        return self.assets.active_version()

    def prepare_inputs_for_confirmation(
        self, inputs: ExportInputs | None = None
    ) -> ExportInputs:
        """Resolve media durations so the UI shows the exact render envelope."""

        return self._resolve_music_mix_for_fingerprint(
            inputs or self.collect_inputs()
        )

    def build_input_fingerprint(self, inputs: ExportInputs) -> dict[str, Any]:
        """Describe every immutable input and setting that can affect bytes."""
        return {
            "fingerprint_schema_version": 3,
            "video": {
                "version": inputs.assembly_version,
                "sha256": self._sha256(inputs.video_path),
            },
            "voice": (
                {
                    "version": int(inputs.voice["version"]),
                    "sha256": self._sha256(inputs.voice_path),
                    "timing": self._voice_timing_fingerprint(
                        inputs.voice_timing
                    ),
                }
                if inputs.voice and inputs.voice_path
                else None
            ),
            "subtitle": (
                {
                    "version": int(inputs.subtitle["version"]),
                    "sha256": self._sha256(inputs.subtitle_path),
                }
                if inputs.subtitle and inputs.subtitle_path
                else None
            ),
            "music": (
                {
                    "version": int(inputs.music["version"]),
                    "sha256": self._sha256(inputs.music_path),
                    "mix": music_mix_fingerprint(inputs.music_mix),
                }
                if inputs.music and inputs.music_path
                else None
            ),
            "subtitle_style": (
                deepcopy(DEFAULT_SUBTITLE_STYLE) if inputs.subtitle else None
            ),
            "render_config": deepcopy(EXPORT_RENDER_CONFIG),
        }

    def find_existing_export(
        self, inputs: ExportInputs | None = None
    ) -> dict[str, Any] | None:
        """Return the active successful Export when its fingerprint is identical."""
        selected = self._resolve_music_mix_for_fingerprint(
            inputs or self.collect_inputs()
        )
        current_fingerprint = self.build_input_fingerprint(selected)
        current_digest = self._fingerprint_digest(current_fingerprint)
        entry = self.assets.active_version()
        if not entry:
            return None
        output_relative = entry.get("final_video_path") or entry.get("video_path")
        if not output_relative:
            return None
        try:
            output_path = self._project_file(str(output_relative), "已有 Final Export")
            manifest_relative = entry.get("manifest_path") or entry.get("metadata_path")
            if not manifest_relative:
                return None
            manifest_path = self._project_file(
                str(manifest_relative), "已有 Export Manifest"
            )
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (ExportPipelineError, OSError, json.JSONDecodeError, TypeError):
            return None
        if (
            payload.get("input_fingerprint_sha256") != current_digest
            or payload.get("input_fingerprint") != current_fingerprint
        ):
            return None
        return {
            "entry": deepcopy(entry),
            "manifest": payload,
            "final_video_path": output_path,
            "input_fingerprint": current_fingerprint,
        }

    def collect_inputs(self) -> ExportInputs:
        assembly = self.checkpoint.assembly_checkpoint()
        if str(assembly.get("status")) != "COMPLETED":
            raise ExportPipelineError("完整视频尚未合片完成，不能导出。")
        if bool(assembly.get("needs_update")):
            raise ExportPipelineError(
                "完整视频已过期，请先使用最新 Approved Shot 重新合片。"
            )
        source_relative = assembly.get("final_video_path")
        if not source_relative:
            raise ExportPipelineError("Assembly 未记录 final_video_path。")
        video_path = self._project_file(str(source_relative), "完整视频")

        try:
            voice = VoiceAssetManager(self.project).active_version()
            subtitle = SubtitleAssetManager(self.project).active_version()
            music = MusicAssetManager(self.project).active_version()
        except (VoiceAssetError, SubtitleAssetError, MusicAssetError) as exc:
            raise ExportPipelineError(str(exc)) from exc

        voice_path = (
            self._project_file(str(voice.get("audio_path") or ""), "配音")
            if voice
            else None
        )
        voice_timing = self._load_voice_timing(voice) if voice else None
        subtitle_path = (
            self._project_file(str(subtitle.get("subtitle_path") or ""), "字幕")
            if subtitle
            else None
        )
        music_path = (
            self._project_file(str(music.get("music_path") or ""), "背景音乐")
            if music
            else None
        )
        music_volume = float(music.get("music_volume", 0.25)) if music else 0.25
        music_mix = None
        if music:
            try:
                settings = MusicMixSettingsManager(self.checkpoint).current(
                    legacy_base_volume=music_volume
                )
                music_volume = float(settings["base_volume"])
                music_duration = self._optional_positive_number(
                    music.get("duration_seconds") or music.get("duration")
                )
                video_duration = self._optional_positive_number(
                    assembly.get("total_duration")
                )
                if music_duration is not None and video_duration is not None:
                    music_mix = build_music_mix_plan(
                        settings,
                        original_music_duration=music_duration,
                        video_duration=video_duration,
                        voice_timing=voice_timing,
                    )
                else:
                    music_mix = {
                        **deepcopy(settings),
                        "original_duration": music_duration,
                        "video_duration": video_duration,
                        "effective_music_end": None,
                        "media_timing_resolved": False,
                    }
            except MusicMixError as exc:
                raise ExportPipelineError(f"Music Mix 配置无效：{exc}") from exc
        assembly_version = assembly.get("final_video_version")
        return ExportInputs(
            video_path=video_path,
            assembly_version=(
                int(assembly_version) if assembly_version is not None else None
            ),
            voice=deepcopy(voice),
            voice_path=voice_path,
            voice_timing=voice_timing,
            subtitle=deepcopy(subtitle),
            subtitle_path=subtitle_path,
            music=deepcopy(music),
            music_path=music_path,
            music_volume=music_volume,
            music_mix=music_mix,
        )

    def export_current(self, *, force: bool = False) -> dict[str, Any]:
        inputs = self.collect_inputs()
        self.validate_voice_timing(inputs)
        if not force:
            existing = self.find_existing_export(inputs)
            if existing:
                raise ExportAlreadyExistsError(existing)
        pipeline_state = PostProductionPipeline(self.checkpoint)
        pipeline_state.mark_running(PostProductionStage.FINAL_EXPORT)
        staging: Path | None = None
        try:
            tools = self._detect_tools()
            video_info = self._probe(
                tools["ffprobe"], inputs.video_path, require_video=True, label="视频"
            )
            self.validate_voice_timing(
                inputs, video_duration=float(video_info["duration"])
            )
            if inputs.voice_path:
                self._probe(
                    tools["ffprobe"],
                    inputs.voice_path,
                    require_audio=True,
                    label="配音",
                )
            music_info = None
            if inputs.music_path:
                music_info = self._probe(
                    tools["ffprobe"],
                    inputs.music_path,
                    require_audio=True,
                    label="背景音乐",
                )
                inputs = self._resolve_music_mix(
                    inputs,
                    video_duration=float(video_info["duration"]),
                    music_duration=float(music_info["duration"]),
                )

            version = self.assets.next_version()
            staging = self.assets.create_staging_dir(version)
            if inputs.subtitle_path:
                shutil.copy2(
                    inputs.subtitle_path,
                    self.project.ensure_within_project(staging / "subtitle.srt"),
                )
            output_path = self.project.ensure_within_project(
                staging / "final_video.mp4"
            )
            command = self._build_ffmpeg_command(
                ffmpeg=tools["ffmpeg"],
                inputs=inputs,
                output_path=output_path,
            )
            self._event(
                "FINAL_EXPORT_FFMPEG_STARTED",
                export_version=version,
                voice_version=inputs.voice.get("version") if inputs.voice else None,
                subtitle_version=(
                    inputs.subtitle.get("version") if inputs.subtitle else None
                ),
                music_version=inputs.music.get("version") if inputs.music else None,
            )
            self._run_ffmpeg(command, cwd=staging)
            output_info = self._probe(
                tools["ffprobe"],
                output_path,
                require_video=True,
                require_audio=bool(inputs.voice_path or inputs.music_path),
                label="Final Export",
            )
            created_at = _now_iso()
            version_manifest = self._version_manifest(
                version=version,
                created_at=created_at,
                inputs=inputs,
                output_info=output_info,
                tools=tools,
            )
            entry = self.assets.commit_staging(
                version=version,
                staging_dir=staging,
                version_manifest=version_manifest,
            )
            staging = None
            pipeline_state.mark_final_export_completed(
                version=version,
                path=str(entry["final_video_path"]),
                created_at=created_at,
            )
            self._event(
                "FINAL_EXPORT_COMPLETED",
                export_version=version,
                final_video_path=entry["final_video_path"],
            )
            return entry
        except (
            ExportAssetError,
            ExportPipelineError,
            ProjectDirectoryError,
            OSError,
            ValueError,
        ) as exc:
            self.assets.discard_staging(staging)
            pipeline_state.mark_failed(PostProductionStage.FINAL_EXPORT, str(exc))
            self._log_error(exc)
            if isinstance(exc, ExportPipelineError):
                raise
            raise ExportPipelineError(f"Final Export 失败：{exc}") from exc

    def _detect_tools(self) -> dict[str, str]:
        missing: list[str] = []
        tools: dict[str, str] = {}
        versions: dict[str, str] = {}
        for name in ("ffmpeg", "ffprobe"):
            executable = self.which(name)
            if not executable:
                missing.append(name)
                continue
            result = self._run([executable, "-version"])
            if result.returncode != 0:
                missing.append(name)
                continue
            tools[name] = executable
            first_line = (result.stdout or result.stderr or "").splitlines()
            versions[name] = first_line[0] if first_line else executable
        if missing:
            raise ExportPipelineError(
                "当前电脑未检测到 FFmpeg / FFprobe，无法导出最终视频。"
                f"\n缺少：{', '.join(missing)}。"
            )
        self._event("FINAL_EXPORT_TOOLS_READY", **versions)
        return tools

    def _probe(
        self,
        ffprobe: str,
        path: Path,
        *,
        require_video: bool = False,
        require_audio: bool = False,
        label: str,
    ) -> dict[str, Any]:
        command = [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name",
            "-of",
            "json",
            str(path),
        ]
        result = self._run(command)
        if result.returncode != 0:
            raise ExportPipelineError(
                f"{label} 无法通过 ffprobe 读取：{path}\n{result.stderr.strip()}"
            )
        try:
            payload = json.loads(result.stdout)
            streams = list(payload.get("streams") or [])
            duration = float((payload.get("format") or {}).get("duration"))
            if duration <= 0:
                raise ValueError("duration 无效")
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ExportPipelineError(f"{label} 的媒体信息不完整：{path}") from exc
        if require_video and not any(
            stream.get("codec_type") == "video" for stream in streams
        ):
            raise ExportPipelineError(f"{label} 不包含视频流：{path}")
        if require_audio and not any(
            stream.get("codec_type") == "audio" for stream in streams
        ):
            raise ExportPipelineError(f"{label} 不包含音频流：{path}")
        self._event(
            "FINAL_EXPORT_MEDIA_VALIDATED",
            label=label,
            path=path,
            duration=duration,
        )
        return {"duration": duration, "streams": streams}

    def _build_ffmpeg_command(
        self,
        *,
        ffmpeg: str,
        inputs: ExportInputs,
        output_path: Path,
    ) -> list[str]:
        command = [ffmpeg, "-hide_banner", "-y", "-i", str(inputs.video_path)]
        voice_index: int | None = None
        music_index: int | None = None
        next_index = 1
        if inputs.voice_path:
            voice_index = next_index
            next_index += 1
            command.extend(["-i", str(inputs.voice_path)])
        if inputs.music_path:
            music_index = next_index
            command.extend(["-i", str(inputs.music_path)])

        filters: list[str] = []
        if voice_index is not None:
            voice_start = float(
                (inputs.voice_timing or {}).get("voice_track_start", 0.0)
            )
            delay_ms = int(round(voice_start * 1000.0))
            voice_filters: list[str] = []
            if delay_ms > 0:
                voice_filters.append(f"adelay={delay_ms}:all=1")
            voice_filters.append(
                f"volume={float(EXPORT_RENDER_CONFIG['voice_volume']):.1f}"
            )
            filters.append(
                f"[{voice_index}:a]{','.join(voice_filters)}[voice]"
            )
        if music_index is not None:
            plan = inputs.music_mix
            if not isinstance(plan, dict) or not bool(
                plan.get("media_timing_resolved")
            ):
                raise ExportPipelineError("Music Mix 缺少已解析的媒体时长。")
            effective_end = float(plan["effective_music_end"])
            video_duration = float(plan["video_duration"])
            music_filters = [
                f"atrim=start=0:end={effective_end:.12g}",
                "asetpts=PTS-STARTPTS",
                f"volume={float(plan['base_volume']):.12g}",
            ]
            duck_expression = music_ducking_expression(plan)
            if duck_expression:
                music_filters.append(
                    f"volume='{duck_expression}':eval=frame"
                )
            fade_in = float(plan["fade_in_seconds"])
            fade_out = float(plan["fade_out_seconds"])
            if fade_in > 0:
                music_filters.append(f"afade=t=in:st=0:d={fade_in:.12g}")
            if fade_out > 0:
                fade_out_start = max(0.0, effective_end - fade_out)
                music_filters.append(
                    f"afade=t=out:st={fade_out_start:.12g}:d={fade_out:.12g}"
                )
            music_filters.append(f"apad=whole_dur={video_duration:.12g}")
            filters.append(
                f"[{music_index}:a]{','.join(music_filters)}[music]"
            )
        if voice_index is not None and music_index is not None:
            filters.append(
                "[voice][music]amix=inputs=2:duration=longest:"
                "dropout_transition=2,apad[aout]"
            )
        elif voice_index is not None:
            filters.append("[voice]apad[aout]")
        elif music_index is not None:
            filters.append("[music]apad[aout]")
        if filters:
            command.extend(["-filter_complex", ";".join(filters)])

        if inputs.subtitle_path:
            style = DEFAULT_SUBTITLE_STYLE
            subtitle_filter = (
                "subtitles=subtitle.srt:force_style='"
                f"Alignment={style['alignment']},FontName={style['font_name']},"
                f"FontSize={style['font_size']},PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H00000000,BorderStyle=1,"
                f"Outline={style['outline_width']},Shadow={style['shadow']},"
                f"MarginV={style['margin_vertical']}'"
            )
            command.extend(["-vf", subtitle_filter])

        command.extend(["-map", "0:v:0"])
        if filters:
            command.extend(["-map", "[aout]"])
        if inputs.subtitle_path:
            command.extend(
                [
                    "-c:v",
                    str(EXPORT_RENDER_CONFIG["subtitle_video_codec"]),
                    "-preset",
                    str(EXPORT_RENDER_CONFIG["subtitle_video_preset"]),
                    "-crf",
                    str(EXPORT_RENDER_CONFIG["subtitle_video_crf"]),
                    "-pix_fmt",
                    str(EXPORT_RENDER_CONFIG["subtitle_pixel_format"]),
                ]
            )
        else:
            command.extend(["-c:v", str(EXPORT_RENDER_CONFIG["plain_video_codec"])])
        if filters:
            command.extend(
                [
                    "-c:a",
                    str(EXPORT_RENDER_CONFIG["audio_codec"]),
                    "-b:a",
                    str(EXPORT_RENDER_CONFIG["audio_bitrate"]),
                    "-shortest",
                ]
            )
        else:
            command.append("-an")
        command.extend(
            ["-movflags", str(EXPORT_RENDER_CONFIG["movflags"]), str(output_path)]
        )
        return command

    def _run_ffmpeg(self, command: list[str], *, cwd: Path) -> None:
        result = self._run(command, cwd=cwd)
        if result.returncode != 0:
            raise ExportPipelineError(
                f"FFmpeg 执行失败（return code {result.returncode}）。"
                f"\n{result.stderr.strip()}"
            )

    def _run(
        self, command: list[str], *, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )

    def _version_manifest(
        self,
        *,
        version: int,
        created_at: str,
        inputs: ExportInputs,
        output_info: dict[str, Any],
        tools: dict[str, str],
    ) -> dict[str, Any]:
        relative = lambda path: self._relative_optional(path)
        fingerprint = self.build_input_fingerprint(inputs)
        voice_timeline = deepcopy(inputs.voice_timing) if inputs.voice else None
        return {
            "export_version": version,
            "created_at": created_at,
            "final_video_path": f"exports/v{version:03d}/final_video.mp4",
            "video_version": inputs.assembly_version,
            "assembly_version": inputs.assembly_version,
            "voice_version": inputs.voice.get("version") if inputs.voice else None,
            "voice": (
                {
                    "version": int(inputs.voice["version"]),
                    "source": inputs.voice.get("script_source") or "manual",
                    "timing_mode": voice_timeline.get("timing_mode"),
                    "voice_track_start": voice_timeline.get("voice_track_start"),
                    "actual_audio_duration": voice_timeline.get(
                        "actual_audio_duration"
                    ),
                    "actual_voice_end": voice_timeline.get("actual_voice_end"),
                    "calibration_status": voice_timeline.get("status"),
                    "cue_level_alignment": voice_timeline.get(
                        "cue_level_alignment"
                    ),
                    "timing_acceptance": deepcopy(
                        voice_timeline.get("timing_acceptance")
                    ),
                }
                if inputs.voice and voice_timeline
                else None
            ),
            "subtitle_version": (
                inputs.subtitle.get("version") if inputs.subtitle else None
            ),
            "music_version": inputs.music.get("version") if inputs.music else None,
            "music_mix": (
                {
                    "music_version": int(inputs.music["version"]),
                    **deepcopy(inputs.music_mix),
                }
                if inputs.music and inputs.music_mix
                else None
            ),
            "inputs": {
                "video_path": relative(inputs.video_path),
                "voice_path": relative(inputs.voice_path),
                "subtitle_path": relative(inputs.subtitle_path),
                "music_path": relative(inputs.music_path),
            },
            "settings": {
                "voice_volume": EXPORT_RENDER_CONFIG["voice_volume"]
                if inputs.voice
                else None,
                "music_volume": inputs.music_volume if inputs.music else None,
                "music_mix": deepcopy(inputs.music_mix) if inputs.music else None,
                "subtitle": deepcopy(DEFAULT_SUBTITLE_STYLE)
                if inputs.subtitle
                else None,
                "render_config": deepcopy(EXPORT_RENDER_CONFIG),
            },
            "audio_muxed": bool(inputs.voice or inputs.music),
            "subtitle_burned": bool(inputs.subtitle),
            "duration_seconds": output_info["duration"],
            "tools": {
                "ffmpeg": Path(tools["ffmpeg"]).name,
                "ffprobe": Path(tools["ffprobe"]).name,
            },
            "input_fingerprint": fingerprint,
            "input_fingerprint_sha256": self._fingerprint_digest(fingerprint),
        }

    def validate_voice_timing(
        self,
        inputs: ExportInputs,
        *,
        video_duration: float | None = None,
    ) -> None:
        """Enforce saved Phase 2.3 results without recalibrating them."""

        if not inputs.voice:
            return
        timing = inputs.voice_timing or {}
        status = str(timing.get("status") or "LEGACY_NO_CALIBRATION")
        start = self._timing_number(
            timing.get("voice_track_start", 0.0), "voice_track_start"
        )
        if start < 0:
            raise ExportPipelineError("Voice Track Start 不能小于 0。")
        if status == "OUT_OF_BOUNDS":
            raise ExportPipelineError(
                "Voice Audio exceeds final video duration."
                f"\nVoice Start: {start:g}s"
                f"\nActual End: {timing.get('actual_voice_end')}s"
                f"\nVideo Duration: {timing.get('total_video_duration')}s"
            )
        if status == "OUT_OF_TOLERANCE":
            acceptance = timing.get("timing_acceptance")
            if not isinstance(acceptance, dict) or not bool(
                acceptance.get("accepted")
            ):
                raise ExportPipelineError(
                    "Voice Timing 为 OUT_OF_TOLERANCE，必须由用户明确接受后才能导出。"
                )
        end_value = timing.get("actual_voice_end")
        if status not in {"LEGACY_NO_CALIBRATION", "NOT_APPLICABLE"}:
            if end_value is None:
                raise ExportPipelineError("Voice Calibration 缺少 actual_voice_end。")
            actual_end = self._timing_number(end_value, "actual_voice_end")
            if actual_end <= start:
                raise ExportPipelineError(
                    "Voice Timing actual_voice_end 必须大于 voice_track_start。"
                )
            if video_duration is not None and actual_end > video_duration + 1e-6:
                raise ExportPipelineError(
                    "Voice Audio exceeds final video duration."
                    f"\nVoice Start: {start:g}s"
                    f"\nActual End: {actual_end:g}s"
                    f"\nVideo Duration: {video_duration:g}s"
                )

    def _load_voice_timing(self, voice: dict[str, Any]) -> dict[str, Any]:
        calibration = voice.get("timing_calibration")
        acceptance = voice.get("timing_acceptance")
        if not isinstance(calibration, dict):
            config_relative = voice.get("config_path")
            if config_relative:
                try:
                    config_path = self._project_file(
                        str(config_relative), "Voice Config"
                    )
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    candidate = config.get("timing_calibration")
                    if isinstance(candidate, dict):
                        calibration = candidate
                    if acceptance is None:
                        acceptance = config.get("timing_acceptance")
                except (ExportPipelineError, OSError, json.JSONDecodeError):
                    calibration = None
        if not isinstance(calibration, dict):
            actual = voice.get(
                "actual_audio_duration", voice.get("duration_seconds")
            )
            return {
                "timing_mode": "legacy",
                "status": "LEGACY_NO_CALIBRATION",
                "planned_narration_duration": None,
                "planned_voice_span": None,
                "actual_audio_duration": actual,
                "voice_track_start": 0.0,
                "actual_voice_end": actual,
                "total_video_duration": None,
                "cue_level_alignment": False,
                "timing_acceptance": None,
            }
        timing = deepcopy(calibration)
        timing["timing_acceptance"] = deepcopy(acceptance)
        return timing

    def _resolve_music_mix_for_fingerprint(
        self, inputs: ExportInputs
    ) -> ExportInputs:
        if not inputs.music or not inputs.music_path:
            return inputs
        # Always fingerprint probed durations, not possibly stale import
        # metadata, so duplicate detection matches the bytes actually rendered.
        tools = self._detect_tools()
        video_info = self._probe(
            tools["ffprobe"], inputs.video_path, require_video=True, label="视频"
        )
        music_info = self._probe(
            tools["ffprobe"],
            inputs.music_path,
            require_audio=True,
            label="背景音乐",
        )
        return self._resolve_music_mix(
            inputs,
            video_duration=float(video_info["duration"]),
            music_duration=float(music_info["duration"]),
        )

    def _resolve_music_mix(
        self,
        inputs: ExportInputs,
        *,
        video_duration: float,
        music_duration: float,
    ) -> ExportInputs:
        if not inputs.music:
            return inputs
        raw = inputs.music_mix if isinstance(inputs.music_mix, dict) else {}
        configured = raw.get("settings")
        if isinstance(configured, dict):
            raw = configured
        settings = {
            key: raw.get(key)
            for key in (
                "base_volume",
                "ducking_enabled",
                "ducking_ratio",
                "duck_attack_seconds",
                "duck_release_seconds",
                "fade_in_seconds",
                "fade_out_seconds",
                "loop_music",
            )
            if raw.get(key) is not None
        }
        try:
            plan = build_music_mix_plan(
                settings,
                original_music_duration=music_duration,
                video_duration=video_duration,
                voice_timing=inputs.voice_timing,
            )
        except MusicMixError as exc:
            raise ExportPipelineError(f"Music Mix 无法执行：{exc}") from exc
        return replace(
            inputs,
            music_volume=float(plan["base_volume"]),
            music_mix=plan,
        )

    @staticmethod
    def _optional_positive_number(value: Any) -> float | None:
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) and number > 0 else None

    @staticmethod
    def _timing_number(value: Any, field: str) -> float:
        if isinstance(value, bool):
            raise ExportPipelineError(f"Voice Timing {field} 必须是有限数字。")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ExportPipelineError(
                f"Voice Timing {field} 必须是有限数字。"
            ) from exc
        if not math.isfinite(number):
            raise ExportPipelineError(f"Voice Timing {field} 必须是有限数字。")
        return number

    @staticmethod
    def _voice_timing_fingerprint(
        timing: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        """Keep only fields that affect rendering or its safety gate."""

        if not isinstance(timing, dict):
            return None
        acceptance = timing.get("timing_acceptance")
        return {
            "timing_mode": timing.get("timing_mode"),
            "status": timing.get("status"),
            "voice_track_start": timing.get("voice_track_start"),
            "actual_audio_duration": timing.get("actual_audio_duration"),
            "actual_voice_end": timing.get("actual_voice_end"),
            "cue_level_alignment": timing.get("cue_level_alignment"),
            "timing_accepted": bool(
                isinstance(acceptance, dict) and acceptance.get("accepted")
            ),
        }

    def _project_file(self, relative_path: str, label: str) -> Path:
        path = self.project.ensure_within_project(
            self.project.project_path / relative_path
        )
        if not path.is_file() or path.stat().st_size <= 0:
            raise ExportPipelineError(f"{label}不存在或为空：{path}")
        return path

    def _relative_optional(self, path: Path | None) -> str | None:
        if path is None:
            return None
        target = self.project.ensure_within_project(path)
        return target.relative_to(self.project.project_path.resolve()).as_posix()

    @staticmethod
    def _sha256(path: Path | None) -> str:
        if path is None:
            raise ExportPipelineError("Export 输入文件不存在，无法计算 SHA-256。")
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
        except OSError as exc:
            raise ExportPipelineError(
                f"无法读取 Export 输入文件以计算 SHA-256：{path}：{exc}"
            ) from exc
        return digest.hexdigest()

    @staticmethod
    def _fingerprint_digest(fingerprint: dict[str, Any]) -> str:
        canonical = json.dumps(
            fingerprint,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _event(self, name: str, **fields: Any) -> None:
        if self.task_logger is not None:
            self.task_logger.event(name, **fields)

    def _log_error(self, error: BaseException) -> None:
        if self.task_logger is not None:
            self.task_logger.error(error, stage="final_export")
