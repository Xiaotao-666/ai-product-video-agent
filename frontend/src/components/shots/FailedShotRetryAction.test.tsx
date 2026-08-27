import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  ApiClientError, getFailedRetryOptions, getProjectTasks, getReferenceAssets, getTask,
  preflightFailedRetry, resumeShotGeneration, retryFailedShotGeneration,
} from "../../api/client";
import type { FailedRetryOptions, FailedRetryPreflight, FailureRecovery, TaskRecord } from "../../api/types";
import { FailedShotRetryAction } from "./FailedShotRetryAction";

vi.mock("../../api/client", async (original) => ({
  ...await original<typeof import("../../api/client")>(),
  getFailedRetryOptions: vi.fn(), getProjectTasks: vi.fn(), getReferenceAssets: vi.fn(),
  getTask: vi.fn(), preflightFailedRetry: vi.fn(), resumeShotGeneration: vi.fn(),
  retryFailedShotGeneration: vi.fn(),
}));

const recovery: FailureRecovery = {
  state: "RETRY_ALLOWED", reason_code: "VIDEO_PROVIDER_INVALID_REQUEST", can_retry: true,
  requires_new_preflight: true, requires_external_cost_confirmation: true,
  safe_message: "当前套餐不支持所选模型配置，请调整模型、时长或分辨率后重新尝试。",
  last_attempt_version: 1, active_task_id: null,
};
const options: FailedRetryOptions = {
  project_id: "project-a", eligible: true, failure_recovery: recovery,
  shot: { shot_id: "shot_01", prompt_version: 2, next_video_version: 2, duration_seconds: 10, resolution: "768P" },
  selection_modes: ["AUTO", "MANUAL"], paid_call_required: true, issues: [],
  visual_input_modes: [
    { mode: "none", display_name: "无参考图", description: "", compatible_model_ids: ["MiniMax-H3"] },
    { mode: "reference_asset", display_name: "主体参考", description: "", compatible_model_ids: ["MiniMax-H3"] },
    { mode: "first_frame", display_name: "首帧", description: "", compatible_model_ids: ["MiniMax-H3"] },
  ],
  models: [{
    model_id: "MiniMax-H3", display_name: "MiniMax H3", provider: "minimax", provider_display_name: "MiniMax",
    api_version: "v2", available: true, supported_resolutions: ["768P", "2K"],
    supported_durations: [], min_duration: 4, max_duration: 15,
    supported_visual_input_modes: ["none", "reference_asset", "first_frame"],
  }],
};
const ready: FailedRetryPreflight = {
  intent: "FAILED_RETRY", failure_recovery: recovery, ready: true,
  shot: { ...options.shot, duration_seconds: 6, resolution: "2K" },
  resolved: {
    provider: "minimax", provider_display_name: "MiniMax", model: "MiniMax-H3",
    model_display_name: "MiniMax H3", api_version: "v2", generation_mode: "text_to_video",
    generation_mode_display_name: "纯文本生成", visual_input_mode: "none", model_selection: "MANUAL",
  },
  selected_asset_ids: [], issues: [], warnings: [], paid_call_required: true,
  provider_available: true, preflight_fingerprint: "a".repeat(64),
};
const queued: TaskRecord = {
  task_id: "task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", project_id: "project-a",
  operation: "SHOT_GENERATE", target_id: "shot_01", status: "QUEUED",
  created_at: new Date().toISOString(), started_at: null, finished_at: null,
  correlation_id: "req_retry", error: null, result: null,
};
const completed = vi.fn();
function show(value = recovery) {
  return render(<FailedShotRetryAction projectId="project-a" shotId="shot_01" recovery={value} onCompleted={completed} />);
}
async function prepare() {
  fireEvent.click(screen.getByRole("button", { name: "调整配置并重新尝试" }));
  await screen.findByRole("heading", { name: "Failed Retry Preparation" });
  fireEvent.change(screen.getByLabelText("重试模型"), { target: { value: "MiniMax-H3" } });
  fireEvent.change(screen.getByLabelText("重试时长"), { target: { value: "6" } });
  fireEvent.change(screen.getByLabelText("重试分辨率"), { target: { value: "2K" } });
}
async function confirmation() {
  await prepare();
  fireEvent.click(screen.getByRole("button", { name: "检查重试配置" }));
  await screen.findByRole("heading", { name: "配置检查通过" });
  fireEvent.click(screen.getByRole("button", { name: "查看重试确认" }));
  return screen.getByRole("dialog");
}

