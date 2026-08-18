"""Backward-compatible facade for the implemented Final Export Pipeline.

New code should import :mod:`export_pipeline` and :mod:`export_assets`.
"""

from __future__ import annotations

from typing import Any

from export_assets import EXPORT_SCHEMA_VERSION, ExportAssetManager
from export_pipeline import ExportPipeline, ExportPipelineError
from project_manager import ProjectPaths


FinalExportError = ExportPipelineError


class FinalExportManager(ExportPipeline):
    """Compatibility name retained for existing integrations and old tests."""

    def __init__(
        self,
        project: ProjectPaths,
        checkpoint: Any,
        task_logger: Any | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(project, checkpoint, task_logger, **kwargs)


__all__ = [
    "EXPORT_SCHEMA_VERSION",
    "ExportAssetManager",
    "FinalExportError",
    "FinalExportManager",
]
