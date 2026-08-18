import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getCapabilities,
  getHealth,
  getProjects,
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
});
