"""FastAPI dependency accessors for application-scoped backend resources."""

from __future__ import annotations

from fastapi import Request

from web_backend.locking import ProjectLockManager
from web_backend.repositories.project_repository import ProjectRepository
from web_backend.services.capabilities import CapabilityService
from web_backend.services.projects import ProjectService
from web_backend.settings import BackendSettings


def get_settings(request: Request) -> BackendSettings:
    return request.app.state.settings


def get_project_repository(request: Request) -> ProjectRepository:
    return request.app.state.project_repository


def get_project_service(request: Request) -> ProjectService:
    return request.app.state.project_service


def get_project_lock_manager(request: Request) -> ProjectLockManager:
    return request.app.state.project_lock_manager


def get_capability_service(request: Request) -> CapabilityService:
    return request.app.state.capability_service
