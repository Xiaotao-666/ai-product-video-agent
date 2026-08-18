"""Import-safe settings for the local Web backend."""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class BackendSettings(BaseModel):
    """Non-secret process settings used by the local-only backend."""

    model_config = ConfigDict(frozen=True)

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    projects_root: Path = Path(r"D:\desktop\视频生成Agent产出")

    @classmethod
    def from_environment(cls) -> "BackendSettings":
        """Read only Web-specific environment variables without loading secrets."""

        return cls(
            host=os.getenv("WEB_HOST", "127.0.0.1"),
            port=os.getenv("WEB_PORT", "8000"),
            projects_root=os.getenv(
                "WEB_PROJECTS_ROOT",
                r"D:\desktop\视频生成Agent产出",
            ),
        )
