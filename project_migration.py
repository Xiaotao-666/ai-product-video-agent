"""Transactional migration from legacy Shot Storage Schema v1 to v2.

Schema 1 assets stay in place during migration.  A validated Schema 2 bundle
tree is copied alongside them and ``project.json`` is switched atomically only
after both the legacy backup and the new runtime tree have been verified.
"""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from project_manager import ProjectPaths


class ProjectMigrationError(RuntimeError):
    """Raised when a v1 project cannot be proven safe to commit as v2."""


@dataclass(frozen=True)
class MigrationResult:
    project_path: Path
    backup_path: Path
    legacy_backup_path: Path
    report_path: Path
    source_video_count: int
    migrated_video_count: int
    sha256_verified: bool


@dataclass(frozen=True)
class LegacyCleanupResult:
    project_path: Path
    cleanup_pending: bool
    removed_paths: tuple[Path, ...]
    error: str | None = None


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProjectMigrationError(f"无法读取 JSON：{path}：{exc}") from exc
    if not isinstance(data, dict):
        raise ProjectMigrationError(f"JSON 顶层必须是对象：{path}")
    return data


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def detect_project_schema(project_path: str | Path) -> int | None:
    state_path = Path(project_path) / "project.json"
    if not state_path.is_file():
        return None
    data = _read_json(state_path)
    runtime = data.get("project_schema_version")
    if runtime is not None:
        return int(runtime)
    legacy = data.get("schema_version")
    return int(legacy) if legacy is not None else 1


