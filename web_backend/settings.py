"""Import-safe settings for the local Web backend."""

from __future__ import annotations

import os
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator


DEFAULT_CORS_ORIGINS = (
    "http://127.0.0.1:5173",
    "http://localhost:5173",
)


class BackendSettings(BaseModel):
    """Non-secret process settings used by the local-only backend."""

    model_config = ConfigDict(frozen=True)

    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=8000, ge=1, le=65535)
    projects_root: Path = Path(r"D:\desktop\视频生成Agent产出")
    runtime_root: Path | None = None
    task_workers: int = Field(default=2, ge=1, le=8)
    cors_origins: tuple[str, ...] = DEFAULT_CORS_ORIGINS

    @property
    def web_runtime_root(self) -> Path:
        return self.runtime_root or (self.projects_root / ".web_runtime")

    @field_validator("cors_origins")
    @classmethod
    def reject_wildcard_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(
            origin.strip().rstrip("/") for origin in value if origin.strip()
        )
        if not normalized or "*" in normalized:
            raise ValueError("CORS origins must be explicit")
        for origin in normalized:
            parsed = urlsplit(origin)
            hostname = (parsed.hostname or "").casefold()
            try:
                is_loopback = hostname == "localhost" or ip_address(hostname).is_loopback
                port = parsed.port
            except ValueError as exc:
                raise ValueError("CORS origins must be valid loopback URLs") from exc
            if (
                parsed.scheme not in {"http", "https"}
                or not is_loopback
                or port is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS origins must be loopback origins with ports")
        return normalized

    @classmethod
    def from_environment(cls) -> "BackendSettings":
        """Read only Web-specific environment variables without loading secrets."""

        raw_origins = os.getenv("WEB_CORS_ORIGINS")
        raw_runtime_root = os.getenv("WEB_RUNTIME_ROOT")
        cors_origins = (
            tuple(part for part in raw_origins.split(","))
            if raw_origins is not None
            else DEFAULT_CORS_ORIGINS
        )
        return cls(
            host=os.getenv("WEB_HOST", "127.0.0.1"),
            port=os.getenv("WEB_PORT", "8000"),
            projects_root=os.getenv(
                "WEB_PROJECTS_ROOT",
                r"D:\desktop\视频生成Agent产出",
            ),
            runtime_root=raw_runtime_root or None,
            task_workers=os.getenv("WEB_TASK_WORKERS", "2"),
            cors_origins=cors_origins,
        )
