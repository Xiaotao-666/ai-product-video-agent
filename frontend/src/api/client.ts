import { API_BASE_URL } from "../config";
import {
  ERROR_TASK_STATUSES,
  TASK_OPERATIONS,
  TASK_STATUSES,
  TERMINAL_TASK_STATUSES,
} from "./types";
import type {
  ApiResult,
  AssemblyDetail,
  AssemblyFinalVideoVersion,
  AssemblyFinalVideoSource,
  AssemblyPlan,
  AssemblyPlanningStatus,
  AssemblyPlanShot,
  AssemblyReadiness,
  AssemblyReadinessIssue,
  AssemblyShotVersion,
  AssemblyState,
  AvailableAction,
  BackendErrorResponse,
  CapabilitiesResponse,
  ComponentState,
  CreativeContentResponse,
  CreativeForbiddenWindow,
  CreativePlanningContent,
  CreateProjectRequest,
  CreateProjectResponse,
  ExportDetail,
  ExportVoiceTimingSummary,
  FinalExportState,
  GenerationIssue,
  GenerationIntent,
  GenerationModelOption,
  GenerationModelSelection,
  GenerationOptionsResponse,
  GenerationPreflightRequest,
  GenerationPreflightResponse,
  GenerationStartRequest,
  GenerationShotContext,
  GenerationVisualInputMode,
  GenerationVisualInputOption,
  HealthResponse,
  MusicDetail,
  MusicMixDetail,
  MultiShotGenerationAggregation,
  MultiShotGenerationOptionsResponse,
  MultiShotGenerationPlanResponse,
  MultiShotGenerationStartRequest,
  MultiShotPlanStatus,
  PostProductionState,
  PromptRevisionDraftRequest,
  PromptRevisionDraftAdoptResponse,
  PromptRevisionDraftResponse,
  ProjectDetail,
  ProjectListResponse,
  ProjectRequest,
  ReferenceAsset,
  ReferenceAssetListResponse,
  ReferenceAssetUploadResponse,
  ResolvedGeneration,
  ProjectSummary,
  ProjectWorkflowResponse,
  PlanningCue,
  ShotDetail,
  ShotGenerationSummary,
  ShotGenerationState,
  ShotGenerationStatusResponse,
  ShotListResponse,
  ShotPromptSummary,
  ShotPromptVersionSummary,
  ShotStageState,
  ShotSummary,
  ShotVersion,
  ShotVersionHistoryReason,
  ShotVersionRole,
  ShotVisualInputMode,
  StageState,
  StoryboardContentResponse,
  StoryboardPlanningContent,
  StoryboardShotContent,
  SubtitleCue,
  SubtitleDetail,
  SubtitleGenerateRequest,
  SubtitleHistoryResponse,
  SubtitleIssue,
  SubtitleOptionsResponse,
  SubtitleSourceSummary,
  SubtitleVersionSummary,
  ProjectTaskListResponse,
  TaskError,
  TaskOperation,
  TaskRecord,
  TaskResultReference,
  TaskStatus,
  VideoPromptShotContent,
  VideoPromptsContentResponse,
  VoiceCalibrationStatus,
  VoiceDetail,
  VoiceGenerateRequest,
  VoiceHistoryResponse,
  VoiceIntent,
  VoiceIssue,
  VoiceOptionsResponse,
  VoicePlannedTiming,
  VoicePreflightRequest,
  VoicePreflightResponse,
  VoiceProviderOption,
  VoiceScriptSummary,
  VoiceTimingAcceptance,
  VoiceVersionSummary,
  WorkflowPhase,
  WorkflowStages,
  WorkflowState,
} from "./types";

const CORRELATION_HEADER = "X-Correlation-ID";
const UNSAFE_CONTENT = /(?:[a-z]:[\\/]|\\\\|file:\/\/|api[_ -]?key|credential(?:_env_name)?|authorization|provider secret|bearer\s+\S+|sk-[A-Za-z0-9_-]{12,})/i;
const HIDDEN_CONTENT = "[敏感内容已隐藏]";
const SHOT_ID_PATTERN = /^shot_(?:0[1-9]|[1-9][0-9]*)$/;
const SHOT_VERSION_ROLES: ReadonlySet<string> = new Set([
  "OFFICIAL",
  "PENDING_REVIEW",
  "HISTORY",
]);
const SHOT_VERSION_HISTORY_REASONS: ReadonlySet<string> = new Set([
  "PREVIOUSLY_APPROVED",
  "SUPERSEDED",
  "EXPLICITLY_REJECTED",
  "UNKNOWN",
]);
const SHOT_VISUAL_INPUT_MODES: ReadonlySet<string> = new Set([
  "NONE",
  "FIRST_FRAME",
  "REFERENCE_ASSET",
  "UNKNOWN",
]);
const GENERATION_VISUAL_INPUT_MODES: ReadonlySet<string> = new Set([
  "none",
  "reference_asset",
  "first_frame",
]);
const GENERATION_MODEL_SELECTIONS: ReadonlySet<string> = new Set([
  "AUTO",
  "MANUAL",
]);
const VOICE_CALIBRATION_STATUSES: ReadonlySet<string> = new Set([
  "PASS",
  "WARNING",
  "OUT_OF_TOLERANCE",
  "OUT_OF_BOUNDS",
  "NOT_APPLICABLE",
  "UNKNOWN",
]);
const ASSEMBLY_PLANNING_STATUSES: ReadonlySet<string> = new Set([
  "NOT_READY",
  "READY",
  "OUTDATED",
]);
const VOICE_INTENTS: ReadonlySet<string> = new Set(["GENERATE", "REGENERATE"]);
const TASK_ID_PATTERN = /^task_[0-9a-f]{32}$/;
const TASK_REFERENCE_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const TASK_LOCATION_PATTERN = /^\/api\/tasks\/(task_[0-9a-f]{32})$/;
const TASK_STATUS_SET: ReadonlySet<string> = new Set(TASK_STATUSES);
const TERMINAL_TASK_STATUS_SET: ReadonlySet<string> = new Set(
  TERMINAL_TASK_STATUSES,
);
const ERROR_TASK_STATUS_SET: ReadonlySet<string> = new Set(ERROR_TASK_STATUSES);
const TASK_OPERATION_SET: ReadonlySet<string> = new Set(TASK_OPERATIONS);
const WORKFLOW_PHASES: ReadonlySet<string> = new Set([
  "CREATIVE",
  "CREATIVE_REVIEW",
  "STORYBOARD",
  "STORYBOARD_REVIEW",
  "VIDEO_PROMPT",
  "VIDEO_PROMPT_REVIEW",
  "VIDEO_GENERATION",
  "SHOT_REVIEW",
  "ASSEMBLY",
  "ASSEMBLY_REQUIRED",
  "POST_PRODUCTION",
  "FINAL_EXPORT",
  "COMPLETED",
  "FAILED",
  "CANCELLED",
  "ERROR",
]);
const AVAILABLE_ACTIONS: ReadonlySet<string> = new Set([
  "GENERATE_CREATIVE",
  "RETRY_GENERATE_CREATIVE",
  "APPROVE_CREATIVE",
  "REVISE_CREATIVE",
  "REGENERATE_CREATIVE",
  "GENERATE_STORYBOARD",
  "APPROVE_STORYBOARD",
  "REVISE_STORYBOARD",
  "REGENERATE_STORYBOARD",
  "GENERATE_VIDEO_PROMPTS",
  "APPROVE_VIDEO_PROMPTS",
  "REVISE_VIDEO_PROMPTS",
  "REGENERATE_VIDEO_PROMPTS",
  "GENERATE_SHOTS",
  "REVIEW_SHOTS",
  "MANAGE_SHOT_VERSIONS",
  "ASSEMBLE",
  "GENERATE_VOICE",
  "GENERATE_SUBTITLE",
  "SET_MUSIC",
  "FINAL_EXPORT",
]);

export class ApiClientError extends Error {
  readonly status: number | null;
  readonly code: string;
  readonly correlationId: string | null;
  readonly retryable: boolean;
  readonly requestAccepted: boolean;
  readonly taskLocation: string | null;

  constructor(options: {
    message: string;
    status?: number | null;
    code: string;
    correlationId?: string | null;
    retryable?: boolean;
    requestAccepted?: boolean;
    taskLocation?: string | null;
  }) {
    super(options.message);
    this.name = "ApiClientError";
    this.status = options.status ?? null;
    this.code = options.code;
    this.correlationId = options.correlationId ?? null;
    this.retryable = options.retryable ?? false;
    this.requestAccepted = options.requestAccepted ?? false;
    this.taskLocation = options.taskLocation ?? null;
  }
}

function isBackendError(value: unknown): value is BackendErrorResponse {
  if (!value || typeof value !== "object" || !("error" in value)) {
    return false;
  }
  const error = (value as { error?: unknown }).error;
  return Boolean(
    error &&
      typeof error === "object" &&
      "code" in error &&
      "message" in error &&
      "correlation_id" in error,
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value && typeof value === "object" && !Array.isArray(value));
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function isNullableNonNegativeNumber(value: unknown): value is number | null {
  return value === null ||
    (typeof value === "number" && Number.isFinite(value) && value >= 0);
}

function isNullableBoolean(value: unknown): value is boolean | null {
  return value === null || typeof value === "boolean";
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value > 0;
}

function isNullablePositiveInteger(value: unknown): value is number | null {
  return value === null || isPositiveInteger(value);
}

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= 0;
}

function isWorkflowPhase(value: unknown): value is WorkflowPhase {
  return typeof value === "string" && WORKFLOW_PHASES.has(value);
}

function isAvailableAction(value: unknown): value is AvailableAction {
  return typeof value === "string" && AVAILABLE_ACTIONS.has(value);
}

function isAssemblyState(value: unknown): value is AssemblyState {
  return Boolean(
    isRecord(value) &&
      typeof value.status === "string" &&
      typeof value.needs_update === "boolean" &&
      isNullableNumber(value.version),
  );
}

function isFinalExportState(value: unknown): value is FinalExportState {
  return Boolean(
    isRecord(value) &&
      typeof value.status === "string" &&
      isNullableNumber(value.version) &&
      isNullableString(value.created_at) &&
      typeof value.stale === "boolean",
  );
}

function invalidResponse(
  message: string,
  correlationId: string | null,
): never {
  throw new ApiClientError({
    message,
    code: "INVALID_RESPONSE",
    correlationId,
  });
}

function parseStageState(
  value: unknown,
  correlationId: string | null,
): StageState {
  if (!isRecord(value) || typeof value.status !== "string") {
    return invalidResponse("Backend 返回了无法读取的工作流。", correlationId);
  }
  return { status: value.status };
}

function parseShotStageState(
  value: unknown,
  correlationId: string | null,
): ShotStageState {
  if (
    !isRecord(value) ||
    typeof value.status !== "string" ||
    typeof value.approved !== "number" ||
    !Number.isInteger(value.approved) ||
    value.approved < 0 ||
    typeof value.total !== "number" ||
    !Number.isInteger(value.total) ||
    value.total < 0 ||
    value.approved > value.total
  ) {
    return invalidResponse("Backend 返回了无法读取的工作流。", correlationId);
  }
  return {
    status: value.status,
    approved: value.approved,
    total: value.total,
  };
}

function parseAssemblyState(
  value: unknown,
  correlationId: string | null,
): AssemblyState {
  if (!isAssemblyState(value)) {
    return invalidResponse("Backend 返回了无法读取的工作流。", correlationId);
  }
  return {
    status: value.status,
    needs_update: value.needs_update,
    version: value.version,
  };
}

function parseComponentState(
  value: unknown,
  correlationId: string | null,
): ComponentState {
  if (
    !isRecord(value) ||
    typeof value.status !== "string" ||
    !isNullableNumber(value.version)
  ) {
    return invalidResponse("Backend 返回了无法读取的工作流。", correlationId);
  }
  return { status: value.status, version: value.version };
}

function parseFinalExportState(
  value: unknown,
  correlationId: string | null,
): FinalExportState {
  if (!isFinalExportState(value)) {
    return invalidResponse("Backend 返回了无法读取的工作流。", correlationId);
  }
  return {
    status: value.status,
    version: value.version,
    created_at: value.created_at,
    stale: value.stale,
  };
}

function parseWorkflowStages(
  value: unknown,
  correlationId: string | null,
): WorkflowStages {
  if (!isRecord(value)) {
    return invalidResponse("Backend 返回了无法读取的工作流。", correlationId);
  }
  return {
    creative: parseStageState(value.creative, correlationId),
    storyboard: parseStageState(value.storyboard, correlationId),
    video_prompt: parseStageState(value.video_prompt, correlationId),
    shots: parseShotStageState(value.shots, correlationId),
    assembly: parseAssemblyState(value.assembly, correlationId),
    voice: parseComponentState(value.voice, correlationId),
    subtitle: parseComponentState(value.subtitle, correlationId),
    music: parseComponentState(value.music, correlationId),
    export: parseFinalExportState(value.export, correlationId),
  };
}

function parseWorkflowState(
  value: unknown,
  correlationId: string | null,
): WorkflowState {
  if (
    !isRecord(value) ||
    !isWorkflowPhase(value.workflow_phase) ||
    typeof value.status !== "string" ||
    !Array.isArray(value.available_actions) ||
    !value.available_actions.every(isAvailableAction)
  ) {
    return invalidResponse("Backend 返回了无法读取的工作流。", correlationId);
  }
  return {
    workflow_phase: value.workflow_phase,
    status: value.status,
    stages: parseWorkflowStages(value.stages, correlationId),
    available_actions: [...value.available_actions],
  };
}

