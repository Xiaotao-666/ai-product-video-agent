import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, getCapabilities, getHealth } from "./client";

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
});
