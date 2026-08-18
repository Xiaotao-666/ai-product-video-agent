import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  createProject,
  getCapabilities,
  getHealth,
  getProject,
  getProjects,
  getProjectWorkflow,
} from "./client";

function responseOf(
  payload: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response;
}

const projectListPayload = {
  projects: [
    {
      project_id: "project-1",
      name: "LEE柠檬",
      workflow_phase: "COMPLETED",
      status: "COMPLETED",
      updated_at: "2026-08-17T20:58:53+08:00",
      assembly: {
        status: "COMPLETED",
        needs_update: false,
        version: 2,
      },
      final_export: {
        status: "COMPLETED",
        version: 1,
        created_at: "2026-08-17T20:57:00+08:00",
        stale: false,
      },
    },
  ],
};

const createRequest = {
  product_name: "测试柠檬",
  product_description: "新鲜柠檬饮料",
  user_notes: "不要出现人物",
  duration_seconds: 18,
  video_style: "清爽、年轻",
  video_purpose: "提升产品知名度",
};

const createResponse = {
  project_id: "0123456789abcdef0123456789abcdef",
  name: "测试柠檬",
  workflow_phase: "CREATIVE",
  status: "NOT_STARTED",
  created_at: "2026-08-18T10:00:00+08:00",
  updated_at: "2026-08-18T10:00:00+08:00",
};

const workflowStagesPayload = {
  creative: { status: "APPROVED" },
  storyboard: { status: "APPROVED" },
  video_prompt: { status: "APPROVED" },
  shots: { status: "COMPLETED", approved: 3, total: 3 },
  assembly: { status: "COMPLETED", needs_update: false, version: 2 },
  voice: { status: "COMPLETED", version: 1 },
  subtitle: { status: "NOT_STARTED", version: null },
  music: { status: "COMPLETED", version: 2 },
  export: {
    status: "COMPLETED",
    version: 3,
    created_at: "2026-08-18T14:20:00+08:00",
    stale: false,
  },
};

const projectWorkflowPayload = {
  project_id: "LEE柠檬",
  workflow_phase: "COMPLETED",
  status: "COMPLETED",
  stages: workflowStagesPayload,
  available_actions: [],
  updated_at: "2026-08-18T14:30:00+08:00",
};

const projectDetailPayload = {
  project_id: "LEE柠檬",
  name: "LEE柠檬清爽饮品",
  request: {
    product_name: "LEE柠檬",
    product_description: "新鲜柠檬饮料",
    user_notes: "不要出现人物",
    duration_seconds: 18,
    video_style: "清爽、年轻",
    video_purpose: "提升产品知名度",
  },
  workflow: {
    workflow_phase: "COMPLETED",
    status: "COMPLETED",
    stages: workflowStagesPayload,
    available_actions: [],
  },
  assembly: workflowStagesPayload.assembly,
  post_production: {
    status: "RUNNING",
    voice: workflowStagesPayload.voice,
    subtitle: workflowStagesPayload.subtitle,
    music: workflowStagesPayload.music,
  },
  final_export: workflowStagesPayload.export,
  updated_at: "2026-08-18T14:30:00+08:00",
};