function parseProjectRequest(
  value: unknown,
  correlationId: string | null,
): ProjectRequest {
  if (!isRecord(value)) {
    return invalidResponse("Backend 返回了无法读取的项目详情。", correlationId);
  }
  const textFields = [
    value.product_name,
    value.product_description,
    value.user_notes,
    value.video_style,
    value.video_purpose,
  ];
  if (
    textFields.some(
      (field) =>
        field !== undefined && field !== null && typeof field !== "string",
    ) ||
    (value.duration_seconds !== undefined &&
      !isNullableNumber(value.duration_seconds))
  ) {
    return invalidResponse("Backend 返回了无法读取的项目详情。", correlationId);
  }
  return {
    product_name:
      typeof value.product_name === "string" ? value.product_name : null,
    product_description:
      typeof value.product_description === "string"
        ? value.product_description
        : null,
    user_notes: typeof value.user_notes === "string" ? value.user_notes : null,
    duration_seconds:
      typeof value.duration_seconds === "number" ? value.duration_seconds : null,
    video_style:
      typeof value.video_style === "string" ? value.video_style : null,
    video_purpose:
      typeof value.video_purpose === "string" ? value.video_purpose : null,
  };
}

function parsePostProductionState(
  value: unknown,
  correlationId: string | null,
): PostProductionState {
  if (!isRecord(value) || typeof value.status !== "string") {
    return invalidResponse("Backend 返回了无法读取的项目详情。", correlationId);
  }
  return {
    status: value.status,
    voice: parseComponentState(value.voice, correlationId),
    subtitle: parseComponentState(value.subtitle, correlationId),
    music: parseComponentState(value.music, correlationId),
  };
}

function parseProjectDetail(
  value: unknown,
  correlationId: string | null,
): ProjectDetail {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.name !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    return invalidResponse("Backend 返回了无法读取的项目详情。", correlationId);
  }
  return {
    project_id: value.project_id,
    name: value.name,
    request: parseProjectRequest(value.request, correlationId),
    workflow: parseWorkflowState(value.workflow, correlationId),
    assembly: parseAssemblyState(value.assembly, correlationId),
    post_production: parsePostProductionState(
      value.post_production,
      correlationId,
    ),
    final_export: parseFinalExportState(value.final_export, correlationId),
    updated_at: value.updated_at,
  };
}

function parseProjectWorkflow(
  value: unknown,
  correlationId: string | null,
): ProjectWorkflowResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    return invalidResponse("Backend 返回了无法读取的工作流。", correlationId);
  }
  return {
    project_id: value.project_id,
    ...parseWorkflowState(value, correlationId),
    updated_at: value.updated_at,
  };
}

function isProjectSummary(value: unknown): value is ProjectSummary {
  return Boolean(
    isRecord(value) &&
      typeof value.project_id === "string" &&
      typeof value.name === "string" &&
      isWorkflowPhase(value.workflow_phase) &&
      typeof value.status === "string" &&
      typeof value.updated_at === "string" &&
      isAssemblyState(value.assembly) &&
      isFinalExportState(value.final_export),
  );
}

function parseProjectList(
  value: unknown,
  correlationId: string | null,
): ProjectListResponse {
  if (
    !isRecord(value) ||
    !Array.isArray(value.projects) ||
    !value.projects.every(isProjectSummary)
  ) {
    throw new ApiClientError({
      message: "Backend 返回了无法读取的项目列表。",
      code: "INVALID_RESPONSE",
      correlationId,
    });
  }
  return { projects: value.projects };
}

function parseCreateProjectResponse(
  value: unknown,
  correlationId: string | null,
): CreateProjectResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.name !== "string" ||
    !isWorkflowPhase(value.workflow_phase) ||
    typeof value.status !== "string" ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    throw new ApiClientError({
      message: "Backend 返回了无法读取的创建结果。",
      code: "INVALID_RESPONSE",
      correlationId,
    });
  }
  return {
    project_id: value.project_id,
    name: value.name,
    workflow_phase: value.workflow_phase,
    status: value.status,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

function parseContentText(
  value: unknown,
  correlationId: string | null,
): string | null {
  if (!isNullableString(value)) {
    return invalidResponse("Backend 返回了无法读取的规划内容。", correlationId);
  }
  if (typeof value === "string" && UNSAFE_CONTENT.test(value)) {
    return HIDDEN_CONTENT;
  }
  return value;
}

function parseContentTextList(
  value: unknown,
  correlationId: string | null,
): string[] {
  if (!Array.isArray(value) || !value.every(isNullableString)) {
    return invalidResponse("Backend 返回了无法读取的规划内容。", correlationId);
  }
  return value
    .filter((item): item is string => typeof item === "string")
    .map((item) => (UNSAFE_CONTENT.test(item) ? HIDDEN_CONTENT : item));
}

function parseCreativeWindow(
  value: unknown,
  correlationId: string | null,
): CreativeForbiddenWindow {
  if (
    !isRecord(value) ||
    !isNullableNumber(value.start) ||
    !isNullableNumber(value.end)
  ) {
    return invalidResponse("Backend 返回了无法读取的创意内容。", correlationId);
  }
  return {
    start: value.start,
    end: value.end,
    tracks: parseContentTextList(value.tracks, correlationId),
  };
}

function parseCreativeContent(
  value: unknown,
  correlationId: string | null,
): CreativePlanningContent {
  if (!isRecord(value)) {
    return invalidResponse("Backend 返回了无法读取的创意内容。", correlationId);
  }
  const narration = value.narration_plan;
  const subtitles = value.subtitle_strategy;
  const constraints = value.global_constraints;
  const timeline = value.av_timeline_constraints;
  if (
    !isRecord(narration) ||
    typeof narration.enabled !== "boolean" ||
    !isNullableNumber(narration.target_duration_seconds) ||
    !isRecord(subtitles) ||
    typeof subtitles.enabled !== "boolean" ||
    (subtitles.max_lines !== null &&
      (typeof subtitles.max_lines !== "number" ||
        !Number.isInteger(subtitles.max_lines))) ||
    !isRecord(constraints) ||
    !isRecord(timeline) ||
    !Array.isArray(timeline.forbidden_windows)
  ) {
    return invalidResponse("Backend 返回了无法读取的创意内容。", correlationId);
  }
  return {
    creative_concept: parseContentText(value.creative_concept, correlationId),
    target_audience: parseContentText(value.target_audience, correlationId),
    key_message: parseContentText(value.key_message, correlationId),
    visual_direction: parseContentText(value.visual_direction, correlationId),
    narrative_arc: parseContentText(value.narrative_arc, correlationId),
    narration_plan: {
      enabled: narration.enabled,
      tone: parseContentText(narration.tone, correlationId),
      full_script: parseContentText(narration.full_script, correlationId),
      target_duration_seconds: narration.target_duration_seconds,
    },
    subtitle_strategy: {
      enabled: subtitles.enabled,
      tone: parseContentText(subtitles.tone, correlationId),
      density: parseContentText(subtitles.density, correlationId),
      max_lines: subtitles.max_lines,
      preferred_position: parseContentText(
        subtitles.preferred_position,
        correlationId,
      ),
      principles: parseContentTextList(subtitles.principles, correlationId),
    },
    global_constraints: {
      must: parseContentTextList(constraints.must, correlationId),
      must_not: parseContentTextList(constraints.must_not, correlationId),
    },
    av_timeline_constraints: {
      forbidden_windows: timeline.forbidden_windows.map((window) =>
        parseCreativeWindow(window, correlationId),
      ),
    },
  };
}

function parseCreativeResponse(
  value: unknown,
  correlationId: string | null,
): CreativeContentResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    (value.content !== null && !isRecord(value.content))
  ) {
    return invalidResponse("Backend 返回了无法读取的创意内容。", correlationId);
  }
  return {
    project_id: value.project_id,
    status: value.status,
    content:
      value.content === null
        ? null
        : parseCreativeContent(value.content, correlationId),
  };
}

function parseCue(value: unknown, correlationId: string | null): PlanningCue {
  if (
    !isRecord(value) ||
    !isNullableNumber(value.start_offset) ||
    !isNullableNumber(value.end_offset)
  ) {
    return invalidResponse("Backend 返回了无法读取的分镜内容。", correlationId);
  }
  return {
    text: parseContentText(value.text, correlationId),
    start_offset: value.start_offset,
    end_offset: value.end_offset,
    position: parseContentText(value.position, correlationId),
  };
}

function parseStoryboardShot(
  value: unknown,
  correlationId: string | null,
): StoryboardShotContent {
  if (
    !isRecord(value) ||
    !isNullableNumber(value.shot_id) ||
    !isNullableNumber(value.duration_seconds) ||
    !Array.isArray(value.voiceover_cues) ||
    !Array.isArray(value.subtitle_cues) ||
    !isRecord(value.video_constraints) ||
    typeof value.video_constraints.reserve_subtitle_space !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的分镜内容。", correlationId);
  }
  return {
    shot_id: value.shot_id,
    duration_seconds: value.duration_seconds,
    purpose: parseContentText(value.purpose, correlationId),
    visual: parseContentText(value.visual, correlationId),
    camera: parseContentText(value.camera, correlationId),
    voiceover_cues: value.voiceover_cues.map((cue) =>
      parseCue(cue, correlationId),
    ),
    subtitle_cues: value.subtitle_cues.map((cue) =>
      parseCue(cue, correlationId),
    ),
    video_constraints: {
      reserve_subtitle_space:
        value.video_constraints.reserve_subtitle_space,
      subtitle_safe_area: parseContentText(
        value.video_constraints.subtitle_safe_area,
        correlationId,
      ),
    },
  };
}

function parseStoryboardContent(
  value: unknown,
  correlationId: string | null,
): StoryboardPlanningContent {
  if (
    !isRecord(value) ||
    !isNullableNumber(value.total_duration_seconds) ||
    !Array.isArray(value.shots)
  ) {
    return invalidResponse("Backend 返回了无法读取的分镜内容。", correlationId);
  }
  return {
    total_duration_seconds: value.total_duration_seconds,
    shots: value.shots.map((shot) =>
      parseStoryboardShot(shot, correlationId),
    ),
  };
}

function parseStoryboardResponse(
  value: unknown,
  correlationId: string | null,
): StoryboardContentResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    (value.content !== null && !isRecord(value.content))
  ) {
    return invalidResponse("Backend 返回了无法读取的分镜内容。", correlationId);
  }
  return {
    project_id: value.project_id,
    status: value.status,
    content:
      value.content === null
        ? null
        : parseStoryboardContent(value.content, correlationId),
  };
}

function parseVideoPromptShot(
  value: unknown,
  correlationId: string | null,
): VideoPromptShotContent {
  if (
    !isRecord(value) ||
    !isNullableNumber(value.shot_id) ||
    !isNullableNumber(value.prompt_version)
  ) {
    return invalidResponse("Backend 返回了无法读取的视频提示词。", correlationId);
  }
  return {
    shot_id: value.shot_id,
    prompt_version: value.prompt_version,
    prompt_source: parseContentText(value.prompt_source, correlationId),
    visual_prompt_core: parseContentText(
      value.visual_prompt_core,
      correlationId,
    ),
    prompt_text: parseContentText(value.prompt_text, correlationId),
  };
}

function parseVideoPromptsResponse(
  value: unknown,
  correlationId: string | null,
): VideoPromptsContentResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string"
  ) {
    return invalidResponse(
      "Backend 返回了无法读取的视频提示词。",
      correlationId,
    );
  }
  if (value.content === null) {
    return {
      project_id: value.project_id,
      status: value.status,
      content: null,
    };
  }
  if (!isRecord(value.content)) {
    return invalidResponse(
      "Backend 返回了无法读取的视频提示词。",
      correlationId,
    );
  }
  const shots = value.content.shots;
  if (!Array.isArray(shots)) {
    return invalidResponse(
      "Backend 返回了无法读取的视频提示词。",
      correlationId,
    );
  }
  return {
    project_id: value.project_id,
    status: value.status,
    content: {
      shots: shots.map((shot: unknown) =>
        parseVideoPromptShot(shot, correlationId),
      ),
    },
  };
}

function parseShotId(value: unknown, correlationId: string | null): string {
  if (
    typeof value !== "string" ||
    !SHOT_ID_PATTERN.test(value) ||
    UNSAFE_CONTENT.test(value)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头标识。", correlationId);
  }
  return value;
}

function parseShotSummary(
  value: unknown,
  correlationId: string | null,
): ShotSummary {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.order) ||
    typeof value.title !== "string" ||
    typeof value.status !== "string" ||
    typeof value.prompt_status !== "string" ||
    typeof value.video_status !== "string" ||
    typeof value.review_status !== "string" ||
    !isNullablePositiveInteger(value.official_version) ||
    !isNullablePositiveInteger(value.pending_review_version) ||
    !isNonNegativeInteger(value.version_count) ||
    !isNonNegativeInteger(value.generation_count)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头摘要。", correlationId);
  }
  return {
    shot_id: parseShotId(value.shot_id, correlationId),
    order: value.order,
    title: parseContentText(value.title, correlationId) ?? "",
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    prompt_status:
      parseContentText(value.prompt_status, correlationId) ?? "UNKNOWN",
    video_status:
      parseContentText(value.video_status, correlationId) ?? "UNKNOWN",
    review_status:
      parseContentText(value.review_status, correlationId) ?? "UNKNOWN",
    official_version: value.official_version,
    pending_review_version: value.pending_review_version,
    version_count: value.version_count,
    generation_count: value.generation_count,
  };
}

function parseShotAggregation(
  value: unknown,
  correlationId: string | null,
) {
  if (
    !isRecord(value) ||
    !isNonNegativeInteger(value.total) ||
    !isNonNegativeInteger(value.approved) ||
    !isNonNegativeInteger(value.waiting_review) ||
    !isNonNegativeInteger(value.generating) ||
    !isNonNegativeInteger(value.not_started) ||
    !isNonNegativeInteger(value.failed)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头汇总。", correlationId);
  }
  const aggregation = {
    total: value.total,
    approved: value.approved,
    waiting_review: value.waiting_review,
    generating: value.generating,
    not_started: value.not_started,
    failed: value.failed,
  };
  if (
    aggregation.approved +
      aggregation.waiting_review +
      aggregation.generating +
      aggregation.not_started +
      aggregation.failed !==
    aggregation.total
  ) {
    return invalidResponse("Backend 返回了不一致的镜头汇总。", correlationId);
  }
  return aggregation;
}

