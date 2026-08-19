"""Checkpointed human-in-the-loop creative, storyboard, prompt, and video pipeline."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping, TypeVar

from dotenv import load_dotenv
from pydantic import BaseModel, ValidationError

from creative_workflow import (
    approve_creative_stage,
    generate_creative_stage,
    regenerate_creative_stage,
    retry_failed_creative_stage,
    revise_creative_stage,
)
from evaluation import EvaluationRecorder
from project_manager import ProjectDirectoryError, ProjectPaths, ask_project_paths
from project_migration import detect_project_schema, migrate_project_to_v2
from project_state import (
    ProjectCheckpoint,
    ProjectStage,
    ProjectStateError,
    ShotStatus,
    StageStatus,
    ask_existing_project_action,
    ask_restart_stage,
    display_project_status,
)
from prompt_generator import (
    DEEPSEEK_MODEL,
    PromptGenerationError,
    PromptSafetyReview,
    ProductVideoRequest,
    review_prompt_safety,
)
from post_production_menu import (
    has_completed_assembly,
    project_resume_menu,
)
from review_manager import (
    ReviewRecorder,
    TaskCancelled,
    human_review_gate,
    print_cancelled,
)
from reference_assets import (
    ReferenceAssetError,
    ReferenceAssetManager,
    select_regeneration_visual_input,
    select_shot_visual_input,
    setup_project_references,
)
from storyboard import (
    CreativeBrief,
    Storyboard,
    StoryboardError,
    VideoPromptPlan,
    generate_creative_brief,
    generate_video_prompts,
    plan_shot_durations,
    revise_shot_video_prompt,
    revise_video_prompts,
)
from storyboard_workflow import (
    approve_storyboard_stage,
    generate_storyboard_stage,
    regenerate_storyboard_stage,
    revise_storyboard_stage,
)
from shot_review import (
    ShotReviewError,
    active_prompt_payload,
    active_prompt_safety,
    archive_active_video,
    ensure_initial_prompt_versions,
    save_safety_to_active_prompt,
    shot_video_review_gate,
)
from shot_manager import ShotManagerError, shot_management_menu
from task_logger import TaskLogger
from task_state import TaskState
from video_generator import generate_video
from video_generation_request import VideoGenerationRequest
from video_model_selection import choose_and_confirm_video_generation
from video_provider import ProviderErrorCode, VideoProviderError
from video_provider_registry import (
    VideoProviderRegistry,
    create_default_registry,
    load_provider_credentials_from_env,
    provider_secret_values,
)
from video_assembly import AssemblyError, assemble_approved_shots, assembly_menu


BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)
active_task_logger: TaskLogger | None = None
active_checkpoint: ProjectCheckpoint | None = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

ModelT = TypeVar("ModelT", bound=BaseModel)


def ask_required(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print("此项不能为空，请重新输入。")


def ask_total_duration() -> int:
    while True:
        raw = input("视频总时长（例如 30 秒，默认 30）: ").strip() or "30"
        try:
            duration = int(raw)
            plan_shot_durations(duration)
            return duration
        except (ValueError, StoryboardError) as exc:
            print(f"时长不可用：{exc}")


def collect_request() -> ProductVideoRequest:
    print("\n请输入产品与视频需求：")
    return ProductVideoRequest(
        product_name=ask_required("产品名称"),
        product_description=ask_required("产品信息"),
        user_notes=input(
            "用户备注（人物、镜头、品牌调性、禁止元素等，可留空）: "
        ).strip(),
        duration_seconds=ask_total_duration(),
        video_style=ask_required("视频风格"),
        video_purpose=ask_required("宣传目标"),
    )


def load_artifact(path: Path, model_type: type[ModelT], label: str) -> ModelT:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return model_type.model_validate(payload)
    except FileNotFoundError as exc:
        raise ProjectStateError(
            f"{label} 已标记完成，但保存文件不存在：{path}"
        ) from exc
    except (OSError, json.JSONDecodeError, ValidationError) as exc:
        raise ProjectStateError(f"无法恢复{label}：{exc}") from exc


def prepare_project(
    paths: ProjectPaths,
) -> tuple[ProductVideoRequest, ProjectCheckpoint, str] | None:
    if not ProjectCheckpoint.exists(paths):
        request = collect_request()
        checkpoint = ProjectCheckpoint.create(
            paths, request.product_name, request.model_dump()
        )
        return request, checkpoint, "new"

    schema = detect_project_schema(paths.project_path)
    if schema != 2:
        print("\n检测到旧版 Shot Storage Schema。")
        print("继续打开前，需要执行 Copy → Validate → Commit 安全迁移。")
        print("迁移会先创建项目旁完整备份，且不会删除旧素材。")
        print("\n1. 创建备份并迁移到 Schema 2")
        print("2. 退出，不修改项目")
        while True:
            choice = input("请输入 1 或 2: ").strip()
            if choice == "2":
                return None
            if choice == "1":
                result = migrate_project_to_v2(paths)
                print("\nSchema 2 迁移完成。")
                print(f"完整备份：\n{result.backup_path}")
                print(f"旧结构保留：\n{result.legacy_backup_path}")
                print(f"迁移报告：\n{result.report_path}")
                break
            print("无效选择，请输入 1 或 2。")

    checkpoint = ProjectCheckpoint.load(paths)
    while True:
        action = ask_existing_project_action(checkpoint)
        if action == "exit":
            return None
        if action == "shot_management":
            break
        if action == "restart":
            stage = ask_restart_stage()
            archived = checkpoint.reset_from(stage)
            print(f"已从 {stage.value} 重置，旧版本已保留 {len(archived)} 个文件。")
            display_project_status(checkpoint)
            break
        if action == "continue":
            interrupted = checkpoint.interrupted_stage()
            if interrupted:
                print("\n检测到上次任务可能在此阶段异常中断")
                print(f"中断阶段：{interrupted.value}")
                print("1. 重新执行该阶段")
                print("2. 退出")
                while True:
                    choice = input("请输入 1 或 2: ").strip()
                    if choice == "1":
                        break
                    if choice == "2":
                        return None
                    print("无效选择，请重新输入。")
            break

    try:
        request = ProductVideoRequest.model_validate(checkpoint.data["request"])
    except ValidationError as exc:
        raise ProjectStateError(f"project.json 中的用户需求无效：{exc}") from exc
    return request, checkpoint, action


def initial_review_state(checkpoint: ProjectCheckpoint) -> TaskState:
    if checkpoint.next_stage() in {
        ProjectStage.CREATIVE,
        ProjectStage.CREATIVE_REVIEW,
    }:
        return TaskState.PENDING
    return TaskState.APPROVED


def _visual_kwargs(
    visual_analysis_result: list[dict[str, Any]] | None,
    visual_constraints: dict[str, Any] | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Legacy arguments remain accepted, but automatic visual analysis is disabled.
    del visual_analysis_result, visual_constraints
    return (
        {"reference_asset_context": reference_asset_context}
        if reference_asset_context
        else {}
    )


def _record_prompt_evaluation(
    recorder: EvaluationRecorder | None,
    stage: str,
    output: ModelT,
    request: ProductVideoRequest,
    visual_analysis_result: list[dict[str, Any]] | None,
    visual_constraints: dict[str, Any] | None,
    reference_asset_context: dict[str, Any] | None,
    *,
    operation: str = "generate",
    **extra_inputs: Any,
) -> ModelT:
    if recorder is not None:
        recorder.record_prompt(
            stage,
            model=DEEPSEEK_MODEL,
            operation=operation,
            input_fields={
                "product_information": request.model_dump(),
                "user_notes": request.user_notes,
                "reference_assets": reference_asset_context
                or {"available": False, "asset_count": 0},
                **extra_inputs,
            },
            output_result=output.model_dump(),
        )
    return output


def load_project_workflow_artifacts(
    paths: ProjectPaths,
) -> tuple[CreativeBrief, Storyboard, VideoPromptPlan]:
    brief = load_artifact(paths.creative_brief_path(), CreativeBrief, "Creative")
    board = load_artifact(paths.storyboard_file_path(), Storyboard, "Storyboard")
    prompt_plan = load_artifact(
        paths.video_prompts_path(), VideoPromptPlan, "Video Prompt"
    )
    return brief, board, prompt_plan


def run_shot_management(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    task_logger: TaskLogger,
    deepseek_key: str,
    video_provider_credentials: Mapping[str, Any] | str,
    reference_manager: ReferenceAssetManager | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    interactive_model_selection: bool = False,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
    initial_shot_id: int | None = None,
) -> None:
    # Browsing or selecting an immutable local version requires no API
    # credential. Provider/LLM preflight still runs if the user explicitly
    # chooses to create a new version.
    brief, board, prompt_plan = load_project_workflow_artifacts(paths)
    checkpoint.ensure_shots([shot.shot_id for shot in board.shots])
    ensure_initial_prompt_versions(paths, checkpoint, prompt_plan, task_logger)
    recorder = ReviewRecorder(
        paths,
        request.model_dump(),
        task_logger.task_id,
        task_logger,
        initial_state=TaskState.APPROVED,
    )
    def revise_prompt_with_context(*args: Any, **kwargs: Any) -> str:
        result = revise_shot_video_prompt(
            *args,
            **kwargs,
            **_visual_kwargs(
                visual_analysis_result, visual_constraints, reference_asset_context
            ),
        )
        if evaluation_recorder is not None:
            shot = args[2] if len(args) > 2 else None
            evaluation_recorder.record_prompt(
                "video_prompt",
                model=DEEPSEEK_MODEL,
                operation="shot_revision",
                input_fields={
                    "product_information": request.model_dump(),
                    "user_notes": request.user_notes,
                    "reference_assets": reference_asset_context
                    or {"available": False, "asset_count": 0},
                    "shot_id": getattr(shot, "shot_id", None),
                    "current_prompt": args[3] if len(args) > 3 else None,
                    "user_feedback": args[4] if len(args) > 4 else None,
                },
                output_result={"video_prompt": result},
            )
        return result

    shot_management_menu(
        paths,
        checkpoint,
        request,
        brief,
        board,
        prompt_plan,
        deepseek_key,
        video_provider_credentials,
        task_logger,
        recorder,
        revise_prompt=revise_prompt_with_context,
        safety_review=review_prompt_safety,
        video_generate=generate_video,
        reference_manager=reference_manager,
        provider_registry=provider_registry,
        interactive_model_selection=interactive_model_selection,
        initial_shot_id=initial_shot_id,
    )


def run_assembly_menu(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    task_logger: TaskLogger,
    deepseek_key: str,
    video_provider_credentials: Mapping[str, Any] | str,
    reference_manager: ReferenceAssetManager | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    interactive_model_selection: bool = False,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> Path | None:
    _brief, board, _prompt_plan = load_project_workflow_artifacts(paths)
    def open_shot_management(shot_id: int | None = None) -> None:
        run_shot_management(
            paths,
            request,
            checkpoint,
            task_logger,
            deepseek_key,
            video_provider_credentials,
            reference_manager,
            provider_registry,
            interactive_model_selection,
            visual_analysis_result,
            visual_constraints,
            evaluation_recorder,
            reference_asset_context,
            shot_id,
        )

    def regenerate_assembly() -> Path | None:
        return assemble_approved_shots(
            paths,
            checkpoint,
            board,
            task_logger,
        )

    if not has_completed_assembly(paths, checkpoint):
        result = assembly_menu(
            paths,
            checkpoint,
            board,
            task_logger,
            open_shot_management=open_shot_management,
        )
        if result is None or not has_completed_assembly(paths, checkpoint):
            return result

    project_resume_menu(
        paths,
        checkpoint,
        task_logger,
        regenerate_assembly=regenerate_assembly,
        open_shot_management=open_shot_management,
    )
    assembly_path = checkpoint.assembly_checkpoint().get("final_video_path")
    return (
        paths.ensure_within_project(paths.project_path / str(assembly_path))
        if assembly_path
        else None
    )


def run_pipeline(
    paths: ProjectPaths,
    request: ProductVideoRequest,
    checkpoint: ProjectCheckpoint,
    deepseek_key: str,
    video_provider_credentials: Mapping[str, Any] | str,
    task_logger: TaskLogger,
    reference_manager: ReferenceAssetManager | None = None,
    provider_registry: VideoProviderRegistry | None = None,
    interactive_model_selection: bool = False,
    visual_analysis_result: list[dict[str, Any]] | None = None,
    visual_constraints: dict[str, Any] | None = None,
    evaluation_recorder: EvaluationRecorder | None = None,
    reference_asset_context: dict[str, Any] | None = None,
) -> None:
    registry = provider_registry or create_default_registry(
        video_provider_credentials
    )
    if checkpoint.stage_status(ProjectStage.COMPLETED) == StageStatus.COMPLETED:
        print("当前项目已经完成，无需重复执行。")
        display_project_status(checkpoint)
        return

    recorder = ReviewRecorder(
        paths,
        request.model_dump(),
        task_logger.task_id,
        task_logger,
        initial_state=initial_review_state(checkpoint),
    )
    print(f"审核记录：{recorder.path}")
    print(f"任务 ID：{task_logger.task_id}")
    print(f"任务日志：{task_logger.task_log_path}")

    # Creative generation and review.
    creative_status = checkpoint.stage_status(ProjectStage.CREATIVE)
    if creative_status == StageStatus.COMPLETED:
        brief = load_artifact(paths.creative_brief_path(), CreativeBrief, "Creative")
    elif creative_status == StageStatus.FAILED:
        brief = retry_failed_creative_stage(
            paths,
            request,
            checkpoint,
            deepseek_key,
            task_logger,
            evaluation_recorder=evaluation_recorder,
            reference_asset_context=reference_asset_context,
        )
    else:
        brief = generate_creative_stage(
            paths,
            request,
            checkpoint,
            deepseek_key,
            task_logger,
            evaluation_recorder=evaluation_recorder,
            reference_asset_context=reference_asset_context,
        )

    if checkpoint.stage_status(ProjectStage.CREATIVE_REVIEW) != StageStatus.APPROVED:
        brief = human_review_gate(
            "AI创意方案",
            "creative_brief",
            "Creative审核",
            brief,
            recorder,
            revise=lambda current, comment: revise_creative_stage(
                paths,
                request,
                checkpoint,
                current,
                comment,
                deepseek_key,
                task_logger,
                evaluation_recorder=evaluation_recorder,
                reference_asset_context=reference_asset_context,
            ),
            regenerate=lambda: regenerate_creative_stage(
                paths,
                request,
                checkpoint,
                deepseek_key,
                task_logger,
                evaluation_recorder=evaluation_recorder,
                reference_asset_context=reference_asset_context,
            ),
            persist=lambda value: paths.save_json(
                paths.creative_brief_path(), value.model_dump()
            ),
            on_waiting=lambda: checkpoint.advance_to(
                ProjectStage.CREATIVE_REVIEW, StageStatus.WAITING_REVIEW
            ),
            on_approved=lambda: approve_creative_stage(checkpoint),
            on_cancel=lambda: checkpoint.cancel(ProjectStage.CREATIVE_REVIEW),
        )

    # Storyboard generation and review.
    if checkpoint.stage_status(ProjectStage.STORYBOARD) == StageStatus.COMPLETED:
        board = load_artifact(paths.storyboard_file_path(), Storyboard, "Storyboard")
    else:
        board = generate_storyboard_stage(
            paths,
            request,
            checkpoint,
            deepseek_key,
            task_logger,
            evaluation_recorder=evaluation_recorder,
            reference_asset_context=reference_asset_context,
        )

    if checkpoint.stage_status(ProjectStage.STORYBOARD_REVIEW) != StageStatus.APPROVED:
        board = human_review_gate(
            "Storyboard 分镜方案",
            "storyboard",
            "Storyboard审核",
            board,
            recorder,
            revise=lambda current, comment: revise_storyboard_stage(
                paths,
                request,
                checkpoint,
                current,
                comment,
                deepseek_key,
                task_logger,
                approved_creative=brief,
                evaluation_recorder=evaluation_recorder,
                visual_analysis_result=visual_analysis_result,
                visual_constraints=visual_constraints,
                reference_asset_context=reference_asset_context,
            ),
            regenerate=lambda: regenerate_storyboard_stage(
                paths,
                request,
                checkpoint,
                deepseek_key,
                task_logger,
                approved_creative=brief,
                evaluation_recorder=evaluation_recorder,
                visual_analysis_result=visual_analysis_result,
                visual_constraints=visual_constraints,
                reference_asset_context=reference_asset_context,
            ),
            on_approved=lambda: approve_storyboard_stage(checkpoint),
            on_cancel=lambda: checkpoint.cancel(ProjectStage.STORYBOARD_REVIEW),
        )

    # Initialize every Shot without overwriting existing checkpoint/API data.
    checkpoint.ensure_shots([shot.shot_id for shot in board.shots])

    # Video Prompt generation and review.
    if checkpoint.stage_status(ProjectStage.VIDEO_PROMPT) == StageStatus.COMPLETED:
        prompt_plan = load_artifact(
            paths.video_prompts_path(), VideoPromptPlan, "Video Prompt"
        )
    else:
        checkpoint.update_stage(ProjectStage.VIDEO_PROMPT, StageStatus.RUNNING)
        task_logger.set_stage("video_prompt")
        prompt_plan = _record_prompt_evaluation(
            evaluation_recorder,
            "video_prompt",
            generate_video_prompts(
                request,
                brief,
                board,
                deepseek_key,
                task_logger,
                **_visual_kwargs(
                    visual_analysis_result,
                    visual_constraints,
                    reference_asset_context,
                ),
                progress_path=paths.video_prompt_generation_progress_path(),
            ),
            request,
            visual_analysis_result,
            visual_constraints,
            reference_asset_context,
            creative_brief=brief.model_dump(),
            storyboard=board.model_dump(),
        )
        paths.save_json(paths.video_prompts_path(), prompt_plan.model_dump())
        checkpoint.update_stage(ProjectStage.VIDEO_PROMPT, StageStatus.COMPLETED)
        checkpoint.advance_to(ProjectStage.PROMPT_REVIEW, StageStatus.WAITING_REVIEW)
        task_logger.event("PROMPT_GENERATED", "Video Prompt 生成完成")

    if checkpoint.stage_status(ProjectStage.PROMPT_REVIEW) != StageStatus.APPROVED:
        prompt_plan = human_review_gate(
            "Video Prompt 方案",
            "video_prompts",
            "Prompt审核",
            prompt_plan,
            recorder,
            revise=lambda current, comment: _record_prompt_evaluation(
                evaluation_recorder,
                "video_prompt",
                revise_video_prompts(
                    request,
                    brief,
                    board,
                    current,
                    comment,
                    deepseek_key,
                    task_logger,
                    **_visual_kwargs(
                        visual_analysis_result,
                        visual_constraints,
                        reference_asset_context,
                    ),
                ),
                request,
                visual_analysis_result,
                visual_constraints,
                reference_asset_context,
                operation="revise",
                creative_brief=brief.model_dump(),
                storyboard=board.model_dump(),
                current_output=current.model_dump(),
                user_feedback=comment,
            ),
            regenerate=lambda: _record_prompt_evaluation(
                evaluation_recorder,
                "video_prompt",
                generate_video_prompts(
                    request,
                    brief,
                    board,
                    deepseek_key,
                    task_logger,
                    **_visual_kwargs(
                        visual_analysis_result,
                        visual_constraints,
                        reference_asset_context,
                    ),
                    progress_path=paths.video_prompt_generation_progress_path(),
                    force_regenerate=True,
                ),
                request,
                visual_analysis_result,
                visual_constraints,
                reference_asset_context,
                operation="regenerate",
                creative_brief=brief.model_dump(),
                storyboard=board.model_dump(),
            ),
            persist=lambda value: paths.save_json(
                paths.video_prompts_path(), value.model_dump()
            ),
            on_waiting=lambda: checkpoint.advance_to(
                ProjectStage.PROMPT_REVIEW, StageStatus.WAITING_REVIEW
            ),
            on_approved=lambda: checkpoint.update_stage(
                ProjectStage.PROMPT_REVIEW, StageStatus.APPROVED
            ),
            on_cancel=lambda: checkpoint.cancel(ProjectStage.PROMPT_REVIEW),
        )

    ensure_initial_prompt_versions(paths, checkpoint, prompt_plan, task_logger)

    # Generate and review one Shot at a time. APPROVED Shots are never touched.
    recorder.start_generating()
    checkpoint.update_stage(ProjectStage.VIDEO_GENERATION, StageStatus.RUNNING)
    task_logger.set_stage("video_generation")
    task_logger.event("VIDEO_GENERATING", "开始调用视频生成 API")
    completed: list[Path] = []
    for shot in board.shots:
        shot_id = shot.shot_id
        failure_retry = False
        if checkpoint.shot_status(shot_id) == ShotStatus.APPROVED:
            output_path = checkpoint.approved_video_path(shot_id)
            if output_path is None:
                raise ProjectStateError(f"Shot {shot_id:02d} 缺少 Approved Video 指针。")
            completed.append(output_path)
            task_logger.event(
                "SHOT_APPROVED_REUSED",
                "跳过已经人工通过的 Shot",
                shot_id=shot_id,
                output_path=output_path,
            )
            continue

        while checkpoint.shot_status(shot_id) != ShotStatus.APPROVED:
            status = checkpoint.shot_status(shot_id)
            if status == ShotStatus.FAILED:
                last_error = checkpoint.shot_checkpoint(shot_id).get("last_error")
                print(f"\nShot {shot_id:02d} 上次生成失败。")
                print("\n请选择：")
                print(f"1. 重新生成 Shot {shot_id:02d}")
                print("2. 查看错误")
                print("3. 取消任务")
                failure_choice = input("请输入 1、2 或 3: ").strip()
                if failure_choice == "2":
                    print(json.dumps(last_error, ensure_ascii=False, indent=2))
                    continue
                if failure_choice == "3":
                    checkpoint.cancel(ProjectStage.VIDEO_GENERATION, shot_id=shot_id)
                    recorder.cancel_shot(shot_id, f"Shot {shot_id:02d}生成失败")
                    raise TaskCancelled(f"Shot {shot_id:02d}审核")
                if failure_choice != "1":
                    print("无效选择。")
                    continue
                failure_retry = True
                archive_active_video(paths, checkpoint, shot_id, task_logger)
                if reference_manager is not None:
                    visual = select_regeneration_visual_input(
                        reference_manager,
                        shot_id,
                        checkpoint.shot_visual_input(shot_id),
                    )
                    if visual is None:
                        checkpoint.cancel(ProjectStage.VIDEO_GENERATION, shot_id=shot_id)
                        recorder.cancel_shot(shot_id, f"Shot {shot_id:02d} Visual Input")
                        raise TaskCancelled(f"Shot {shot_id:02d} Visual Input")
                    checkpoint.set_shot_visual_input(shot_id, visual)
                    task_logger.event(
                        "SHOT_VISUAL_INPUT_CHANGED",
                        shot_id=shot_id,
                        visual_input_mode=visual["mode"],
                    )
                checkpoint.prepare_shot_generation(shot_id)
                status = ShotStatus.GENERATING

            if status == ShotStatus.NOT_STARTED:
                if reference_manager is not None:
                    entry = checkpoint.shot_checkpoint(shot_id)
                    if not entry.get("visual_input_selected"):
                        visual = select_shot_visual_input(
                            reference_manager,
                            shot_id,
                            checkpoint.shot_visual_input(shot_id),
                        )
                        visual = reference_manager.validate_visual_input(visual)
                        checkpoint.set_shot_visual_input(shot_id, visual)
                        task_logger.event(
                            "SHOT_VISUAL_INPUT_SELECTED",
                            shot_id=shot_id,
                            visual_input_mode=visual["mode"],
                            reference_asset_ids=[
                                item["asset_id"] for item in visual["assets"]
                            ],
                        )
                checkpoint.prepare_shot_generation(shot_id)
                status = ShotStatus.GENERATING

            if status == ShotStatus.GENERATING:
                logger.info("处理镜头 %02d/%02d", shot_id, len(board.shots))
                shot_checkpoint = checkpoint.shot_checkpoint(shot_id)
                generation_version = int(
                    shot_checkpoint.get("current_generation_version")
                    or shot_checkpoint.get("pending_video_version")
                    or 0
                )
                if generation_version <= 0:
                    raise ProjectStateError(
                        f"Shot {shot_id:02d} 缺少待生成的 Bundle version。"
                    )
                output_path = paths.shot_version_video_path(
                    shot_id, generation_version
                )
                prompt_payload = active_prompt_payload(
                    paths, checkpoint, prompt_plan, shot_id
                )
                resume_task = checkpoint.generation_provider_task(
                    shot_id, shot_checkpoint.get("current_generation_version")
                )
                resuming_provider_task = resume_task is not None
                generation_visual_input = (
                    checkpoint.generation_visual_input(
                        shot_id, shot_checkpoint.get("current_generation_version")
                    )
                    if resuming_provider_task
                    else checkpoint.shot_visual_input(shot_id)
                )
                safety = (
                    active_prompt_safety(paths, checkpoint, prompt_plan, shot_id)
                    if resuming_provider_task
                    else None
                )
                if safety is None:
                    safety = review_prompt_safety(
                        prompt_payload["prompt"],
                        deepseek_key,
                        task_logger,
                        raw_stage=f"prompt_safety_shot_{shot_id:02d}",
                    )
                    save_safety_to_active_prompt(
                        paths, checkpoint, prompt_plan, shot_id, safety
                    )
                print(f"\n=== 镜头 {shot_id:02d} 安全预检 ===")
                print(json.dumps(safety.model_dump(), ensure_ascii=False, indent=2))
                if not safety.is_safe:
                    error = PromptGenerationError(
                        f"镜头 {shot_id:02d} 安全预检未通过。"
                    )
                    checkpoint.mark_shot_failed(shot_id, error)
                    continue
                entry_before_generation = checkpoint.shot_checkpoint(shot_id)
                is_regeneration = (
                    int(entry_before_generation.get("generation_count", 0)) > 0
                    or failure_retry
                )
                provider_selection = None
                if interactive_model_selection and not resuming_provider_task:
                    previous_version = (
                        entry_before_generation.get("active_video_version")
                        or entry_before_generation.get("approved_video_version")
                    )
                    previous_metadata = checkpoint.generation_provider_metadata(
                        shot_id,
                        int(previous_version) if previous_version is not None else None,
                    ) or entry_before_generation.get("last_provider_route")
                    while True:
                        route_request = VideoGenerationRequest(
                            shot_id=shot_id,
                            prompt=safety.reviewed_video_prompt,
                            duration=shot.duration,
                            resolution="768P",
                            visual_input=generation_visual_input,
                            project=paths,
                        )
                        if previous_metadata is None and is_regeneration:
                            previous_metadata = registry.provider_metadata(
                                route_request
                            )
                        decision = choose_and_confirm_video_generation(
                            registry,
                            route_request,
                            prompt_version=entry_before_generation.get(
                                "active_prompt_version"
                            ),
                            regeneration=is_regeneration,
                            previous_metadata=previous_metadata,
                        )
                        if decision.action == "generate":
                            provider_selection = decision.provider_selection
                            task_logger.event(
                                "VIDEO_MODEL_SELECTION_CONFIRMED",
                                shot_id=shot_id,
                                **dict(decision.metadata or {}),
                            )
                            break
                        if decision.action == "change_visual":
                            if reference_manager is None:
                                raise ReferenceAssetError(
                                    "当前流程无法更换 Visual Input。"
                                )
                            visual = select_regeneration_visual_input(
                                reference_manager,
                                shot_id,
                                checkpoint.shot_visual_input(shot_id),
                            )
                            if visual is None:
                                checkpoint.cancel(
                                    ProjectStage.VIDEO_GENERATION, shot_id=shot_id
                                )
                                recorder.cancel_shot(
                                    shot_id, f"Shot {shot_id:02d} Visual Input"
                                )
                                raise TaskCancelled(
                                    f"Shot {shot_id:02d} Visual Input"
                                )
                            generation_visual_input = (
                                reference_manager.validate_visual_input(visual)
                            )
                            checkpoint.set_shot_visual_input(
                                shot_id, generation_visual_input
                            )
                            task_logger.event(
                                "SHOT_VISUAL_INPUT_CHANGED",
                                shot_id=shot_id,
                                visual_input_mode=generation_visual_input["mode"],
                                reference_asset_ids=[
                                    item["asset_id"]
                                    for item in generation_visual_input["assets"]
                                ],
                            )
                            continue
                        checkpoint.cancel(
                            ProjectStage.VIDEO_GENERATION, shot_id=shot_id
                        )
                        recorder.cancel_shot(
                            shot_id, f"Shot {shot_id:02d} 视频生成确认"
                        )
                        task_logger.event(
                            "VIDEO_SUBMISSION_CANCELLED", shot_id=shot_id
                        )
                        raise TaskCancelled(f"Shot {shot_id:02d} 视频生成确认")
                task_logger.event(
                    "SHOT_VIDEO_REGENERATION_STARTED"
                    if is_regeneration
                    else "SHOT_VIDEO_GENERATION_STARTED",
                    shot_id=shot_id,
                    prompt_version=entry_before_generation.get("active_prompt_version"),
                    generation_count=entry_before_generation.get("generation_count", 0),
                )
                try:
                    generated_path = generate_video(
                        provider_credentials=video_provider_credentials,
                        prompt=safety.reviewed_video_prompt,
                        duration=shot.duration,
                        resolution="768P",
                        project=paths,
                        output_path=output_path,
                        task_logger=task_logger,
                        shot_id=shot_id,
                        visual_input=generation_visual_input,
                        provider_selection=provider_selection,
                        provider_registry=registry,
                        resume_task=resume_task,
                        on_preflight=lambda metadata, current_shot=shot_id: checkpoint.mark_shot_preflight(
                            current_shot, metadata
                        ),
                        on_submitted=lambda provider_task, current_shot=shot_id: checkpoint.mark_shot_submitted(
                            current_shot, provider_task
                        ),
                        on_task_updated=lambda provider_task, current_shot=shot_id: checkpoint.mark_shot_task_updated(
                            current_shot, provider_task
                        ),
                    )
                except (VideoProviderError, OSError) as exc:
                    checkpoint.mark_shot_failed(shot_id, exc)
                    task_logger.event("SHOT_GENERATION_FAILED", shot_id=shot_id, error=exc)
                    continue
                checkpoint.mark_shot_ready_for_review(shot_id)
                completed.append(generated_path)
                task_logger.event(
                    "SHOT_VIDEO_REGENERATION_COMPLETED"
                    if is_regeneration
                    else "VIDEO_COMPLETED",
                    "视频生成成功",
                    shot_id=shot_id,
                    output_path=generated_path,
                    video_version=checkpoint.shot_checkpoint(shot_id).get(
                        "active_video_version"
                    ),
                    generation_count=checkpoint.shot_checkpoint(shot_id).get(
                        "generation_count", 0
                    ),
                )
                status = ShotStatus.WAITING_REVIEW

            if status == ShotStatus.WAITING_REVIEW:
                output_path = checkpoint.active_video_path(shot_id)
                if output_path is None:
                    checkpoint.mark_shot_failed(
                        shot_id, "Shot 状态为 WAITING_REVIEW，但缺少 active_version。"
                    )
                    continue
                if not output_path.is_file() or output_path.stat().st_size <= 0:
                    checkpoint.mark_shot_failed(
                        shot_id, "Shot 状态为 WAITING_REVIEW，但 active 视频文件不存在。"
                    )
                    continue
                action = shot_video_review_gate(
                    paths=paths,
                    checkpoint=checkpoint,
                    plan=prompt_plan,
                    request=request,
                    brief=brief,
                    shot=shot,
                    recorder=recorder,
                    task_logger=task_logger,
                    ai_revise=lambda current, feedback, current_shot=shot: revise_shot_video_prompt(
                        request,
                        brief,
                        current_shot,
                        current,
                        feedback,
                        deepseek_key,
                        task_logger,
                        **_visual_kwargs(
                            visual_analysis_result,
                            visual_constraints,
                            reference_asset_context,
                        ),
                    ),
                )
                if action == "regenerate":
                    archive_active_video(paths, checkpoint, shot_id, task_logger)
                    if reference_manager is not None:
                        visual = select_regeneration_visual_input(
                            reference_manager,
                            shot_id,
                            checkpoint.shot_visual_input(shot_id),
                        )
                        if visual is None:
                            checkpoint.cancel(
                                ProjectStage.VIDEO_GENERATION, shot_id=shot_id
                            )
                            recorder.cancel_shot(
                                shot_id, f"Shot {shot_id:02d} Visual Input"
                            )
                            raise TaskCancelled(f"Shot {shot_id:02d} Visual Input")
                        visual = reference_manager.validate_visual_input(visual)
                        checkpoint.set_shot_visual_input(shot_id, visual)
                        task_logger.event(
                            "SHOT_VISUAL_INPUT_CHANGED",
                            shot_id=shot_id,
                            visual_input_mode=visual["mode"],
                            reference_asset_ids=[
                                item["asset_id"] for item in visual["assets"]
                            ],
                        )
                    checkpoint.prepare_shot_generation(shot_id)
                    continue
                completed.append(output_path)
                break

    shot_ids = [shot.shot_id for shot in board.shots]
    if not checkpoint.all_shots_approved(shot_ids):
        raise ProjectStateError("仍有 Shot 未通过人工审核，不能完成视频生成阶段。")

    checkpoint.update_stage(ProjectStage.VIDEO_GENERATION, StageStatus.COMPLETED)
    recorder.complete()
    checkpoint.mark_video_generation_completed()
    task_logger.event(
        "VIDEO_GENERATION_COMPLETED", "全部 Shot 已生成并通过人工审核"
    )
    print("\n========== 所有 Shot 已审核完成 ==========")
    for shot_id in shot_ids:
        # Keep completion output compatible with legacy Windows console encodings.
        print(f"Shot {shot_id:02d}  [APPROVED]")
    print("\n所有镜头已经完成并通过人工审核。")
    print("\n下一阶段可以进行完整视频合片。")
    print("=" * 43)
    print(f"分镜视频目录：\n{paths.shots_dir}")
    print(f"项目目录：\n{paths.project_path}")
    print(f"审核记录：\n{recorder.path}")
    for path in dict.fromkeys(completed):
        print(f"- {path.name}")


def main() -> None:
    global active_task_logger, active_checkpoint
    paths = ask_project_paths()
    prepared = prepare_project(paths)
    if prepared is None:
        return
    request, active_checkpoint, project_action = prepared

    active_task_logger = TaskLogger(paths)
    reference_manager = ReferenceAssetManager(paths, active_task_logger)
    if project_action == "new":
        setup_project_references(reference_manager)
    logger.info("当前视频项目目录：%s", paths.project_path)
    load_dotenv(BASE_DIR / ".env")
    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    video_provider_credentials = load_provider_credentials_from_env()
    provider_registry = create_default_registry(video_provider_credentials)
    evaluation_recorder = EvaluationRecorder(paths)
    if deepseek_key:
        active_task_logger.register_secret(deepseek_key)
    for secret in provider_secret_values(video_provider_credentials):
        active_task_logger.register_secret(secret)
    for variable in (
        "ALIYUN_ACCESS_KEY_ID",
        "ALIYUN_ACCESS_KEY_SECRET",
        "ALIYUN_TTS_APP_KEY",
        "XFYUN_APP_ID",
        "XFYUN_API_KEY",
        "XFYUN_API_SECRET",
    ):
        active_task_logger.register_secret(os.getenv(variable, "").strip())

    reference_assets = reference_manager.list_assets()
    reference_asset_context = {
        "available": bool(reference_assets),
        "asset_count": len(reference_assets),
        "asset_ids": [str(item.get("asset_id")) for item in reference_assets],
    }
    # Historical analysis files remain untouched, but are never loaded or generated.
    visual_analysis_result: list[dict[str, Any]] = []
    visual_constraints: dict[str, Any] = {
        "must_preserve": [],
        "creative_freedom": [],
        "avoid": [],
    }
    active_task_logger.event(
        "VISUAL_UNDERSTANDING_DISABLED",
        "Reference Assets are retained without automatic image analysis.",
        reference_asset_count=len(reference_assets),
    )

    def sync_evaluation() -> None:
        evaluation_recorder.sync_generation_bundles(active_checkpoint)
        evaluation_recorder.sync_final(active_checkpoint)
    if project_action == "shot_management":
        active_task_logger.event(
            "SHOT_MANAGEMENT_STARTED",
            project_path=paths.project_path,
            project_status=active_checkpoint.status,
        )
        try:
            run_shot_management(
                paths,
                request,
                active_checkpoint,
                active_task_logger,
                deepseek_key,
                video_provider_credentials,
                reference_manager,
                provider_registry,
                True,
                visual_analysis_result,
                visual_constraints,
                evaluation_recorder,
                reference_asset_context,
            )
        finally:
            sync_evaluation()
        return

    if active_checkpoint.stage_status(ProjectStage.COMPLETED) == StageStatus.COMPLETED:
        try:
            run_assembly_menu(
                paths,
                request,
                active_checkpoint,
                active_task_logger,
                deepseek_key,
                video_provider_credentials,
                reference_manager,
                provider_registry,
                True,
                visual_analysis_result,
                visual_constraints,
                evaluation_recorder,
                reference_asset_context,
            )
        finally:
            sync_evaluation()
        return

    if not deepseek_key:
        raise PromptGenerationError("DEEPSEEK_API_KEY 缺失。")
    if not provider_secret_values(video_provider_credentials):
        raise VideoProviderError(
            ProviderErrorCode.AUTH_ERROR, "默认视频 Provider API Key 缺失。"
        )

    active_task_logger.event(
        "TASK_RESUMED" if active_checkpoint.data["created_at"] != active_checkpoint.data["updated_at"] else "TASK_START",
        f"用户需求：制作{request.product_name}品牌宣传视频",
        project_path=paths.project_path,
        current_stage=active_checkpoint.current_stage.value,
    )
    try:
        run_pipeline(
            paths,
            request,
            active_checkpoint,
            deepseek_key,
            video_provider_credentials,
            active_task_logger,
            reference_manager,
            provider_registry,
            True,
            visual_analysis_result,
            visual_constraints,
            evaluation_recorder,
            reference_asset_context,
        )
        if active_checkpoint.stage_status(ProjectStage.COMPLETED) == StageStatus.COMPLETED:
            run_assembly_menu(
                paths,
                request,
                active_checkpoint,
                active_task_logger,
                deepseek_key,
                video_provider_credentials,
                reference_manager,
                provider_registry,
                True,
                visual_analysis_result,
                visual_constraints,
                evaluation_recorder,
                reference_asset_context,
            )
    finally:
        sync_evaluation()


if __name__ == "__main__":
    try:
        main()
    except TaskCancelled as exc:
        logger.info("任务已取消，取消节点：%s", exc.cancel_stage)
        print_cancelled(exc.cancel_stage)
        raise SystemExit(0) from None
    except (
        ProjectDirectoryError,
        ReferenceAssetError,
        ProjectStateError,
        PromptGenerationError,
        ShotReviewError,
        ShotManagerError,
        AssemblyError,
        StoryboardError,
        VideoProviderError,
        ValueError,
    ) as exc:
        logger.error("%s", exc)
        if active_checkpoint and active_checkpoint.status == StageStatus.RUNNING.value:
            active_checkpoint.fail(exc)
        if active_task_logger:
            active_task_logger.error(exc)
        raise SystemExit(1) from exc
    except KeyboardInterrupt:
        logger.warning("已由用户停止；Checkpoint 和已完成文件会保留。")
        if active_task_logger:
            active_task_logger.error("用户通过键盘中断任务")
        raise SystemExit(130)
    except Exception as exc:
        logger.exception("发生未知异常")
        if active_checkpoint and active_checkpoint.status == StageStatus.RUNNING.value:
            active_checkpoint.fail(exc)
        if active_task_logger:
            active_task_logger.error(exc)
        raise SystemExit(1) from exc
