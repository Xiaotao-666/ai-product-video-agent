"""Human review for cached visual analysis, independent from the video workflow state."""

from __future__ import annotations

import copy
import json
from datetime import datetime
from enum import Enum
from typing import Any, Callable

from pydantic import ValidationError

from project_manager import ProjectPaths
from review_manager import TaskCancelled
from task_logger import TaskLogger
from vision_provider import VisualAnalysis, VisualConstraints


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


class VisualAnalysisReviewStatus(str, Enum):
    WAITING_REVIEW = "WAITING_REVIEW"
    APPROVED = "APPROVED"
    EDITED = "EDITED"
    REJECTED = "REJECTED"


def _unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = str(value).strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def build_visual_constraints(
    analyses: list[dict[str, Any]],
) -> VisualConstraints:
    must_preserve: list[str] = []
    avoid: list[str] = []
    for item in analyses:
        analysis = VisualAnalysis.model_validate(item.get("analysis"))
        must_preserve.extend(analysis.must_keep_elements)
        avoid.extend(analysis.avoid_elements)
    return VisualConstraints(
        must_preserve=_unique(must_preserve),
        creative_freedom=[
            "背景环境可以在不影响主体识别的前提下创作",
            "灯光氛围可以围绕品牌调性创作",
            "摄像机运动可以围绕镜头目的创作",
        ],
        avoid=_unique(avoid),
    )