function parseShotListResponse(
  value: unknown,
  correlationId: string | null,
): ShotListResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !isRecord(value.aggregation) ||
    !Array.isArray(value.shots)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头列表。", correlationId);
  }
  const shots = value.shots.map((shot) => parseShotSummary(shot, correlationId));
  const aggregation = parseShotAggregation(value.aggregation, correlationId);
  const orders = shots.map((shot) => shot.order);
  if (
    aggregation.total !== shots.length ||
    new Set(orders).size !== orders.length ||
    orders.some((order, index) => index > 0 && order <= orders[index - 1]!)
  ) {
    return invalidResponse("Backend 返回了顺序不一致的镜头列表。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    aggregation,
    shots,
  };
}

const MULTI_SHOT_PLAN_STATUSES: ReadonlySet<string> = new Set([
  "READY",
  "IN_PROGRESS",
  "PARTIAL_PROGRESS",
  "WAITING_REVIEW",
  "COMPLETED",
  "NOT_STARTED",
]);

function parseMultiShotAggregation(
  value: unknown,
  correlationId: string | null,
): MultiShotGenerationAggregation {
  if (
    !isRecord(value) ||
    !isNonNegativeInteger(value.total) ||
    !isNonNegativeInteger(value.queued) ||
    !isNonNegativeInteger(value.running) ||
    !isNonNegativeInteger(value.waiting_review) ||
    !isNonNegativeInteger(value.approved) ||
    !isNonNegativeInteger(value.failed) ||
    !isNonNegativeInteger(value.not_started)
  ) {
    return invalidResponse("Backend 返回了无法读取的多镜头进度。", correlationId);
  }
  const aggregation = {
    total: value.total,
    queued: value.queued,
    running: value.running,
    waiting_review: value.waiting_review,
    approved: value.approved,
    failed: value.failed,
    not_started: value.not_started,
  };
  if (
    aggregation.queued +
      aggregation.running +
      aggregation.waiting_review +
      aggregation.approved +
      aggregation.failed +
      aggregation.not_started !==
    aggregation.total
  ) {
    return invalidResponse("Backend 返回了不一致的多镜头进度。", correlationId);
  }
  return aggregation;
}

function parseMultiShotPlanStatus(
  value: unknown,
  correlationId: string | null,
): MultiShotPlanStatus {
  if (typeof value !== "string" || !MULTI_SHOT_PLAN_STATUSES.has(value)) {
    return invalidResponse("Backend 返回了无法读取的生成计划状态。", correlationId);
  }
  return value as MultiShotPlanStatus;
}

function parseMultiShotOptions(
  value: unknown,
  correlationId: string | null,
): MultiShotGenerationOptionsResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    !isPositiveInteger(value.max_parallel) ||
    !Array.isArray(value.shots)
  ) {
    return invalidResponse("Backend 返回了无法读取的多镜头生成选项。", correlationId);
  }
  const shots = value.shots.map((shot) => {
    if (
      !isRecord(shot) ||
      !isPositiveInteger(shot.order) ||
      typeof shot.title !== "string" ||
      typeof shot.status !== "string" ||
      typeof shot.prompt_ready !== "boolean" ||
      typeof shot.video_status !== "string" ||
      typeof shot.available !== "boolean"
    ) {
      return invalidResponse("Backend 返回了无法读取的多镜头生成选项。", correlationId);
    }
    return {
      shot_id: parseShotId(shot.shot_id, correlationId),
      order: shot.order,
      title: parseContentText(shot.title, correlationId) ?? "",
      status: parseContentText(shot.status, correlationId) ?? "UNKNOWN",
      prompt_ready: shot.prompt_ready,
      video_status: parseContentText(shot.video_status, correlationId) ?? "UNKNOWN",
      available: shot.available,
    };
  });
  const aggregation = parseMultiShotAggregation(value.aggregation, correlationId);
  if (
    aggregation.total !== shots.length ||
    new Set(shots.map((shot) => shot.shot_id)).size !== shots.length
  ) {
    return invalidResponse("Backend 返回了不一致的多镜头生成选项。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseMultiShotPlanStatus(value.status, correlationId),
    max_parallel: value.max_parallel,
    aggregation,
    shots,
  };
}

function parseMultiShotPlan(
  value: unknown,
  correlationId: string | null,
): MultiShotGenerationPlanResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    !isPositiveInteger(value.max_parallel) ||
    !Array.isArray(value.shots)
  ) {
    return invalidResponse("Backend 返回了无法读取的多镜头生成计划。", correlationId);
  }
  const shots = value.shots.map((shot) => {
    if (
      !isRecord(shot) ||
      typeof shot.task_id !== "string" ||
      shot.operation !== "SHOT_GENERATE" ||
      !isTaskStatus(shot.status)
    ) {
      return invalidResponse("Backend 返回了无法读取的多镜头生成计划。", correlationId);
    }
    return {
      shot_id: parseShotId(shot.shot_id, correlationId),
      task_id: parseContentText(shot.task_id, correlationId) ?? "",
      operation: "SHOT_GENERATE" as const,
      status: shot.status,
    };
  });
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseMultiShotPlanStatus(value.status, correlationId),
    max_parallel: value.max_parallel,
    shots,
    aggregation: parseMultiShotAggregation(value.aggregation, correlationId),
  };
}

function parseShotPrompt(
  value: unknown,
  correlationId: string | null,
): ShotPromptSummary {
  if (!isRecord(value) || !isNullablePositiveInteger(value.version)) {
    return invalidResponse("Backend 返回了无法读取的镜头 Prompt。", correlationId);
  }
  return {
    version: value.version,
    source: parseContentText(value.source, correlationId),
    visual_prompt_core: parseContentText(
      value.visual_prompt_core,
      correlationId,
    ),
    final_prompt: parseContentText(value.final_prompt, correlationId),
  };
}

function parseShotGeneration(
  value: unknown,
  correlationId: string | null,
): ShotGenerationSummary {
  if (
    !isRecord(value) ||
    !isNullableString(value.model) ||
    typeof value.visual_input_mode !== "string" ||
    !SHOT_VISUAL_INPUT_MODES.has(value.visual_input_mode)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头生成信息。", correlationId);
  }
  return {
    model: parseContentText(value.model, correlationId),
    visual_input_mode: value.visual_input_mode as ShotVisualInputMode,
  };
}

function parseShotVersion(
  value: unknown,
  correlationId: string | null,
): ShotVersion {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.version) ||
    typeof value.role !== "string" ||
    !SHOT_VERSION_ROLES.has(value.role) ||
    typeof value.review_status !== "string" ||
    (value.history_reason !== undefined &&
      value.history_reason !== null &&
      (typeof value.history_reason !== "string" ||
        !SHOT_VERSION_HISTORY_REASONS.has(value.history_reason))) ||
    !isNullableString(value.created_at) ||
    typeof value.video_available !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头版本。", correlationId);
  }
  return {
    version: value.version,
    role: value.role as ShotVersionRole,
    review_status:
      parseContentText(value.review_status, correlationId) ?? "UNKNOWN",
    history_reason:
      value.history_reason == null
        ? null
        : (value.history_reason as ShotVersionHistoryReason),
    created_at: parseContentText(value.created_at, correlationId),
    prompt: parseShotPrompt(value.prompt, correlationId),
    generation: parseShotGeneration(value.generation, correlationId),
    video_available: value.video_available,
  };
}

function parseShotPromptVersionSummary(
  value: unknown,
  correlationId: string | null,
): ShotPromptVersionSummary {
  if (
    !isRecord(value)
    || !isPositiveInteger(value.version)
    || !isNullableString(value.source)
    || !isNullablePositiveInteger(value.parent_version)
    || !isNullableString(value.created_at)
  ) {
    return invalidResponse("Backend 返回了无法读取的 Prompt Version。", correlationId);
  }
  return {
    version: value.version,
    source: parseContentText(value.source, correlationId),
    parent_version: value.parent_version,
    created_at: parseContentText(value.created_at, correlationId),
  };
}

function parseShotDetailResponse(
  value: unknown,
  correlationId: string | null,
): ShotDetail {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !isNullablePositiveInteger(value.official_version) ||
    !isNullablePositiveInteger(value.pending_review_version) ||
    !isNonNegativeInteger(value.version_count) ||
    !isNonNegativeInteger(value.generation_count) ||
    (value.active_prompt_version !== undefined
      && !isNullablePositiveInteger(value.active_prompt_version)) ||
    (value.approved_prompt_version !== undefined
      && !isNullablePositiveInteger(value.approved_prompt_version)) ||
    (value.prompt_versions !== undefined && !Array.isArray(value.prompt_versions)) ||
    !Array.isArray(value.versions)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头详情。", correlationId);
  }
  const versions = value.versions.map((version) =>
    parseShotVersion(version, correlationId),
  );
  const promptVersions = (value.prompt_versions ?? []).map((version: unknown) =>
    parseShotPromptVersionSummary(version, correlationId),
  );
  if (value.version_count !== versions.length) {
    return invalidResponse("Backend 返回了不一致的镜头版本。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    shot_id: parseShotId(value.shot_id, correlationId),
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    official_version: value.official_version,
    pending_review_version: value.pending_review_version,
    version_count: value.version_count,
    generation_count: value.generation_count,
    active_prompt_version: value.active_prompt_version ?? null,
    approved_prompt_version: value.approved_prompt_version ?? null,
    prompt_versions: promptVersions,
    versions,
  };
}

function parsePromptRevisionDraft(
  value: unknown,
  correlationId: string | null,
): PromptRevisionDraftResponse {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.base_prompt_version) ||
    typeof value.original_prompt !== "string" ||
    value.original_prompt.trim().length === 0 ||
    typeof value.draft_prompt !== "string" ||
    value.draft_prompt.trim().length === 0 ||
    typeof value.feedback !== "string" ||
    value.feedback.trim().length === 0 ||
    typeof value.created_at !== "string" ||
    value.created_at.trim().length === 0
  ) {
    return invalidResponse(
      "Backend 返回了无法读取的Prompt修改建议。",
      correlationId,
    );
  }
  return {
    base_prompt_version: value.base_prompt_version,
    original_prompt: parseContentText(value.original_prompt, correlationId) ?? "",
    draft_prompt: parseContentText(value.draft_prompt, correlationId) ?? "",
    feedback: parseContentText(value.feedback, correlationId) ?? "",
    created_at: parseContentText(value.created_at, correlationId) ?? "",
  };
}

function parsePromptRevisionDraftAdoption(
  value: unknown,
  correlationId: string | null,
): PromptRevisionDraftAdoptResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.shot_id !== "string" ||
    !isPositiveInteger(value.prompt_version) ||
    !isPositiveInteger(value.parent_version) ||
    value.source !== "ai_revision" ||
    !isPositiveInteger(value.active_prompt_version) ||
    !isNullablePositiveInteger(value.approved_prompt_version) ||
    typeof value.created_at !== "string" ||
    value.created_at.trim().length === 0
  ) {
    return invalidResponse(
      "Backend 返回了无法读取的Prompt采用结果。",
      correlationId,
    );
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    shot_id: parseShotId(value.shot_id, correlationId),
    prompt_version: value.prompt_version,
    parent_version: value.parent_version,
    source: value.source,
    active_prompt_version: value.active_prompt_version,
    approved_prompt_version: value.approved_prompt_version,
    created_at: parseContentText(value.created_at, correlationId) ?? "",
  };
}

function parseGenerationVisualMode(
  value: unknown,
  correlationId: string | null,
): GenerationVisualInputMode {
  if (
    typeof value !== "string" ||
    !GENERATION_VISUAL_INPUT_MODES.has(value)
  ) {
    return invalidResponse("Backend 返回了无法读取的 Visual Input。", correlationId);
  }
  return value as GenerationVisualInputMode;
}

function parseGenerationSelection(
  value: unknown,
  correlationId: string | null,
): GenerationModelSelection {
  if (
    typeof value !== "string" ||
    !GENERATION_MODEL_SELECTIONS.has(value)
  ) {
    return invalidResponse("Backend 返回了无法读取的模型选择方式。", correlationId);
  }
  return value as GenerationModelSelection;
}

function parseRequiredSafeText(
  value: unknown,
  message: string,
  correlationId: string | null,
): string {
  if (typeof value !== "string" || value.length === 0) {
    return invalidResponse(message, correlationId);
  }
  return parseContentText(value, correlationId) ?? invalidResponse(message, correlationId);
}

function parseGenerationIssue(
  value: unknown,
  correlationId: string | null,
): GenerationIssue {
  if (
    !isRecord(value) ||
    typeof value.code !== "string" ||
    !/^[A-Z][A-Z0-9_]{0,63}$/.test(value.code)
  ) {
    return invalidResponse("Backend 返回了无法读取的配置问题。", correlationId);
  }
  return {
    code: value.code,
    message: parseRequiredSafeText(
      value.message,
      "Backend 返回了无法读取的配置问题。",
      correlationId,
    ),
  };
}

function parseGenerationShotContext(
  value: unknown,
  correlationId: string | null,
): GenerationShotContext {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.duration_seconds) ||
    !isNullablePositiveInteger(value.prompt_version)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头生成参数。", correlationId);
  }
  return {
    shot_id: parseShotId(value.shot_id, correlationId),
    duration_seconds: value.duration_seconds,
    prompt_version: value.prompt_version,
    resolution: parseRequiredSafeText(
      value.resolution,
      "Backend 返回了无法读取的分辨率。",
      correlationId,
    ),
    official_video_version: isNullablePositiveInteger(value.official_video_version)
      ? value.official_video_version
      : null,
    pending_video_version: isNullablePositiveInteger(value.pending_video_version)
      ? value.pending_video_version
      : null,
    next_video_version: isNullablePositiveInteger(value.next_video_version)
      ? value.next_video_version
      : null,
    base_video_version: isNullablePositiveInteger(value.base_video_version)
      ? value.base_video_version
      : null,
    next_prompt_version: isNullablePositiveInteger(value.next_prompt_version)
      ? value.next_prompt_version
      : null,
    official_prompt_version: isNullablePositiveInteger(value.official_prompt_version)
      ? value.official_prompt_version
      : null,
    prompt_source: isNullableString(value.prompt_source)
      ? parseContentText(value.prompt_source, correlationId)
      : null,
    prompt_parent_version: isNullablePositiveInteger(value.prompt_parent_version)
      ? value.prompt_parent_version
      : null,
  };
}

