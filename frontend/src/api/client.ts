import { API_BASE_URL } from "../config";
import type {
  ApiResult,
  BackendErrorResponse,
  CapabilitiesResponse,
  HealthResponse,
} from "./types";

const CORRELATION_HEADER = "X-Correlation-ID";

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

async function get<T>(path: string): Promise<ApiResult<T>> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: "GET",
      headers: { Accept: "application/json" },
    });
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
    data: payload as T,
    correlationId: headerCorrelationId,
  };
}

export function getHealth(): Promise<ApiResult<HealthResponse>> {
  return get<HealthResponse>("/api/health");
}

export function getCapabilities(): Promise<ApiResult<CapabilitiesResponse>> {
  return get<CapabilitiesResponse>("/api/capabilities");
}
