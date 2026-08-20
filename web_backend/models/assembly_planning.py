"""Public DTOs for project-level Assembly planning."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from web_backend.models.projects import ResponseModel


class AssemblyPlanningStatus(StrEnum):
    NOT_READY = "NOT_READY"
    READY = "READY"
    OUTDATED = "OUTDATED"


class AssemblyPlanShot(ResponseModel):
    shot_id: int = Field(ge=1)
    order: int = Field(ge=1)
    approved_video_version: int = Field(ge=1)
    prompt_version: int = Field(ge=1)
    duration: float = Field(gt=0)
    resolution: str = Field(
        min_length=2,
        max_length=16,
        pattern=r"^[0-9]{2,5}(?:[pP]|x[0-9]{2,5})$",
    )


class AssemblyPlan(ResponseModel):
    project_id: str
    assembly_version: int = Field(ge=1)
    status: AssemblyPlanningStatus
    created_at: str = Field(
        min_length=19,
        max_length=64,
        pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9:.+-]+$",
    )
    total_duration: float = Field(gt=0)
    shots: list[AssemblyPlanShot]


class AssemblyReadinessIssue(ResponseModel):
    shot_id: int | None = Field(default=None, ge=1)
    order: int | None = Field(default=None, ge=1)
    reason: str = Field(min_length=1, max_length=64)


class AssemblyReadiness(ResponseModel):
    project_id: str
    status: AssemblyPlanningStatus
    ready: bool
    shot_count: int = Field(ge=0)
    ready_count: int = Field(ge=0)
    total_duration: float | None = Field(default=None, gt=0)
    shots: list[AssemblyPlanShot]
    issues: list[AssemblyReadinessIssue]
    current_plan: AssemblyPlan | None = None
