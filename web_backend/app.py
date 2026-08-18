"""Import-safe FastAPI application for the local Web V1 backend."""

from __future__ import annotations

from fastapi import FastAPI

from web_backend.errors import register_exception_handlers
from web_backend.middleware import CorrelationIdMiddleware
from web_backend.routers.health import router as health_router
from web_backend.routers.projects import router as projects_router
from web_backend.settings import BackendSettings


def create_app(*, settings: BackendSettings | None = None) -> FastAPI:
    application = FastAPI(
        title="AI Product Video Agent API",
        version="v1",
    )
    application.state.settings = settings or BackendSettings.from_environment()
    application.add_middleware(CorrelationIdMiddleware)
    register_exception_handlers(application)
    application.include_router(health_router, prefix="/api")
    application.include_router(projects_router, prefix="/api")
    return application


app = create_app()
