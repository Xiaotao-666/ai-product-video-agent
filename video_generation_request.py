"""Provider-neutral description of one video generation request."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from project_manager import ProjectPaths
from visual_input import ensure_supported_visual_input, visual_input_snapshot


@dataclass(frozen=True)
class ProviderSelection:
    provider: str
    model: str
    selection_mode: str = "manual"

    def __post_init__(self) -> None:
        mode = str(self.selection_mode or "manual").strip().lower()
        if mode not in {"auto", "manual"}:
            raise ValueError("selection_mode 必须是 auto 或 manual。")
        object.__setattr__(self, "selection_mode", mode)


@dataclass(frozen=True)
class VideoGenerationRequest:
    shot_id: int | None
    prompt: str
    duration: int
    resolution: str
    visual_input: dict[str, Any]
    project: ProjectPaths
    provider_selection: ProviderSelection | None = None

    def __post_init__(self) -> None:
        if not self.prompt.strip():
            raise ValueError("Video prompt 不能为空。")
        if not isinstance(self.duration, int) or self.duration <= 0:
            raise ValueError("Video duration 必须是正整数。")
        if not self.resolution.strip():
            raise ValueError("Video resolution 不能为空。")
        object.__setattr__(
            self, "visual_input", visual_input_snapshot(self.visual_input)
        )

    @property
    def required_capability(self) -> str:
        return ensure_supported_visual_input(self.visual_input)["mode"]
