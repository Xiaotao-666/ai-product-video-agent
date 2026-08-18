"""Side-effect-free projection of locally configured backend capabilities."""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping

from web_backend.models.capabilities import (
    CapabilitiesResponse,
    CapabilityAvailability,
    PlanningCapabilities,
    VideoCapabilities,
    VoiceCapabilities,
)


def _configured(environment: Mapping[str, str], *names: str) -> bool:
    return all(bool(str(environment.get(name, "")).strip()) for name in names)


class CapabilityService:
    """Inspect configuration presence and tool discovery without invoking either."""

    def __init__(
        self,
        environment: Mapping[str, str] | None = None,
        which: Callable[[str], str | None] | None = None,
    ) -> None:
        self._environment = environment if environment is not None else os.environ
        self._which = which or shutil.which

    def get_capabilities(self) -> CapabilitiesResponse:
        available = CapabilityAvailability
        ffmpeg_available = bool(self._which("ffmpeg"))
        ffprobe_available = bool(self._which("ffprobe"))
        return CapabilitiesResponse(
            planning=PlanningCapabilities(
                deepseek=available(
                    available=_configured(self._environment, "DEEPSEEK_API_KEY")
                )
            ),
            video=VideoCapabilities(
                minimax_hailuo=available(
                    available=_configured(self._environment, "MINIMAX_API_KEY")
                ),
                minimax_h3=available(
                    available=_configured(self._environment, "MINIMAX_H3_API_KEY")
                ),
            ),
            voice=VoiceCapabilities(
                aliyun_tts=available(
                    available=_configured(
                        self._environment,
                        "ALIYUN_ACCESS_KEY_ID",
                        "ALIYUN_ACCESS_KEY_SECRET",
                        "ALIYUN_TTS_APP_KEY",
                    )
                ),
                xfyun_tts=available(
                    available=_configured(
                        self._environment,
                        "XFYUN_APP_ID",
                        "XFYUN_API_KEY",
                        "XFYUN_API_SECRET",
                    )
                ),
            ),
            ffmpeg=available(available=ffmpeg_available and ffprobe_available),
        )