def _relative(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _legacy_prompt_payloads(root: Path, shot_id: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    folder = root / "prompts" / "versions"
    for path in sorted(folder.glob(f"shot_{shot_id:02d}_prompt_v*.json")):
        payload = _read_json(path)
        payload["shot_id"] = int(shot_id)
        payload["version"] = int(payload.get("version") or 0)
        result.append(payload)
    return sorted(result, key=lambda item: int(item["version"]))


def _fallback_prompt(root: Path, shot_id: int, version: int) -> dict[str, Any]:
    prompt_text = ""
    plan_path = root / "prompts" / "video_prompts.json"
    if plan_path.is_file():
        for item in _read_json(plan_path).get("shots", []):
            if int(item.get("shot_id") or 0) == int(shot_id):
                prompt_text = str(item.get("video_prompt") or "")
                break
    return {
        "shot_id": int(shot_id),
        "version": int(version),
        "source": "legacy_unknown",
        "created_at": None,
        "prompt": prompt_text,
        "parent_version": None,
        "user_feedback": None,
        "safety_prompt": None,
        "safety_checked_at": None,
    }


def _review_events(root: Path, shot_id: int, video_version: int) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for path in sorted((root / "reviews").glob("review_*.json")):
        try:
            payload = _read_json(path)
        except ProjectMigrationError:
            continue
        for event in payload.get("shot_reviews", []):
            if int(event.get("shot_id") or 0) != int(shot_id):
                continue
            raw_version = event.get("video_version")
            if raw_version is not None and int(raw_version) != int(video_version):
                continue
            result.append(copy.deepcopy(event))
    return result


def _source_video(root: Path, generation: dict[str, Any]) -> Path | None:
    for key in ("video_path", "archived_path", "candidate_path", "active_path"):
        value = generation.get(key)
        if not value:
            continue
        path = Path(str(value))
        candidate = path if path.is_absolute() else root / path
        if candidate.is_file():
            return candidate
    return None


def _safety_payload(prompt: dict[str, Any]) -> dict[str, Any]:
    text = str(prompt.get("prompt") or "")
    reviewed = prompt.get("safety_prompt") or text
    return {
        "is_safe": bool(prompt.get("safety_is_safe", True)),
        "risk_notes": list(prompt.get("safety_risk_notes") or []),
        "input_prompt": text,
        "reviewed_video_prompt": reviewed,
        "final_submit_prompt": reviewed,
        "checked_at": prompt.get("safety_checked_at"),
    }


def _build_staging(
    root: Path, state: dict[str, Any], staging: Path
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    staged_shots = staging / "shots"
    staged_shots.mkdir(parents=True, exist_ok=False)
    old_plan = root / "prompts" / "video_prompts.json"
    if old_plan.is_file():
        shutil.copy2(old_plan, staging / "video_prompts.json")

    migrated = copy.deepcopy(state)
    migrated["project_schema_version"] = 2
    migrated.pop("schema_version", None)
    migrated["legacy_cleanup_pending"] = True
    migrated["updated_at"] = _now()
    shots = migrated.setdefault("video_generation", {}).setdefault("shots", {})
    copy_map: list[dict[str, Any]] = []

    storyboard_ids: list[int] = []
    storyboard_path = root / "storyboard" / "storyboard.json"
    if storyboard_path.is_file():
        storyboard_ids = [
            int(item["shot_id"])
            for item in _read_json(storyboard_path).get("shots", [])
        ]
    all_ids = sorted({*storyboard_ids, *(int(value) for value in shots)})

    for shot_id in all_ids:
        key = str(shot_id)
        entry = shots.setdefault(key, {})
        entry["shot_id"] = shot_id
        entry.setdefault("status", "NOT_STARTED")
        entry.setdefault("generation_count", 0)
        entry.setdefault("active_prompt_version", None)
        entry.setdefault("active_video_version", None)
        entry.setdefault("approved_prompt_version", None)
        entry.setdefault("approved_video_version", None)
        entry.setdefault("generation_versions", [])
        entry.setdefault("candidate", {"status": "NONE"})
        entry.setdefault("candidate_history", [])
        prompts = _legacy_prompt_payloads(root, shot_id)
        entry["prompt_versions"] = prompts
        entry["prompt_version_count"] = max(
            [int(item.get("version") or 0) for item in prompts] or [0]
        )
        shot_dir = staged_shots / f"shot_{shot_id:02d}"
        shot_dir.mkdir(parents=True, exist_ok=False)

        versions: list[int] = []
        for generation in entry["generation_versions"]:
            version = int(generation["video_version"])
            prompt_version = int(generation.get("prompt_version") or 1)
            prompt = next(
                (
                    item
                    for item in prompts
                    if int(item.get("version") or 0) == prompt_version
                ),
                _fallback_prompt(root, shot_id, prompt_version),
            )
            bundle = shot_dir / f"v{version:03d}"
            bundle.mkdir(parents=True, exist_ok=False)
            source = _source_video(root, generation)
            target = bundle / "video.mp4"
            if source is not None:
                shutil.copy2(source, target)
                copy_map.append(
                    {
                        "shot_id": shot_id,
                        "video_version": version,
                        "source": source,
                        "target": target,
                    }
                )
            elif not (
                str(generation.get("status")) in {"GENERATING", "FAILED"}
                and (generation.get("provider_task_id") or generation.get("file_id"))
            ):
                raise ProjectMigrationError(
                    f"Shot {shot_id:02d} Video v{version} 找不到旧视频文件。"
                )
            prompt_snapshot = {
                "shot_id": shot_id,
                "video_version": version,
                "prompt_version": prompt_version,
                "prompt_source": prompt.get("source", "legacy_unknown"),
                "prompt_text": prompt.get("prompt", ""),
                "parent_version": prompt.get("parent_version"),
                "user_feedback": prompt.get("user_feedback"),
                "created_at": prompt.get("created_at"),
            }
            safety = _safety_payload(prompt)
            generation_payload = copy.deepcopy(generation)
            generation_payload.update(
                {
                    "shot_id": shot_id,
                    "video_version": version,
                    "prompt_version": prompt_version,
                    "generation_count": int(entry.get("generation_count") or 0),
                    "video_path": f"shots/shot_{shot_id:02d}/v{version:03d}/video.mp4",
                    "prompt_source": prompt.get("source", "legacy_unknown"),
                }
            )
            review_history = _review_events(root, shot_id, version)
            review_payload = {
                "shot_id": shot_id,
                "video_version": version,
                "review_result": generation.get("review_result")
                or generation.get("status")
                or "NOT_STARTED",
                "review_time": generation.get("completed_at"),
                "history": review_history,
            }
            _write_json(bundle / "prompt.json", prompt_snapshot)
            _write_json(bundle / "safety.json", safety)
            _write_json(bundle / "generation.json", generation_payload)
            _write_json(bundle / "review.json", review_payload)
            relative_video = f"shots/shot_{shot_id:02d}/v{version:03d}/video.mp4"
            generation["video_path"] = relative_video
            generation.pop("archived_path", None)
            generation.pop("candidate_path", None)
            generation.pop("active_path", None)
            generation["prompt_snapshot"] = copy.deepcopy(prompt)
            versions.append(version)

        active = entry.get("active_video_version")
        approved = entry.get("approved_video_version")
        candidate = entry.get("candidate") or {"status": "NONE"}
        candidate_version = candidate.get("video_version")
        if active is not None:
            entry["video_path"] = (
                f"shots/shot_{shot_id:02d}/v{int(active):03d}/video.mp4"
            )
        else:
            entry["video_path"] = None
        if candidate_version is not None:
            candidate["video_path"] = (
                f"shots/shot_{shot_id:02d}/v{int(candidate_version):03d}/video.mp4"
            )
        manifest = {
            "shot_schema_version": 2,
            "shot_id": shot_id,
            "status": entry["status"],
            "generation_count": int(entry.get("generation_count") or 0),
            "active_version": int(active) if active is not None else None,
            "approved_version": int(approved) if approved is not None else None,
            "candidate_version": (
                int(candidate_version)
                if candidate_version is not None
                and str(candidate.get("status", "NONE")) not in {"NONE", "EDITING"}
                else None
            ),
            "versions": sorted(set(versions)),
            "updated_at": entry.get("updated_at") or _now(),
        }
        _write_json(shot_dir / "shot.json", manifest)

    _write_json(staging / "project.json", migrated)
    return migrated, copy_map


def _validate_staging(
    root: Path,
    old_state: dict[str, Any],
    migrated: dict[str, Any],
    staging: Path,
    copy_map: list[dict[str, Any]],
) -> dict[str, Any]:
    source_videos = _legacy_video_files(root)
    targets = [item["target"] for item in copy_map]
    if len(source_videos) != len(targets):
        raise ProjectMigrationError(
            f"视频数量不一致：旧={len(source_videos)}，新={len(targets)}。"
        )
    hashes: list[dict[str, Any]] = []
    for item in copy_map:
        source = Path(item["source"])
        target = Path(item["target"])
        if source.stat().st_size != target.stat().st_size:
            raise ProjectMigrationError(f"文件大小不一致：{source} -> {target}")
        source_hash = _sha256(source)
        target_hash = _sha256(target)
        if source_hash != target_hash:
            raise ProjectMigrationError(f"SHA-256 不一致：{source} -> {target}")
        hashes.append(
            {
                "shot_id": item["shot_id"],
                "video_version": item["video_version"],
                "size": source.stat().st_size,
                "sha256": source_hash,
            }
        )

    old_shots = old_state.get("video_generation", {}).get("shots", {})
    new_shots = migrated.get("video_generation", {}).get("shots", {})
    for shot_id, old in old_shots.items():
        new = new_shots.get(str(shot_id))
        if new is None:
            raise ProjectMigrationError(f"Shot {int(shot_id):02d} 状态丢失。")
        for field in (
            "generation_count",
            "active_prompt_version",
            "active_video_version",
            "approved_prompt_version",
            "approved_video_version",
            "provider_task_id",
            "file_id",
            "status",
            "generation_attempt_pending",
            "pending_video_version",
            "current_generation_version",
        ):
            if old.get(field) != new.get(field):
                raise ProjectMigrationError(
                    f"Shot {int(shot_id):02d} Resume 字段 {field} 映射错误。"
                )
        old_candidate = old.get("candidate") or {}
        new_candidate = new.get("candidate") or {}
        for field in ("status", "video_version", "provider_task_id", "file_id"):
            if old_candidate.get(field) != new_candidate.get(field):
                raise ProjectMigrationError(
                    f"Shot {int(shot_id):02d} Candidate 字段 {field} 映射错误。"
                )
        old_generations = old.get("generation_versions") or []
        new_generations = new.get("generation_versions") or []
        if len(old_generations) != len(new_generations):
            raise ProjectMigrationError(
                f"Shot {int(shot_id):02d} Video Version 数量不一致。"
            )
        for before, after in zip(old_generations, new_generations, strict=True):
            for field in (
                "video_version",
                "prompt_version",
                "provider_task_id",
                "file_id",
                "status",
            ):
                if before.get(field) != after.get(field):
                    raise ProjectMigrationError(
                        f"Shot {int(shot_id):02d} generation.{field} 映射错误。"
                    )
            bundle = (
                staging
                / "shots"
                / f"shot_{int(shot_id):02d}"
                / f"v{int(after['video_version']):03d}"
            )
            prompt = _read_json(bundle / "prompt.json")
            generation = _read_json(bundle / "generation.json")
            if prompt.get("prompt_version") != after.get("prompt_version"):
                raise ProjectMigrationError("Prompt 与 Video 对应关系错误。")
            legacy_prompt = next(
                (
                    item
                    for item in new.get("prompt_versions", [])
                    if int(item.get("version") or 0)
                    == int(after.get("prompt_version") or 0)
                ),
                None,
            )
            if legacy_prompt is None or (
                prompt.get("prompt_source") != legacy_prompt.get("source")
                or prompt.get("prompt_text") != legacy_prompt.get("prompt")
            ):
                raise ProjectMigrationError("Prompt Version 内容映射错误。")
            for field in ("provider_task_id", "file_id"):
                if generation.get(field) != after.get(field):
                    raise ProjectMigrationError(f"Bundle {field} 丢失。")
        manifest = _read_json(
            staging / "shots" / f"shot_{int(shot_id):02d}" / "shot.json"
        )
        if manifest.get("active_version") != new.get("active_video_version"):
            raise ProjectMigrationError("active_version 指针错误。")
        if manifest.get("approved_version") != new.get("approved_video_version"):
            raise ProjectMigrationError("approved_version 指针错误。")

    if migrated.get("project_schema_version") != 2 or "schema_version" in migrated:
        raise ProjectMigrationError("Schema 版本字段未完成单一化。")
    return {
        "source_video_count": len(source_videos),
        "migrated_video_count": len(targets),
        "sha256_verified": True,
        "files": hashes,
        "resume_verified": True,
        "mapping_verified": True,
    }


def _legacy_video_files(root: Path) -> list[Path]:
    """Return Schema 1 videos without ever scanning Schema 2 shot bundles."""
    shots = root / "shots"
    result = list(shots.glob("shot_*.mp4"))
    for name in ("versions", "candidates"):
        folder = shots / name
        if folder.is_dir():
            result.extend(folder.rglob("*.mp4"))
    return sorted({path.resolve() for path in result if path.is_file()})


def _tree_inventory(root: Path) -> dict[str, dict[str, Any]]:
    if not root.exists():
        return {}
    if root.is_file():
        return {
            root.name: {
                "size": root.stat().st_size,
                "sha256": _sha256(root),
            }
        }
    return {
        path.relative_to(root).as_posix(): {
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _copy_legacy_backup(root: Path, legacy: Path) -> dict[str, Any]:
    """Copy and validate the live Schema 1 assets without moving them."""
    legacy.mkdir(parents=True, exist_ok=False)
    copied: dict[str, Any] = {}
    for name in ("shots", "prompts"):
        source = root / name
        if not source.exists():
            continue
        destination = legacy / name
        shutil.copytree(source, destination)
        before = _tree_inventory(source)
        after = _tree_inventory(destination)
        if before != after:
            raise ProjectMigrationError(
                f"Legacy Backup 校验失败：{source} -> {destination}"
            )
        copied[name] = {
            "file_count": len(before),
            "size_verified": True,
            "sha256_verified": True,
        }
    source_state = root / "project.json"
    destination_state = legacy / "project.json"
    shutil.copy2(source_state, destination_state)
    if _tree_inventory(source_state) != _tree_inventory(destination_state):
        raise ProjectMigrationError("Legacy Backup project.json 校验失败。")
    copied["project.json"] = {
        "file_count": 1,
        "size_verified": True,
        "sha256_verified": True,
    }
    return copied


def _install_schema2_tree(
    paths: ProjectPaths,
    staging: Path,
    migrated: dict[str, Any],
    copy_map: list[dict[str, Any]],
    created: list[Path],
) -> None:
    """Copy validated bundles beside legacy files and verify the live copies."""
    staged_shots = staging / "shots"
    for staged_shot in sorted(staged_shots.glob("shot_*")):
        destination = paths.shots_dir / staged_shot.name
        if destination.exists():
            raise ProjectMigrationError(
                f"检测到未启用或冲突的 Schema 2 目录：{destination}"
            )
        shutil.copytree(staged_shot, destination)
        created.append(destination)

    staged_plan = staging / "video_prompts.json"
    if staged_plan.is_file():
        runtime_plan = paths.video_prompts_path()
        if runtime_plan.exists():
            if _sha256(runtime_plan) != _sha256(staged_plan):
                raise ProjectMigrationError(
                    f"Schema 2 Video Prompt 路径存在冲突：{runtime_plan}"
                )
        else:
            shutil.copy2(staged_plan, runtime_plan)
            created.append(runtime_plan)

    from shot_storage import load_shot_manifest, validate_bundle

    new_shots = migrated.get("video_generation", {}).get("shots", {})
    for raw_id, entry in new_shots.items():
        shot_id = int(raw_id)
        manifest = load_shot_manifest(paths, shot_id)
        if manifest.get("active_version") != entry.get("active_video_version"):
            raise ProjectMigrationError("运行时 active_version 校验失败。")
        if manifest.get("approved_version") != entry.get("approved_video_version"):
            raise ProjectMigrationError("运行时 approved_version 校验失败。")
        candidate = entry.get("candidate") or {}
        expected_candidate = candidate.get("video_version")
        if str(candidate.get("status", "NONE")) in {"NONE", "EDITING"}:
            expected_candidate = None
        if manifest.get("candidate_version") != expected_candidate:
            raise ProjectMigrationError("运行时 candidate_version 校验失败。")
        for generation in entry.get("generation_versions") or []:
            version = int(generation["video_version"])
            require_video = not (
                str(generation.get("status")) in {"GENERATING", "FAILED"}
                and (generation.get("provider_task_id") or generation.get("file_id"))
            )
            summary = validate_bundle(
                paths, shot_id, version, require_video=require_video
            )
            bundle_generation = summary["generation"]
            for field in ("provider_task_id", "file_id"):
                if bundle_generation.get(field) != generation.get(field):
                    raise ProjectMigrationError(f"运行时 Bundle {field} 校验失败。")

    for item in copy_map:
        live_video = paths.shot_version_video_path(
            int(item["shot_id"]), int(item["video_version"])
        )
        source = Path(item["source"])
        if source.stat().st_size != live_video.stat().st_size:
            raise ProjectMigrationError(f"运行时视频大小不一致：{live_video}")
        if _sha256(source) != _sha256(live_video):
            raise ProjectMigrationError(f"运行时视频 SHA-256 不一致：{live_video}")


def _write_error(
    root: Path,
    exc: BaseException,
    *,
    operation: str = "unknown",
    source_path: Path | None = None,
    destination_path: Path | None = None,
) -> Path | None:
    path = root / "logs" / "errors" / f"migration_{_stamp()}.log"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "time": _now(),
            "exception_type": type(exc).__name__,
            "errno": getattr(exc, "errno", None),
            "winerror": getattr(exc, "winerror", None),
            "source_path": str(source_path) if source_path else None,
            "destination_path": str(destination_path) if destination_path else None,
            "operation": operation,
            "error": str(exc),
            "traceback": "".join(
                traceback.format_exception(type(exc), exc, exc.__traceback__)
            ),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path
    except OSError:
        return None


def migrate_project_to_v2(paths: ProjectPaths) -> MigrationResult:
    root = paths.project_path.resolve()
    state_path = root / "project.json"
    old_state = _read_json(state_path)
    if old_state.get("project_schema_version") == 2:
        raise ProjectMigrationError("项目已经是 Shot Storage Schema v2。")
    if int(old_state.get("schema_version") or 1) != 1:
        raise ProjectMigrationError("无法识别旧项目 Schema 版本。")

    stamp = _stamp()
    backup = root.parent / f"{root.name}_schema1_full_backup_{stamp}"
    if backup.exists():
        backup = root.parent / f"{backup.name}_{uuid4().hex[:6]}"
    staging = root / ".migration_staging" / f"schema2_{stamp}_{uuid4().hex[:6]}"
    legacy = root / "legacy_backup" / f"schema1_{stamp}"
    created_schema2_paths: list[Path] = []
    operation = "initialize"
    source_path: Path | None = state_path
    destination_path: Path | None = backup
    try:
        operation = "copy_full_backup"
        shutil.copytree(root, backup)
        operation = "build_schema2_staging"
        source_path = root
        destination_path = staging
        staging.mkdir(parents=True, exist_ok=False)
        migrated, copy_map = _build_staging(root, old_state, staging)
        migrated["schema1_full_backup_path"] = str(backup)
        migrated["legacy_backup_path"] = str(legacy)
        _write_json(staging / "project.json", migrated)
        operation = "validate_schema2_staging"
        validation = _validate_staging(
            root, old_state, migrated, staging, copy_map
        )
        operation = "copy_and_validate_legacy_backup"
        source_path = root
        destination_path = legacy
        legacy_validation = _copy_legacy_backup(root, legacy)
        report = {
            "migration": "Shot Storage Schema v1 -> v2",
            "created_at": _now(),
            "source_project": str(root),
            "full_backup": str(backup),
            "legacy_backup": str(legacy),
            "legacy_validation": legacy_validation,
            "validation": validation,
            "commit_status": "VALIDATED",
        }
        _write_json(staging / "migration_report.json", report)

        operation = "install_and_validate_schema2_bundles"
        source_path = staging / "shots"
        destination_path = paths.shots_dir
        _install_schema2_tree(
            paths, staging, migrated, copy_map, created_schema2_paths
        )

        # This is the sole activation point. Until this atomic replacement the
        # live project.json remains Schema 1 and ignores the adjacent bundles.
        operation = "activate_schema2_project_json"
        source_path = staging / "project.json"
        destination_path = state_path
        activation = state_path.with_suffix(".json.schema2.tmp")
        shutil.copy2(staging / "project.json", activation)
        activation.replace(state_path)
        report["commit_status"] = "COMMITTED"
        report["legacy_cleanup_pending"] = True
        report_path = root / "logs" / "migration_schema2_report.json"
        _write_json(report_path, report)
        shutil.rmtree(staging.parent, ignore_errors=True)
        return MigrationResult(
            project_path=root,
            backup_path=backup,
            legacy_backup_path=legacy,
            report_path=report_path,
            source_video_count=int(validation["source_video_count"]),
            migrated_video_count=int(validation["migrated_video_count"]),
            sha256_verified=bool(validation["sha256_verified"]),
        )
    except Exception as exc:
        # Before activation, Schema 1 remains authoritative. Remove only the
        # new adjacent Bundle directories created by this attempt. Legacy
        # files, the sibling backup, and the copied legacy_backup are retained.
        live_schema = detect_project_schema(root)
        if live_schema != 2:
            for created in reversed(created_schema2_paths):
                try:
                    if created.is_dir():
                        shutil.rmtree(created)
                    else:
                        created.unlink(missing_ok=True)
                except OSError:
                    pass
            try:
                state_path.with_suffix(".json.schema2.tmp").unlink(missing_ok=True)
            except OSError:
                pass
        _write_error(
            root,
            exc,
            operation=operation,
            source_path=source_path,
            destination_path=destination_path,
        )
        if isinstance(exc, ProjectMigrationError):
            raise
        raise ProjectMigrationError(f"Schema 2 迁移失败：{exc}") from exc


def _validate_cleanup_preconditions(
    paths: ProjectPaths, state: dict[str, Any]
) -> tuple[Path, dict[str, Any]]:
    if state.get("project_schema_version") != 2:
        raise ProjectMigrationError("只有 Schema 2 项目可以清理 Legacy 文件。")
    backup_value = state.get("schema1_full_backup_path")
    backup = Path(str(backup_value)) if backup_value else Path()
    if not backup_value or not backup.is_dir():
        raise ProjectMigrationError("找不到迁移前完整 backup，已拒绝清理 Legacy。")
    report_path = paths.logs_dir / "migration_schema2_report.json"
    report = _read_json(report_path)
    from shot_storage import validate_bundle

    shots = state.get("video_generation", {}).get("shots", {})
    for raw_id, entry in shots.items():
        shot_id = int(raw_id)
        for generation in entry.get("generation_versions") or []:
            version = int(generation["video_version"])
            require_video = not (
                str(generation.get("status")) in {"GENERATING", "FAILED"}
                and (generation.get("provider_task_id") or generation.get("file_id"))
            )
            validate_bundle(paths, shot_id, version, require_video=require_video)
    for item in report.get("validation", {}).get("files", []):
        video = paths.shot_version_video_path(
            int(item["shot_id"]), int(item["video_version"])
        )
        if not video.is_file() or _sha256(video) != item.get("sha256"):
            raise ProjectMigrationError(f"Schema 2 视频 SHA-256 校验失败：{video}")
    return backup, report


def cleanup_legacy_schema1(paths: ProjectPaths) -> LegacyCleanupResult:
    """Explicitly remove live v1 leftovers after proving v2 and backup safety."""
    root = paths.project_path.resolve()
    state_path = paths.project_state_path()
    state = _read_json(state_path)
    _validate_cleanup_preconditions(paths, state)
    targets = [
        paths.shots_dir / "versions",
        paths.shots_dir / "candidates",
        *sorted(paths.shots_dir.glob("shot_*.mp4")),
        root / "prompts",
    ]
    removed: list[Path] = []
    operation = "legacy_cleanup"
    try:
        for target in targets:
            if not target.exists():
                continue
            if target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
            removed.append(target)
    except OSError as exc:
        state["legacy_cleanup_pending"] = True
        _write_json(state_path, state)
        _write_error(
            root,
            exc,
            operation=operation,
            source_path=target,
            destination_path=None,
        )
        return LegacyCleanupResult(
            project_path=root,
            cleanup_pending=True,
            removed_paths=tuple(removed),
            error=(
                "旧文件当前可能被其他程序占用，暂时无法清理。\n\n"
                "项目已经正常运行于 Schema 2，关闭资源管理器或播放器后可稍后重试。"
            ),
        )
    state["legacy_cleanup_pending"] = False
    state["legacy_cleaned_at"] = _now()
    _write_json(state_path, state)
    return LegacyCleanupResult(
        project_path=root,
        cleanup_pending=False,
        removed_paths=tuple(removed),
    )
