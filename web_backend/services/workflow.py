"""Pure projection from durable Core state to a user-facing workflow."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from web_backend.models.projects import (
    AvailableAction,
    AssemblyState,
    ComponentState,
    FinalExportState,
    ShotStageState,
    StageState,
    WorkflowPhase,
    WorkflowStages,
    WorkflowState,
)


_KNOWN_STATUS = {
    "NOT_STARTED",
    "RUNNING",
    "GENERATING",
    "WAITING_REVIEW",
    "APPROVED",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "FINAL_COMPLETED",
}
_DONE = {"APPROVED", "COMPLETED"}


_PHASE_ACTIONS: dict[WorkflowPhase, tuple[AvailableAction, ...]] = {
    WorkflowPhase.CREATIVE: (AvailableAction.GENERATE_CREATIVE,),
    WorkflowPhase.CREATIVE_REVIEW: (
        AvailableAction.APPROVE_CREATIVE,
        AvailableAction.REVISE_CREATIVE,
        AvailableAction.REGENERATE_CREATIVE,
    ),
    WorkflowPhase.STORYBOARD: (AvailableAction.GENERATE_STORYBOARD,),
    WorkflowPhase.STORYBOARD_REVIEW: (
        AvailableAction.APPROVE_STORYBOARD,
        AvailableAction.REVISE_STORYBOARD,
        AvailableAction.REGENERATE_STORYBOARD,
    ),
    WorkflowPhase.VIDEO_PROMPT: (AvailableAction.GENERATE_VIDEO_PROMPTS,),
    WorkflowPhase.VIDEO_PROMPT_REVIEW: (
        AvailableAction.APPROVE_VIDEO_PROMPTS,
        AvailableAction.REVISE_VIDEO_PROMPTS,
        AvailableAction.REGENERATE_VIDEO_PROMPTS,
    ),
    WorkflowPhase.VIDEO_GENERATION: (AvailableAction.GENERATE_SHOTS,),
    WorkflowPhase.SHOT_REVIEW: (
        AvailableAction.REVIEW_SHOTS,
        AvailableAction.MANAGE_SHOT_VERSIONS,
    ),
    WorkflowPhase.ASSEMBLY: (
        AvailableAction.ASSEMBLE,
        AvailableAction.MANAGE_SHOT_VERSIONS,
    ),
    WorkflowPhase.ASSEMBLY_REQUIRED: (
        AvailableAction.ASSEMBLE,
        AvailableAction.MANAGE_SHOT_VERSIONS,
    ),
    WorkflowPhase.FINAL_EXPORT: (AvailableAction.FINAL_EXPORT,),
}


@dataclass(frozen=True)
class ProjectManifests:
    assembly: Mapping[str, Any] | None = None
    voice: Mapping[str, Any] | None = None
    subtitle: Mapping[str, Any] | None = None
    music: Mapping[str, Any] | None = None
    export: Mapping[str, Any] | None = None
    creative_exists: bool = False
    storyboard_exists: bool = False
    video_prompts_exist: bool = False
    video_prompt_progress_exists: bool = False
    shot_artifacts_exist: bool = False


def _failed_creative_is_retryable(
    data: Mapping[str, Any],
    manifests: ProjectManifests,
) -> bool:
    generation = _mapping(data.get("video_generation"))
    downstream = (
        "STORYBOARD",
        "STORYBOARD_REVIEW",
        "VIDEO_PROMPT",
        "PROMPT_REVIEW",
        "VIDEO_GENERATION",
        "COMPLETED",
    )
    return (
        _status(data.get("status")) == "FAILED"
        and str(data.get("current_stage") or "").upper() == "CREATIVE"
        and _stage_status(data, "CREATIVE") == "FAILED"
        and _stage_status(data, "CREATIVE_REVIEW") == "NOT_STARTED"
        and all(_stage_status(data, stage) == "NOT_STARTED" for stage in downstream)
        and not generation.get("completed_shots")
        and not generation.get("shots")
        and not manifests.creative_exists
        and not manifests.storyboard_exists
        and not manifests.video_prompts_exist
        and not manifests.video_prompt_progress_exists
        and not manifests.shot_artifacts_exist
    )


def _video_prompt_generation_is_allowed(
    data: Mapping[str, Any],
    manifests: ProjectManifests,
) -> bool:
    prompt_status = _stage_status(data, "VIDEO_PROMPT")
    current_stage = str(data.get("current_stage") or "").upper()
    resumable_state = (
        prompt_status == "NOT_STARTED"
        or (
            prompt_status in {"RUNNING", "FAILED"}
            and current_stage == "VIDEO_PROMPT"
        )
    )
    return (
        _stage_status(data, "CREATIVE") == "COMPLETED"
        and _stage_status(data, "CREATIVE_REVIEW") == "APPROVED"
        and _stage_status(data, "STORYBOARD") == "COMPLETED"
        and _stage_status(data, "STORYBOARD_REVIEW") == "APPROVED"
        and resumable_state
        and _stage_status(data, "PROMPT_REVIEW") == "NOT_STARTED"
        and _stage_status(data, "VIDEO_GENERATION") == "NOT_STARTED"
        and _stage_status(data, "COMPLETED") == "NOT_STARTED"
        and not manifests.video_prompts_exist
    )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _status(value: Any, default: str = "NOT_STARTED") -> str:
    normalized = str(value or default).upper()
    return normalized if normalized in _KNOWN_STATUS else default


def _stage_status(data: Mapping[str, Any], name: str) -> str:
    stages = _mapping(data.get("stages"))
    return _status(_mapping(stages.get(name)).get("status"))


def _reviewed_stage_status(
    data: Mapping[str, Any],
    generation_name: str,
    review_name: str,
) -> str:
    generation = _stage_status(data, generation_name)
    review = _stage_status(data, review_name)
    if review == "APPROVED":
        return "APPROVED"
    if review != "NOT_STARTED":
        return review
    return generation


def _shot_state(data: Mapping[str, Any]) -> ShotStageState:
    generation = _mapping(data.get("video_generation"))
    raw_shots = generation.get("shots")
    if isinstance(raw_shots, Mapping):
        entries = [_mapping(value) for value in raw_shots.values()]
    elif isinstance(raw_shots, list):
        entries = [_mapping(value) for value in raw_shots]
    else:
        entries = []

    statuses = [_status(entry.get("status")) for entry in entries]
    total = len(statuses)
    approved = sum(status == "APPROVED" for status in statuses)
    video_stage = _stage_status(data, "VIDEO_GENERATION")
    if "FAILED" in statuses:
        status = "FAILED"
    elif total and approved == total:
        status = "COMPLETED"
    elif any(value in {"WAITING_REVIEW", "COMPLETED"} for value in statuses):
        status = "WAITING_REVIEW"
    elif any(value in {"RUNNING", "GENERATING"} for value in statuses):
        status = "RUNNING"
    elif video_stage == "COMPLETED":
        status = "COMPLETED"
    else:
        status = video_stage
    return ShotStageState(status=status, approved=approved, total=total)


def _active_manifest_entry(manifest: Mapping[str, Any] | None) -> Mapping[str, Any]:
    payload = _mapping(manifest)
    active = payload.get("active_version")
    versions = payload.get("versions")
    if active is None or not isinstance(versions, list):
        return {}
    for value in versions:
        entry = _mapping(value)
        if entry.get("version") == active:
            return entry
    return {}


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.isoformat()


def _component_state(
    post_production: Mapping[str, Any],
    component_name: str,
    manifest: Mapping[str, Any] | None,
) -> ComponentState:
    components = _mapping(post_production.get("components"))
    recorded = _mapping(components.get(component_name))
    active_entry = _active_manifest_entry(manifest)
    active_version = _optional_int(_mapping(manifest).get("active_version"))
    status = "COMPLETED" if active_entry else _status(recorded.get("status"))
    version = active_version or _optional_int(recorded.get("active_version"))
    return ComponentState(status=status, version=version)


def _assembly_state(
    data: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
) -> AssemblyState:
    recorded = _mapping(data.get("assembly"))
    manifest_data = _mapping(manifest)
    assemblies = manifest_data.get("assemblies")
    has_manifest = isinstance(assemblies, list) and bool(assemblies)
    status = _status(recorded.get("status"))
    if status == "NOT_STARTED" and has_manifest:
        status = "COMPLETED"
    version = _optional_int(recorded.get("final_video_version"))
    if version is None:
        version = _optional_int(
            manifest_data.get("latest_assembly_version")
            or manifest_data.get("assembly_version")
        )
    return AssemblyState(
        status=status,
        needs_update=bool(recorded.get("needs_update")),
        version=version,
    )


def _export_state(
    post_production: Mapping[str, Any],
    manifest: Mapping[str, Any] | None,
    assembly: AssemblyState,
) -> FinalExportState:
    components = _mapping(post_production.get("components"))
    recorded = _mapping(components.get("final_export"))
    active_entry = _active_manifest_entry(manifest)
    active_version = _optional_int(_mapping(manifest).get("active_version"))
    exported_assembly = _optional_int(active_entry.get("assembly_version"))
    version_mismatch = (
        assembly.version is not None
        and exported_assembly is not None
        and assembly.version != exported_assembly
    )
    stale = bool(active_entry) and (assembly.needs_update or version_mismatch)
    if active_entry:
        status = "STALE" if stale else "COMPLETED"
    else:
        recorded_status = _status(recorded.get("status"))
        status = recorded_status if recorded_status in {"RUNNING", "FAILED"} else "NOT_STARTED"
    return FinalExportState(
        status=status,
        version=active_version or _optional_int(recorded.get("active_version")),
        created_at=_safe_timestamp(active_entry.get("created_at")),
        stale=stale,
    )


def derive_workflow(
    data: Mapping[str, Any],
    manifests: ProjectManifests,
) -> WorkflowState:
    project_status = _status(data.get("status"))
    creative = StageState(
        status=_reviewed_stage_status(data, "CREATIVE", "CREATIVE_REVIEW")
    )
    storyboard = StageState(
        status=_reviewed_stage_status(data, "STORYBOARD", "STORYBOARD_REVIEW")
    )
    video_prompt = StageState(
        status=_reviewed_stage_status(data, "VIDEO_PROMPT", "PROMPT_REVIEW")
    )
    shots = _shot_state(data)
    assembly = _assembly_state(data, manifests.assembly)
    post_production = _mapping(data.get("post_production"))
    voice = _component_state(post_production, "voice", manifests.voice)
    subtitle = _component_state(post_production, "subtitle", manifests.subtitle)
    music = _component_state(post_production, "music", manifests.music)
    export = _export_state(post_production, manifests.export, assembly)
    stages = WorkflowStages(
        creative=creative,
        storyboard=storyboard,
        video_prompt=video_prompt,
        shots=shots,
        assembly=assembly,
        voice=voice,
        subtitle=subtitle,
        music=music,
        export=export,
    )

    primary_statuses = {
        creative.status,
        storyboard.status,
        video_prompt.status,
        shots.status,
        assembly.status,
        voice.status,
        subtitle.status,
        music.status,
        export.status,
    }
    if project_status == "CANCELLED":
        phase = WorkflowPhase.CANCELLED
    elif project_status == "FAILED" or "FAILED" in primary_statuses:
        phase = WorkflowPhase.FAILED
    elif _stage_status(data, "CREATIVE") not in _DONE:
        phase = WorkflowPhase.CREATIVE
    elif _stage_status(data, "CREATIVE_REVIEW") != "APPROVED":
        phase = WorkflowPhase.CREATIVE_REVIEW
    elif _stage_status(data, "STORYBOARD") not in _DONE:
        phase = WorkflowPhase.STORYBOARD
    elif _stage_status(data, "STORYBOARD_REVIEW") != "APPROVED":
        phase = WorkflowPhase.STORYBOARD_REVIEW
    elif _stage_status(data, "VIDEO_PROMPT") not in _DONE:
        phase = WorkflowPhase.VIDEO_PROMPT
    elif _stage_status(data, "PROMPT_REVIEW") != "APPROVED":
        phase = WorkflowPhase.VIDEO_PROMPT_REVIEW
    elif _stage_status(data, "VIDEO_GENERATION") not in _DONE or shots.status != "COMPLETED":
        phase = (
            WorkflowPhase.SHOT_REVIEW
            if shots.status == "WAITING_REVIEW"
            else WorkflowPhase.VIDEO_GENERATION
        )
    elif assembly.needs_update:
        phase = WorkflowPhase.ASSEMBLY_REQUIRED
    elif assembly.status != "COMPLETED":
        phase = WorkflowPhase.ASSEMBLY
    elif export.status == "COMPLETED":
        phase = WorkflowPhase.COMPLETED
    elif all(
        component.status == "COMPLETED"
        for component in (voice, subtitle, music)
    ) or _status(post_production.get("status")) in {"COMPLETED", "FINAL_COMPLETED"}:
        phase = WorkflowPhase.FINAL_EXPORT
    else:
        phase = WorkflowPhase.POST_PRODUCTION

    if phase is WorkflowPhase.FAILED and _failed_creative_is_retryable(data, manifests):
        available_actions = [AvailableAction.RETRY_GENERATE_CREATIVE]
    elif phase is WorkflowPhase.FAILED and _video_prompt_generation_is_allowed(
        data, manifests
    ):
        # The canonical Generate action deliberately doubles as the explicit
        # manual resume action. Core's per-Shot progress cache decides which
        # successful Shots can be skipped; no provider work starts automatically.
        available_actions = [AvailableAction.GENERATE_VIDEO_PROMPTS]
    elif phase is WorkflowPhase.VIDEO_PROMPT:
        available_actions = (
            [AvailableAction.GENERATE_VIDEO_PROMPTS]
            if _video_prompt_generation_is_allowed(data, manifests)
            else []
        )
    elif phase is WorkflowPhase.POST_PRODUCTION:
        available_actions = [
            action
            for component, action in (
                (voice, AvailableAction.GENERATE_VOICE),
                (subtitle, AvailableAction.GENERATE_SUBTITLE),
                (music, AvailableAction.SET_MUSIC),
            )
            if component.status != "COMPLETED"
        ]
    else:
        available_actions = list(_PHASE_ACTIONS.get(phase, ()))

    return WorkflowState(
        workflow_phase=phase,
        status=project_status,
        stages=stages,
        available_actions=available_actions,
    )
