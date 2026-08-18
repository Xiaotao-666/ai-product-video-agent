"""Import-safe FastAPI application for the local Web V1 backend."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from ipaddress import ip_address

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from web_backend.errors import register_exception_handlers
from web_backend.locking import DEFAULT_PROJECT_LOCK_MANAGER, ProjectLockManager
from web_backend.middleware import CorrelationIdMiddleware
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.routers.capabilities import router as capabilities_router
from web_backend.routers.health import router as health_router
from web_backend.routers.projects import router as projects_router
from web_backend.services.capabilities import CapabilityService
from web_backend.services.projects import ProjectService
from web_backend.settings import BackendSettings


lifecycle_logger = logging.getLogger("uvicorn.error.web_lifecycle")


def _is_loopback_host(host: str) -> bool:
    normalized = str(host).strip().strip("[]").casefold()
    if normalized == "localhost":
        return True
    try:
        return ip_address(normalized).is_loopback
    except ValueError:
        return False


def _initialize_local_resources(application: FastAPI) -> None:
    """Build lightweight objects only; constructors perform no project I/O."""

    settings = application.state.settings
    lock_manager = application.state.project_lock_manager
    application.state.project_repository = ProjectRepository(settings.projects_root)
    application.state.project_service = ProjectService(
        settings.projects_root,
        lock_manager,
    )
    application.state.capability_service = CapabilityService()


@asynccontextmanager
async def backend_lifespan(application: FastAPI):
    _initialize_local_resources(application)
    application.state.lifecycle_started = True
    settings = application.state.settings
    if not _is_loopback_host(settings.host):
        lifecycle_logger.warning(
            "Web backend host is not loopback; use an explicit trusted network boundary"
        )
    try:
        yield
    finally:
        # Current resources own no file, network, provider, or subprocess handles.
        application.state.lifecycle_started = False


def create_app(
    *,
    settings: BackendSettings | None = None,
    lock_manager: ProjectLockManager | None = None,
) -> FastAPI:
    application = FastAPI(
        title="AI Product Video Agent API",
        version="v1",
        lifespan=backend_lifespan,
    )
    application.state.settings = settings or BackendSettings.from_environment()
    application.state.project_lock_manager = (
        lock_manager or DEFAULT_PROJECT_LOCK_MANAGER
    )
    # Preserve compatibility with non-context-managed TestClient callers while
    # lifespan reinitializes the same lightweight resource graph at startup.
    _initialize_local_resources(application)
    application.state.lifecycle_started = False
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(application.state.settings.cors_origins),
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-Correlation-ID"],
        expose_headers=["Location", "X-Correlation-ID"],
        max_age=600,
    )
    application.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router, prefix="/api")
    application.include_router(capabilities_router, prefix="/api")
    application.include_router(projects_router, prefix="/api")
    return application


app = create_app()
