"""Health endpoint with a deliberately non-sensitive response."""

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict


router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    status: Literal["ok"] = "ok"
    service: Literal["ai-product-video-agent"] = "ai-product-video-agent"
    api_version: Literal["v1"] = "v1"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()
