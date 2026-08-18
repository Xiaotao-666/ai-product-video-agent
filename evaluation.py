"""Project-scoped, secret-safe records for evaluating the real generation chain."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from project_manager import ProjectPaths
from shot_storage import ShotStorageError, read_bundle_json


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class EvaluationRecorder:
    """Persist reproducible inputs/outputs without credentials or image bodies."""

    _SENSITIVE_KEY_PARTS = (
        "api_key",
        "authorization",
        "access_token",
        "secret",
        "base64",
        "inline_data",
        "inlinedata",
    )

    def __init__(self, project: ProjectPaths) -> None:
        self.project = project

    @classmethod
    def _safe(cls, value: Any, key: str = "") -> Any:
        normalized = str(key).strip().lower().replace("-", "_")
        if any(part in normalized for part in cls._SENSITIVE_KEY_PARTS):
            return "***REDACTED***"
        if isinstance(value, dict):
            return {str(k): cls._safe(v, str(k)) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._safe(item) for item in value]
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, bytes):
            return "***BINARY_OMITTED***"
        return value

    def _load_records(self, path: Path, record_type: str) -> dict[str, Any]:
        if not path.is_file():
            return {"evaluation_schema_version": 1, "type": record_type, "records": []}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"evaluation_schema_version": 1, "type": record_type, "records": []}
        if not isinstance(payload, dict) or not isinstance(payload.get("records"), list):
            return {"evaluation_schema_version": 1, "type": record_type, "records": []}
        return payload

    def record_visual_analysis(
        self,
        *,
        provider: str,
        model: str,
        api_version: str,
        asset_id: str,
        asset_sha256: str,
        input_fields: dict[str, Any],
        analysis_result: dict[str, Any],
        cache_hit: bool,
    ) -> Path:
        path = self.project.evaluation_visual_analysis_path()
        payload = self._load_records(path, "visual_analysis")
        records = payload["records"]
        matching = next(
            (
                item
                for item in records
                if str(item.get("asset_id")) == str(asset_id)
                and str(item.get("asset_sha256")) == str(asset_sha256)
            ),
            None,
        )
        if matching is None:
            matching = {
                "provider": provider,
                "model": model,
                "provider_api_version": api_version,
                "asset_id": asset_id,
                "asset_sha256": asset_sha256,
                "timestamp": _now_iso(),
                "input": self._safe(input_fields),
                "analysis_result": self._safe(analysis_result),
                "initial_cache_hit": bool(cache_hit),
                "cache_hit_count": 0,
            }
            records.append(matching)
        if cache_hit:
            matching["cache_hit_count"] = int(matching.get("cache_hit_count") or 0) + 1
        matching["last_accessed_at"] = _now_iso()
        return self.project.save_json(path, payload)

    def record_prompt(
        self,
        stage: str,
        *,
        model: str,
        input_fields: dict[str, Any],
        output_result: dict[str, Any],
        operation: str = "generate",
    ) -> Path:
        path = self.project.evaluation_prompt_path(stage)
        payload = self._load_records(path, f"{stage}_prompt")
        payload["records"].append(
            {
                "timestamp": _now_iso(),
                "model": model,
                "operation": operation,
                "input_fields": self._safe(input_fields),
                "output_result": self._safe(output_result),
            }
        )
        return self.project.save_json(path, payload)

    def sync_generation_bundles(self, checkpoint: Any) -> None:
        shots = checkpoint.data.get("video_generation", {}).get("shots", {})
        for raw_shot_id, entry in shots.items():
            try:
                shot_id = int(raw_shot_id)
            except (TypeError, ValueError):
                continue
            versions = {
                int(item.get("video_version"))
                for item in entry.get("generation_versions", [])
                if item.get("video_version") is not None
            }
            for pointer in (
                entry.get("active_video_version"),
                entry.get("approved_video_version"),
                entry.get("current_generation_version"),
            ):
                if pointer is not None:
                    versions.add(int(pointer))
            for version in sorted(versions):
                try:
                    generation = read_bundle_json(
                        self.project, shot_id, version, "generation.json"
                    )
                except ShotStorageError:
                    continue
                path = self.project.evaluation_generation_path(shot_id)
                payload = self._load_records(path, "video_generation")
                record = {
                    "timestamp": _now_iso(),
                    "shot_id": shot_id,
                    "version": version,
                    "provider": generation.get("provider"),
                    "model": generation.get("provider_model"),
                    "provider_api_version": generation.get("provider_api_version"),
                    "generation_mode": generation.get("generation_mode"),
                    "visual_input": generation.get("visual_input"),
                    "result": {
                        "status": generation.get("status"),
                        "provider_task_id": generation.get("provider_task_id"),
                        "file_id": generation.get("file_id"),
                        "video_path": self.project.shot_version_video_path(
                            shot_id, version
                        ).relative_to(self.project.project_path).as_posix(),
                        "video_exists": self.project.shot_version_video_path(
                            shot_id, version
                        ).is_file(),
                    },
                }
                records = payload["records"]
                existing_index = next(
                    (
                        index
                        for index, item in enumerate(records)
                        if int(item.get("version") or 0) == version
                    ),
                    None,
                )
                safe_record = self._safe(record)
                if existing_index is None:
                    records.append(safe_record)
                else:
                    records[existing_index] = safe_record
                self.project.save_json(path, payload)

    def sync_final(self, checkpoint: Any) -> None:
        assembly = dict(checkpoint.data.get("assembly") or {})
        if not assembly or assembly.get("status") == "NOT_STARTED":
            return
        path = self.project.evaluation_final_video_path()
        payload = self._load_records(path, "final_video")
        version = assembly.get("final_video_version") or assembly.get(
            "pending_final_video_version"
        )
        record = {
            "timestamp": _now_iso(),
            "status": assembly.get("status"),
            "final_video_version": version,
            "final_video_path": assembly.get("final_video_path")
            or assembly.get("pending_final_video_path"),
            "total_duration": assembly.get("total_duration"),
            "shot_versions": assembly.get("shot_versions")
            or assembly.get("pending_shot_versions")
            or [],
            "needs_update": bool(assembly.get("needs_update")),
            "assembled_at": assembly.get("assembled_at"),
        }
        records = payload["records"]
        existing_index = next(
            (
                index
                for index, item in enumerate(records)
                if item.get("final_video_version") == version
                and item.get("status") == record["status"]
            ),
            None,
        )
        safe_record = self._safe(record)
        if existing_index is None:
            records.append(safe_record)
        else:
            records[existing_index] = safe_record
        self.project.save_json(path, payload)