class VisualAnalysisReviewManager:
    def __init__(
        self, project: ProjectPaths, task_logger: TaskLogger | None = None
    ) -> None:
        self.project = project
        self.task_logger = task_logger
        self.path = project.visual_analysis_review_path()

    @staticmethod
    def _asset_signature(analyses: list[dict[str, Any]]) -> list[dict[str, str]]:
        return [
            {
                "asset_id": str(item.get("asset_id") or ""),
                "asset_sha256": str(item.get("asset_sha256") or ""),
            }
            for item in analyses
        ]

    @staticmethod
    def _validated_copy(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = copy.deepcopy(analyses)
        for item in result:
            item["analysis"] = VisualAnalysis.model_validate(
                item.get("analysis")
            ).model_dump()
        return result

    def _load(self) -> dict[str, Any] | None:
        if not self.path.is_file():
            return None
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def approved_result(
        self, analyses: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]] | None:
        payload = self._load()
        if not payload or payload.get("status") not in {
            VisualAnalysisReviewStatus.APPROVED.value,
            VisualAnalysisReviewStatus.EDITED.value,
        }:
            return None
        if payload.get("source_assets") != self._asset_signature(analyses):
            return None
        approved = payload.get("approved_analysis")
        try:
            validated = self._validated_copy(approved)
            constraints = VisualConstraints.model_validate(
                payload.get("visual_constraints")
            ).model_dump()
        except (TypeError, ValidationError):
            return None
        return validated, constraints

    def use_legacy_without_review(
        self, analyses: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Keep projects already past Creative resumable without a new gate."""
        approved = self.approved_result(analyses)
        if approved is not None:
            return approved
        validated = self._validated_copy(analyses)
        return validated, build_visual_constraints(validated).model_dump()

    def _save_decision(
        self,
        *,
        status: VisualAnalysisReviewStatus,
        original: list[dict[str, Any]],
        edited: list[dict[str, Any]] | None,
        action: str,
        existing_history: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        approved = edited if status == VisualAnalysisReviewStatus.EDITED else original
        if status == VisualAnalysisReviewStatus.REJECTED:
            approved = []
        history = list(existing_history or [])
        history.append({"timestamp": _now_iso(), "action": action, "status": status.value})
        payload = {
            "review_schema_version": 1,
            "status": status.value,
            "source_assets": self._asset_signature(original),
            "original_analysis": self._validated_copy(original),
            "edited_analysis": self._validated_copy(edited) if edited is not None else None,
            "edited_at": _now_iso() if edited is not None else None,
            "approved_analysis": self._validated_copy(approved) if approved else [],
            "visual_constraints": (
                build_visual_constraints(approved).model_dump() if approved else None
            ),
            "review_time": _now_iso(),
            "history": history,
        }
        self.project.save_json(self.path, payload)
        if self.task_logger:
            self.task_logger.review_action("visual_analysis", action)
            self.task_logger.event(
                "VISUAL_ANALYSIS_REVIEWED", status=status.value, action=action
            )
        return payload

    @staticmethod
    def _print_list(
        title: str, values: list[str], output: Callable[[str], None]
    ) -> None:
        output(f"\n{title}：")
        if not values:
            output("（无）")
            return
        for value in values:
            output(f"✓ {value}")

    def display(
        self, analyses: list[dict[str, Any]], output: Callable[[str], None] = print
    ) -> None:
        output("\n========== Visual Analysis Review ==========")
        output("\nAI视觉分析结果：")
        for item in analyses:
            analysis = VisualAnalysis.model_validate(item["analysis"])
            output(f"\n参考素材：{item.get('asset_id')}")
            output(f"\n产品：\n{analysis.product_identity}")
            output(f"\n品牌风格：\n{analysis.brand_style}")
            self._print_list("视觉特征", analysis.visual_features, output)
            self._print_list("材质", analysis.materials, output)
            self._print_list("颜色", analysis.colors, output)
            output(f"\n构图分析：\n{analysis.composition}")
            self._print_list("必须保持", analysis.must_keep_elements, output)
            self._print_list("避免", analysis.avoid_elements, output)
        output("\n============================================")

    @staticmethod
    def _edit_analysis(
        analyses: list[dict[str, Any]],
        input_fn: Callable[[str], str],
        output: Callable[[str], None],
    ) -> list[dict[str, Any]]:
        edited = copy.deepcopy(analyses)
        text_fields = (
            ("product_identity", "产品身份"),
            ("brand_style", "品牌风格"),
            ("composition", "构图分析"),
        )
        list_fields = (
            ("visual_features", "视觉特征"),
            ("materials", "材质"),
            ("colors", "颜色"),
            ("must_keep_elements", "必须保持元素"),
            ("avoid_elements", "避免元素"),
        )
        output("\n留空表示保留原值；列表请使用中文或英文逗号分隔。")
        for item in edited:
            output(f"\n编辑参考素材：{item.get('asset_id')}")
            analysis = dict(item["analysis"])
            for key, label in text_fields:
                value = input_fn(f"{label} [{analysis[key]}]：").strip()
                if value:
                    analysis[key] = value
            for key, label in list_fields:
                current = "、".join(analysis[key])
                value = input_fn(f"{label} [{current}]：").strip()
                if value:
                    analysis[key] = _unique(
                        value.replace("，", ",").split(",")
                    )
            item["analysis"] = VisualAnalysis.model_validate(analysis).model_dump()
        return edited

    def review(
        self,
        analyses: list[dict[str, Any]],
        *,
        reanalyze: Callable[[], list[dict[str, Any]]],
        input_fn: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
        on_cancel: Callable[[], None] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not analyses:
            return [], VisualConstraints(
                must_preserve=[], creative_freedom=[], avoid=[]
            ).model_dump()
        approved = self.approved_result(analyses)
        if approved is not None:
            if self.task_logger:
                self.task_logger.event("VISUAL_ANALYSIS_REVIEW_REUSED")
            return approved

        current = self._validated_copy(analyses)
        history = list((self._load() or {}).get("history") or [])
        while True:
            self.display(current, output)
            output("\n选项：")
            output("1. 确认并继续")
            output("2. 编辑视觉分析结果")
            output("3. 重新调用 Vision Provider")
            output("4. 取消")
            choice = input_fn("请输入 1、2、3 或 4：").strip()
            if choice == "1":
                payload = self._save_decision(
                    status=VisualAnalysisReviewStatus.APPROVED,
                    original=current,
                    edited=None,
                    action="approve",
                    existing_history=history,
                )
                return payload["approved_analysis"], payload["visual_constraints"]
            if choice == "2":
                edited = self._edit_analysis(current, input_fn, output)
                payload = self._save_decision(
                    status=VisualAnalysisReviewStatus.EDITED,
                    original=current,
                    edited=edited,
                    action="edit_and_approve",
                    existing_history=history,
                )
                return payload["approved_analysis"], payload["visual_constraints"]
            if choice == "3":
                rejected = self._save_decision(
                    status=VisualAnalysisReviewStatus.REJECTED,
                    original=current,
                    edited=None,
                    action="reanalyze",
                    existing_history=history,
                )
                history = rejected["history"]
                current = self._validated_copy(reanalyze())
                continue
            if choice == "4":
                self._save_decision(
                    status=VisualAnalysisReviewStatus.REJECTED,
                    original=current,
                    edited=None,
                    action="cancel",
                    existing_history=history,
                )
                if on_cancel:
                    on_cancel()
                raise TaskCancelled("Visual Analysis审核")
            output("无效选择，请输入 1、2、3 或 4。")