function parseGenerationModel(
  value: unknown,
  correlationId: string | null,
): GenerationModelOption {
  if (
    !isRecord(value) ||
    typeof value.available !== "boolean" ||
    !Array.isArray(value.supported_visual_input_modes) ||
    !Array.isArray(value.supported_resolutions) ||
    !Array.isArray(value.supported_durations) ||
    !value.supported_durations.every(isPositiveInteger) ||
    !isNullablePositiveInteger(value.min_duration) ||
    !isNullablePositiveInteger(value.max_duration)
  ) {
    return invalidResponse("Backend 返回了无法读取的视频模型。", correlationId);
  }
  return {
    model_id: parseRequiredSafeText(value.model_id, "视频模型无效。", correlationId),
    display_name: parseRequiredSafeText(value.display_name, "视频模型无效。", correlationId),
    provider: parseRequiredSafeText(value.provider, "视频 Provider 无效。", correlationId),
    provider_display_name: parseRequiredSafeText(value.provider_display_name, "视频 Provider 无效。", correlationId),
    api_version: parseRequiredSafeText(value.api_version, "视频 API Version 无效。", correlationId),
    available: value.available,
    supported_visual_input_modes: value.supported_visual_input_modes.map((mode) =>
      parseGenerationVisualMode(mode, correlationId),
    ),
    supported_resolutions: value.supported_resolutions.map((resolution) =>
      parseRequiredSafeText(resolution, "视频分辨率无效。", correlationId),
    ),
    supported_durations: [...value.supported_durations],
    min_duration: value.min_duration,
    max_duration: value.max_duration,
  };
}

function parseGenerationVisualOption(
  value: unknown,
  correlationId: string | null,
): GenerationVisualInputOption {
  if (!isRecord(value) || !Array.isArray(value.compatible_model_ids)) {
    return invalidResponse("Backend 返回了无法读取的 Visual Input 选项。", correlationId);
  }
  return {
    mode: parseGenerationVisualMode(value.mode, correlationId),
    display_name: parseRequiredSafeText(value.display_name, "Visual Input 选项无效。", correlationId),
    description: parseRequiredSafeText(value.description, "Visual Input 说明无效。", correlationId),
    compatible_model_ids: value.compatible_model_ids.map((model) =>
      parseRequiredSafeText(model, "兼容模型无效。", correlationId),
    ),
  };
}

function parseGenerationOptions(
  value: unknown,
  correlationId: string | null,
): GenerationOptionsResponse {
  if (
    !isRecord(value) ||
    typeof value.eligible !== "boolean" ||
    !Array.isArray(value.selection_modes) ||
    !Array.isArray(value.visual_input_modes) ||
    !Array.isArray(value.models) ||
    !Array.isArray(value.issues) ||
    typeof value.paid_call_required !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的生成选项。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(value.project_id, "项目标识无效。", correlationId),
    eligible: value.eligible,
    shot: parseGenerationShotContext(value.shot, correlationId),
    selection_modes: value.selection_modes.map((selection) =>
      parseGenerationSelection(selection, correlationId),
    ),
    visual_input_modes: value.visual_input_modes.map((option) =>
      parseGenerationVisualOption(option, correlationId),
    ),
    models: value.models.map((model) => parseGenerationModel(model, correlationId)),
    issues: value.issues.map((issue) => parseGenerationIssue(issue, correlationId)),
    paid_call_required: value.paid_call_required,
  };
}

function parseReferenceAsset(
  value: unknown,
  correlationId: string | null,
): ReferenceAsset {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.width) ||
    !isPositiveInteger(value.height)
  ) {
    return invalidResponse("Backend 返回了无法读取的参考素材。", correlationId);
  }
  return {
    asset_id: parseRequiredSafeText(value.asset_id, "参考素材标识无效。", correlationId),
    filename: parseRequiredSafeText(value.filename, "参考素材文件名无效。", correlationId),
    media_type: parseRequiredSafeText(value.media_type, "参考素材类型无效。", correlationId),
    width: value.width,
    height: value.height,
  };
}

function parseReferenceAssets(
  value: unknown,
  correlationId: string | null,
): ReferenceAssetListResponse {
  if (!isRecord(value) || !Array.isArray(value.assets)) {
    return invalidResponse("Backend 返回了无法读取的参考素材列表。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(value.project_id, "项目标识无效。", correlationId),
    assets: value.assets.map((asset) => parseReferenceAsset(asset, correlationId)),
  };
}

function parseReferenceAssetUpload(
  value: unknown,
  correlationId: string | null,
): ReferenceAssetUploadResponse {
  if (!isRecord(value) || typeof value.deduplicated !== "boolean") {
    return invalidResponse("Backend 返回了无法读取的素材上传结果。", correlationId);
  }
  return {
    ...parseReferenceAsset(value, correlationId),
    deduplicated: value.deduplicated,
  };
}

function parseResolvedGeneration(
  value: unknown,
  correlationId: string | null,
): ResolvedGeneration | null {
  if (value === null) {
    return null;
  }
  if (!isRecord(value)) {
    return invalidResponse("Backend 返回了无法读取的模型解析结果。", correlationId);
  }
  return {
    provider: parseRequiredSafeText(value.provider, "视频 Provider 无效。", correlationId),
    provider_display_name: parseRequiredSafeText(value.provider_display_name, "视频 Provider 无效。", correlationId),
    model: parseRequiredSafeText(value.model, "视频模型无效。", correlationId),
    model_display_name: parseRequiredSafeText(value.model_display_name, "视频模型无效。", correlationId),
    api_version: parseRequiredSafeText(value.api_version, "视频 API Version 无效。", correlationId),
    generation_mode: parseRequiredSafeText(value.generation_mode, "生成模式无效。", correlationId),
    generation_mode_display_name: parseRequiredSafeText(value.generation_mode_display_name, "生成模式无效。", correlationId),
    visual_input_mode: parseGenerationVisualMode(value.visual_input_mode, correlationId),
    model_selection: parseGenerationSelection(value.model_selection, correlationId),
  };
}

function parseGenerationPreflight(
  value: unknown,
  correlationId: string | null,
): GenerationPreflightResponse {
  if (
    !isRecord(value) ||
    typeof value.ready !== "boolean" ||
    typeof value.provider_available !== "boolean" ||
    !Array.isArray(value.selected_asset_ids) ||
    !Array.isArray(value.issues) ||
    !Array.isArray(value.warnings) ||
    typeof value.paid_call_required !== "boolean" ||
    !isNullableString(value.preflight_fingerprint) ||
    (typeof value.preflight_fingerprint === "string" &&
      !/^[0-9a-f]{64}$/.test(value.preflight_fingerprint))
  ) {
    return invalidResponse("Backend 返回了无法读取的配置检查结果。", correlationId);
  }
  return {
    ready: value.ready,
    shot: parseGenerationShotContext(value.shot, correlationId),
    resolved: parseResolvedGeneration(value.resolved, correlationId),
    provider_available: value.provider_available,
    selected_asset_ids: value.selected_asset_ids.map((assetId) =>
      parseRequiredSafeText(assetId, "参考素材标识无效。", correlationId),
    ),
    issues: value.issues.map((issue) => parseGenerationIssue(issue, correlationId)),
    warnings: value.warnings.map((issue) => parseGenerationIssue(issue, correlationId)),
    paid_call_required: value.paid_call_required,
    preflight_fingerprint: value.preflight_fingerprint,
  };
}

function parseShotGenerationStatus(
  value: unknown,
  correlationId: string | null,
): ShotGenerationStatusResponse {
  const states: ReadonlySet<string> = new Set([
    "NOT_STARTED", "QUEUED", "SUBMITTING", "PROVIDER_RUNNING",
    "READY_TO_DOWNLOAD", "DOWNLOADING", "LOCAL_FINALIZING",
    "WAITING_REVIEW", "APPROVED", "FAILED", "INTERRUPTED", "SUBMISSION_UNKNOWN",
  ]);
  const resumeKinds: ReadonlySet<string> = new Set([
    "POLL_EXISTING_TASK", "DOWNLOAD_EXISTING_FILE", "FINALIZE_LOCAL_VIDEO",
  ]);
  if (
    !isRecord(value) ||
    typeof value.state !== "string" ||
    !states.has(value.state) ||
    typeof value.resume_available !== "boolean" ||
    !isNullableString(value.resume_kind) ||
    (typeof value.resume_kind === "string" && !resumeKinds.has(value.resume_kind)) ||
    !isNullablePositiveInteger(value.video_version) ||
    (value.prompt_version !== undefined
      && !isNullablePositiveInteger(value.prompt_version)) ||
    typeof value.provider_submission_known !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头生成状态。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(value.project_id, "项目标识无效。", correlationId),
    shot_id: parseRequiredSafeText(value.shot_id, "镜头标识无效。", correlationId),
    state: value.state as ShotGenerationState,
    resume_available: value.resume_available,
    resume_kind: value.resume_kind as ShotGenerationStatusResponse["resume_kind"],
    video_version: value.video_version,
    prompt_version: value.prompt_version ?? null,
    provider_submission_known: value.provider_submission_known,
    generation_intent:
      value.generation_intent === "INITIAL"
      || value.generation_intent === "REGENERATE_CURRENT_PROMPT"
      || value.generation_intent === "REGENERATE_MANUAL_PROMPT"
      || value.generation_intent === "GENERATE_WITH_PROMPT_VERSION"
        ? value.generation_intent
        : null,
  };
}

function parseAssemblyShotVersion(
  value: unknown,
  correlationId: string | null,
): AssemblyShotVersion {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.shot_id) ||
    !isPositiveInteger(value.video_version)
  ) {
    return invalidResponse("Backend 返回了无法读取的合片镜头版本。", correlationId);
  }
  return { shot_id: value.shot_id, video_version: value.video_version };
}

function parseAssemblyFinalVideoSource(
  value: unknown,
  correlationId: string | null,
): AssemblyFinalVideoSource {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.shot_id) ||
    !isPositiveInteger(value.video_version) ||
    !isNullablePositiveInteger(value.prompt_version) ||
    !isNullablePositiveInteger(value.order)
  ) {
    return invalidResponse("Backend 返回了无法读取的成片来源版本。", correlationId);
  }
  return {
    shot_id: value.shot_id,
    video_version: value.video_version,
    prompt_version: value.prompt_version,
    order: value.order,
  };
}

function parseAssemblyFinalVideoVersion(
  value: unknown,
  correlationId: string | null,
): AssemblyFinalVideoVersion {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.final_video_version) ||
    !isNullablePositiveInteger(value.assembly_version) ||
    !isNullableString(value.created_at) ||
    !isNullableNonNegativeNumber(value.total_duration) ||
    typeof value.video_available !== "boolean" ||
    typeof value.is_current !== "boolean" ||
    !Array.isArray(value.shots)
  ) {
    return invalidResponse("Backend 返回了无法读取的成片版本。", correlationId);
  }
  return {
    final_video_version: value.final_video_version,
    assembly_version: value.assembly_version,
    created_at: parseContentText(value.created_at, correlationId),
    total_duration: value.total_duration,
    video_available: value.video_available,
    is_current: value.is_current,
    shots: value.shots.map((shot) =>
      parseAssemblyFinalVideoSource(shot, correlationId),
    ),
  };
}

function parseAssemblyDetail(
  value: unknown,
  correlationId: string | null,
): AssemblyDetail {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !isNullablePositiveInteger(value.current_version) ||
    typeof value.needs_update !== "boolean" ||
    !isNullablePositiveInteger(value.changed_shot_id) ||
    !isNullableString(value.created_at) ||
    !isNullableNonNegativeNumber(value.total_duration) ||
    typeof value.video_available !== "boolean" ||
    !Array.isArray(value.shots) ||
    !Array.isArray(value.final_videos)
  ) {
    return invalidResponse("Backend 返回了无法读取的合片详情。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    current_version: value.current_version,
    needs_update: value.needs_update,
    changed_shot_id: value.changed_shot_id,
    created_at: parseContentText(value.created_at, correlationId),
    total_duration: value.total_duration,
    video_available: value.video_available,
    shots: value.shots.map((shot) =>
      parseAssemblyShotVersion(shot, correlationId),
    ),
    current_plan:
      value.current_plan === null
        ? null
        : parseAssemblyPlan(value.current_plan, correlationId),
    final_videos: value.final_videos.map((version) =>
      parseAssemblyFinalVideoVersion(version, correlationId),
    ),
  };
}

function parseAssemblyPlanningStatus(
  value: unknown,
  correlationId: string | null,
): AssemblyPlanningStatus {
  if (typeof value !== "string" || !ASSEMBLY_PLANNING_STATUSES.has(value)) {
    return invalidResponse("Backend 返回了无法读取的 Assembly 计划状态。", correlationId);
  }
  return value as AssemblyPlanningStatus;
}

function parseAssemblyPlanShot(
  value: unknown,
  correlationId: string | null,
): AssemblyPlanShot {
  if (
    !isRecord(value)
    || !isPositiveInteger(value.shot_id)
    || !isPositiveInteger(value.order)
    || !isPositiveInteger(value.approved_video_version)
    || !isPositiveInteger(value.prompt_version)
    || typeof value.duration !== "number"
    || !Number.isFinite(value.duration)
    || value.duration <= 0
    || typeof value.resolution !== "string"
  ) {
    return invalidResponse("Backend 返回了无法读取的 Assembly 镜头计划。", correlationId);
  }
  return {
    shot_id: value.shot_id,
    order: value.order,
    approved_video_version: value.approved_video_version,
    prompt_version: value.prompt_version,
    duration: value.duration,
    resolution: parseRequiredSafeText(
      value.resolution,
      "Assembly 分辨率无效。",
      correlationId,
    ),
  };
}

