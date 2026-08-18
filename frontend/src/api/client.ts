import { API_BASE_URL } from "../config";
import type {
  ApiResult,
  BackendErrorResponse,
  CapabilitiesResponse,
  CreateProjectRequest,
  CreateProjectResponse,
  HealthResponse,
  ProjectListResponse,
  ProjectSummary,
  WorkflowPhase,
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

function isAssemblyState(value: unknown): boolean {
  return Boolean(
    isRecord(value) &&
      typeof value.status === "string" &&
      typeof value.needs_update === "boolean" &&
      isNullableNumber(value.version),
  );
}

function isFinalExportState(value: unknown): boolean {
  return Boolean(
    isRecord(value) &&
      typeof value.status === "string" &&
      isNullableNumber(value.version) &&
      isNullableString(value.created_at) &&
      typeof value.stale === "boolean",
  );
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
