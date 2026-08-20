"""Public-safe models for one-Shot AI Prompt revision drafts."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from web_backend.models.projects import ResponseModel


MAX_PROMPT_REVISION_FEEDBACK_LENGTH = 2000
_UNSAFE_PUBLIC_TEXT = re.compile(
    r"(?i)(?:[a-z]:[\\/]|\\\\|file://|api[_ -]?key|credential|"
    r"authorization|provider secret|bearer\s+\S+|sk-[A-Za-z0-9_-]{12,})"
)


class PromptRevisionDraftRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    feedback: str = Field(
        min_length=1,
        max_length=MAX_PROMPT_REVISION_FEEDBACK_LENGTH,
    )

    @field_validator("feedback", mode="before")
    @classmethod
    def trim_feedback(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("feedback")
    @classmethod
    def reject_unsafe_feedback(cls, value: str) -> str:
        if _UNSAFE_PUBLIC_TEXT.search(value):
            raise ValueError("feedback contains unsafe content")
        return value


class PromptRevisionDraftResponse(ResponseModel):
    base_prompt_version: int = Field(ge=1)
    original_prompt: str = Field(min_length=1)
    draft_prompt: str = Field(min_length=1)
    feedback: str = Field(min_length=1, max_length=MAX_PROMPT_REVISION_FEEDBACK_LENGTH)
    created_at: datetime

    @field_validator("original_prompt", "draft_prompt", "feedback")
    @classmethod
    def reject_unsafe_public_text(cls, value: str) -> str:
        if _UNSAFE_PUBLIC_TEXT.search(value):
            raise ValueError("draft contains unsafe public content")
        return value

    @field_validator("created_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("draft timestamp must include a timezone")
        return value


class StoredPromptRevisionDraft(PromptRevisionDraftResponse):
    """Runtime-only record; internal identity/fingerprint never reach the API."""

    project_id: str = Field(min_length=1, max_length=255)
    shot_id: str = Field(pattern=r"^shot_[0-9]{2,}$")
    fingerprint_schema_version: int = Field(default=1, ge=1, le=2)
    base_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    content_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )
    state_fingerprint: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{64}$",
    )

    @model_validator(mode="after")
    def require_fingerprints_for_schema(self) -> "StoredPromptRevisionDraft":
        if self.fingerprint_schema_version == 1:
            if self.base_fingerprint is None:
                raise ValueError("legacy draft requires base_fingerprint")
        elif self.content_fingerprint is None or self.state_fingerprint is None:
            raise ValueError("schema v2 draft requires content and state fingerprints")
        return self

    def public_response(self) -> PromptRevisionDraftResponse:
        return PromptRevisionDraftResponse.model_validate(
            self.model_dump(
                include={
                    "base_prompt_version",
                    "original_prompt",
                    "draft_prompt",
                    "feedback",
                    "created_at",
                }
            )
        )


class PromptRevisionDraftAdoptResponse(ResponseModel):
    project_id: str = Field(min_length=1, max_length=255)
    shot_id: str = Field(pattern=r"^shot_[0-9]{2,}$")
    prompt_version: int = Field(ge=1)
    parent_version: int = Field(ge=1)
    source: Literal["ai_revision"]
    active_prompt_version: int = Field(ge=1)
    approved_prompt_version: int | None = Field(default=None, ge=1)
    created_at: str = Field(min_length=1)