describe("API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads health and preserves the response correlation ID", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            status: "ok",
            service: "ai-product-video-agent",
            api_version: "v1",
          },
          200,
          { "X-Correlation-ID": "req_test" },
        ),
      ),
    );
    const result = await getHealth();
    expect(result.data.api_version).toBe("v1");
    expect(result.correlationId).toBe("req_test");
  });

  it("maps the backend safe error DTO", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "PROJECT_ERROR",
              code: "PROJECT_BUSY",
              message: "项目当前正在执行其他操作，请稍后重试。",
              retryable: true,
              correlation_id: "req_busy",
            },
          },
          409,
        ),
      ),
    );
    await expect(getCapabilities()).rejects.toMatchObject({
      status: 409,
      code: "PROJECT_BUSY",
      correlationId: "req_busy",
      retryable: true,
    });
  });

  it("converts network failures into a non-sensitive error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValue(new Error("D:\\private MINIMAX_API_KEY=secret")),
    );
    const error = await getHealth().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: null });
    expect((error as Error).message).not.toContain("MINIMAX_API_KEY");
    expect((error as Error).message).not.toContain("D:\\");
  });

  it("reads and validates the projects response", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        responseOf(projectListPayload, 200, {
          "X-Correlation-ID": "req_projects",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getProjects();

    expect(result.data.projects).toHaveLength(1);
    expect(result.data.projects[0]?.name).toBe("LEE柠檬");
    expect(result.correlationId).toBe("req_projects");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/projects"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("maps the backend safe error DTO for the projects request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "PROJECT_ERROR",
              code: "PROJECT_LIST_UNAVAILABLE",
              message: "项目列表暂时不可用。",
              retryable: true,
              correlation_id: "req_projects_error",
            },
          },
          503,
        ),
      ),
    );

    await expect(getProjects()).rejects.toMatchObject({
      status: 503,
      code: "PROJECT_LIST_UNAVAILABLE",
      message: "项目列表暂时不可用。",
      correlationId: "req_projects_error",
      retryable: true,
    });
  });

  it("converts a projects network failure into a safe error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("D:\\secret\\project.json")),
    );

    const error = await getProjects().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: null });
    expect((error as Error).message).not.toContain("D:\\");
  });

  it("rejects malformed projects JSON and preserves correlation ID", async () => {
    const response = responseOf(null, 200, {
      "X-Correlation-ID": "req_bad_json",
    });
    vi.mocked(response.json).mockRejectedValue(new SyntaxError("bad JSON"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(getProjects()).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
      correlationId: "req_bad_json",
      message: "Backend 返回了无法读取的响应。",
    });
  });

  it("rejects an invalid projects DTO without exposing its fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            projects: [
              {
                project_id: "project-1",
                local_path: "D:\\private\\project.json",
              },
            ],
          },
          200,
          { "X-Correlation-ID": "req_invalid_projects" },
        ),
      ),
    );

    const error = await getProjects().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({
      code: "INVALID_RESPONSE",
      correlationId: "req_invalid_projects",
    });
    expect((error as Error).message).not.toContain("D:\\private");
  });

  it("gets and validates a project detail with an encoded Chinese ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(
        {
          ...projectDetailPayload,
          local_path: "D:\\private\\project.json",
          credential_env_name: "MINIMAX_API_KEY",
        },
        200,
        { "X-Correlation-ID": "req_detail" },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getProject("LEE柠檬");

    expect(result.data.name).toBe("LEE柠檬清爽饮品");
    expect(result.data.request.duration_seconds).toBe(18);
    expect(result.correlationId).toBe("req_detail");
    expect(result.data).not.toHaveProperty("local_path");
    expect(result.data).not.toHaveProperty("credential_env_name");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/projects/LEE%E6%9F%A0%E6%AA%AC"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("gets and validates project workflow with an encoded Chinese ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(
        {
          ...projectWorkflowPayload,
          candidate_state: "CANDIDATE_APPROVE",
          raw_error: "hidden",
        },
        200,
        { "X-Correlation-ID": "req_workflow" },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getProjectWorkflow("LEE柠檬");

    expect(result.data.workflow_phase).toBe("COMPLETED");
    expect(result.data.stages.shots).toEqual({
      status: "COMPLETED",
      approved: 3,
      total: 3,
    });
    expect(result.correlationId).toBe("req_workflow");
    expect(result.data).not.toHaveProperty("candidate_state");
    expect(result.data).not.toHaveProperty("raw_error");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/projects/LEE%E6%9F%A0%E6%AA%AC/workflow",
      ),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it.each([
    [404, "PROJECT_NOT_FOUND"],
    [422, "PROJECT_DATA_CORRUPT"],
  ])("maps project detail HTTP %i with code %s", async (status, code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "PROJECT_ERROR",
              code,
              message: "安全项目错误",
              retryable: false,
              correlation_id: `req_detail_${status}`,
            },
          },
          status,
        ),
      ),
    );

    await expect(getProject("project-1")).rejects.toMatchObject({
      status,
      code,
      correlationId: `req_detail_${status}`,
    });
  });

  it.each([
    [404, "PROJECT_NOT_FOUND"],
    [422, "PROJECT_DATA_UNSUPPORTED"],
  ])("maps project workflow HTTP %i with code %s", async (status, code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "PROJECT_ERROR",
              code,
              message: "安全工作流错误",
              retryable: false,
              correlation_id: `req_workflow_${status}`,
            },
          },
          status,
        ),
      ),
    );

    await expect(getProjectWorkflow("project-1")).rejects.toMatchObject({
      status,
      code,
      correlationId: `req_workflow_${status}`,
    });
  });

  it.each([
    ["project detail", () => getProject("project-1")],
    ["project workflow", () => getProjectWorkflow("project-1")],
  ])("converts %s network failure into a safe error", async (_name, call) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("D:\\private API_KEY=hidden")),
    );

    const error = await call().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: null });
    expect((error as Error).message).not.toContain("D:\\");
    expect((error as Error).message).not.toContain("API_KEY");
  });

  it.each([
    ["project detail", () => getProject("project-1")],
    ["project workflow", () => getProjectWorkflow("project-1")],
  ])("rejects malformed %s JSON with correlation ID", async (_name, call) => {
    const response = responseOf(null, 200, {
      "X-Correlation-ID": "req_malformed_workspace",
    });
    vi.mocked(response.json).mockRejectedValue(new SyntaxError("bad JSON"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(call()).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
      correlationId: "req_malformed_workspace",
    });
  });

  it("creates a project with POST JSON and preserves correlation ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(createResponse, 201, {
        "X-Correlation-ID": "req_create",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProject(createRequest);

    expect(result.data).toEqual(createResponse);
    expect(result.correlationId).toBe("req_create");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/projects");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      Accept: "application/json",
      "Content-Type": "application/json",
    });
    expect(JSON.parse(String(init.body))).toEqual(createRequest);
  });

  it.each([
    [422, "INVALID_VIDEO_DURATION"],
    [409, "PROJECT_BUSY"],
    [500, "PROJECT_CREATE_FAILED"],
  ])("maps create error HTTP %i with code %s", async (status, code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: status === 422 ? "VALIDATION_ERROR" : "PROJECT_ERROR",
              code,
              message: "安全错误消息",
              retryable: status === 409,
              correlation_id: `req_${status}`,
            },
          },
          status,
        ),
      ),
    );

    await expect(createProject(createRequest)).rejects.toMatchObject({
      status,
      code,
      message: "安全错误消息",
      correlationId: `req_${status}`,
      retryable: status === 409,
    });
  });

  it("converts create network failure into a safe error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("D:\\private API_KEY=hidden")),
    );

    const error = await createProject(createRequest).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: null });
    expect((error as Error).message).not.toContain("D:\\");
    expect((error as Error).message).not.toContain("API_KEY");
  });

  it("rejects malformed create JSON and keeps the correlation ID", async () => {
    const response = responseOf(null, 201, {
      "X-Correlation-ID": "req_bad_create_json",
    });
    vi.mocked(response.json).mockRejectedValue(new SyntaxError("bad JSON"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(createProject(createRequest)).rejects.toMatchObject({
      status: 201,
      code: "INVALID_RESPONSE",
      correlationId: "req_bad_create_json",
    });
  });

  it("sanitizes create response extensions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            ...createResponse,
            local_path: "D:\\private\\project.json",
            provider_secret: "hidden",
          },
          201,
        ),
      ),
    );

    const result = await createProject(createRequest);

    expect(result.data).toEqual(createResponse);
    expect(result.data).not.toHaveProperty("local_path");
    expect(result.data).not.toHaveProperty("provider_secret");
  });
});
