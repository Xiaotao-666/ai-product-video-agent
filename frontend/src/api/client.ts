import { API_BASE_URL } from "../config";
import type {
  ApiResult,
  AssemblyState,
  AvailableAction,
  BackendErrorResponse,
  CapabilitiesResponse,
  ComponentState,
  CreateProjectRequest,
  CreateProjectResponse,
  FinalExportState,
  HealthResponse,
  PostProductionState,
  ProjectDetail,
  ProjectListResponse,
  ProjectRequest,
  ProjectSummary,
  ProjectWorkflowResponse,
  ShotStageState,
  StageState,
  WorkflowPhase,
  WorkflowStages,
  WorkflowState,
} from "./types";

const CORRELATION_HEADER = "X-Correlation-ID";
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

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
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
