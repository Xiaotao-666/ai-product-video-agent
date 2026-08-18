"""Public capability DTOs containing availability booleans only."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CapabilityModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class CapabilityAvailability(CapabilityModel):
    available: bool


class PlanningCapabilities(CapabilityModel):
    deepseek: CapabilityAvailability


class VideoCapabilities(CapabilityModel):
    minimax_hailuo: CapabilityAvailability
    minimax_h3: CapabilityAvailability


class VoiceCapabilities(CapabilityModel):
    aliyun_tts: CapabilityAvailability
    xfyun_tts: CapabilityAvailability


class CapabilitiesResponse(CapabilityModel):
    planning: PlanningCapabilities
    video: VideoCapabilities
    voice: VoiceCapabilities
    ffmpeg: CapabilityAvailability
