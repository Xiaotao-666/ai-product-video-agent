import { afterEach, describe, expect, it, vi } from "vitest";
import { getShotGenerationStatus, retryFailedShotGeneration } from "./client";
import type { FailedRetryRequest } from "./types";

const payload: FailedRetryRequest = {
  intent: "FAILED_RETRY", model_selection: "MANUAL", requested_model: "MiniMax-H3",
  duration: 6, resolution: "2K", visual_input: { mode: "none", asset_ids: [] },
  preflight_fingerprint: "a".repeat(64), confirm_external_video_call: true,
};
const task = {
  task_id: "task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", project_id: "project-a",
  operation: "SHOT_GENERATE", target_id: "shot_01", status: "QUEUED",
  created_at: new Date().toISOString(), started_at: null, finished_at: null,
  correlation_id: "req_failed_retry", error: null, result: null,
};
const response = (body: unknown, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json", ...headers } });
afterEach(() => vi.unstubAllGlobals());

describe("failed retry reuses paid Shot transport", () => {
  it("sends one safe payload and retains FAILED_RETRY status intent", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(response(task, 202)).mockResolvedValueOnce(response({
      project_id: "project-a", shot_id: "shot_01", state: "WAITING_REVIEW",
      resume_available: false, resume_kind: null, video_version: 2, prompt_version: 2,
      provider_submission_known: true, generation_intent: "FAILED_RETRY",
    }));
    vi.stubGlobal("fetch", fetch);
    expect((await retryFailedShotGeneration("project-a", "shot_01", payload)).data.operation).toBe("SHOT_GENERATE");
    expect(JSON.parse(fetch.mock.calls[0][1].body)).toEqual(payload);
    expect(fetch.mock.calls[0][0]).toContain("/generation/failed-retry");
    expect(fetch.mock.calls[0][1].body).not.toMatch(/provider_task_id|file_id|credential|path|api_key/);
    expect((await getShotGenerationStatus("project-a", "shot_01")).data.generation_intent).toBe("FAILED_RETRY");
  });
  it.each(["location", "project", "unreadable"] as const)("reconciles malformed 202 via %s without another POST", async (kind) => {
    const accepted = response({}, 202, {
      Location: `/api/tasks/${task.task_id}`, "X-Correlation-ID": task.correlation_id,
    });
    vi.spyOn(accepted, "json").mockRejectedValue(new Error("truncated"));
    const fetch = vi.fn().mockResolvedValueOnce(accepted)
      .mockResolvedValueOnce(kind === "location" ? response(task) : response({}, 503));
    if (kind !== "location") fetch.mockResolvedValueOnce(kind === "project"
      ? response({ project_id: "project-a", tasks: [task] }) : response({}, 503));
    vi.stubGlobal("fetch", fetch);
    const result = retryFailedShotGeneration("project-a", "shot_01", payload);
    if (kind === "unreadable") await expect(result).rejects.toMatchObject({
      requestAccepted: true, status: 202, code: "ACCEPTED_TASK_STATUS_UNREADABLE",
    });
    else expect((await result).data.task_id).toBe(task.task_id);
    expect(fetch.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(fetch).toHaveBeenCalledTimes(kind === "location" ? 2 : 3);
  });
});
