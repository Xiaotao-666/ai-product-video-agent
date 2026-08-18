"""Application-neutral Creative generation and Core persistence workflow."""

from __future__ import annotations

from typing import Any

from evaluation import EvaluationRecorder
from project_manager import ProjectPaths
from project_state import ProjectCheckpoint, ProjectStage, StageStatus
from prompt_generator import DEEPSEEK_MODEL, ProductVideoRequest
from storyboard import CreativeBrief, generate_creative_brief
from task_logger import TaskLogger


def generate_creative_stage(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    deepseek_key: str,
    task_logger: TaskLogger,
    *,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> CreativeBrief:
    """Generate exactly one Creative artifact and advance to human review.

    Provider prompting, structured-output validation, and format retries remain
    owned by ``generate_creative_brief``. This callable owns the same Core
    persistence and checkpoint transitions for both CLI and Web entry points.
    """

    checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.RUNNING)
    task_logger.set_stage("creative")
    visual_kwargs = (
        {"reference_asset_context": reference_asset_context}
        if reference_asset_context
        else {}
    )
    brief = generate_creative_brief(
        request,
        deepseek_key,
        task_logger,
        **visual_kwargs,
    )
    if evaluation_recorder is not None:
        evaluation_recorder.record_prompt(
            "creative",
            model=DEEPSEEK_MODEL,
            operation="generate",
            input_fields={
                "product_information": request.model_dump(),
                "user_notes": request.user_notes,
                "reference_assets": reference_asset_context
                or {"available": False, "asset_count": 0},
            },
            output_result=brief.model_dump(),
        )
    paths.save_json(paths.creative_brief_path(), brief.model_dump())
    checkpoint.update_stage(ProjectStage.CREATIVE, StageStatus.COMPLETED)
    checkpoint.advance_to(
        ProjectStage.CREATIVE_REVIEW,
        StageStatus.WAITING_REVIEW,
    )
    task_logger.event("CREATIVE_GENERATED", "创意方案生成成功")
    return brief
