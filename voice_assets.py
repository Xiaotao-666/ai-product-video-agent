"""Immutable project-local storage for generated voice assets."""

from __future__ import annotations

import json
import io
import hashlib
import shutil
import wave
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from audio_timeline_calibration import calibrate_voice_timeline

from project_manager import ProjectDirectoryError, ProjectPaths
from voice_provider import (
    VoiceGenerationRequest,
    VoiceGenerationResult,
    VoiceProvider,
    VoiceProviderError,
)


VOICE_SCHEMA_VERSION = 1
VOICE_PLANNING_METADATA_FIELDS = (
    "script_source",
    "source_storyboard_path",
    "planned_narration_duration",
    "planned_first_voice_start",
    "planned_last_voice_end",
    "planned_voice_span",
    "total_video_duration",
    "cue_count",
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_voice_config() -> dict[str, Any]:
    return {
        "enabled": False,
        "provider": None,
        "voice": None,
        "language": "zh-CN",
    }


def normalize_voice_config(value: Any) -> tuple[dict[str, Any], bool]:
    """Backfill an old project without changing existing user choices."""
    changed = False
    if not isinstance(value, dict):
        value = {}
        changed = True
    normalized = deepcopy(value)
    for key, default in default_voice_config().items():
        if key not in normalized:
            normalized[key] = default
            changed = True
    normalized["enabled"] = bool(normalized.get("enabled", False))
    return normalized, changed


def _new_manifest() -> dict[str, Any]:
    return {
        "voice_schema_version": VOICE_SCHEMA_VERSION,
        "active_version": None,
        "versions": [],
    }


class VoiceAssetError(RuntimeError):
    """Raised when an immutable voice Bundle cannot be validated or saved."""


class VoiceAssetManager:
    """Persist each real TTS result as a never-overwritten version Bundle."""

    def __init__(self, project: ProjectPaths) -> None:
        self.project = project

    def load_manifest(self) -> dict[str, Any]:
        path = self.project.voice_manifest_path()
        if not path.exists():
            return _new_manifest()
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise VoiceAssetError(f"Voice manifest 无法读取：{exc}") from exc
        if not isinstance(payload, dict) or not isinstance(
            payload.get("versions"), list
        ):
            raise VoiceAssetError("Voice manifest 结构无效。")
        if payload.get("voice_schema_version") != VOICE_SCHEMA_VERSION:
            raise VoiceAssetError("不支持的 Voice Asset Schema。")
        return payload

    def active_version(self) -> dict[str, Any] | None:
        manifest = self.load_manifest()
        target = manifest.get("active_version")
        for entry in manifest["versions"]:
            if entry.get("version") == target:
                return deepcopy(entry)
        return None

    def set_timing_acceptance(
        self,
        version: int,
        *,
        accepted: bool,
        accepted_at: str | None = None,
    ) -> dict[str, Any]:
        """Persist an explicit human timing decision without touching audio."""

        manifest = self.load_manifest()
        target = next(
            (
                entry
                for entry in manifest["versions"]
                if int(entry.get("version", 0)) == int(version)
            ),
            None,
        )
        if target is None:
            raise VoiceAssetError(f"Voice v{int(version):03d} 不存在。")
        calibration = target.get("timing_calibration")
        if not isinstance(calibration, dict):
            raise VoiceAssetError("Legacy Voice Version 没有可接受的校准结果。")
        acceptance = {
            "accepted": bool(accepted),
            "accepted_at": accepted_at or now_iso(),
        }
        target["timing_acceptance"] = acceptance
        config_path = self.project.voice_version_config_path(int(version))
        try:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            if not isinstance(config, dict):
                raise VoiceAssetError("voice_config.json 结构无效。")
            config["timing_acceptance"] = acceptance
            self.project.save_json(config_path, config)
            self.project.save_json(self.project.voice_manifest_path(), manifest)
        except (OSError, json.JSONDecodeError, ProjectDirectoryError) as exc:
            raise VoiceAssetError(f"Voice Timing Acceptance 保存失败：{exc}") from exc
        return deepcopy(target)

    def generate_and_save(
        self,
        request: VoiceGenerationRequest,
        provider: VoiceProvider,
    ) -> dict[str, Any]:
        if not provider.supports(request):
            raise VoiceProviderError(
                f"Voice Provider {provider.provider_name} 不支持本次语言或格式。"
            )
        result = provider.generate_voice(request)
        return self.save_result(request, result, provider.get_metadata())

    def save_result(
        self,
        request: VoiceGenerationRequest,
        result: VoiceGenerationResult,
        provider_metadata: Mapping[str, Any],
    ) -> dict[str, Any]:
        if request.output_format != "wav":
            raise VoiceAssetError("Voice Asset v1 当前只保存 WAV。")
        self._validate_wav(result.audio_bytes)
        created_at = now_iso()
        # WAV is the persisted source of truth; never estimate duration from bytes.
        duration_seconds = self._wav_duration(result.audio_bytes)
        planning_metadata = {
            key: request.settings.get(key)
            for key in VOICE_PLANNING_METADATA_FIELDS
        }
        planning_metadata["script_source"] = (
            planning_metadata.get("script_source") or "manual"
        )
        manifest = self.load_manifest()
        versions = manifest["versions"]
        version = max((int(item["version"]) for item in versions), default=0) + 1
        audio_sha256 = hashlib.sha256(result.audio_bytes).hexdigest()
        timing_calibration = calibrate_voice_timeline(
            script_source=str(planning_metadata["script_source"]),
            actual_audio_duration=duration_seconds,
            planned_narration_duration=planning_metadata.get(
                "planned_narration_duration"
            ),
            planned_voice_span=planning_metadata.get("planned_voice_span"),
            planned_first_voice_start=planning_metadata.get(
                "planned_first_voice_start"
            ),
            total_video_duration=planning_metadata.get("total_video_duration"),
            source_storyboard_path=planning_metadata.get(
                "source_storyboard_path"
            ),
            storyboard_revision=request.settings.get("storyboard_revision"),
            voice_version=version,
            audio_sha256=audio_sha256,
            calibrated_at=created_at,
        )
        version_dir = self.project.voice_version_dir(version)
        script_history = self.project.voice_script_history_path(version)
        if version_dir.exists() or script_history.exists():
            raise VoiceAssetError(f"Voice v{version:03d} 已存在，已阻止覆盖。")

        staging = self.project.voice_staging_dir(version, uuid4().hex[:8])
        created_version = False
        created_script = False
        try:
            staging.mkdir(parents=True, exist_ok=False)
            staging_script = self.project.ensure_within_project(
                staging / "script.txt"
            )
            staging_config = self.project.ensure_within_project(
                staging / "voice_config.json"
            )
            staging_audio = self.project.ensure_within_project(staging / "audio.wav")
            staging_script.write_text(request.script, encoding="utf-8")
            config_payload = {
                "provider": provider_metadata.get("provider"),
                "model": provider_metadata.get("model"),
                "api_version": provider_metadata.get("api_version"),
                "voice": request.voice,
                "language": request.language,
                "output_format": request.output_format,
                "created_at": created_at,
                "duration": duration_seconds,
                "actual_audio_duration": duration_seconds,
                "audio_sha256": audio_sha256,
                "timing_calibration": timing_calibration,
                "settings": dict(request.settings),
            }
            config_payload.update(planning_metadata)
            self.project.save_json(staging_config, config_payload)
            staging_audio.write_bytes(result.audio_bytes)
            self._validate_bundle(staging_script, staging_config, staging_audio)

            staging.rename(version_dir)
            created_version = True
            script_history.write_text(request.script, encoding="utf-8")
            created_script = True
            entry = {
                "version": version,
                "created_at": created_at,
                "provider": provider_metadata.get("provider"),
                "model": provider_metadata.get("model"),
                "api_version": provider_metadata.get("api_version"),
                "voice": request.voice,
                "language": request.language,
                "provider_task_id": result.provider_task_id,
                "duration_seconds": duration_seconds,
                "actual_audio_duration": duration_seconds,
                "audio_sha256": audio_sha256,
                "timing_calibration": timing_calibration,
                "script_path": self._relative(
                    self.project.voice_version_script_path(version)
                ),
                "config_path": self._relative(
                    self.project.voice_version_config_path(version)
                ),
                "audio_path": self._relative(
                    self.project.voice_version_audio_path(version)
                ),
                "bytes": len(result.audio_bytes),
            }
            entry.update(planning_metadata)
            manifest["active_version"] = version
            versions.append(entry)
            self.project.save_json(self.project.voice_manifest_path(), manifest)
            return deepcopy(entry)
        except (
            OSError,
            ProjectDirectoryError,
            ValueError,
            VoiceAssetError,
            json.JSONDecodeError,
        ) as exc:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if created_version and version_dir.exists():
                shutil.rmtree(version_dir, ignore_errors=True)
            if created_script and script_history.exists():
                script_history.unlink(missing_ok=True)
            raise VoiceAssetError(f"Voice version 保存失败：{exc}") from exc

    def _relative(self, path: Path) -> str:
        target = self.project.ensure_within_project(path)
        return target.relative_to(self.project.project_path.resolve()).as_posix()

    @staticmethod
    def _validate_wav(payload: bytes) -> None:
        if len(payload) < 12 or payload[:4] != b"RIFF" or payload[8:12] != b"WAVE":
            raise VoiceAssetError("Voice Provider 返回的内容不是有效 WAV 容器。")

    @staticmethod
    def _wav_duration(payload: bytes) -> float:
        try:
            with wave.open(io.BytesIO(payload), "rb") as stream:
                rate = stream.getframerate()
                if rate <= 0:
                    raise VoiceAssetError("WAV 采样率无效。")
                return round(stream.getnframes() / rate, 6)
        except (wave.Error, EOFError) as exc:
            raise VoiceAssetError(f"无法读取 WAV 时长：{exc}") from exc

    @classmethod
    def _validate_bundle(
        cls, script_path: Path, config_path: Path, audio_path: Path
    ) -> None:
        if not script_path.read_text(encoding="utf-8").strip():
            raise VoiceAssetError("Voice script 为空。")
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(config, dict) or not config.get("voice"):
            raise VoiceAssetError("voice_config.json 无效。")
        cls._validate_wav(audio_path.read_bytes())