beforeEach(() => {
  vi.resetAllMocks(); sessionStorage.clear();
  vi.mocked(getFailedRetryOptions).mockResolvedValue({ data: options, correlationId: null });
  vi.mocked(getReferenceAssets).mockResolvedValue({ data: { project_id: "project-a", assets: [
    { asset_id: "ref_001", filename: "product.png", media_type: "image/png", width: 100, height: 100 },
  ] }, correlationId: null });
  vi.mocked(getProjectTasks).mockResolvedValue({ data: { project_id: "project-a", tasks: [] }, correlationId: null });
  vi.mocked(preflightFailedRetry).mockResolvedValue({ data: ready, correlationId: null });
  vi.mocked(retryFailedShotGeneration).mockResolvedValue({ data: queued, correlationId: "req_retry" });
  vi.mocked(getTask).mockResolvedValue({ data: queued, correlationId: null });
});

describe("explicit failed Shot retry", () => {
  it("shows safe failure and unavailable video without making any POST", () => {
    show();
    expect(screen.getByText("未生成可用视频")).toBeInTheDocument();
    expect(screen.getByText(recovery.safe_message)).toBeInTheDocument();
    expect(screen.getByText("上一次尝试：v001")).toBeInTheDocument();
    expect(screen.queryByText("已就绪")).not.toBeInTheDocument();
    expect(retryFailedShotGeneration).not.toHaveBeenCalled();
    expect(preflightFailedRetry).not.toHaveBeenCalled();
  });
  it("requires explicit configuration and preflight; cancel creates no task", async () => {
    show();
    const dialog = await confirmation();
    expect(within(dialog).getAllByText("v002")).toHaveLength(2);
    expect(within(dialog).getByText("MiniMax H3")).toBeInTheDocument();
    expect(within(dialog).getByText("6 秒")).toBeInTheDocument();
    expect(within(dialog).getByText("2K")).toBeInTheDocument();
    expect(within(dialog).getByText(/可能产生费用/)).toBeInTheDocument();
    expect(preflightFailedRetry).toHaveBeenCalledWith("project-a", "shot_01", {
      intent: "FAILED_RETRY", model_selection: "MANUAL", requested_model: "MiniMax-H3",
      duration: 6, resolution: "2K", visual_input: { mode: "none", asset_ids: [] },
    });
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(retryFailedShotGeneration).not.toHaveBeenCalled();
    expect(resumeShotGeneration).not.toHaveBeenCalled();
  });
  it("supports visual input and invalidates preflight after configuration edits", async () => {
    show(); await prepare();
    fireEvent.change(screen.getByLabelText("重试 Visual Input"), { target: { value: "reference_asset" } });
    fireEvent.change(screen.getByLabelText("重试参考素材"), { target: { value: "ref_001" } });
    fireEvent.click(screen.getByRole("button", { name: "检查重试配置" }));
    await screen.findByRole("heading", { name: "配置检查通过" });
    expect(vi.mocked(preflightFailedRetry).mock.calls[0][2].visual_input).toEqual({ mode: "reference_asset", asset_ids: ["ref_001"] });
    fireEvent.change(screen.getByLabelText("重试时长"), { target: { value: "10" } });
    expect(screen.queryByRole("button", { name: "查看重试确认" })).not.toBeInTheDocument();
    expect(retryFailedShotGeneration).not.toHaveBeenCalled();
  });
  it("confirms once, blocks double clicks and attaches SHOT_GENERATE", async () => {
    show(); const dialog = await confirmation();
    const button = within(dialog).getByRole("button", { name: "确认并重新生成" });
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(retryFailedShotGeneration).toHaveBeenCalledTimes(1));
    expect(vi.mocked(retryFailedShotGeneration).mock.calls[0][2]).toMatchObject({
      intent: "FAILED_RETRY", confirm_external_video_call: true, duration: 6, resolution: "2K",
      preflight_fingerprint: "a".repeat(64),
    });
    await screen.findByText(/Task · SHOT_GENERATE · QUEUED/);
    expect(screen.getByRole("button", { name: "调整配置并重新尝试" })).toBeDisabled();
  });
  it("refreshes business after success and clears the accepted barrier", async () => {
    vi.mocked(getTask).mockResolvedValue({ data: { ...queued, status: "SUCCEEDED",
      result: { resource_type: "SHOT_VIDEO", resource_id: "shot_01", version: 2 } }, correlationId: null });
    show(); const dialog = await confirmation();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并重新生成" }));
    await waitFor(() => expect(completed).toHaveBeenCalledTimes(1));
    expect(sessionStorage.getItem("shot-failed-retry:project-a:shot_01")).toBeNull();
    expect(screen.getByText(/等待审核/)).toBeInTheDocument();
    expect(retryFailedShotGeneration).toHaveBeenCalledTimes(1);
  });
  it("keeps unreadable 202 locked across refresh; never falls back to paid POST", async () => {
    vi.mocked(retryFailedShotGeneration).mockRejectedValue(new ApiClientError({
      code: "ACCEPTED_TASK_STATUS_UNREADABLE", message: "任务暂不可读", status: 202,
      requestAccepted: true, correlationId: "req_uncertain",
    }));
    const view = show(); const dialog = await confirmation();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并重新生成" }));
    await screen.findByText(/已进入请求恢复保护/);
    await waitFor(() => expect(screen.getByRole("button", { name: "调整配置并重新尝试" })).toBeDisabled());
    view.unmount(); show();
    fireEvent.click(screen.getByRole("button", { name: "检查已接受任务状态" }));
    await waitFor(() => expect(getProjectTasks).toHaveBeenCalled());
    expect(screen.getByRole("button", { name: "调整配置并重新尝试" })).toBeDisabled();
    expect(retryFailedShotGeneration).toHaveBeenCalledTimes(1);
  });
  it("attaches a known task after refresh without resubmitting", async () => {
    sessionStorage.setItem("shot-failed-retry:project-a:shot_01", JSON.stringify({
      taskId: queued.task_id, submittedAt: Date.now(), previousIds: [], correlationId: "req_retry",
    }));
    show();
    await screen.findByText(/Task · SHOT_GENERATE · QUEUED/);
    expect(getTask).toHaveBeenCalledWith(queued.task_id);
    expect(retryFailedShotGeneration).not.toHaveBeenCalled();
  });
  it("does not mistake the earlier failed task for the newly accepted request", async () => {
    sessionStorage.setItem("shot-failed-retry:project-a:shot_01", JSON.stringify({
      taskId: null, submittedAt: Date.now(), previousIds: [queued.task_id], correlationId: null,
    }));
    vi.mocked(getProjectTasks).mockResolvedValue({ data: { project_id: "project-a",
      tasks: [{ ...queued, status: "FAILED" }] }, correlationId: null });
    show(); await screen.findByText(/请求可能已被接受/);
    expect(screen.getByRole("button", { name: "调整配置并重新尝试" })).toBeDisabled();
    expect(completed).not.toHaveBeenCalled();
  });
  it.each(["RETRY_BLOCKED_SUBMISSION_UNKNOWN", "BLOCKED"] as const)("never offers ordinary retry for %s", (state) => {
    show({ ...recovery, state, can_retry: false });
    expect(screen.queryByRole("button", { name: "调整配置并重新尝试" })).not.toBeInTheDocument();
    if (state === "RETRY_BLOCKED_SUBMISSION_UNKNOWN") expect(screen.getByText(/外部请求状态未知/)).toBeInTheDocument();
  });
  it.each(["RESUME_AVAILABLE", "BUSINESS_ALREADY_COMPLETE"] as const)("only resumes existing progress for %s", async (state) => {
    vi.mocked(resumeShotGeneration).mockResolvedValue({ data: { ...queued, operation: "SHOT_RESUME" }, correlationId: null });
    show({ ...recovery, state, can_retry: false });
    expect(screen.queryByRole("button", { name: "调整配置并重新尝试" })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: state === "RESUME_AVAILABLE" ? /Resume/ : "恢复已有视频" }));
    await waitFor(() => expect(resumeShotGeneration).toHaveBeenCalledTimes(1));
    expect(retryFailedShotGeneration).not.toHaveBeenCalled();
  });
  it("requires a new preflight after a definitive stale rejection", async () => {
    vi.mocked(retryFailedShotGeneration).mockRejectedValue(new ApiClientError({
      code: "FAILED_RETRY_STALE", message: "stale", status: 409,
    }));
    show(); const dialog = await confirmation();
    fireEvent.click(within(dialog).getByRole("button", { name: "确认并重新生成" }));
    await screen.findByText(/失败恢复状态或配置已变化/);
    expect(screen.queryByRole("button", { name: "查看重试确认" })).not.toBeInTheDocument();
    expect(retryFailedShotGeneration).toHaveBeenCalledTimes(1);
  });
});
