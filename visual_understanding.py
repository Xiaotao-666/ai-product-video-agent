"""Project-scoped visual analysis orchestration and SHA-256 cache."""

from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from project_manager import ProjectPaths
from prompt_generator import ProductVideoRequest
from reference_assets import ReferenceAssetManager, inspect_image
from task_logger import TaskLogger
from vision_provider import VisualAnalysis, VisionAnalysisRequest, VisionProviderError
from vision_provider_registry import VisionProviderRegistry

if TYPE_CHECKING:
    from evaluation import EvaluationRecorder


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class VisualUnderstandingLayer:
    """Analyze project reference assets once and reuse immutable cached semantics."""

    def __init__(
        self,
        project: ProjectPaths,
        registry: VisionProviderRegistry,
        task_logger: TaskLogger | None = None,
        evaluation_recorder: "EvaluationRecorder | None" = None,
    ) -> None:
        self.project = project
        self.registry = registry
        self.task_logger = task_logger
        self.evaluation_recorder = evaluation_recorder

    def _record_evaluation(
        self,
        payload: dict[str, Any],
        asset: dict[str, Any],
        request: ProductVideoRequest,
        *,
        cache_hit: bool,
    ) -> None:
        if self.evaluation_recorder is None:
            return
        self.evaluation_recorder.record_visual_analysis(
            provider=str(payload.get("provider") or "unknown"),
            model=str(payload.get("provider_model") or "unknown"),
            api_version=str(payload.get("provider_api_version") or "unknown"),
            asset_id=str(payload.get("asset_id") or asset.get("asset_id") or ""),
            asset_sha256=str(
                payload.get("asset_sha256") or asset.get("sha256") or ""
            ),
            input_fields={
                "asset": {
                    "asset_id": asset.get("asset_id"),
                    "sha256": asset.get("sha256"),
                    "filename": asset.get("filename"),
                    "width": asset.get("width"),
                    "height": asset.get("height"),
                    "file_size": asset.get("file_size"),
                },
                "product_name": request.product_name,
                "product_description": request.product_description,
                "user_notes": request.user_notes,
            },
            analysis_result=dict(payload.get("analysis") or {}),
            cache_hit=cache_hit,
        )

    def _load_cached(self, asset: dict[str, Any]) -> dict[str, Any] | None:
        path = self.project.visual_analysis_path(str(asset["asset_id"]))
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if str(payload.get("asset_sha256")) != str(asset.get("sha256")):
                return None
            VisualAnalysis.model_validate(payload.get("analysis"))
            return payload
        except (OSError, json.JSONDecodeError, ValidationError, AttributeError):
            return None

    def cached_project_results(
        self, manager: ReferenceAssetManager
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for asset in manager.list_assets():
            cached = self._load_cached(asset)
            if cached is not None:
                results.append(cached)
        return results

    def analyze_asset(
        self,
        manager: ReferenceAssetManager,
        asset: dict[str, Any],
        request: ProductVideoRequest,
        *,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        cached = None if force_refresh else self._load_cached(asset)
        if cached is not None:
            if self.task_logger:
                self.task_logger.event(
                    "VISION_ANALYSIS_CACHE_HIT",
                    asset_id=asset.get("asset_id"),
                    sha256=asset.get("sha256"),
                )
            self._record_evaluation(cached, asset, request, cache_hit=True)
            return cached

        image_path = manager.asset_path(str(asset["asset_id"]))
        image_format, width, height = inspect_image(image_path)
        provider_request = VisionAnalysisRequest(
            image_path=image_path,
            asset_id=str(asset["asset_id"]),
            asset_sha256=str(asset["sha256"]),
            image_format=image_format,
            product_name=request.product_name,
            product_description=request.product_description,
            user_notes=request.user_notes,
        )
        adapter = self.registry.resolve(provider_request)
        if self.task_logger:
            self.task_logger.set_stage("visual_analysis")
            self.task_logger.event(
                "VISION_ANALYSIS_STARTED",
                asset_id=asset.get("asset_id"),
                sha256=asset.get("sha256"),
                image_format=image_format,
                width=width,
                height=height,
                provider=adapter.provider_name,
                model=adapter.model_name,
            )
            self.task_logger.api(
                "VISION_API_REQUESTED",
                adapter.provider_name,
                model=adapter.model_name,
                api_version=adapter.api_version,
                asset_id=asset.get("asset_id"),
                sha256=asset.get("sha256"),
            )
        try:
            analysis = adapter.analyze_image(provider_request)
        except VisionProviderError as exc:
            if self.task_logger:
                self.task_logger.api(
                    "VISION_API_FAILED",
                    adapter.provider_name,
                    model=adapter.model_name,
                    asset_id=asset.get("asset_id"),
                    error=exc,
                )
                self.task_logger.error(exc, stage="visual_analysis")
            raise

        payload = {
            "analysis_schema_version": 1,
            "asset_id": str(asset["asset_id"]),
            "asset_sha256": str(asset["sha256"]),
            "provider": adapter.provider_name,
            "provider_model": adapter.model_name,
            "provider_api_version": adapter.api_version,
            "analyzed_at": _now_iso(),
            "analysis": analysis.model_dump(),
        }
        self.project.visual_analysis_asset_dir(str(asset["asset_id"])).mkdir(
            parents=True, exist_ok=True
        )
        self.project.save_json(
            self.project.visual_analysis_path(str(asset["asset_id"])), payload
        )
        if self.task_logger:
            self.task_logger.api(
                "VISION_API_COMPLETED",
                adapter.provider_name,
                model=adapter.model_name,
                asset_id=asset.get("asset_id"),
            )
            self.task_logger.event(
                "VISION_ANALYSIS_COMPLETED",
                asset_id=asset.get("asset_id"),
                cache_path=self.project.visual_analysis_path(str(asset["asset_id"])),
            )
        self._record_evaluation(payload, asset, request, cache_hit=False)
        return payload

    def analyze_project_references(
        self,
        manager: ReferenceAssetManager,
        request: ProductVideoRequest,
        *,
        force_refresh: bool = False,
    ) -> list[dict[str, Any]]:
        return [
            self.analyze_asset(
                manager, asset, request, force_refresh=force_refresh
            )
            for asset in manager.list_assets()
        ]
