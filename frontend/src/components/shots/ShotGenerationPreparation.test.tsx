import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import {
  ApiClientError,
  getReferenceAssets,
  getProjectTasks,
  getShotGenerationOptions,
  getShotGenerationStatus,
  getTask,
  preflightShotGeneration,
  resumeShotGeneration,
  regenerateShotGeneration,
  startShotGeneration,
} from "../../api/client";
import type {
  GenerationOptionsResponse,
  GenerationPreflightResponse,
  ReferenceAssetListResponse,
} from "../../api/types";
import { ShotGenerationPreparation } from "./ShotGenerationPreparation";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getShotGenerationOptions: vi.fn(),
    getReferenceAssets: vi.fn(),
    getProjectTasks: vi.fn(),
    preflightShotGeneration: vi.fn(),
    getShotGenerationStatus: vi.fn(),
    getTask: vi.fn(),
    resumeShotGeneration: vi.fn(),
    regenerateShotGeneration: vi.fn(),
    startShotGeneration: vi.fn(),
  };
});

const mockOptions = vi.mocked(getShotGenerationOptions);
const mockReferences = vi.mocked(getReferenceAssets);
const mockPreflight = vi.mocked(preflightShotGeneration);
const mockTasks = vi.mocked(getProjectTasks);
const mockStatus = vi.mocked(getShotGenerationStatus);
const mockTask = vi.mocked(getTask);
const mockResume = vi.mocked(resumeShotGeneration);
const mockRegenerate = vi.mocked(regenerateShotGeneration);
const mockStart = vi.mocked(startShotGeneration);

const options: GenerationOptionsResponse = {
  project_id: "project-a",
  eligible: true,
  shot: {
    shot_id: "shot_01",
    duration_seconds: 6,
    prompt_version: 2,
    resolution: "768P",
  },
  selection_modes: ["AUTO", "MANUAL"],
  visual_input_modes: [
    {
      mode: "none",
      display_name: "不使用参考图",
      description: "完全根据提示词生成。",
      compatible_model_ids: ["MiniMax-Hailuo-2.3", "MiniMax-H3"],
    },
    {
      mode: "reference_asset",
      display_name: "主体参考",
      description: "保持产品或主体身份，但允许 AI 重新构图和设计场景。",
      compatible_model_ids: ["MiniMax-H3"],
    },
    {
      mode: "first_frame",
      display_name: "作为首帧",
      description: "这张图片将作为视频的第一帧继续生成。",
      compatible_model_ids: ["MiniMax-Hailuo-2.3", "MiniMax-H3"],
    },
  ],
  models: [
    {
      model_id: "MiniMax-Hailuo-2.3",
      display_name: "MiniMax Hailuo 2.3",
      provider: "minimax",
      provider_display_name: "MiniMax",
      api_version: "v1",
      available: true,
      supported_visual_input_modes: ["none", "first_frame"],
      supported_resolutions: ["768P"],
      supported_durations: [6, 10],
      min_duration: null,
      max_duration: null,
    },
    {
      model_id: "MiniMax-H3",
      display_name: "MiniMax H3",
      provider: "minimax",
      provider_display_name: "MiniMax",
      api_version: "v2",
      available: true,
      supported_visual_input_modes: ["none", "reference_asset", "first_frame"],
      supported_resolutions: ["2K", "768P"],
      supported_durations: [],
      min_duration: 4,
      max_duration: 15,
    },
  ],
  issues: [],
  paid_call_required: true,
};

const references: ReferenceAssetListResponse = {
  project_id: "project-a",
  assets: [
    {
      asset_id: "ref_001",
      filename: "product.png",
      media_type: "image/png",
      width: 1024,
      height: 1024,
    },
  ],
};