function parseAssemblyPlan(
  value: unknown,
  correlationId: string | null,
): AssemblyPlan {
  if (
    !isRecord(value)
    || typeof value.project_id !== "string"
    || !isPositiveInteger(value.assembly_version)
    || typeof value.created_at !== "string"
    || typeof value.total_duration !== "number"
    || !Number.isFinite(value.total_duration)
    || value.total_duration <= 0
    || !Array.isArray(value.shots)
  ) {
    return invalidResponse("Backend 返回了无法读取的 Assembly 计划。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(
      value.project_id,
      "项目标识无效。",
      correlationId,
    ),
    assembly_version: value.assembly_version,
    status: parseAssemblyPlanningStatus(value.status, correlationId),
    created_at: parseRequiredSafeText(
      value.created_at,
      "Assembly 计划时间无效。",
      correlationId,
    ),
    total_duration: value.total_duration,
    shots: value.shots.map((shot) => parseAssemblyPlanShot(shot, correlationId)),
  };
}

function parseAssemblyReadinessIssue(
  value: unknown,
  correlationId: string | null,
): AssemblyReadinessIssue {
  if (
    !isRecord(value)
    || !isNullablePositiveInteger(value.shot_id)
    || !isNullablePositiveInteger(value.order)
    || typeof value.reason !== "string"
  ) {
    return invalidResponse("Backend 返回了无法读取的 Assembly 就绪问题。", correlationId);
  }
  return {
    shot_id: value.shot_id,
    order: value.order,
    reason: parseRequiredSafeText(
      value.reason,
      "Assembly 就绪问题无效。",
      correlationId,
    ),
  };
}

function parseAssemblyReadiness(
  value: unknown,
  correlationId: string | null,
): AssemblyReadiness {
  if (
    !isRecord(value)
    || typeof value.project_id !== "string"
    || typeof value.ready !== "boolean"
    || !isNonNegativeInteger(value.shot_count)
    || !isNonNegativeInteger(value.ready_count)
    || !isNullableNonNegativeNumber(value.total_duration)
    || !Array.isArray(value.shots)
    || !Array.isArray(value.issues)
    || (value.current_plan !== null && !isRecord(value.current_plan))
  ) {
    return invalidResponse("Backend 返回了无法读取的 Assembly 就绪状态。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(
      value.project_id,
      "项目标识无效。",
      correlationId,
    ),
    status: parseAssemblyPlanningStatus(value.status, correlationId),
    ready: value.ready,
    shot_count: value.shot_count,
    ready_count: value.ready_count,
    total_duration: value.total_duration,
    shots: value.shots.map((shot) => parseAssemblyPlanShot(shot, correlationId)),
    issues: value.issues.map((issue) =>
      parseAssemblyReadinessIssue(issue, correlationId),
    ),
    current_plan:
      value.current_plan === null
        ? null
        : parseAssemblyPlan(value.current_plan, correlationId),
  };
}

function parseCalibrationStatus(
  value: unknown,
  correlationId: string | null,
): VoiceCalibrationStatus {
  if (
    typeof value !== "string" ||
    !VOICE_CALIBRATION_STATUSES.has(value)
  ) {
    return invalidResponse("Backend 返回了无法读取的校准状态。", correlationId);
  }
  return value as VoiceCalibrationStatus;
}

function parseVoiceDetail(
  value: unknown,
  correlationId: string | null,
): VoiceDetail {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !isNullablePositiveInteger(value.version) ||
    !isNullableString(value.created_at) ||
    !isNullableString(value.script) ||
    !isNullableString(value.script_source) ||
    !isNullableString(value.provider) ||
    !isNullableString(value.model) ||
    !isNullableString(value.voice) ||
    !isNullableString(value.language) ||
    typeof value.audio_available !== "boolean" ||
    !isNullableNonNegativeNumber(value.planned_narration_duration) ||
    !isNullableNonNegativeNumber(value.planned_first_voice_start) ||
    !isNullableNonNegativeNumber(value.planned_last_voice_end) ||
    !isNullableNonNegativeNumber(value.planned_voice_span) ||
    !isNullableNonNegativeNumber(value.actual_audio_duration) ||
    !isNullableNonNegativeNumber(value.voice_track_start) ||
    !isNullableNonNegativeNumber(value.actual_voice_end) ||
    !isNullableNonNegativeNumber(value.total_video_duration) ||
    !isNullableNumber(value.duration_difference_seconds) ||
    !isNullableNumber(value.duration_difference_ratio) ||
    !isNullableString(value.timing_mode) ||
    !isNullableBoolean(value.cue_level_alignment) ||
    !isNullableBoolean(value.script_matches_storyboard)
  ) {
    return invalidResponse("Backend 返回了无法读取的配音详情。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    version: value.version,
    created_at: parseContentText(value.created_at, correlationId),
    script: parseContentText(value.script, correlationId),
    script_source: parseContentText(value.script_source, correlationId),
    provider: parseContentText(value.provider, correlationId),
    model: parseContentText(value.model, correlationId),
    voice: parseContentText(value.voice, correlationId),
    language: parseContentText(value.language, correlationId),
    audio_available: value.audio_available,
    planned_narration_duration: value.planned_narration_duration,
    planned_first_voice_start: value.planned_first_voice_start,
    planned_last_voice_end: value.planned_last_voice_end,
    planned_voice_span: value.planned_voice_span,
    actual_audio_duration: value.actual_audio_duration,
    voice_track_start: value.voice_track_start,
    actual_voice_end: value.actual_voice_end,
    total_video_duration: value.total_video_duration,
    duration_difference_seconds: value.duration_difference_seconds,
    duration_difference_ratio: value.duration_difference_ratio,
    timing_mode: parseContentText(value.timing_mode, correlationId),
    cue_level_alignment: value.cue_level_alignment,
    script_matches_storyboard: value.script_matches_storyboard,
    calibration_status: parseCalibrationStatus(
      value.calibration_status,
      correlationId,
    ),
    timing_acceptance: parseVoiceTimingAcceptance(
      value.timing_acceptance,
      correlationId,
    ),
  };
}

function isNonNegativeNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function parseVoiceTimingAcceptance(
  value: unknown,
  correlationId: string | null,
): VoiceTimingAcceptance | null {
  if (value === null) return null;
  if (
    !isRecord(value)
    || typeof value.accepted !== "boolean"
    || !isNullableString(value.accepted_at)
  ) {
    return invalidResponse("Backend 返回了无法读取的配音时间确认。", correlationId);
  }
  return {
    accepted: value.accepted,
    accepted_at: parseContentText(value.accepted_at, correlationId),
  };
}

function parseVoiceIntent(
  value: unknown,
  correlationId: string | null,
): VoiceIntent {
  if (typeof value !== "string" || !VOICE_INTENTS.has(value)) {
    return invalidResponse("Backend 返回了无法读取的配音操作。", correlationId);
  }
  return value as VoiceIntent;
}

function parseVoiceIssue(
  value: unknown,
  correlationId: string | null,
): VoiceIssue {
  return parseGenerationIssue(value, correlationId);
}

function parseVoicePlannedTiming(
  value: unknown,
  correlationId: string | null,
): VoicePlannedTiming {
  if (
    !isRecord(value)
    || !isNullableNonNegativeNumber(value.first_start)
    || !isNullableNonNegativeNumber(value.last_end)
    || !isNullableNonNegativeNumber(value.span)
    || !isNullableNonNegativeNumber(value.narration_duration)
  ) {
    return invalidResponse("Backend 返回了无法读取的配音计划时间。", correlationId);
  }
  return {
    first_start: value.first_start,
    last_end: value.last_end,
    span: value.span,
    narration_duration: value.narration_duration,
  };
}

function parseVoiceScriptSummary(
  value: unknown,
  correlationId: string | null,
): VoiceScriptSummary | null {
  if (value === null) return null;
  if (
    !isRecord(value)
    || typeof value.source !== "string"
    || typeof value.text !== "string"
    || !isNonNegativeInteger(value.character_count)
    || !isNonNegativeInteger(value.cue_count)
  ) {
    return invalidResponse("Backend 返回了无法读取的配音脚本摘要。", correlationId);
  }
  return {
    source: parseRequiredSafeText(value.source, "配音脚本来源无效。", correlationId),
    text: parseRequiredSafeText(value.text, "配音脚本无效。", correlationId),
    character_count: value.character_count,
    cue_count: value.cue_count,
  };
}

function parseVoiceProviderOption(
  value: unknown,
  correlationId: string | null,
): VoiceProviderOption {
  if (
    !isRecord(value)
    || typeof value.provider_id !== "string"
    || typeof value.display_name !== "string"
    || typeof value.model !== "string"
    || !isNullableString(value.default_voice)
    || typeof value.language !== "string"
    || !Array.isArray(value.supported_languages)
    || !value.supported_languages.every((item) => typeof item === "string")
    || !Array.isArray(value.allowed_voices)
    || !value.allowed_voices.every((item) => typeof item === "string")
    || typeof value.available !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的 TTS Provider。", correlationId);
  }
  return {
    provider_id: parseRequiredSafeText(value.provider_id, "TTS Provider 无效。", correlationId),
    display_name: parseRequiredSafeText(value.display_name, "TTS Provider 名称无效。", correlationId),
    model: parseRequiredSafeText(value.model, "TTS Provider 模型无效。", correlationId),
    default_voice: parseContentText(value.default_voice, correlationId),
    language: parseRequiredSafeText(value.language, "TTS 语言无效。", correlationId),
    supported_languages: value.supported_languages.map((item) =>
      parseRequiredSafeText(item, "TTS 语言无效。", correlationId)),
    allowed_voices: value.allowed_voices.map((item) =>
      parseRequiredSafeText(item, "TTS Voice 无效。", correlationId)),
    available: value.available,
  };
}

function parseVoiceOptions(
  value: unknown,
  correlationId: string | null,
): VoiceOptionsResponse {
  if (
    !isRecord(value)
    || typeof value.project_id !== "string"
    || typeof value.enabled !== "boolean"
    || typeof value.has_active_voice !== "boolean"
    || !isNullablePositiveInteger(value.active_version)
    || !isPositiveInteger(value.next_version)
    || !Array.isArray(value.providers)
    || !isNullableString(value.default_provider)
    || !isNullableString(value.default_voice)
    || typeof value.default_language !== "string"
    || typeof value.manual_script_required !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的配音选项。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(value.project_id, "项目标识无效。", correlationId),
    enabled: value.enabled,
    has_active_voice: value.has_active_voice,
    active_version: value.active_version,
    next_version: value.next_version,
    script: parseVoiceScriptSummary(value.script, correlationId),
    planned_timing: parseVoicePlannedTiming(value.planned_timing, correlationId),
    providers: value.providers.map((item) => parseVoiceProviderOption(item, correlationId)),
    default_provider: parseContentText(value.default_provider, correlationId),
    default_voice: parseContentText(value.default_voice, correlationId),
    default_language: parseRequiredSafeText(value.default_language, "TTS 语言无效。", correlationId),
    manual_script_required: value.manual_script_required,
  };
}

function parseVoicePreflight(
  value: unknown,
  correlationId: string | null,
): VoicePreflightResponse {
  if (
    !isRecord(value)
    || typeof value.project_id !== "string"
    || typeof value.ready !== "boolean"
    || !isPositiveInteger(value.next_voice_version)
    || !Array.isArray(value.issues)
    || !Array.isArray(value.warnings)
    || typeof value.external_call_required !== "boolean"
    || typeof value.external_cost_possible !== "boolean"
    || !isNullableString(value.preflight_fingerprint)
  ) {
    return invalidResponse("Backend 返回了无法读取的配音预检。", correlationId);
  }
  const provider = value.provider === null
    ? null
    : parseVoiceProviderOption(value.provider, correlationId);
  return {
    project_id: parseRequiredSafeText(value.project_id, "项目标识无效。", correlationId),
    ready: value.ready,
    intent: parseVoiceIntent(value.intent, correlationId),
    next_voice_version: value.next_voice_version,
    script: parseVoiceScriptSummary(value.script, correlationId),
    provider,
    planned_timing: parseVoicePlannedTiming(value.planned_timing, correlationId),
    issues: value.issues.map((item) => parseVoiceIssue(item, correlationId)),
    warnings: value.warnings.map((item) => parseVoiceIssue(item, correlationId)),
    external_call_required: value.external_call_required,
    external_cost_possible: value.external_cost_possible,
    preflight_fingerprint: parseContentText(value.preflight_fingerprint, correlationId),
  };
}

function parseVoiceVersionSummary(
  value: unknown,
  correlationId: string | null,
): VoiceVersionSummary {
  if (
    !isRecord(value)
    || !isPositiveInteger(value.version)
    || !isNullableString(value.created_at)
    || !isNullableString(value.provider)
    || !isNullableString(value.model)
    || !isNullableString(value.voice)
    || !isNullableString(value.language)
    || !isNullableString(value.script_source)
    || !isNullableNonNegativeNumber(value.duration_seconds)
    || typeof value.audio_available !== "boolean"
    || typeof value.is_active !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的配音历史。", correlationId);
  }
  return {
    version: value.version,
    created_at: parseContentText(value.created_at, correlationId),
    provider: parseContentText(value.provider, correlationId),
    model: parseContentText(value.model, correlationId),
    voice: parseContentText(value.voice, correlationId),
    language: parseContentText(value.language, correlationId),
    script_source: parseContentText(value.script_source, correlationId),
    duration_seconds: value.duration_seconds,
    calibration_status: parseCalibrationStatus(value.calibration_status, correlationId),
    timing_acceptance: parseVoiceTimingAcceptance(value.timing_acceptance, correlationId),
    audio_available: value.audio_available,
    is_active: value.is_active,
  };
}

