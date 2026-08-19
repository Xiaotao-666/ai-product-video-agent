import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import {
  getReferenceAssets,
  getShotGenerationOptions,
  preflightShotGeneration,
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
    preflightShotGeneration: vi.fn(),
  };
});

const mockOptions = vi.mocked(getShotGenerationOptions);
const mockReferences = vi.mocked(getReferenceAssets);
const mockPreflight = vi.mocked(preflightShotGeneration);

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
    mockOptions.mockResolvedValue({ data: options, correlationId: "req_options" });
    mockReferences.mockResolvedValue({ data: references, correlationId: "req_refs" });
    mockPreflight.mockResolvedValue({ data: ready, correlationId: "req_preflight" });
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
    expect(screen.getByText("生成视频将在下一阶段开放。")).toBeInTheDocument();
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
    expect(within(summary).getByText(/没有创建任务或调用视频模型/)).toBeInTheDocument();
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
});