const ready: GenerationPreflightResponse = {
  ready: true,
  shot: options.shot,
  resolved: {
    provider: "minimax",
    provider_display_name: "MiniMax",
    model: "MiniMax-Hailuo-2.3",
    model_display_name: "MiniMax Hailuo 2.3",
    api_version: "v1",
    generation_mode: "text_to_video",
    generation_mode_display_name: "纯文本生成",
    visual_input_mode: "none",
    model_selection: "AUTO",
  },
  provider_available: true,
  selected_asset_ids: [],
  issues: [],
  warnings: [],
  paid_call_required: true,
  preflight_fingerprint: "a".repeat(64),
};

const queuedTask = {
  task_id: "task_0123456789abcdef0123456789abcdef",
  project_id: "project-a",
  operation: "SHOT_GENERATE" as const,
  target_id: "shot_01",
  status: "QUEUED" as const,
  created_at: "2026-08-19T00:00:00Z",
  started_at: null,
  finished_at: null,
  correlation_id: "req_generate",
  error: null,
  result: null,
};

function renderPreparation() {
  return render(
    <MemoryRouter>
      <ShotGenerationPreparation projectId="project-a" shotId="shot_01" />
    </MemoryRouter>,
  );
}

describe("ShotGenerationPreparation", () => {
  beforeEach(() => {
    mockOptions.mockReset();
    mockReferences.mockReset();
    mockPreflight.mockReset();
    mockTasks.mockReset();
    mockStatus.mockReset();
    mockTask.mockReset();
    mockResume.mockReset();
    mockRegenerate.mockReset();
    mockStart.mockReset();
    mockOptions.mockResolvedValue({ data: options, correlationId: "req_options" });
    mockReferences.mockResolvedValue({ data: references, correlationId: "req_refs" });
    mockPreflight.mockResolvedValue({ data: ready, correlationId: "req_preflight" });
    mockTasks.mockResolvedValue({ data: { project_id: "project-a", tasks: [] }, correlationId: "req_tasks" });
    mockStatus.mockResolvedValue({
      data: {
        project_id: "project-a", shot_id: "shot_01", state: "NOT_STARTED",
        resume_available: false, resume_kind: null, video_version: null,
        provider_submission_known: true,
      },
      correlationId: "req_status",
    });
    mockStart.mockResolvedValue({ data: queuedTask, correlationId: "req_generate" });
    mockResume.mockResolvedValue({ data: { ...queuedTask, operation: "SHOT_RESUME" }, correlationId: "req_resume" });
    mockTask.mockResolvedValue({ data: queuedTask, correlationId: "req_task" });
  });

  it("prepares and confirms a paid current-Prompt regeneration exactly once", async () => {
    const regenerationOptions: GenerationOptionsResponse = {
      ...options,
      shot: {
        ...options.shot,
        official_video_version: 1,
        pending_video_version: null,
        next_video_version: 2,
      },
    };
    const regenerationReady: GenerationPreflightResponse = {
      ...ready,
      shot: regenerationOptions.shot,
    };
    mockOptions.mockResolvedValue({ data: regenerationOptions, correlationId: "req_options" });
    mockPreflight.mockResolvedValue({ data: regenerationReady, correlationId: "req_preflight" });
    mockRegenerate.mockResolvedValue({
      data: { ...queuedTask, operation: "SHOT_REGENERATE" },
      correlationId: "req_regenerate",
    });
    render(
      <MemoryRouter>
        <ShotGenerationPreparation
          projectId="project-a"
          shotId="shot_01"
          intent="REGENERATE_CURRENT_PROMPT"
        />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("heading", { name: "用当前 Prompt 重新生成" })).toBeInTheDocument();
    expect(screen.getByText("当前正式版本会保留，只有新版本审核通过后才会替换。", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByText("v2").length).toBeGreaterThanOrEqual(1);
    expect(mockOptions).toHaveBeenCalledWith("project-a", "shot_01", "REGENERATE_CURRENT_PROMPT");
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    await screen.findByRole("button", { name: "生成新的待审核版本" });
    expect(mockPreflight).toHaveBeenCalledWith("project-a", "shot_01", expect.objectContaining({
      intent: "REGENERATE_CURRENT_PROMPT",
    }));
    fireEvent.click(screen.getByRole("button", { name: "生成新的待审核版本" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("确认后将调用付费视频模型并创建新的视频版本。");
    const confirm = screen.getByRole("button", { name: "确认并生成视频" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(mockRegenerate).toHaveBeenCalledTimes(1));
    expect(mockStart).not.toHaveBeenCalled();
  });

  it("shows initial Shot context, three explained modes, and no generate action", async () => {
    renderPreparation();
    expect(screen.getByText("正在读取生成选项…")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "生成设置" })).toBeInTheDocument();
    expect(screen.getByText("v2")).toBeInTheDocument();
    expect(screen.getByText("6 秒")).toBeInTheDocument();
    expect(screen.getByText("完全根据提示词生成。")).toBeInTheDocument();
    expect(screen.getByText(/保持产品或主体身份/)).toBeInTheDocument();
    expect(screen.getByText(/作为视频的第一帧/)).toBeInTheDocument();
    expect(screen.getByText(/真正生成视频会调用付费视频模型/)).toBeInTheDocument();
    expect(screen.queryByText(/\$0\./)).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成视频" })).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("switches AUTO/MANUAL and exposes incompatibility without changing the model", async () => {
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("radio", { name: "手动" }));
    fireEvent.click(screen.getByRole("radio", { name: /主体参考/ }));
    fireEvent.change(screen.getByLabelText("视频模型"), {
      target: { value: "MiniMax-Hailuo-2.3" },
    });
    expect(screen.getByText(/不会自动更换模型/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    await waitFor(() => expect(mockPreflight).toHaveBeenCalledTimes(1));
    expect(mockPreflight).toHaveBeenCalledWith("project-a", "shot_01", {
      model_selection: "MANUAL",
      requested_model: "MiniMax-Hailuo-2.3",
      visual_input: { mode: "reference_asset", asset_ids: [] },
    });
  });

  it("shows project references and uses the Backend image URL for preview", async () => {
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("radio", { name: /作为首帧/ }));
    const preview = screen.getByRole("img", { name: "product.png 参考图预览" });
    expect(preview).toHaveAttribute(
      "src",
      "http://127.0.0.1:8000/api/projects/project-a/references/ref_001/image",
    );
    fireEvent.click(screen.getByRole("radio", { name: /product.png/ }));
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    await waitFor(() => expect(mockPreflight).toHaveBeenCalledTimes(1));
    expect(mockPreflight.mock.calls[0][2].visual_input).toEqual({
      mode: "first_frame",
      asset_ids: ["ref_001"],
    });
  });

  it("shows an empty reference library without upload controls", async () => {
    mockReferences.mockResolvedValue({
      data: { project_id: "project-a", assets: [] },
      correlationId: "req_empty",
    });
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("radio", { name: /主体参考/ }));
    expect(screen.getByText(/当前项目暂无参考素材/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往项目素材库添加" })).toHaveAttribute(
      "href",
      "/projects/project-a",
    );
    expect(screen.queryByText(/上传/)).not.toBeInTheDocument();
  });

  it("submits one pure preflight and renders the resolved confirmation summary", async () => {
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    const button = screen.getByRole("button", { name: "检查生成配置" });
    fireEvent.click(button);
    fireEvent.click(button);
    const summary = await screen.findByRole("region", { name: "生成前确认摘要" });
    expect(mockPreflight).toHaveBeenCalledTimes(1);
    expect(within(summary).getByRole("heading", { name: "配置检查通过" })).toBeInTheDocument();
    expect(within(summary).getByText("MiniMax Hailuo 2.3")).toBeInTheDocument();
    expect(within(summary).getByText("MiniMax")).toBeInTheDocument();
    expect(within(summary).getByText("纯文本生成")).toBeInTheDocument();
    expect(within(summary).getByRole("button", { name: "生成视频" })).toBeInTheDocument();
    expect(mockStart).not.toHaveBeenCalled();
    expect(screen.queryByText(/QUEUED|RUNNING/)).not.toBeInTheDocument();
  });

  it("renders Backend not-ready issues and preserves the selected route", async () => {
    mockPreflight.mockResolvedValue({
      data: {
        ...ready,
        ready: false,
        provider_available: false,
        resolved: {
          ...ready.resolved!,
          model: "MiniMax-H3",
          model_display_name: "MiniMax H3",
          api_version: "v2",
          generation_mode: "reference_generation",
          generation_mode_display_name: "主体参考生成",
          visual_input_mode: "reference_asset",
        },
        issues: [
          { code: "PROVIDER_NOT_CONFIGURED", message: "当前模式所需的视频模型尚未配置。" },
        ],
      },
      correlationId: "req_not_ready",
    });
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("radio", { name: /主体参考/ }));
    fireEvent.click(screen.getByRole("radio", { name: /product.png/ }));
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    const summary = await screen.findByRole("region", { name: "生成前确认摘要" });
    expect(within(summary).getByRole("heading", { name: "配置尚未就绪" })).toBeInTheDocument();
    expect(within(summary).getByText("MiniMax H3")).toBeInTheDocument();
    expect(within(summary).getByText("当前模式所需的视频模型尚未配置。")).toBeInTheDocument();
  });

  it("disables preflight when Backend says the Shot is not eligible", async () => {
    mockOptions.mockResolvedValue({
      data: {
        ...options,
        eligible: false,
        issues: [{ code: "PROMPT_NOT_APPROVED", message: "视频提示词尚未正式审核通过。" }],
      },
      correlationId: "req_blocked",
    });
    renderPreparation();
    expect(await screen.findByText("视频提示词尚未正式审核通过。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "检查生成配置" })).toBeDisabled();
    expect(mockPreflight).not.toHaveBeenCalled();
  });

  it("requires a final paid-call confirmation and cancel creates no task", async () => {
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成视频" }));
    const dialog = screen.getByRole("dialog", { name: "确认生成视频" });
    expect(within(dialog).getByText("MiniMax Hailuo 2.3")).toBeInTheDocument();
    expect(within(dialog).getByText("确认后将向视频生成模型提交付费请求。")).toBeInTheDocument();
    expect(within(dialog).getByText("768P")).toBeInTheDocument();
    fireEvent.click(within(dialog).getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockStart).not.toHaveBeenCalled();
  });

  it("confirms exactly once even on a rapid double click", async () => {
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成视频" }));
    const confirm = screen.getByRole("button", { name: "确认并生成视频" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1));
    expect(mockStart).toHaveBeenCalledWith("project-a", "shot_01", {
      model_selection: "AUTO",
      requested_model: null,
      visual_input: { mode: "none", asset_ids: [] },
      preflight_fingerprint: "a".repeat(64),
      confirm_paid_call: true,
    });
    expect(await screen.findByText("排队中…")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("locks initial paid generation when a 202 was accepted but task status is unreadable", async () => {
    mockStart.mockRejectedValue(new ApiClientError({
      code: "ACCEPTED_TASK_STATUS_UNREADABLE",
      status: 202,
      message: "生成请求已被后端接受，但当前无法读取任务状态。请勿重复提交生成请求。",
      correlationId: "req_uncertain_start",
      requestAccepted: true,
    }));
    const view = renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成视频" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成视频" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请求已被后端接受");
    expect(screen.getByRole("button", { name: "生成视频" })).toBeDisabled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成视频" }));
    view.rerender(
      <MemoryRouter>
        <ShotGenerationPreparation projectId="project-a" shotId="shot_01" />
      </MemoryRouter>,
    );
    await waitFor(() => expect(mockStart).toHaveBeenCalledTimes(1));
  });

  it("rechecks accepted regeneration with GETs only and attaches the existing task", async () => {
    const completed = {
      ...queuedTask,
      operation: "SHOT_REGENERATE" as const,
      status: "SUCCEEDED" as const,
      started_at: "2026-08-19T00:00:01Z",
      finished_at: "2026-08-19T00:01:00Z",
      correlation_id: "req_uncertain_regenerate",
      result: { resource_type: "SHOT_VIDEO", resource_id: "shot_01", version: 2 },
    };
    mockRegenerate.mockRejectedValue(new ApiClientError({
      code: "ACCEPTED_TASK_STATUS_UNREADABLE",
      status: 202,
      message: "生成请求已被后端接受，但当前无法读取任务状态。请勿重复提交生成请求。",
      correlationId: completed.correlation_id,
      requestAccepted: true,
    }));
    mockTasks
      .mockResolvedValueOnce({ data: { project_id: "project-a", tasks: [] }, correlationId: "req_initial" })
      .mockResolvedValue({ data: { project_id: "project-a", tasks: [completed] }, correlationId: "req_refresh" });
    mockStatus.mockResolvedValue({
      data: {
        project_id: "project-a", shot_id: "shot_01", state: "WAITING_REVIEW",
        resume_available: false, resume_kind: null, video_version: 2,
        provider_submission_known: true, generation_intent: "REGENERATE_CURRENT_PROMPT",
      },
      correlationId: "req_status",
    });
    const onCompleted = vi.fn();
    render(
      <MemoryRouter>
        <ShotGenerationPreparation
          projectId="project-a"
          shotId="shot_01"
          intent="REGENERATE_CURRENT_PROMPT"
          onCompleted={onCompleted}
        />
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: "用当前 Prompt 重新生成" });
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成新的待审核版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成视频" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("请勿重复提交生成请求");
    fireEvent.click(screen.getByRole("button", { name: "重新检查状态" }));
    await waitFor(() => expect(onCompleted).toHaveBeenCalledTimes(1));
    expect(mockTasks).toHaveBeenCalledTimes(2);
    expect(mockStatus).toHaveBeenCalledTimes(2);
    expect(mockRegenerate).toHaveBeenCalledTimes(1);
    expect(mockStart).not.toHaveBeenCalled();
  });

  it("keeps regeneration locked when the GET-only status recheck still fails", async () => {
    mockRegenerate.mockRejectedValue(new ApiClientError({
      code: "ACCEPTED_TASK_STATUS_UNREADABLE",
      status: 202,
      message: "生成请求已被后端接受，但当前无法读取任务状态。请勿重复提交生成请求。",
      correlationId: "req_still_uncertain",
      requestAccepted: true,
    }));
    mockTasks
      .mockResolvedValueOnce({ data: { project_id: "project-a", tasks: [] }, correlationId: "req_initial" })
      .mockRejectedValue(new ApiClientError({ code: "NETWORK_ERROR", message: "offline" }));
    render(
      <MemoryRouter>
        <ShotGenerationPreparation
          projectId="project-a"
          shotId="shot_01"
          intent="REGENERATE_CURRENT_PROMPT"
        />
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { name: "用当前 Prompt 重新生成" });
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成新的待审核版本" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成视频" }));
    fireEvent.click(await screen.findByRole("button", { name: "重新检查状态" }));

    await waitFor(() => expect(mockTasks).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("alert")).toHaveTextContent("请勿重复提交生成请求");
    expect(screen.getByRole("button", { name: "生成新的待审核版本" })).toBeDisabled();
    expect(mockRegenerate).toHaveBeenCalledTimes(1);
  });

  it("clears stale preflight and asks the user to check again", async () => {
    mockStart.mockRejectedValue(new ApiClientError({
      code: "GENERATION_PREFLIGHT_STALE",
      status: 409,
      message: "生成配置已发生变化，请重新检查配置。",
    }));
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成视频" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成视频" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("生成配置已发生变化，请重新检查配置。");
    expect(screen.queryByRole("button", { name: "生成视频" })).not.toBeInTheDocument();
  });

  it("offers manual resume from durable progress without model choices in the request", async () => {
    mockOptions.mockResolvedValue({ data: { ...options, eligible: false }, correlationId: "req_options" });
    mockStatus.mockResolvedValue({
      data: {
        project_id: "project-a", shot_id: "shot_01", state: "PROVIDER_RUNNING",
        resume_available: true, resume_kind: "POLL_EXISTING_TASK", video_version: 1,
        provider_submission_known: true,
      },
      correlationId: "req_status",
    });
    renderPreparation();
    const resume = await screen.findByRole("button", { name: "继续生成" });
    fireEvent.click(resume);
    await waitFor(() => expect(mockResume).toHaveBeenCalledTimes(1));
    expect(mockResume).toHaveBeenCalledWith("project-a", "shot_01");
  });

  it("shows submission ambiguity without a retry or resume button", async () => {
    mockOptions.mockResolvedValue({ data: { ...options, eligible: false }, correlationId: "req_options" });
    mockStatus.mockResolvedValue({
      data: {
        project_id: "project-a", shot_id: "shot_01", state: "SUBMISSION_UNKNOWN",
        resume_available: false, resume_kind: null, video_version: 1,
        provider_submission_known: false,
      },
      correlationId: "req_status",
    });
    renderPreparation();
    expect(await screen.findByText(/请不要立即重复生成/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "继续生成" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成视频" })).not.toBeInTheDocument();
  });

  it("reattaches the current Shot task after a page reload", async () => {
    mockTasks.mockResolvedValue({
      data: { project_id: "project-a", tasks: [queuedTask] },
      correlationId: "req_tasks",
    });
    renderPreparation();
    expect(await screen.findByText("排队中…")).toBeInTheDocument();
    expect(mockStart).not.toHaveBeenCalled();
  });

  it("shows a durable Core phase instead of inventing progress", async () => {
    const runningTask = {
      ...queuedTask,
      status: "RUNNING" as const,
      started_at: "2026-08-19T00:00:01Z",
    };
    mockTasks.mockResolvedValue({
      data: { project_id: "project-a", tasks: [runningTask] },
      correlationId: "req_tasks",
    });
    mockTask.mockResolvedValue({ data: runningTask, correlationId: "req_task" });
    mockStatus.mockResolvedValue({
      data: {
        project_id: "project-a", shot_id: "shot_01", state: "DOWNLOADING",
        resume_available: true, resume_kind: "DOWNLOAD_EXISTING_FILE", video_version: 1,
        provider_submission_known: true,
      },
      correlationId: "req_status",
    });
    renderPreparation();
    expect(await screen.findByText("正在下载视频…")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("attaches PROJECT_BUSY only when the active task targets this Shot", async () => {
    mockStart.mockRejectedValue(new ApiClientError({
      code: "PROJECT_BUSY",
      status: 409,
      message: "项目当前正在执行其他操作。",
    }));
    mockTasks
      .mockResolvedValueOnce({
        data: { project_id: "project-a", tasks: [] },
        correlationId: "req_initial_tasks",
      })
      .mockResolvedValue({
        data: { project_id: "project-a", tasks: [queuedTask] },
        correlationId: "req_busy_tasks",
      });
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成视频" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成视频" }));
    expect(await screen.findByText("排队中…")).toBeInTheDocument();
  });

  it("keeps PROJECT_BUSY visible when another Shot owns the active task", async () => {
    mockStart.mockRejectedValue(new ApiClientError({
      code: "PROJECT_BUSY",
      status: 409,
      message: "项目当前正在执行其他操作。",
    }));
    mockTasks
      .mockResolvedValueOnce({
        data: { project_id: "project-a", tasks: [] },
        correlationId: "req_initial_tasks",
      })
      .mockResolvedValue({
        data: {
          project_id: "project-a",
          tasks: [{ ...queuedTask, target_id: "shot_02" }],
        },
        correlationId: "req_busy_tasks",
      });
    renderPreparation();
    await screen.findByRole("heading", { name: "生成设置" });
    fireEvent.click(screen.getByRole("button", { name: "检查生成配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "生成视频" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并生成视频" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("项目当前正在执行其他操作");
    expect(screen.queryByText("排队中…")).not.toBeInTheDocument();
  });
});