function parseVoiceHistory(
  value: unknown,
  correlationId: string | null,
): VoiceHistoryResponse {
  if (
    !isRecord(value)
    || typeof value.project_id !== "string"
    || !isNullablePositiveInteger(value.active_version)
    || !Array.isArray(value.versions)
  ) {
    return invalidResponse("Backend 返回了无法读取的配音历史。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(value.project_id, "项目标识无效。", correlationId),
    active_version: value.active_version,
    versions: value.versions.map((item) => parseVoiceVersionSummary(item, correlationId)),
  };
}

function parseSubtitleCue(
  value: unknown,
  correlationId: string | null,
): SubtitleCue {
  if (
    !isRecord(value) ||
    !isPositiveInteger(value.index) ||
    typeof value.start !== "string" ||
    typeof value.end !== "string" ||
    typeof value.text !== "string"
  ) {
    return invalidResponse("Backend 返回了无法读取的字幕 Cue。", correlationId);
  }
  return {
    index: value.index,
    start: parseContentText(value.start, correlationId) ?? "",
    end: parseContentText(value.end, correlationId) ?? "",
    text: parseContentText(value.text, correlationId) ?? "",
  };
}

function parseSubtitleDetail(
  value: unknown,
  correlationId: string | null,
): SubtitleDetail {
  const sourceVoiceVersion = isRecord(value) && value.source_voice_version === undefined
    ? null
    : isRecord(value) ? value.source_voice_version : null;
  const provider = isRecord(value) && value.provider === undefined ? null : isRecord(value) ? value.provider : null;
  const model = isRecord(value) && value.model === undefined ? null : isRecord(value) ? value.model : null;
  const language = isRecord(value) && value.language === undefined ? null : isRecord(value) ? value.language : null;
  const durationSeconds = isRecord(value) && value.duration_seconds === undefined
    ? null
    : isRecord(value) ? value.duration_seconds : null;
  const semanticType = isRecord(value) && value.semantic_type === undefined ? null : isRecord(value) ? value.semantic_type : null;
  const actualAudioDuration = isRecord(value) && value.actual_audio_duration === undefined ? null : isRecord(value) ? value.actual_audio_duration : null;
  const voiceTrackStart = isRecord(value) && value.voice_track_start === undefined ? null : isRecord(value) ? value.voice_track_start : null;
  const actualVoiceEnd = isRecord(value) && value.actual_voice_end === undefined ? null : isRecord(value) ? value.actual_voice_end : null;
  const cueLevelAlignment = isRecord(value) && value.cue_level_alignment === undefined ? null : isRecord(value) ? value.cue_level_alignment : null;
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !isNullablePositiveInteger(value.version) ||
    !isNullableString(value.source) ||
    !isNullableString(value.timing_source) ||
    !isNullableString(semanticType) ||
    !isNullablePositiveInteger(sourceVoiceVersion) ||
    !isNullableNonNegativeNumber(actualAudioDuration) ||
    !isNullableNonNegativeNumber(voiceTrackStart) ||
    !isNullableNonNegativeNumber(actualVoiceEnd) ||
    !isNullableBoolean(cueLevelAlignment) ||
    !isNullableString(provider) ||
    !isNullableString(model) ||
    !isNullableString(language) ||
    !isNullableNonNegativeNumber(durationSeconds) ||
    !isNullableString(value.created_at) ||
    !isNonNegativeInteger(value.cue_count) ||
    typeof value.content_available !== "boolean" ||
    !Array.isArray(value.cues)
  ) {
    return invalidResponse("Backend 返回了无法读取的字幕详情。", correlationId);
  }
  const cues = value.cues.map((cue) => parseSubtitleCue(cue, correlationId));
  if (value.content_available && value.cue_count !== cues.length) {
    return invalidResponse("Backend 返回了不一致的字幕 Cue。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    version: value.version,
    source: parseContentText(value.source, correlationId),
    timing_source: parseContentText(value.timing_source, correlationId),
    semantic_type: parseContentText(semanticType, correlationId),
    source_voice_version: sourceVoiceVersion,
    actual_audio_duration: actualAudioDuration,
    voice_track_start: voiceTrackStart,
    actual_voice_end: actualVoiceEnd,
    cue_level_alignment: cueLevelAlignment,
    provider: parseContentText(provider, correlationId),
    model: parseContentText(model, correlationId),
    language: parseContentText(language, correlationId),
    duration_seconds: durationSeconds,
    created_at: parseContentText(value.created_at, correlationId),
    cue_count: value.cue_count,
    content_available: value.content_available,
    cues,
  };
}

function parseSubtitleIssue(value: unknown, correlationId: string | null): SubtitleIssue {
  if (!isRecord(value) || typeof value.code !== "string" || typeof value.message !== "string") {
    return invalidResponse("Backend 返回了无法读取的字幕就绪问题。", correlationId);
  }
  return {
    code: parseRequiredSafeText(value.code, "字幕问题代码无效。", correlationId),
    message: parseRequiredSafeText(value.message, "字幕问题信息无效。", correlationId),
  };
}

function parseSubtitleSource(value: unknown, correlationId: string | null): SubtitleSourceSummary {
  if (
    !isRecord(value)
    || value.type !== "active_voice"
    || typeof value.label !== "string"
    || !isNonNegativeInteger(value.cue_count)
    || typeof value.timing_source !== "string"
    || !isNullablePositiveInteger(value.voice_version)
    || typeof value.semantic_type !== "string"
    || typeof value.script !== "string"
    || !isNonNegativeNumber(value.actual_audio_duration)
    || !isNonNegativeNumber(value.voice_track_start)
    || !isNonNegativeNumber(value.actual_voice_end)
    || typeof value.cue_level_alignment !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的字幕来源。", correlationId);
  }
  return {
    type: value.type,
    label: parseRequiredSafeText(value.label, "字幕来源无效。", correlationId),
    cue_count: value.cue_count,
    timing_source: parseRequiredSafeText(value.timing_source, "字幕 Timing 来源无效。", correlationId),
    voice_version: value.voice_version,
    semantic_type: parseRequiredSafeText(value.semantic_type, "字幕语义无效。", correlationId),
    script: parseRequiredSafeText(value.script, "Voice Script 无效。", correlationId),
    actual_audio_duration: value.actual_audio_duration,
    voice_track_start: value.voice_track_start,
    actual_voice_end: value.actual_voice_end,
    cue_level_alignment: value.cue_level_alignment,
  };
}

function parseSubtitleOptions(value: unknown, correlationId: string | null): SubtitleOptionsResponse {
  if (
    !isRecord(value)
    || typeof value.project_id !== "string"
    || typeof value.applicable !== "boolean"
    || typeof value.ready !== "boolean"
    || typeof value.stale !== "boolean"
    || !isNullableString(value.stale_reason)
    || !isNullablePositiveInteger(value.active_version)
    || !isPositiveInteger(value.next_version)
    || (value.source !== null && !isRecord(value.source))
    || !Array.isArray(value.issues)
  ) {
    return invalidResponse("Backend 返回了无法读取的字幕选项。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(value.project_id, "项目标识无效。", correlationId),
    applicable: value.applicable,
    ready: value.ready,
    stale: value.stale,
    stale_reason: parseContentText(value.stale_reason, correlationId),
    active_version: value.active_version,
    next_version: value.next_version,
    source: value.source === null ? null : parseSubtitleSource(value.source, correlationId),
    issues: value.issues.map((item) => parseSubtitleIssue(item, correlationId)),
  };
}

function parseSubtitleVersionSummary(value: unknown, correlationId: string | null): SubtitleVersionSummary {
  const semanticType = isRecord(value) && value.semantic_type === undefined ? null : isRecord(value) ? value.semantic_type : null;
  const actualAudioDuration = isRecord(value) && value.actual_audio_duration === undefined ? null : isRecord(value) ? value.actual_audio_duration : null;
  const voiceTrackStart = isRecord(value) && value.voice_track_start === undefined ? null : isRecord(value) ? value.voice_track_start : null;
  const actualVoiceEnd = isRecord(value) && value.actual_voice_end === undefined ? null : isRecord(value) ? value.actual_voice_end : null;
  const cueLevelAlignment = isRecord(value) && value.cue_level_alignment === undefined ? null : isRecord(value) ? value.cue_level_alignment : null;
  if (
    !isRecord(value)
    || !isPositiveInteger(value.version)
    || !isNullableString(value.created_at)
    || !isNullableString(value.provider)
    || !isNullableString(value.model)
    || !isNullableString(value.language)
    || !isNullableNonNegativeNumber(value.duration_seconds)
    || !isNonNegativeInteger(value.cue_count)
    || !isNullableString(value.source)
    || !isNullableString(value.timing_source)
    || !isNullableString(semanticType)
    || !isNullablePositiveInteger(value.source_voice_version)
    || !isNullableNonNegativeNumber(actualAudioDuration)
    || !isNullableNonNegativeNumber(voiceTrackStart)
    || !isNullableNonNegativeNumber(actualVoiceEnd)
    || !isNullableBoolean(cueLevelAlignment)
    || typeof value.is_active !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的字幕历史。", correlationId);
  }
  return {
    version: value.version,
    created_at: parseContentText(value.created_at, correlationId),
    provider: parseContentText(value.provider, correlationId),
    model: parseContentText(value.model, correlationId),
    language: parseContentText(value.language, correlationId),
    duration_seconds: value.duration_seconds,
    cue_count: value.cue_count,
    source: parseContentText(value.source, correlationId),
    timing_source: parseContentText(value.timing_source, correlationId),
    semantic_type: parseContentText(semanticType, correlationId),
    source_voice_version: value.source_voice_version,
    actual_audio_duration: actualAudioDuration,
    voice_track_start: voiceTrackStart,
    actual_voice_end: actualVoiceEnd,
    cue_level_alignment: cueLevelAlignment,
    is_active: value.is_active,
  };
}

function parseSubtitleHistory(value: unknown, correlationId: string | null): SubtitleHistoryResponse {
  if (
    !isRecord(value)
    || typeof value.project_id !== "string"
    || !isNullablePositiveInteger(value.active_version)
    || !Array.isArray(value.versions)
  ) {
    return invalidResponse("Backend 返回了无法读取的字幕历史。", correlationId);
  }
  return {
    project_id: parseRequiredSafeText(value.project_id, "项目标识无效。", correlationId),
    active_version: value.active_version,
    versions: value.versions.map((item) => parseSubtitleVersionSummary(item, correlationId)),
  };
}

function parseMusicMix(
  value: unknown,
  correlationId: string | null,
): MusicMixDetail | null {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    !isNullableNonNegativeNumber(value.base_volume) ||
    !isNullableBoolean(value.ducking_enabled) ||
    !isNullableNonNegativeNumber(value.ducking_ratio) ||
    !isNullableNonNegativeNumber(value.duck_attack_seconds) ||
    !isNullableNonNegativeNumber(value.duck_release_seconds) ||
    !isNullableNonNegativeNumber(value.fade_in_seconds) ||
    !isNullableNonNegativeNumber(value.fade_out_seconds) ||
    !isNullableBoolean(value.loop_music) ||
    !isNullableString(value.ducking_status) ||
    (typeof value.base_volume === "number" && value.base_volume > 1) ||
    (typeof value.ducking_ratio === "number" && value.ducking_ratio > 1)
  ) {
    return invalidResponse("Backend 返回了无法读取的音乐 Mix。", correlationId);
  }
  return {
    base_volume: value.base_volume,
    ducking_enabled: value.ducking_enabled,
    ducking_ratio: value.ducking_ratio,
    duck_attack_seconds: value.duck_attack_seconds,
    duck_release_seconds: value.duck_release_seconds,
    fade_in_seconds: value.fade_in_seconds,
    fade_out_seconds: value.fade_out_seconds,
    loop_music: value.loop_music,
    ducking_status: parseContentText(value.ducking_status, correlationId),
  };
}

function parseMusicDetail(
  value: unknown,
  correlationId: string | null,
): MusicDetail {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !isNullablePositiveInteger(value.version) ||
    !isNullableString(value.created_at) ||
    typeof value.audio_available !== "boolean" ||
    !isNullableString(value.format) ||
    !isNullableNonNegativeNumber(value.duration_seconds)
  ) {
    return invalidResponse("Backend 返回了无法读取的音乐详情。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    version: value.version,
    created_at: parseContentText(value.created_at, correlationId),
    audio_available: value.audio_available,
    format: parseContentText(value.format, correlationId),
    duration_seconds: value.duration_seconds,
    music_mix: parseMusicMix(value.music_mix, correlationId),
  };
}

function parseExportVoiceTiming(
  value: unknown,
  correlationId: string | null,
): ExportVoiceTimingSummary | null {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    !isNullableString(value.timing_mode) ||
    !isNullableNonNegativeNumber(value.voice_track_start) ||
    !isNullableNonNegativeNumber(value.actual_audio_duration) ||
    !isNullableNonNegativeNumber(value.actual_voice_end) ||
    !isNullableBoolean(value.cue_level_alignment)
  ) {
    return invalidResponse("Backend 返回了无法读取的导出配音摘要。", correlationId);
  }
  return {
    timing_mode: parseContentText(value.timing_mode, correlationId),
    voice_track_start: value.voice_track_start,
    actual_audio_duration: value.actual_audio_duration,
    actual_voice_end: value.actual_voice_end,
    calibration_status: parseCalibrationStatus(
      value.calibration_status,
      correlationId,
    ),
    cue_level_alignment: value.cue_level_alignment,
  };
}

function parseExportDetail(
  value: unknown,
  correlationId: string | null,
): ExportDetail {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !isNullablePositiveInteger(value.version) ||
    !isNullableString(value.created_at) ||
    typeof value.stale !== "boolean" ||
    typeof value.video_available !== "boolean" ||
    !isNullablePositiveInteger(value.assembly_version) ||
    !isNullablePositiveInteger(value.voice_version) ||
    !isNullablePositiveInteger(value.subtitle_version) ||
    !isNullablePositiveInteger(value.music_version)
  ) {
    return invalidResponse("Backend 返回了无法读取的最终导出详情。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    version: value.version,
    created_at: parseContentText(value.created_at, correlationId),
    stale: value.stale,
    video_available: value.video_available,
    assembly_version: value.assembly_version,
    voice_version: value.voice_version,
    subtitle_version: value.subtitle_version,
    music_version: value.music_version,
    voice_timing: parseExportVoiceTiming(value.voice_timing, correlationId),
    music_mix: parseMusicMix(value.music_mix, correlationId),
  };
}

