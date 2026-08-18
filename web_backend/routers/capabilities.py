"""Read-only local capability discovery endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from web_backend.dependencies import get_capability_service
from web_backend.models.capabilities import CapabilitiesResponse
from web_backend.services.capabilities import CapabilityService


router = APIRouter(tags=["capabilities"])


@router.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities(
    service: Annotated[CapabilityService, Depends(get_capability_service)],
) -> CapabilitiesResponse:
    return service.get_capabilities()
