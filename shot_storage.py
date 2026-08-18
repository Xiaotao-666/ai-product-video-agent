"""Schema v2 Shot manifests and immutable video generation bundles."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from project_manager import ProjectPaths
from visual_input import none_visual_input, normalize_visual_input, visual_input_snapshot


PROJECT_SCHEMA_VERSION = 2
BUNDLE_FILES = (
    "video.mp4",
    "prompt.json",
    "safety.json",
    "generation.json",
    "review.json",
)


class ShotStorageError(RuntimeError):
    """Raised when a Schema v2 Shot bundle is missing or inconsistent."""


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ShotStorageError(f"文件不存在：{path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ShotStorageError(f"JSON 文件无法读取：{path}：{exc}") from exc
    if not isinstance(data, dict):
        raise ShotStorageError(f"JSON 文件不是对象：{path}")
    return data


def relative_path(paths: ProjectPaths, path: Path) -> str:
    target = paths.ensure_within_project(path)
    return target.resolve().relative_to(paths.project_path.resolve()).as_posix()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def new_shot_manifest(shot_id: int, *, status: str = "NOT_STARTED") -> dict[str, Any]:
    return {
        "shot_schema_version": 2,
        "shot_id": int(shot_id),
        "status": status,
        "generation_count": 0,
        "active_version": None,
        "approved_version": None,
        "candidate_version": None,
        "visual_input": none_visual_input(),
        "versions": [],
        "updated_at": now_iso(),
    }


def ensure_shot_manifest(
    paths: ProjectPaths, shot_id: int, *, status: str = "NOT_STARTED"
) -> dict[str, Any]:
    directory = paths.shot_dir(shot_id)
    directory.mkdir(parents=True, exist_ok=True)
    paths.shot_editing_dir(shot_id).mkdir(parents=True, exist_ok=True)
    manifest_path = paths.shot_manifest_path(shot_id)
    if manifest_path.is_file():
        manifest = _read_json(manifest_path)
    else:
        manifest = new_shot_manifest(shot_id, status=status)
    defaults = new_shot_manifest(shot_id, status=status)
    for key, value in defaults.items():
        manifest.setdefault(key, value)
    manifest["shot_schema_version"] = 2
    manifest["shot_id"] = int(shot_id)
    manifest["versions"] = sorted(
        {int(version) for version in manifest.get("versions", [])}
    )
    manifest["updated_at"] = now_iso()
    paths.save_json(manifest_path, manifest)
    return manifest


def load_shot_manifest(paths: ProjectPaths, shot_id: int) -> dict[str, Any]:
    return ensure_shot_manifest(paths, shot_id)


def save_shot_manifest(
    paths: ProjectPaths, shot_id: int, manifest: dict[str, Any]
) -> Path:
    manifest = dict(manifest)
    manifest["shot_schema_version"] = 2
    manifest["shot_id"] = int(shot_id)
    manifest["versions"] = sorted(
        {int(version) for version in manifest.get("versions", [])}
    )
    manifest["updated_at"] = now_iso()
    return paths.save_json(paths.shot_manifest_path(shot_id), manifest)


def sync_shot_manifest_from_checkpoint(
    paths: ProjectPaths, shot_id: int, entry: dict[str, Any]
) -> dict[str, Any]:
    manifest = ensure_shot_manifest(paths, shot_id, status=str(entry.get("status")))
    versions = {
        int(item["video_version"])
        for item in entry.get("generation_versions", [])
        if item.get("video_version") is not None
    }
    for version in manifest.get("versions", []):
        versions.add(int(version))
    candidate = entry.get("candidate") or {}
    candidate_version = candidate.get("video_version")
    if str(candidate.get("status", "NONE")) in {"NONE", "EDITING"}:
        candidate_version = None
    manifest.update(
        {
            "status": str(entry.get("status", "NOT_STARTED")),
            "generation_count": int(entry.get("generation_count", 0)),
            "active_version": entry.get("active_video_version"),
            "approved_version": entry.get("approved_video_version"),
            "candidate_version": candidate_version,
            "visual_input": visual_input_snapshot(entry.get("visual_input")),
            "versions": sorted(versions),
        }
    )
    save_shot_manifest(paths, shot_id, manifest)
    return manifest


def read_bundle_json(
    paths: ProjectPaths, shot_id: int, version: int, filename: str
) -> dict[str, Any]:
    mapping = {
        "prompt.json": paths.shot_version_prompt_path,
        "safety.json": paths.shot_version_safety_path,
        "generation.json": paths.shot_version_generation_path,
        "review.json": paths.shot_version_review_path,
    }
    if filename not in mapping:
        raise ShotStorageError(f"未知 Bundle JSON 文件：{filename}")
    return _read_json(mapping[filename](shot_id, version))


def write_prompt_snapshot(
    paths: ProjectPaths,
    shot_id: int,
    video_version: int,
    payload: dict[str, Any],
) -> Path:
    version_dir = paths.shot_version_dir(shot_id, video_version)
    version_dir.mkdir(parents=True, exist_ok=True)
    prompt_version = payload.get("version", payload.get("prompt_version"))
    snapshot = {
        "shot_id": int(shot_id),
        "video_version": int(video_version),
        "prompt_version": int(prompt_version) if prompt_version is not None else None,
        "prompt_source": payload.get("source", payload.get("prompt_source", "unknown")),
        "prompt_text": payload.get("prompt", payload.get("prompt_text", "")),
        "parent_version": payload.get("parent_version"),
        "user_feedback": payload.get("user_feedback"),
        "created_at": payload.get("created_at") or now_iso(),
    }
    return paths.save_json(
        paths.shot_version_prompt_path(shot_id, video_version), snapshot
    )


def write_safety_snapshot(
    paths: ProjectPaths,
    shot_id: int,
    video_version: int,
    *,
    input_prompt: str,
    safety_payload: dict[str, Any] | None,
) -> Path:
    paths.shot_version_dir(shot_id, video_version).mkdir(parents=True, exist_ok=True)
    payload = dict(safety_payload or {})
    payload.update(
        {
            "shot_id": int(shot_id),
            "video_version": int(video_version),
            "input_prompt": input_prompt,
            "final_submit_prompt": payload.get("reviewed_video_prompt", input_prompt),
            "checked_at": payload.get("checked_at") or now_iso(),
        }
    )
    return paths.save_json(
        paths.shot_version_safety_path(shot_id, video_version), payload
    )


def write_generation_snapshot(
    paths: ProjectPaths,
    shot_id: int,
    video_version: int,
    payload: dict[str, Any],
) -> Path:
    paths.shot_version_dir(shot_id, video_version).mkdir(parents=True, exist_ok=True)
    existing = (
        _read_json(paths.shot_version_generation_path(shot_id, video_version))
        if paths.shot_version_generation_path(shot_id, video_version).is_file()
        else {}
    )
    snapshot = {
        "shot_id": int(shot_id),
        "video_version": int(video_version),
        "prompt_version": payload.get("prompt_version"),
        "created_at": payload.get("created_at") or payload.get("submitted_at") or now_iso(),
        "provider": payload.get("provider", existing.get("provider")),
        "provider_task_id": payload.get("provider_task_id"),
        "file_id": payload.get("file_id"),
        "generation_mode": payload.get(
            "generation_mode", existing.get("generation_mode")
        ),
        "provider_model": payload.get(
            "provider_model", existing.get("provider_model")
        ),
        "provider_api_version": payload.get(
            "provider_api_version", existing.get("provider_api_version")
        ),
        "selection_mode": payload.get(
            "selection_mode", existing.get("selection_mode")
        ),
        "credential_env_name": payload.get(
            "credential_env_name", existing.get("credential_env_name")
        ),
        "generation_count": payload.get("generation_count"),
        "status": payload.get("status", "NOT_STARTED"),
        "duration": payload.get("duration"),
        "submitted_at": payload.get("submitted_at"),
        "file_ready_at": payload.get("file_ready_at"),
        "completed_at": payload.get("completed_at"),
        "visual_input": visual_input_snapshot(
            payload.get("visual_input", existing.get("visual_input"))
        ),
        "updated_at": now_iso(),
    }
    for key, value in existing.items():
        if key not in snapshot:
            snapshot[key] = value
    if payload.get("last_error") is not None:
        snapshot["last_error"] = payload.get("last_error")
    return paths.save_json(
        paths.shot_version_generation_path(shot_id, video_version), snapshot
    )


def update_generation_snapshot(
    paths: ProjectPaths, shot_id: int, version: int, **fields: Any
) -> Path:
    path = paths.shot_version_generation_path(shot_id, version)
    payload = _read_json(path) if path.is_file() else {
        "shot_id": int(shot_id),
        "video_version": int(version),
        "provider": None,
        "created_at": now_iso(),
    }
    payload.update(fields)
    payload["updated_at"] = now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    return paths.save_json(path, payload)


def write_review_snapshot(
    paths: ProjectPaths,
    shot_id: int,
    video_version: int,
    *,
    review_result: str,
    user_action: str | None = None,
    user_feedback: str | None = None,
    review_time: str | None = None,
) -> Path:
    path = paths.shot_version_review_path(shot_id, video_version)
    payload = _read_json(path) if path.is_file() else {
        "shot_id": int(shot_id),
        "video_version": int(video_version),
        "history": [],
    }
    timestamp = review_time or now_iso()
    event = {
        "review_result": review_result,
        "review_time": timestamp,
        "user_action": user_action,
        "user_feedback": user_feedback,
    }
    payload.update(event)
    history = payload.setdefault("history", [])
    if not history or history[-1] != event:
        history.append(event)
    path.parent.mkdir(parents=True, exist_ok=True)
    return paths.save_json(path, payload)


def ensure_bundle_placeholders(
    paths: ProjectPaths,
    shot_id: int,
    version: int,
    *,
    prompt_payload: dict[str, Any],
    generation_payload: dict[str, Any],
    safety_payload: dict[str, Any] | None = None,
    review_result: str = "NOT_STARTED",
) -> None:
    paths.shot_version_dir(shot_id, version).mkdir(parents=True, exist_ok=True)
    write_prompt_snapshot(paths, shot_id, version, prompt_payload)
    write_safety_snapshot(
        paths,
        shot_id,
        version,
        input_prompt=str(prompt_payload.get("prompt") or prompt_payload.get("prompt_text") or ""),
        safety_payload=safety_payload,
    )
    write_generation_snapshot(paths, shot_id, version, generation_payload)
    write_review_snapshot(
        paths, shot_id, version, review_result=review_result
    )


def bundle_summary(paths: ProjectPaths, shot_id: int, version: int) -> dict[str, Any]:
    video = paths.shot_version_video_path(shot_id, version)
    return {
        "shot_id": int(shot_id),
        "video_version": int(version),
        "video_path": relative_path(paths, video),
        "video_exists": video.is_file(),
        "video_size": video.stat().st_size if video.is_file() else 0,
        "video_sha256": sha256_file(video) if video.is_file() else None,
        "prompt": read_bundle_json(paths, shot_id, version, "prompt.json"),
        "generation": read_bundle_json(paths, shot_id, version, "generation.json"),
        "review": read_bundle_json(paths, shot_id, version, "review.json"),
    }


def validate_bundle(
    paths: ProjectPaths,
    shot_id: int,
    version: int,
    *,
    require_video: bool = True,
) -> dict[str, Any]:
    """Validate a complete immutable generation bundle."""
    version_dir = paths.shot_version_dir(shot_id, version)
    missing = [
        name
        for name in BUNDLE_FILES
        if name != "video.mp4" and not (version_dir / name).is_file()
    ]
    video = paths.shot_version_video_path(shot_id, version)
    if require_video and (not video.is_file() or video.stat().st_size <= 0):
        missing.append("video.mp4")
    if missing:
        raise ShotStorageError(
            f"Shot {shot_id:02d} v{version:03d} Bundle 缺少：{', '.join(missing)}"
        )
    prompt = read_bundle_json(paths, shot_id, version, "prompt.json")
    generation = read_bundle_json(paths, shot_id, version, "generation.json")
    generation["visual_input"] = normalize_visual_input(
        generation.get("visual_input")
    )
    if int(prompt.get("video_version") or 0) != int(version):
        raise ShotStorageError("prompt.json video_version 不一致。")
    if int(generation.get("video_version") or 0) != int(version):
        raise ShotStorageError("generation.json video_version 不一致。")
    if prompt.get("prompt_version") != generation.get("prompt_version"):
        raise ShotStorageError("Prompt 与 Video 的版本映射不一致。")
    return bundle_summary(paths, shot_id, version)