function isTaskStatus(value: unknown): value is TaskStatus {
  return typeof value === "string" && TASK_STATUS_SET.has(value);
}

function isTaskOperation(value: unknown): value is TaskOperation {
  return typeof value === "string" && TASK_OPERATION_SET.has(value);
}

function parseTaskError(
  value: unknown,
  correlationId: string | null,
): TaskError | null {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    typeof value.code !== "string" ||
    !/^[A-Z][A-Z0-9_]{0,63}$/.test(value.code) ||
    typeof value.message !== "string" ||
    typeof value.retryable !== "boolean"
  ) {
    return invalidResponse("Backend 返回了无法读取的任务错误。", correlationId);
  }
  return {
    code: value.code,
    message: parseContentText(value.message, correlationId) ?? "任务执行失败。",
    retryable: value.retryable,
  };
}

function parseTaskResult(
  value: unknown,
  correlationId: string | null,
): TaskResultReference | null {
  if (value === null) {
    return null;
  }
  if (
    !isRecord(value) ||
    typeof value.resource_type !== "string" ||
    !/^[A-Z][A-Z0-9_]{0,63}$/.test(value.resource_type) ||
    !isNullableString(value.resource_id) ||
    !isNullablePositiveInteger(value.version)
  ) {
    return invalidResponse("Backend 返回了无法读取的任务结果。", correlationId);
  }
  return {
    resource_type: value.resource_type,
    resource_id: parseContentText(value.resource_id, correlationId),
    version: value.version,
  };
}

function parseTaskRecord(
  value: unknown,
  correlationId: string | null,
): TaskRecord {
  if (
    !isRecord(value) ||
    typeof value.task_id !== "string" ||
    !TASK_ID_PATTERN.test(value.task_id) ||
    typeof value.project_id !== "string" ||
    value.project_id.length === 0 ||
    UNSAFE_CONTENT.test(value.project_id) ||
    !isTaskOperation(value.operation) ||
    (value.target_id !== undefined &&
      value.target_id !== null &&
      (typeof value.target_id !== "string" ||
        !TASK_REFERENCE_PATTERN.test(value.target_id) ||
        UNSAFE_CONTENT.test(value.target_id))) ||
    !isTaskStatus(value.status) ||
    typeof value.created_at !== "string" ||
    !isNullableString(value.started_at) ||
    !isNullableString(value.finished_at) ||
    typeof value.correlation_id !== "string" ||
    UNSAFE_CONTENT.test(value.correlation_id)
  ) {
    return invalidResponse("Backend 返回了无法读取的任务记录。", correlationId);
  }

  const error = parseTaskError(value.error, correlationId);
  const result = parseTaskResult(value.result, correlationId);
  const isTerminal = TERMINAL_TASK_STATUS_SET.has(value.status);
  const isError = ERROR_TASK_STATUS_SET.has(value.status);
  if (
    (value.status === "QUEUED" &&
      (value.started_at !== null || value.finished_at !== null)) ||
    (value.status === "RUNNING" &&
      (value.started_at === null || value.finished_at !== null)) ||
    (isTerminal && value.finished_at === null) ||
    (isError && error === null) ||
    (!isError && error !== null) ||
    (result !== null && value.status !== "SUCCEEDED")
  ) {
    return invalidResponse("Backend 返回了状态不一致的任务记录。", correlationId);
  }

  return {
    task_id: value.task_id,
    project_id: value.project_id,
    operation: value.operation,
    target_id: value.target_id ?? null,
    status: value.status,
    created_at: value.created_at,
    started_at: value.started_at,
    finished_at: value.finished_at,
    correlation_id: value.correlation_id,
    error,
    result,
  };
}

function parseProjectTaskList(
  value: unknown,
  correlationId: string | null,
): ProjectTaskListResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    value.project_id.length === 0 ||
    UNSAFE_CONTENT.test(value.project_id) ||
    !Array.isArray(value.tasks)
  ) {
    return invalidResponse("Backend 返回了无法读取的项目任务列表。", correlationId);
  }
  return {
    project_id: value.project_id,
    tasks: value.tasks.map((task) => parseTaskRecord(task, correlationId)),
  };
}

async function readJson(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ApiClientError({
      message: "Backend 返回了无法读取的响应。",
      status: response.status,
      code: "INVALID_RESPONSE",
      correlationId: response.headers.get(CORRELATION_HEADER),
      requestAccepted: response.status === 202,
      taskLocation: response.headers.get("Location"),
    });
  }
}

async function request(
  path: string,
  init: RequestInit,
): Promise<ApiResult<unknown> & { status: number; location: string | null }> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, init);
  } catch {
    throw new ApiClientError({
      message: "无法连接本地 Backend。",
      code: "NETWORK_ERROR",
    });
  }

  const payload = await readJson(response);
  const headerCorrelationId = response.headers.get(CORRELATION_HEADER);
  if (!response.ok) {
    if (isBackendError(payload)) {
      throw new ApiClientError({
        message: payload.error.message,
        status: response.status,
        code: payload.error.code,
        correlationId: payload.error.correlation_id || headerCorrelationId,
        retryable: payload.error.retryable,
      });
    }
    throw new ApiClientError({
      message: "Backend 请求失败。",
      status: response.status,
      code: "HTTP_ERROR",
      correlationId: headerCorrelationId,
    });
  }

  return {
    data: payload,
    correlationId: headerCorrelationId,
    status: response.status,
    location: response.headers.get("Location"),
  };
}

