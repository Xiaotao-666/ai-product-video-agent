"""Pure read-only projection of Schema 2 Shot manifests and bundles."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from web_backend.models.shots import (
    ShotDetail,
    ShotGenerationSummary,
    ShotListResponse,
    ShotPromptSummary,
    ShotSummary,
    ShotVersion,
    ShotVersionHistoryReason,
    ShotVersionRole,
    ShotVisualInputMode,
)
from web_backend.repositories.project_repository import (
    ProjectDataCorrupt,
    ProjectRepository,
    ProjectRepositoryError,
)


_SHOT_ID = re.compile(r"^shot_([1-9][0-9]*|0[1-9][0-9]*)$")
_VERSION = re.compile(r"^[1-9][0-9]*$")
_VERSION_DIR = re.compile(r"^v([0-9]+)$")
_WINDOWS_ABSOLUTE = re.compile(r"(?i)(?:[a-z]:[\\/]|\\\\|file://)")
_SECRET_MARKER = re.compile(
    r"(?i)(?:api[_ -]?key|credential(?:_env_name)?|authorization|"
    r"provider secret|provider[_ -]?task[_ -]?id|task[_ -]?id|file[_ -]?id|"
    r"bearer\s+\S+|sk-[A-Za-z0-9_-]{12,})"
)
_MAX_TEXT_LENGTH = 50000
_HIDDEN_TEXT = "[敏感内容已隐藏]"
_PUBLIC_REVIEW_STATUSES = {
    "NOT_STARTED",
    "GENERATING",
    "WAITING_REVIEW",
    "APPROVED",
    "REJECTED",
    "FAILED",
    "CANCELLED",
    "COMPLETED",
    "HISTORY",
    "UNKNOWN",
}


class ShotRepositoryError(ProjectRepositoryError):
    """Base class for Shot failures translated by the HTTP layer."""


class InvalidShotId(ShotRepositoryError):
    pass


class ShotNotFound(ShotRepositoryError):
    pass


class ShotDataCorrupt(ShotRepositoryError):
    pass


class InvalidShotVersion(ShotRepositoryError):
    pass


class VideoNotFound(ShotRepositoryError):
    pass


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _fully_unquote(value: str) -> str:
    decoded = str(value or "").strip()
    for _ in range(3):
        next_value = unquote(decoded)
        if next_value == decoded:
            break
        decoded = next_value
    return decoded


def normalize_shot_id(value: str) -> tuple[str, int]:
    decoded = _fully_unquote(value)
    match = _SHOT_ID.fullmatch(decoded)
    if match is None:
        raise InvalidShotId("unsafe shot id")
    number = int(match.group(1))
    canonical = f"shot_{number:02d}"
    if number <= 0 or decoded != canonical:
        raise InvalidShotId("non-canonical shot id")
    return canonical, number


def normalize_shot_version(value: str | int) -> int:
    decoded = _fully_unquote(str(value))
    if _VERSION.fullmatch(decoded) is None:
        raise InvalidShotVersion("unsafe shot version")
    number = int(decoded)
    if number <= 0 or number > 999999:
        raise InvalidShotVersion("shot version is out of range")
    return number


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return path != root
    except ValueError:
        return False


def _safe_positive_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or str(value).strip() not in {str(number), f"{float(number)}"}:
        return None
    return number


def _safe_count(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return max(number, 0)


def _safe_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    if _WINDOWS_ABSOLUTE.search(value) or _SECRET_MARKER.search(value):
        return _HIDDEN_TEXT
    return value[:_MAX_TEXT_LENGTH]


def _safe_status(value: Any, *, fallback: str = "UNKNOWN") -> str:
    normalized = str(value or fallback).strip().upper()
    return normalized if normalized in _PUBLIC_REVIEW_STATUSES else fallback


class ShotRepository:
    """Read fixed Shot files without importing mutable Core managers."""

    def __init__(self, project_repository: ProjectRepository) -> None:
        self.project_repository = project_repository

    def list_shots(self, project_id: str) -> ShotListResponse:
        workflow, project_dir, project_data = self._project_context(project_id)
        checkpoint_shots = self._checkpoint_shots(project_data)
        shot_numbers = set(checkpoint_shots)
        shot_numbers.update(self._directory_shot_numbers(project_dir))
        summaries = [
            self._shot_summary(project_dir, number, checkpoint_shots.get(number))
            for number in sorted(shot_numbers)
        ]
        return ShotListResponse(
            project_id=workflow.project_id,
            status=workflow.stages.shots.status,
            shots=summaries,
        )

    def get_shot(self, project_id: str, shot_id: str) -> ShotDetail:
        canonical_id, shot_number = normalize_shot_id(shot_id)
        workflow, project_dir, project_data = self._project_context(project_id)
        checkpoint_shots = self._checkpoint_shots(project_data)
        shot_dir, manifest, checkpoint = self._shot_context(
            project_dir, shot_number, checkpoint_shots.get(shot_number)
        )
        summary = self._summary_from_data(
            canonical_id, shot_dir, manifest, checkpoint
        )
        canonical_prompt = self._canonical_prompt(project_dir, shot_number)
        prompt_versions = self._records_by_version(checkpoint.get("prompt_versions"))
        generation_versions = self._records_by_version(
            checkpoint.get("generation_versions"), key="video_version"
        )
        approved_prompt_version = _safe_positive_int(
            checkpoint.get("approved_prompt_version")
        ) or _safe_positive_int(checkpoint.get("active_prompt_version"))
        versions = [
            self._version_detail(
                project_dir=project_dir,
                shot_dir=shot_dir,
                shot_number=shot_number,
                version=version,
                official_version=summary.official_version,
                pending_review_version=summary.pending_review_version,
                approved_prompt_version=approved_prompt_version,
                canonical_prompt=canonical_prompt,
                prompt_versions=prompt_versions,
                checkpoint_generation=generation_versions.get(version, {}),
            )
            for version in sorted(
                self._version_numbers(shot_dir, manifest, checkpoint), reverse=True
            )
        ]
        return ShotDetail(
            project_id=workflow.project_id,
            shot_id=summary.shot_id,
            status=summary.status,
            official_version=summary.official_version,
            pending_review_version=summary.pending_review_version,
            version_count=len(versions),
            generation_count=summary.generation_count,
            versions=versions,
        )

    def resolve_video(
        self, project_id: str, shot_id: str, version: str | int
    ) -> Path:
        normalized_version = normalize_shot_version(version)
        detail = self.get_shot(project_id, shot_id)
        if not any(item.version == normalized_version for item in detail.versions):
            raise VideoNotFound("shot version was not found")
        _, shot_number = normalize_shot_id(shot_id)
        project_dir = self.project_repository.resolve_project_dir(project_id).resolve()
        shot_dir = project_dir / "shots" / f"shot_{shot_number:02d}"
        return self._video_path(
            project_dir, shot_dir, normalized_version, require_file=True
        )

    def _project_context(self, project_id: str):
        workflow = self.project_repository.get_workflow(project_id)
        project_dir = self.project_repository.resolve_project_dir(project_id).resolve()
        project_data = self._read_object(project_dir, ("project.json",), required=True)
        assert project_data is not None
        return workflow, project_dir, project_data

    @staticmethod
    def _checkpoint_shots(project_data: Mapping[str, Any]) -> dict[int, Mapping[str, Any]]:
        shots = _mapping(_mapping(project_data.get("video_generation")).get("shots"))
        result: dict[int, Mapping[str, Any]] = {}
        for key, raw in shots.items():
            checkpoint = _mapping(raw)
            number = _safe_positive_int(checkpoint.get("shot_id"))
            if number is None:
                number = _safe_positive_int(key)
            if number is not None:
                result[number] = checkpoint
        return result

    def _directory_shot_numbers(self, project_dir: Path) -> set[int]:
        shots_root = self._shots_root(project_dir)
        if shots_root is None:
            return set()
        numbers: set[int] = set()
        for child in shots_root.iterdir():
            match = _SHOT_ID.fullmatch(child.name)
            if match is None:
                continue
            number = int(match.group(1))
            if child.name != f"shot_{number:02d}":
                continue
            try:
                resolved = child.resolve()
            except OSError as exc:
                raise ShotDataCorrupt("shot directory cannot be resolved") from exc
            if not child.is_dir() or resolved.parent != shots_root:
                raise ShotDataCorrupt("shot directory escaped project")
            numbers.add(number)
        return numbers

    def _shot_summary(
        self,
        project_dir: Path,
        shot_number: int,
        checkpoint: Mapping[str, Any] | None,
    ) -> ShotSummary:
        shot_dir, manifest, resolved_checkpoint = self._shot_context(
            project_dir, shot_number, checkpoint
        )
        return self._summary_from_data(
            f"shot_{shot_number:02d}", shot_dir, manifest, resolved_checkpoint
        )

    def _shot_context(
        self,
        project_dir: Path,
        shot_number: int,
        checkpoint: Mapping[str, Any] | None,
    ) -> tuple[Path, Mapping[str, Any], Mapping[str, Any]]:
        canonical = f"shot_{shot_number:02d}"
        shots_root = self._shots_root(project_dir)
        shot_dir = (shots_root or (project_dir / "shots")) / canonical
        directory_exists = shot_dir.exists()
        if directory_exists:
            try:
                resolved_shot = shot_dir.resolve()
            except OSError as exc:
                raise ShotDataCorrupt("shot directory cannot be resolved") from exc
            expected_parent = (shots_root or (project_dir / "shots").resolve())
            if not shot_dir.is_dir() or resolved_shot.parent != expected_parent:
                raise InvalidShotId("shot path escaped project")
            shot_dir = resolved_shot
        manifest = self._read_object(shot_dir, ("shot.json",), required=False)
        if checkpoint is None and manifest is None and not directory_exists:
            raise ShotNotFound("shot was not found")
        if manifest is not None:
            manifest_id = _safe_positive_int(manifest.get("shot_id"))
            if manifest_id is not None and manifest_id != shot_number:
                raise ShotDataCorrupt("shot manifest id mismatch")
        return shot_dir, _mapping(manifest), _mapping(checkpoint)

    def _summary_from_data(
        self,
        shot_id: str,
        shot_dir: Path,
        manifest: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
    ) -> ShotSummary:
        status = _safe_status(
            checkpoint.get("status", manifest.get("status")), fallback="NOT_STARTED"
        )
        official = (
            _safe_positive_int(checkpoint.get("approved_video_version"))
            or _safe_positive_int(manifest.get("approved_version"))
        )
        if official is None and status == "APPROVED":
            official = (
                _safe_positive_int(checkpoint.get("active_video_version"))
                or _safe_positive_int(manifest.get("active_version"))
            )
        pending = self._pending_review_version(manifest, checkpoint)
        versions = self._version_numbers(shot_dir, manifest, checkpoint)
        return ShotSummary(
            shot_id=shot_id,
            status=status,
            official_version=official,
            pending_review_version=pending,
            version_count=len(versions),
            generation_count=_safe_count(
                checkpoint.get("generation_count", manifest.get("generation_count"))
            ),
        )

    @staticmethod
    def _pending_review_version(
        manifest: Mapping[str, Any], checkpoint: Mapping[str, Any]
    ) -> int | None:
        internal = checkpoint.get("candidate")
        if isinstance(internal, Mapping):
            status = str(internal.get("status") or "NONE").upper()
            if status != "NONE":
                return _safe_positive_int(internal.get("video_version"))
            if str(checkpoint.get("status") or "").upper() == "WAITING_REVIEW":
                return _safe_positive_int(checkpoint.get("active_video_version"))
            return None
        if str(checkpoint.get("status") or "").upper() == "WAITING_REVIEW":
            return _safe_positive_int(checkpoint.get("active_video_version"))
        return _safe_positive_int(manifest.get("candidate_version"))

    def _version_numbers(
        self,
        shot_dir: Path,
        manifest: Mapping[str, Any],
        checkpoint: Mapping[str, Any],
    ) -> set[int]:
        versions: set[int] = set()
        raw_manifest_versions = manifest.get("versions")
        if isinstance(raw_manifest_versions, list):
            versions.update(
                number
                for item in raw_manifest_versions
                if (number := _safe_positive_int(item)) is not None
            )
        raw_generations = checkpoint.get("generation_versions")
        if isinstance(raw_generations, list):
            versions.update(
                number
                for item in raw_generations
                if (
                    number := _safe_positive_int(
                        _mapping(item).get("video_version")
                    )
                )
                is not None
            )
        for key in (
            "active_video_version",
            "approved_video_version",
            "pending_video_version",
        ):
            number = _safe_positive_int(checkpoint.get(key))
            if number is not None:
                versions.add(number)
        internal = _mapping(checkpoint.get("candidate"))
        internal_version = _safe_positive_int(internal.get("video_version"))
        if internal_version is not None:
            versions.add(internal_version)
        if shot_dir.exists() and shot_dir.is_dir():
            resolved_shot = shot_dir.resolve()
            for child in shot_dir.iterdir():
                match = _VERSION_DIR.fullmatch(child.name)
                if match is None:
                    continue
                number = int(match.group(1))
                if number <= 0 or child.name != f"v{number:03d}":
                    continue
                resolved = child.resolve()
                if not child.is_dir() or resolved.parent != resolved_shot:
                    raise ShotDataCorrupt("version directory escaped shot")
                versions.add(number)
        return versions

    def _version_detail(
        self,
        *,
        project_dir: Path,
        shot_dir: Path,
        shot_number: int,
        version: int,
        official_version: int | None,
        pending_review_version: int | None,
        approved_prompt_version: int | None,
        canonical_prompt: Mapping[str, Any],
        prompt_versions: Mapping[int, Mapping[str, Any]],
        checkpoint_generation: Mapping[str, Any],
    ) -> ShotVersion:
        prompt = self._read_version_object(shot_dir, version, "prompt.json")
        safety = self._read_version_object(shot_dir, version, "safety.json")
        generation = self._read_version_object(shot_dir, version, "generation.json")
        review = self._read_version_object(shot_dir, version, "review.json")
        prompt_version = (
            _safe_positive_int(prompt.get("prompt_version"))
            or _safe_positive_int(generation.get("prompt_version"))
            or _safe_positive_int(checkpoint_generation.get("prompt_version"))
        )
        prompt_record = prompt_versions.get(prompt_version or -1, {})
        role = (
            ShotVersionRole.OFFICIAL
            if version == official_version
            else ShotVersionRole.PENDING_REVIEW
            if version == pending_review_version
            else ShotVersionRole.HISTORY
        )
        visual_core = (
            _safe_text(prompt.get("visual_prompt_core"))
            or _safe_text(prompt_record.get("visual_prompt_core"))
        )
        if visual_core is None and prompt_version == approved_prompt_version:
            visual_core = _safe_text(canonical_prompt.get("visual_prompt_core"))
        final_prompt = (
            _safe_text(safety.get("final_submit_prompt"))
            or _safe_text(prompt.get("prompt_text"))
            or _safe_text(prompt_record.get("prompt"))
        )
        if final_prompt is None and prompt_version == approved_prompt_version:
            final_prompt = _safe_text(canonical_prompt.get("video_prompt"))
        merged_generation = dict(checkpoint_generation)
        merged_generation.update(generation)
        created_at = (
            _safe_text(prompt.get("created_at"))
            or _safe_text(merged_generation.get("created_at"))
            or _safe_text(merged_generation.get("submitted_at"))
        )
        review_status = _safe_status(
            review.get("review_result")
            or checkpoint_generation.get("review_result")
            or merged_generation.get("status")
            or ("WAITING_REVIEW" if role is ShotVersionRole.PENDING_REVIEW else None)
        )
        history_reason = self._history_reason(role, review_status, review)
        return ShotVersion(
            version=version,
            role=role,
            review_status=review_status,
            history_reason=history_reason,
            created_at=created_at,
            prompt=ShotPromptSummary(
                version=prompt_version,
                source=_safe_text(
                    prompt.get("prompt_source", prompt_record.get("source"))
                ),
                visual_prompt_core=visual_core,
                final_prompt=final_prompt,
            ),
            generation=ShotGenerationSummary(
                model=_safe_text(merged_generation.get("provider_model")),
                visual_input_mode=self._visual_input_mode(
                    merged_generation.get("visual_input")
                ),
            ),
            video_available=self._video_path(
                project_dir, shot_dir, version, require_file=False
            )
            is not None,
        )

    @staticmethod
    def _history_reason(
        role: ShotVersionRole,
        review_status: str,
        review: Mapping[str, Any],
    ) -> ShotVersionHistoryReason | None:
        if role is not ShotVersionRole.HISTORY:
            return None
        if review_status == "APPROVED":
            return ShotVersionHistoryReason.PREVIOUSLY_APPROVED

        events = review.get("history")
        event_records = (
            [item for item in events if isinstance(item, Mapping)]
            if isinstance(events, list)
            else []
        )
        current = {
            "review_result": review.get("review_result"),
            "user_action": review.get("user_action"),
        }
        event_records.append(current)
        for event in reversed(event_records):
            if str(event.get("review_result") or "").upper() != "REJECTED":
                continue
            action = str(event.get("user_action") or "").lower()
            if action == "regenerate_current_prompt":
                return ShotVersionHistoryReason.SUPERSEDED
            if action == "candidate_rejected":
                return ShotVersionHistoryReason.EXPLICITLY_REJECTED
        return ShotVersionHistoryReason.UNKNOWN

    @staticmethod
    def _records_by_version(
        value: Any, *, key: str = "version"
    ) -> dict[int, Mapping[str, Any]]:
        if not isinstance(value, list):
            return {}
        result: dict[int, Mapping[str, Any]] = {}
        for raw in value:
            item = _mapping(raw)
            version = _safe_positive_int(item.get(key))
            if version is not None:
                result[version] = item
        return result

    def _canonical_prompt(
        self, project_dir: Path, shot_number: int
    ) -> Mapping[str, Any]:
        payload = self._read_object(
            project_dir, ("storyboard", "video_prompts.json"), required=False
        )
        if payload is None or not isinstance(payload.get("shots"), list):
            return {}
        for raw in payload["shots"]:
            item = _mapping(raw)
            if _safe_positive_int(item.get("shot_id")) == shot_number:
                return item
        return {}

    def _read_version_object(
        self, shot_dir: Path, version: int, filename: str
    ) -> Mapping[str, Any]:
        if filename not in {"prompt.json", "safety.json", "generation.json", "review.json"}:
            raise ShotDataCorrupt("unexpected bundle filename")
        return _mapping(
            self._read_object(
                shot_dir, (f"v{version:03d}", filename), required=False
            )
        )

    def _read_object(
        self, base: Path, parts: tuple[str, ...], *, required: bool
    ) -> Mapping[str, Any] | None:
        try:
            root = base.resolve()
            path = base.joinpath(*parts)
            if not path.exists():
                if path.is_symlink():
                    raise ShotDataCorrupt("broken JSON link")
                if required:
                    raise ProjectDataCorrupt("required project data is missing")
                return None
            resolved = path.resolve()
        except OSError as exc:
            raise ShotDataCorrupt("shot JSON path cannot be resolved") from exc
        if not path.is_file() or not _is_within(resolved, root):
            raise ShotDataCorrupt("shot JSON escaped its fixed directory")
        try:
            with resolved.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ShotDataCorrupt("shot JSON is unreadable") from exc
        if not isinstance(payload, Mapping):
            raise ShotDataCorrupt("shot JSON is not an object")
        return payload

    @staticmethod
    def _visual_input_mode(value: Any) -> ShotVisualInputMode:
        mode = str(_mapping(value).get("mode") or "none").lower()
        return {
            "none": ShotVisualInputMode.NONE,
            "first_frame": ShotVisualInputMode.FIRST_FRAME,
            "reference_asset": ShotVisualInputMode.REFERENCE_ASSET,
        }.get(mode, ShotVisualInputMode.UNKNOWN)

    def _shots_root(self, project_dir: Path) -> Path | None:
        root = project_dir.resolve()
        path = project_dir / "shots"
        if not path.exists():
            if path.is_symlink():
                raise ShotDataCorrupt("broken shots directory link")
            return None
        try:
            resolved = path.resolve()
        except OSError as exc:
            raise ShotDataCorrupt("shots directory cannot be resolved") from exc
        if not path.is_dir() or not _is_within(resolved, root):
            raise ShotDataCorrupt("shots directory escaped project")
        return resolved

    def _video_path(
        self,
        project_dir: Path,
        shot_dir: Path,
        version: int,
        *,
        require_file: bool,
    ) -> Path | None:
        try:
            project_root = project_dir.resolve()
            resolved_shot = shot_dir.resolve()
            version_dir = shot_dir / f"v{version:03d}"
            if not version_dir.exists() or not version_dir.is_dir():
                if require_file:
                    raise VideoNotFound("video version directory is missing")
                return None
            resolved_version = version_dir.resolve()
            video = version_dir / "video.mp4"
            if not video.exists() or not video.is_file() or video.stat().st_size <= 0:
                if require_file:
                    raise VideoNotFound("video file is missing")
                return None
            resolved_video = video.resolve()
        except VideoNotFound:
            raise
        except OSError as exc:
            raise ShotDataCorrupt("video path cannot be resolved") from exc
        if (
            not _is_within(resolved_shot, project_root)
            or resolved_version.parent != resolved_shot
            or resolved_video.parent != resolved_version
            or not _is_within(resolved_video, project_root)
        ):
            raise InvalidShotId("video path escaped its fixed bundle")
        return resolved_video
