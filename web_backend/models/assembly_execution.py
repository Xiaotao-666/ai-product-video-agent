"""Public request models for durable Assembly execution."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AssemblyExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assembly_version: int = Field(ge=1)
