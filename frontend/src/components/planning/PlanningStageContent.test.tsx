import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getCreativeContent,
  getStoryboardContent,
  getVideoPrompts,
} from "../../api/client";
import type {
  CreativeContentResponse,
  StoryboardContentResponse,
  VideoPromptsContentResponse,
} from "../../api/types";
import { PlanningStageContent } from "./PlanningStageContent";

vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getCreativeContent: vi.fn(),
    getStoryboardContent: vi.fn(),
    getVideoPrompts: vi.fn(),
  };
});

const mockCreative = vi.mocked(getCreativeContent);
const mockStoryboard = vi.mocked(getStoryboardContent);
const mockPrompts = vi.mocked(getVideoPrompts);

const creativeResponse: CreativeContentResponse = {
  project_id: "LEE柠檬",
  status: "APPROVED",
  content: {
    creative_concept: "在明亮黄色世界中呈现年轻活力",
    target_audience: "18-30岁年轻消费者",
    key_message: "新鲜看得见，酸甜刚刚好",
    visual_direction: "高明度高饱和度黄色插画",
    narrative_arc: "从柠檬轮廓到品牌收束",
    narration_plan: {
      enabled: true,
      tone: "年轻活泼",
      full_script: "每一颗LEE柠檬，饱满多汁。",
      target_duration_seconds: 12,
    },
    subtitle_strategy: {
      enabled: true,
      tone: "简洁明快",
      density: "low",
      max_lines: 1,
      preferred_position: "bottom_center",
      principles: ["不遮挡主要视觉元素", "与旁白同步"],
    },
    global_constraints: { must: [], must_not: ["people"] },
    av_timeline_constraints: {
      forbidden_windows: [{ start: 0, end: 3, tracks: ["voiceover"] }],
    },
  },
};

const storyboardResponse: StoryboardContentResponse = {
  project_id: "LEE柠檬",
  status: "APPROVED",
  content: {
    total_duration_seconds: 12,
    shots: [
      {
        shot_id: 1,
        duration_seconds: 6,
        purpose: "建立视觉基调",
        visual: "黄色背景与柠檬轮廓",
        camera: "平稳推近",
        voiceover_cues: [
          { text: "新鲜看得见", start_offset: 1, end_offset: 3, position: null },
        ],
        subtitle_cues: [
          {
            text: "LEE柠檬",
            start_offset: 2,
            end_offset: 4,
            position: "bottom_center",
          },
        ],
        video_constraints: {
          reserve_subtitle_space: true,
          subtitle_safe_area: "bottom_center",
        },
      },
      {
        shot_id: 2,
        duration_seconds: 6,
        purpose: "品牌收束",
        visual: "柠檬轻微跳动",
        camera: "固定镜头",
        voiceover_cues: [],
        subtitle_cues: [],
        video_constraints: {
          reserve_subtitle_space: false,
          subtitle_safe_area: "none",
        },
      },
    ],
  },
};

const promptsResponse: VideoPromptsContentResponse = {
  project_id: "LEE柠檬",
  status: "APPROVED",
  content: {
    shots: [
      {
        shot_id: 1,
        prompt_version: 2,
        prompt_source: "ai_revision",
        visual_prompt_core: "bright lemon visual core",
        prompt_text: "approved final prompt with deterministic control blocks",
      },
      {
        shot_id: 2,
        prompt_version: 1,
        prompt_source: "ai_generated",
        visual_prompt_core: "closing lemon visual core",
        prompt_text: "shot two final prompt",
      },
    ],
  },
};

function renderContent(stageKey: "creative" | "storyboard" | "video-prompt" | "shots") {
  return render(
    <PlanningStageContent projectId="LEE柠檬" stageKey={stageKey} />,
  );
}

