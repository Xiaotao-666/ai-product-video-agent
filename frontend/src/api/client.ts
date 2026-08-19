import { API_BASE_URL } from "../config";
import type {
  ApiResult,
  AssemblyDetail,
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
  PostProductionState,
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
  ShotStageState,
  ShotSummary,
  ShotVersion,
  ShotVersionRole,
  ShotVisualInputMode,
  StageState,
  StoryboardContentResponse,
  StoryboardPlanningContent,
  StoryboardShotContent,
  SubtitleCue,
  SubtitleDetail,
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
const TASK_ID_PATTERN = /^task_[0-9a-f]{32}$/;
const TASK_STATUSES: ReadonlySet<string> = new Set([
  "QUEUED",
  "RUNNING",
  "SUCCEEDED",
  "FAILED",
  "INTERRUPTED",
  "CANCELLED",
]);
const TASK_OPERATIONS: ReadonlySet<string> = new Set([
  "CREATIVE_GENERATE",
  "CREATIVE_RETRY",
  "CREATIVE_REVISE",
  "CREATIVE_REGENERATE",
  "STORYBOARD_GENERATE",
  "STORYBOARD_REVISE",
  "STORYBOARD_REGENERATE",
  "VIDEO_PROMPT_GENERATE",
  "VIDEO_PROMPT_REVISE",
  "VIDEO_PROMPT_REGENERATE",
  "SHOT_GENERATE",
  "SHOT_RESUME",
  "ASSEMBLY",
  "VOICE_GENERATE",
  "SUBTITLE_GENERATE",
  "FINAL_EXPORT",
]);
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

  constructor(options: {
    message: string;
    status?: number | null;
    code: string;
    correlationId?: string | null;
    retryable?: boolean;
  }) {
    super(options.message);
    this.name = "ApiClientError";
    this.status = options.status ?? null;
    this.code = options.code;
    this.correlationId = options.correlationId ?? null;
    this.retryable = options.retryable ?? false;
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
    typeof value.status !== "string" ||
    !isNullablePositiveInteger(value.official_version) ||
    !isNullablePositiveInteger(value.pending_review_version) ||
    !isNonNegativeInteger(value.version_count) ||
    !isNonNegativeInteger(value.generation_count)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头摘要。", correlationId);
  }
  return {
    shot_id: parseShotId(value.shot_id, correlationId),
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    official_version: value.official_version,
    pending_review_version: value.pending_review_version,
    version_count: value.version_count,
    generation_count: value.generation_count,
  };
}

function parseShotListResponse(
  value: unknown,
  correlationId: string | null,
): ShotListResponse {
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !Array.isArray(value.shots)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头列表。", correlationId);
  }
  return {
    project_id: parseContentText(value.project_id, correlationId) ?? "",
    status: parseContentText(value.status, correlationId) ?? "UNKNOWN",
    shots: value.shots.map((shot) => parseShotSummary(shot, correlationId)),
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
    created_at: parseContentText(value.created_at, correlationId),
    prompt: parseShotPrompt(value.prompt, correlationId),
    generation: parseShotGeneration(value.generation, correlationId),
    video_available: value.video_available,
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
    !Array.isArray(value.versions)
  ) {
    return invalidResponse("Backend 返回了无法读取的镜头详情。", correlationId);
  }
  const versions = value.versions.map((version) =>
    parseShotVersion(version, correlationId),
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
    versions,
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
    "WAITING_REVIEW", "FAILED", "INTERRUPTED", "SUBMISSION_UNKNOWN",
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
    provider_submission_known: value.provider_submission_known,
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
    !Array.isArray(value.shots)
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
    timing_mode: parseContentText(value.timing_mode, correlationId),
    cue_level_alignment: value.cue_level_alignment,
    script_matches_storyboard: value.script_matches_storyboard,
    calibration_status: parseCalibrationStatus(
      value.calibration_status,
      correlationId,
    ),
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
  if (
    !isRecord(value) ||
    typeof value.project_id !== "string" ||
    typeof value.status !== "string" ||
    !isNullablePositiveInteger(value.version) ||
    !isNullableString(value.source) ||
    !isNullableString(value.timing_source) ||
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
    created_at: parseContentText(value.created_at, correlationId),
    cue_count: value.cue_count,
    content_available: value.content_available,
    cues,
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
  return typeof value === "string" && TASK_STATUSES.has(value);
}

function isTaskOperation(value: unknown): value is TaskOperation {
  return typeof value === "string" && TASK_OPERATIONS.has(value);
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
  const isTerminal = ["SUCCEEDED", "FAILED", "INTERRUPTED", "CANCELLED"].includes(
    value.status,
  );
  if (
    (value.status === "QUEUED" &&
      (value.started_at !== null || value.finished_at !== null)) ||
    (value.status === "RUNNING" &&
      (value.started_at === null || value.finished_at !== null)) ||
    (isTerminal && value.finished_at === null) ||
    (["FAILED", "INTERRUPTED"].includes(value.status) && error === null) ||
    (!["FAILED", "INTERRUPTED"].includes(value.status) && error !== null) ||
    (result !== null && value.status !== "SUCCEEDED")
  ) {
    return invalidResponse("Backend 返回了状态不一致的任务记录。", correlationId);
  }

  return {
    task_id: value.task_id,
    project_id: value.project_id,
    operation: value.operation,
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
    });
  }
}

async function request(
  path: string,
  init: RequestInit,
): Promise<ApiResult<unknown>> {
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

export async function getShotGenerationOptions(
  projectId: string,
  shotId: string,
): Promise<ApiResult<GenerationOptionsResponse>> {
  const result = await get<unknown>(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/options`,
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
  const result = await request(
    `/api/projects/${encodeURIComponent(projectId)}/shots/${encodeURIComponent(shotId)}/generation/start`,
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

export function getAssemblyVideoUrl(projectId: string): string {
  return `${API_BASE_URL}/api/projects/${encodeURIComponent(projectId)}/assembly/video`;
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