async function get<T>(path: string): Promise<ApiResult<T>> {
  const result = await request(path, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  return {
    data: result.data as T,
    correlationId: result.correlationId,
  };
}

type PaidShotTaskOperation =
  | "SHOT_GENERATE"
  | "SHOT_REGENERATE"
  | "SHOT_PROMPT_VERSION_GENERATE";

interface AcceptedShotTaskExpectation {
  projectId: string;
  shotId: string;
  operation: PaidShotTaskOperation;
  submittedAt: number;
  correlationId: string | null;
  location: string | null;
}

function acceptedShotTaskMatches(
  task: TaskRecord,
  expectation: AcceptedShotTaskExpectation,
): boolean {
  return task.project_id === expectation.projectId
    && task.operation === expectation.operation
    && task.target_id === expectation.shotId
    && (
      expectation.correlationId === null
      || task.correlation_id === expectation.correlationId
    );
}

function taskIdFromLocation(location: string | null): string | null {
  if (location === null) return null;
  return TASK_LOCATION_PATTERN.exec(location)?.[1] ?? null;
}

function acceptedTaskStatusUnreadable(
  expectation: AcceptedShotTaskExpectation,
): ApiClientError {
  return new ApiClientError({
    message: "生成请求已被后端接受，但当前无法读取任务状态。请勿重复提交生成请求。",
    status: 202,
    code: "ACCEPTED_TASK_STATUS_UNREADABLE",
    correlationId: expectation.correlationId,
    requestAccepted: true,
    taskLocation: expectation.location,
  });
}

async function reconcileAcceptedShotTask(
  expectation: AcceptedShotTaskExpectation,
): Promise<ApiResult<TaskRecord>> {
  const locationTaskId = taskIdFromLocation(expectation.location);
  if (locationTaskId !== null) {
    try {
      const located = await getTask(locationTaskId);
      if (acceptedShotTaskMatches(located.data, expectation)) {
        return located;
      }
    } catch {
      // A failed GET never authorizes another paid POST. Continue with the
      // project task list as the second read-only reconciliation path.
    }
  }

  try {
    const listed = await getProjectTasks(expectation.projectId);
    const exact = listed.data.tasks.filter((task) =>
      acceptedShotTaskMatches(task, expectation)
    );
    if (exact.length === 1) {
      return { data: exact[0], correlationId: expectation.correlationId };
    }
    if (expectation.correlationId === null) {
      const recent = listed.data.tasks.filter((task) => {
        const createdAt = Date.parse(task.created_at);
        return task.project_id === expectation.projectId
          && task.operation === expectation.operation
          && task.target_id === expectation.shotId
          && Number.isFinite(createdAt)
          && createdAt >= expectation.submittedAt - 60_000;
      });
      if (recent.length === 1) {
        return { data: recent[0], correlationId: recent[0].correlation_id };
      }
    }
  } catch {
    // Preserve the accepted-but-unreadable state. Reconciliation remains GET-only.
  }

  throw acceptedTaskStatusUnreadable(expectation);
}

async function submitPaidShotTask(
  path: string,
  init: RequestInit,
  expectation: Omit<AcceptedShotTaskExpectation, "correlationId" | "location">,
): Promise<ApiResult<TaskRecord>> {
  let result: Awaited<ReturnType<typeof request>>;
  try {
    result = await request(path, init);
  } catch (error) {
    if (!(error instanceof ApiClientError) || !error.requestAccepted) throw error;
    return reconcileAcceptedShotTask({
      ...expectation,
      correlationId: error.correlationId,
      location: error.taskLocation,
    });
  }

  try {
    const task = parseTaskRecord(result.data, result.correlationId);
    const acceptedExpectation: AcceptedShotTaskExpectation = {
      ...expectation,
      correlationId: result.correlationId,
      location: result.location,
    };
    if (!acceptedShotTaskMatches(task, acceptedExpectation)) {
      return reconcileAcceptedShotTask(acceptedExpectation);
    }
    return { data: task, correlationId: result.correlationId };
  } catch (error) {
    if (result.status !== 202) throw error;
    return reconcileAcceptedShotTask({
      ...expectation,
      correlationId: result.correlationId,
      location: result.location,
    });
  }
}

interface AcceptedVoiceTaskExpectation {
  projectId: string;
  targetId: string;
  submittedAt: number;
  correlationId: string | null;
  location: string | null;
}

function acceptedVoiceTaskMatches(
  task: TaskRecord,
  expectation: AcceptedVoiceTaskExpectation,
): boolean {
  return task.project_id === expectation.projectId
    && task.operation === "VOICE_GENERATE"
    && task.target_id === expectation.targetId
    && (
      expectation.correlationId === null
      || task.correlation_id === expectation.correlationId
    );
}

async function reconcileAcceptedVoiceTask(
  expectation: AcceptedVoiceTaskExpectation,
): Promise<ApiResult<TaskRecord>> {
  const locationTaskId = taskIdFromLocation(expectation.location);
  if (locationTaskId !== null) {
    try {
      const located = await getTask(locationTaskId);
      if (acceptedVoiceTaskMatches(located.data, expectation)) return located;
    } catch {
      // A failed GET never authorizes another external TTS POST.
    }
  }
  try {
    const listed = await getProjectTasks(expectation.projectId);
    const exact = listed.data.tasks.filter((task) =>
      acceptedVoiceTaskMatches(task, expectation));
    if (exact.length === 1) {
      return { data: exact[0], correlationId: expectation.correlationId };
    }
    if (expectation.correlationId === null) {
      const recent = listed.data.tasks.filter((task) => {
        const createdAt = Date.parse(task.created_at);
        return task.project_id === expectation.projectId
          && task.operation === "VOICE_GENERATE"
          && task.target_id === expectation.targetId
          && Number.isFinite(createdAt)
          && createdAt >= expectation.submittedAt - 60_000;
      });
      if (recent.length === 1) {
        return { data: recent[0], correlationId: recent[0].correlation_id };
      }
    }
  } catch {
    // Preserve the accepted-but-unreadable state. Reconciliation is GET-only.
  }
  throw new ApiClientError({
    message: "配音请求已被后端接受，但当前无法读取任务状态。请勿重复提交。",
    status: 202,
    code: "ACCEPTED_TASK_STATUS_UNREADABLE",
    correlationId: expectation.correlationId,
    requestAccepted: true,
    taskLocation: expectation.location,
  });
}

async function submitPaidVoiceTask(
  path: string,
  payload: VoiceGenerateRequest,
  expectation: Omit<AcceptedVoiceTaskExpectation, "correlationId" | "location">,
): Promise<ApiResult<TaskRecord>> {
  let result: Awaited<ReturnType<typeof request>>;
  try {
    result = await request(path, {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch (error) {
    if (!(error instanceof ApiClientError) || !error.requestAccepted) throw error;
    return reconcileAcceptedVoiceTask({
      ...expectation,
      correlationId: error.correlationId,
      location: error.taskLocation,
    });
  }
  const acceptedExpectation: AcceptedVoiceTaskExpectation = {
    ...expectation,
    correlationId: result.correlationId,
    location: result.location,
  };
  try {
    const task = parseTaskRecord(result.data, result.correlationId);
    if (!acceptedVoiceTaskMatches(task, acceptedExpectation)) {
      return reconcileAcceptedVoiceTask(acceptedExpectation);
    }
    return { data: task, correlationId: result.correlationId };
  } catch (error) {
    if (result.status !== 202) throw error;
    return reconcileAcceptedVoiceTask(acceptedExpectation);
  }
}

export function getHealth(): Promise<ApiResult<HealthResponse>> {
  return get<HealthResponse>("/api/health");
}

export function getCapabilities(): Promise<ApiResult<CapabilitiesResponse>> {
  return get<CapabilitiesResponse>("/api/capabilities");
}

export async function getProjects(): Promise<ApiResult<ProjectListResponse>> {
  const result = await get<unknown>("/api/projects");
  return {
    data: parseProjectList(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getProject(
  projectId: string,
): Promise<ApiResult<ProjectDetail>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}`,
  );
  return {
    data: parseProjectDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getProjectWorkflow(
  projectId: string,
): Promise<ApiResult<ProjectWorkflowResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/workflow`,
  );
  return {
    data: parseProjectWorkflow(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getCreativeContent(
  projectId: string,
): Promise<ApiResult<CreativeContentResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/planning/creative`,
  );
  return {
    data: parseCreativeResponse(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getStoryboardContent(
  projectId: string,
): Promise<ApiResult<StoryboardContentResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/planning/storyboard`,
  );
  return {
    data: parseStoryboardResponse(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getVideoPrompts(
  projectId: string,
): Promise<ApiResult<VideoPromptsContentResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/planning/video-prompts`,
  );
  return {
    data: parseVideoPromptsResponse(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getShots(
  projectId: string,
): Promise<ApiResult<ShotListResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/shots`,
  );
  return {
    data: parseShotListResponse(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getMultiShotGenerationOptions(
  projectId: string,
): Promise<ApiResult<MultiShotGenerationOptionsResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/shots/generation/options`,
  );
  return {
    data: parseMultiShotOptions(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function startMultiShotGeneration(
  projectId: string,
  payload: MultiShotGenerationStartRequest,
): Promise<ApiResult<MultiShotGenerationPlanResponse>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/shots/generation/start`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  );
  return {
    data: parseMultiShotPlan(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getShot(
  projectId: string,
  shotId: string,
): Promise<ApiResult<ShotDetail>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}`,
  );
  return {
    data: parseShotDetailResponse(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getPromptRevisionDraft(
  projectId: string,
  shotId: string,
): Promise<ApiResult<PromptRevisionDraftResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/prompt/revision/draft`,
  );
  return {
    data: parsePromptRevisionDraft(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function submitPromptRevisionDraft(
  projectId: string,
  shotId: string,
  payload: PromptRevisionDraftRequest,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/prompt/revision/draft`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function adoptPromptRevisionDraft(
  projectId: string,
  shotId: string,
): Promise<ApiResult<PromptRevisionDraftAdoptResponse>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/prompt/revision/draft/adopt`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parsePromptRevisionDraftAdoption(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function approveShot(
  projectId: string,
  shotId: string,
): Promise<ApiResult<ShotDetail>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/approve`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseShotDetailResponse(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function setOfficialShotVersion(
  projectId: string,
  shotId: string,
  videoVersion: number,
): Promise<ApiResult<ShotDetail>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/versions/${encodeURIComponent(String(videoVersion))}/set-official`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseShotDetailResponse(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getShotGenerationOptions(
  projectId: string,
  shotId: string,
  intent: GenerationIntent = "INITIAL",
  targetPromptVersion: number | null = null,
): Promise<ApiResult<GenerationOptionsResponse>> {
  const params = new URLSearchParams();
  if (intent !== "INITIAL") params.set("intent", intent);
  if (targetPromptVersion !== null) {
    params.set("target_prompt_version", String(targetPromptVersion));
  }
  const query = params.size > 0 ? `?${params.toString()}` : "";
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/options${query}`,
  );
  return {
    data: parseGenerationOptions(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getReferenceAssets(
  projectId: string,
): Promise<ApiResult<ReferenceAssetListResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/references`,
  );
  return {
    data: parseReferenceAssets(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function uploadReferenceAsset(
  projectId: string,
  file: File,
): Promise<ApiResult<ReferenceAssetUploadResponse>> {
  const form = new FormData();
  form.append("file", file, file.name);
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/references`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      body: form,
    },
  );
  return {
    data: parseReferenceAssetUpload(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export function getReferenceImageUrl(
  projectId: string,
  assetId: string,
): string {
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/references/${encodeURIComponent(assetId)}/image`;
}

export async function preflightShotGeneration(
  projectId: string,
  shotId: string,
  payload: GenerationPreflightRequest,
): Promise<ApiResult<GenerationPreflightResponse>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/preflight`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
  );
  return {
    data: parseGenerationPreflight(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function startShotGeneration(
  projectId: string,
  shotId: string,
  payload: GenerationStartRequest,
): Promise<ApiResult<TaskRecord>> {
  const submittedAt = Date.now();
  return submitPaidShotTask(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/start`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
    { projectId, shotId, operation: "SHOT_GENERATE", submittedAt },
  );
}

export async function regenerateShotGeneration(
  projectId: string,
  shotId: string,
  payload: GenerationStartRequest,
): Promise<ApiResult<TaskRecord>> {
  const submittedAt = Date.now();
  return submitPaidShotTask(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/regenerate`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
    { projectId, shotId, operation: "SHOT_REGENERATE", submittedAt },
  );
}

export async function generateShotWithPromptVersion(
  projectId: string,
  shotId: string,
  payload: GenerationStartRequest,
): Promise<ApiResult<TaskRecord>> {
  const submittedAt = Date.now();
  return submitPaidShotTask(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/prompt-version`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    },
    {
      projectId,
      shotId,
      operation: "SHOT_PROMPT_VERSION_GENERATE",
      submittedAt,
    },
  );
}

export async function resumeShotGeneration(
  projectId: string,
  shotId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/resume`,
    { method: "POST", headers: { Accept: "application/json" } },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getShotGenerationStatus(
  projectId: string,
  shotId: string,
): Promise<ApiResult<ShotGenerationStatusResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/status`,
  );
  return {
    data: parseShotGenerationStatus(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export function getShotVideoUrl(
  projectId: string,
  shotId: string,
  version: number,
): string {
  if (!isPositiveInteger(version)) {
    throw new ApiClientError({
      message: "镜头版本无效。",
      code: "INVALID_SHOT_VERSION",
    });
  }
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/versions/${encodeURIComponent(String(version))}/video`;
}

export async function getAssembly(
  projectId: string,
): Promise<ApiResult<AssemblyDetail>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/assembly`,
  );
  return {
    data: parseAssemblyDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getAssemblyReadiness(
  projectId: string,
): Promise<ApiResult<AssemblyReadiness>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/assembly/readiness`,
  );
  return {
    data: parseAssemblyReadiness(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function createAssemblyPlan(
  projectId: string,
): Promise<ApiResult<AssemblyPlan>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/assembly/plan`,
    { method: "POST", headers: { Accept: "application/json" } },
  );
  return {
    data: parseAssemblyPlan(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function executeAssembly(
  projectId: string,
  assemblyVersion: number,
): Promise<ApiResult<TaskRecord>> {
  if (!isPositiveInteger(assemblyVersion)) {
    throw new ApiClientError({
      message: "Assembly Plan 版本无效。",
      code: "INVALID_ASSEMBLY_VERSION",
    });
  }
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/assembly/execute`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ assembly_version: assemblyVersion }),
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function resumeAssembly(
  projectId: string,
  assemblyVersion: number,
): Promise<ApiResult<TaskRecord>> {
  if (!isPositiveInteger(assemblyVersion)) {
    throw new ApiClientError({
      message: "Assembly Plan 版本无效。",
      code: "INVALID_ASSEMBLY_VERSION",
    });
  }
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/assembly/resume`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ assembly_version: assemblyVersion }),
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export function getAssemblyVideoUrl(projectId: string): string {
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/assembly/video`;
}

export function getAssemblyVersionVideoUrl(
  projectId: string,
  version: number,
): string {
  if (!isPositiveInteger(version)) {
    throw new ApiClientError({
      message: "成片版本无效。",
      code: "INVALID_ASSEMBLY_VERSION",
    });
  }
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/assembly/versions/${encodeURIComponent(String(version))}/video`;
}

export async function getVoice(
  projectId: string,
): Promise<ApiResult<VoiceDetail>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/voice`,
  );
  return {
    data: parseVoiceDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getVoiceOptions(
  projectId: string,
): Promise<ApiResult<VoiceOptionsResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/voice/options`,
  );
  return {
    data: parseVoiceOptions(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function preflightVoice(
  projectId: string,
  payload: VoicePreflightRequest,
): Promise<ApiResult<VoicePreflightResponse>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/voice/preflight`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return {
    data: parseVoicePreflight(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

async function submitVoice(
  projectId: string,
  payload: VoiceGenerateRequest,
  expectedNextVersion: number,
  action: "generate" | "regenerate",
): Promise<ApiResult<TaskRecord>> {
  if (!isPositiveInteger(expectedNextVersion)) {
    throw new ApiClientError({ message: "Voice 版本无效。", code: "INVALID_VOICE_VERSION" });
  }
  const targetId = `voice_v${String(expectedNextVersion).padStart(3, "0")}`;
  return submitPaidVoiceTask(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/voice/${action}`,
    payload,
    { projectId, targetId, submittedAt: Date.now() },
  );
}

export function generateVoice(
  projectId: string,
  payload: VoiceGenerateRequest,
  expectedNextVersion: number,
): Promise<ApiResult<TaskRecord>> {
  return submitVoice(projectId, payload, expectedNextVersion, "generate");
}

export function regenerateVoice(
  projectId: string,
  payload: VoiceGenerateRequest,
  expectedNextVersion: number,
): Promise<ApiResult<TaskRecord>> {
  return submitVoice(projectId, payload, expectedNextVersion, "regenerate");
}

export async function getVoiceHistory(
  projectId: string,
): Promise<ApiResult<VoiceHistoryResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/voice/history`,
  );
  return {
    data: parseVoiceHistory(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getVoiceVersion(
  projectId: string,
  version: number,
): Promise<ApiResult<VoiceDetail>> {
  if (!isPositiveInteger(version)) {
    throw new ApiClientError({ message: "Voice 版本无效。", code: "INVALID_VOICE_VERSION" });
  }
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/voice/versions/${version}`,
  );
  return {
    data: parseVoiceDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export function getVoiceVersionAudioUrl(projectId: string, version: number): string {
  if (!isPositiveInteger(version)) {
    throw new ApiClientError({ message: "Voice 版本无效。", code: "INVALID_VOICE_VERSION" });
  }
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/post-production/voice/versions/${version}/audio`;
}

export async function acceptVoiceTiming(
  projectId: string,
  expectedVoiceVersion: number,
): Promise<ApiResult<VoiceDetail>> {
  if (!isPositiveInteger(expectedVoiceVersion)) {
    throw new ApiClientError({ message: "Voice 版本无效。", code: "INVALID_VOICE_VERSION" });
  }
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/voice/timing-acceptance`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ expected_voice_version: expectedVoiceVersion, accepted: true }),
    },
  );
  return {
    data: parseVoiceDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export function getVoiceAudioUrl(projectId: string): string {
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/post-production/voice/audio`;
}

export async function getSubtitle(
  projectId: string,
): Promise<ApiResult<SubtitleDetail>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/subtitle`,
  );
  return {
    data: parseSubtitleDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getSubtitleOptions(
  projectId: string,
): Promise<ApiResult<SubtitleOptionsResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/subtitle/options`,
  );
  return {
    data: parseSubtitleOptions(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

async function submitSubtitle(
  projectId: string,
  payload: SubtitleGenerateRequest,
  action: "generate" | "regenerate",
): Promise<ApiResult<SubtitleDetail>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/subtitle/${action}`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return {
    data: parseSubtitleDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export function generateSubtitle(
  projectId: string,
  payload: SubtitleGenerateRequest,
): Promise<ApiResult<SubtitleDetail>> {
  return submitSubtitle(projectId, payload, "generate");
}

export function regenerateSubtitle(
  projectId: string,
  payload: SubtitleGenerateRequest,
): Promise<ApiResult<SubtitleDetail>> {
  return submitSubtitle(projectId, payload, "regenerate");
}

export async function getSubtitleHistory(
  projectId: string,
): Promise<ApiResult<SubtitleHistoryResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/subtitle/history`,
  );
  return {
    data: parseSubtitleHistory(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getSubtitleVersion(
  projectId: string,
  version: number,
): Promise<ApiResult<SubtitleDetail>> {
  if (!isPositiveInteger(version)) {
    throw new ApiClientError({ message: "Subtitle 版本无效。", code: "INVALID_SUBTITLE_VERSION" });
  }
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/subtitle/versions/${version}`,
  );
  return {
    data: parseSubtitleDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getMusic(
  projectId: string,
): Promise<ApiResult<MusicDetail>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/post-production/music`,
  );
  return {
    data: parseMusicDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export function getMusicAudioUrl(projectId: string): string {
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/post-production/music/audio`;
}

export async function getExport(
  projectId: string,
): Promise<ApiResult<ExportDetail>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/export`,
  );
  return {
    data: parseExportDetail(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export function getExportVideoUrl(projectId: string): string {
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/export/video`;
}

export async function getTask(
  taskId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await get<unknown>(
    `/api/tasks/${encodeURIComponent(taskId)}`,
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function getProjectTasks(
  projectId: string,
): Promise<ApiResult<ProjectTaskListResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/tasks`,
  );
  return {
    data: parseProjectTaskList(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function generateCreative(
  projectId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/creative/generate`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function retryCreative(
  projectId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/creative/retry`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function generateStoryboard(
  projectId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/storyboard/generate`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function generateVideoPrompts(
  projectId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/video-prompts/generate`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function reviseVideoPrompts(
  projectId: string,
  feedback: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/video-prompts/revise`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ feedback }),
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function regenerateVideoPrompts(
  projectId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/video-prompts/regenerate`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function reviseStoryboard(
  projectId: string,
  feedback: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/storyboard/revise`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ feedback }),
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function regenerateStoryboard(
  projectId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/storyboard/regenerate`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function reviseCreative(
  projectId: string,
  feedback: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/creative/revise`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ feedback }),
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function regenerateCreative(
  projectId: string,
): Promise<ApiResult<TaskRecord>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/creative/regenerate`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseTaskRecord(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function approveCreative(
  projectId: string,
): Promise<ApiResult<ProjectWorkflowResponse>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/creative/approve`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseProjectWorkflow(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function approveStoryboard(
  projectId: string,
): Promise<ApiResult<ProjectWorkflowResponse>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/storyboard/approve`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseProjectWorkflow(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function approveVideoPrompts(
  projectId: string,
): Promise<ApiResult<ProjectWorkflowResponse>> {
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/planning/video-prompts/approve`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
    },
  );
  return {
    data: parseProjectWorkflow(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}

export async function createProject(
  project: CreateProjectRequest,
): Promise<ApiResult<CreateProjectResponse>> {
  const result = await request("/api/projects", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(project),
  });
  return {
    data: parseCreateProjectResponse(result.data, result.correlationId),
    correlationId: result.correlationId,
  };
}