describe("PlanningStageContent", () => {
  beforeEach(() => {
    mockCreative.mockReset();
    mockStoryboard.mockReset();
    mockPrompts.mockReset();
    mockCreative.mockResolvedValue({ data: creativeResponse, correlationId: "req_c" });
    mockStoryboard.mockResolvedValue({ data: storyboardResponse, correlationId: "req_s" });
    mockPrompts.mockResolvedValue({ data: promptsResponse, correlationId: "req_p" });
  });

  it("shows the real Creative overview fields", async () => {
    renderContent("creative");
    expect(await screen.findByText(creativeResponse.content!.creative_concept!)).toBeInTheDocument();
    expect(screen.getByText("18-30岁年轻消费者")).toBeInTheDocument();
    expect(screen.getByText("新鲜看得见，酸甜刚刚好")).toBeInTheDocument();
    expect(screen.getByText("高明度高饱和度黄色插画")).toBeInTheDocument();
    expect(screen.getByText("从柠檬轮廓到品牌收束")).toBeInTheDocument();
  });

  it("shows Creative Narration Plan", async () => {
    renderContent("creative");
    const heading = await screen.findByRole("heading", { name: "旁白规划" });
    const card = heading.closest("article")!;
    expect(within(card).getByText("年轻活泼")).toBeInTheDocument();
    expect(within(card).getByText("12s")).toBeInTheDocument();
    expect(within(card).getByText(/饱满多汁/)).toBeInTheDocument();
  });

  it("shows Creative Subtitle Strategy", async () => {
    renderContent("creative");
    const heading = await screen.findByRole("heading", { name: "字幕策略" });
    const card = heading.closest("article")!;
    expect(within(card).getByText("bottom_center")).toBeInTheDocument();
    expect(within(card).getByText("不遮挡主要视觉元素")).toBeInTheDocument();
    expect(within(card).getByText("与旁白同步")).toBeInTheDocument();
  });

  it("shows the Creative NOT_STARTED empty state", async () => {
    mockCreative.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "NOT_STARTED", content: null },
      correlationId: "req_empty",
    });
    renderContent("creative");
    expect(await screen.findByText("创意策划尚未生成。")).toBeInTheDocument();
  });

  it("shows multiple Storyboard shots with IDs, durations, visual, camera, and purpose", async () => {
    renderContent("storyboard");
    expect(await screen.findByRole("heading", { name: "Shot 01" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Shot 02" })).toBeInTheDocument();
    expect(screen.getAllByText("6s")).toHaveLength(2);
    expect(screen.getByText("黄色背景与柠檬轮廓")).toBeInTheDocument();
    expect(screen.getByText("平稳推近")).toBeInTheDocument();
    expect(screen.getByText("建立视觉基调")).toBeInTheDocument();
  });

  it("shows persisted Voiceover cue text and offsets", async () => {
    renderContent("storyboard");
    expect(await screen.findByText("新鲜看得见")).toBeInTheDocument();
    expect(screen.getByText("1s – 3s")).toBeInTheDocument();
  });

  it("shows Subtitle cue, position, and video constraints", async () => {
    renderContent("storyboard");
    expect(await screen.findByText("LEE柠檬")).toBeInTheDocument();
    expect(screen.getAllByText("bottom_center").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/^视频约束：保留字幕安全区/),
    ).toBeInTheDocument();
  });

  it("shows multiple Video Prompts and their versions", async () => {
    renderContent("video-prompt");
    expect(await screen.findByText("Prompt Version v2")).toBeInTheDocument();
    expect(screen.getByText("Prompt Version v1")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Shot 01" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Shot 02" })).toBeInTheDocument();
  });

  it("keeps visual_prompt_core separate from the final prompt", async () => {
    renderContent("video-prompt");
    expect(await screen.findByText("bright lemon visual core")).toBeInTheDocument();
    expect(
      screen.getByText("approved final prompt with deterministic control blocks"),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { name: "视觉 Prompt 核心" }).length).toBeGreaterThan(0);
    expect(screen.getAllByRole("heading", { name: "最终视频 Prompt" }).length).toBeGreaterThan(0);
  });

  it("shows a content-only loading state", () => {
    mockCreative.mockReturnValue(
      new Promise<Awaited<ReturnType<typeof getCreativeContent>>>(() => undefined),
    );
    renderContent("creative");
    expect(screen.getByText("正在加载创意内容…")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Creative 内容" })).toBeInTheDocument();
    expect(screen.getByText("正在加载创意内容…").closest("div")).toHaveAttribute(
      "aria-busy",
      "true",
    );
  });

  it("keeps the content section available when content loading fails", async () => {
    mockStoryboard.mockRejectedValue(
      new ApiClientError({
        message: "D:\\private API_KEY",
        code: "PROJECT_DATA_CORRUPT",
        correlationId: "req_content_error",
      }),
    );
    renderContent("storyboard");
    expect(await screen.findByText("内容暂时无法读取")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Storyboard 内容" })).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_content_error")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("API_KEY");
  });

  it("retries only Planning content", async () => {
    mockPrompts
      .mockRejectedValueOnce(new ApiClientError({ message: "temporary", code: "HTTP_ERROR" }))
      .mockResolvedValueOnce({ data: promptsResponse, correlationId: "req_retry" });
    renderContent("video-prompt");
    fireEvent.click(await screen.findByRole("button", { name: "重试内容" }));
    expect(await screen.findByText("Prompt Version v2")).toBeInTheDocument();
    expect(mockPrompts).toHaveBeenCalledTimes(2);
    expect(mockCreative).not.toHaveBeenCalled();
    expect(mockStoryboard).not.toHaveBeenCalled();
  });

  it("shows a safe Network Error copy", async () => {
    mockCreative.mockRejectedValue(
      new ApiClientError({ message: "secret", code: "NETWORK_ERROR" }),
    );
    renderContent("creative");
    expect(await screen.findByText(/无法连接本地 Backend/)).toBeInTheDocument();
  });

  it("renders long prompts as read-only wrapping text, never an input", async () => {
    const longPrompt = "long prompt line ".repeat(100);
    mockPrompts.mockResolvedValue({
      data: {
        ...promptsResponse,
        content: { shots: [{ ...promptsResponse.content!.shots[0]!, prompt_text: longPrompt }] },
      },
      correlationId: "req_long",
    });
    renderContent("video-prompt");
    await screen.findByRole("heading", { name: "最终视频 Prompt" });
    const prompt = document.querySelector(".prompt-text-block-final p");
    if (!prompt) throw new Error("Final prompt text is missing");
    expect(prompt).toHaveTextContent("long prompt line");
    expect(prompt.closest(".prompt-text-block")).not.toBeNull();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
  });

  it("does not render Planning content or call APIs for the other six Stages", async () => {
    const { container } = renderContent("shots");
    await waitFor(() => {
      expect(mockCreative).not.toHaveBeenCalled();
      expect(mockStoryboard).not.toHaveBeenCalled();
      expect(mockPrompts).not.toHaveBeenCalled();
    });
    expect(container).toBeEmptyDOMElement();
  });

  it("does not add edit, save, regenerate, approve, or copy controls", async () => {
    renderContent("video-prompt");
    await screen.findByText("Prompt Version v2");
    for (const name of ["编辑", "保存", "重新生成", "审核", "复制 Prompt"]) {
      expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });
});
