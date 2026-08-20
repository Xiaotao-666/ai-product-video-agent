"""Isolated atomic persistence for temporary Prompt revision drafts."""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from threading import RLock

from pydantic import ValidationError

from web_backend.models.prompt_revision import StoredPromptRevisionDraft
from web_backend.repositories.project_repository import normalize_project_id
from web_backend.repositories.shot_repository import normalize_shot_id


class PromptRevisionDraftRepositoryError(RuntimeError):
    pass


class PromptRevisionDraftNotFound(PromptRevisionDraftRepositoryError):
    pass


class PromptRevisionDraftDataCorrupt(PromptRevisionDraftRepositoryError):
    pass


class PromptRevisionDraftRepository:
    """Keep drafts under WEB_RUNTIME_ROOT, never in canonical project data."""

    def __init__(self, runtime_root: Path) -> None:
        self.runtime_root = Path(runtime_root)
        self._guard = RLock()

    @property
    def drafts_root(self) -> Path:
        return self.runtime_root / "prompt_revision_drafts"

    def save(self, draft: StoredPromptRevisionDraft) -> StoredPromptRevisionDraft:
        project_id = normalize_project_id(draft.project_id)
        shot_id, _number = normalize_shot_id(draft.shot_id)
        if project_id != draft.project_id or shot_id != draft.shot_id:
            raise PromptRevisionDraftDataCorrupt("draft identity is not canonical")
        with self._guard:
            project_root = self._ensure_project_root(project_id)
            path = self._draft_path(project_root, shot_id)
            self._atomic_write(path, draft)
        return draft

    def get(self, project_id: str, shot_id: str) -> StoredPromptRevisionDraft:
        canonical_project_id = normalize_project_id(project_id)
        canonical_shot_id, _number = normalize_shot_id(shot_id)
        with self._guard:
            project_root = self._existing_project_root(canonical_project_id)
            if project_root is None:
                raise PromptRevisionDraftNotFound("draft was not found")
            path = self._draft_path(project_root, canonical_shot_id)
            if not path.is_file():
                raise PromptRevisionDraftNotFound("draft was not found")
            try:
                resolved = path.resolve(strict=True)
                if resolved.parent != project_root:
                    raise PromptRevisionDraftDataCorrupt("draft escaped runtime root")
                with resolved.open("r", encoding="utf-8") as handle:
                    payload = json.load(handle)
                draft = StoredPromptRevisionDraft.model_validate(payload)
            except PromptRevisionDraftDataCorrupt:
                raise
            except (OSError, UnicodeError, json.JSONDecodeError, ValidationError) as error:
                raise PromptRevisionDraftDataCorrupt("draft data is unreadable") from error
            if (
                draft.project_id != canonical_project_id
                or draft.shot_id != canonical_shot_id
            ):
                raise PromptRevisionDraftDataCorrupt("draft identity is inconsistent")
            return draft

    def _existing_project_root(self, project_id: str) -> Path | None:
        if not self.runtime_root.exists():
            return None
        try:
            runtime_root = self.runtime_root.resolve(strict=True)
        except OSError as error:
            raise PromptRevisionDraftDataCorrupt("runtime root is unreadable") from error
        drafts_root = runtime_root / "prompt_revision_drafts"
        if not drafts_root.exists():
            return None
        try:
            resolved_drafts = drafts_root.resolve(strict=True)
        except OSError as error:
            raise PromptRevisionDraftDataCorrupt("draft storage is unreadable") from error
        if not drafts_root.is_dir() or resolved_drafts.parent != runtime_root:
            raise PromptRevisionDraftDataCorrupt("draft storage escaped runtime root")
        project_root = resolved_drafts / project_id
        if not project_root.exists():
            return None
        try:
            resolved_project = project_root.resolve(strict=True)
        except OSError as error:
            raise PromptRevisionDraftDataCorrupt("draft project is unreadable") from error
        if not project_root.is_dir() or resolved_project.parent != resolved_drafts:
            raise PromptRevisionDraftDataCorrupt("draft project escaped runtime root")
        return resolved_project

    def _ensure_project_root(self, project_id: str) -> Path:
        self.runtime_root.mkdir(parents=True, exist_ok=True)
        runtime_root = self.runtime_root.resolve(strict=True)
        drafts_root = runtime_root / "prompt_revision_drafts"
        drafts_root.mkdir(exist_ok=True)
        resolved_drafts = drafts_root.resolve(strict=True)
        if resolved_drafts.parent != runtime_root:
            raise PromptRevisionDraftDataCorrupt("draft storage escaped runtime root")
        project_root = resolved_drafts / project_id
        project_root.mkdir(exist_ok=True)
        resolved_project = project_root.resolve(strict=True)
        if resolved_project.parent != resolved_drafts:
            raise PromptRevisionDraftDataCorrupt("draft project escaped runtime root")
        return resolved_project

    @staticmethod
    def _draft_path(project_root: Path, shot_id: str) -> Path:
        canonical_shot_id, _number = normalize_shot_id(shot_id)
        path = project_root / f"{canonical_shot_id}.json"
        if path.parent != project_root:
            raise PromptRevisionDraftDataCorrupt("draft path escaped runtime root")
        return path

    @staticmethod
    def _atomic_write(path: Path, draft: StoredPromptRevisionDraft) -> None:
        rendered = json.dumps(
            draft.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        temporary = path.parent / f".{path.stem}.{uuid.uuid4().hex}.tmp"
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(rendered)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
