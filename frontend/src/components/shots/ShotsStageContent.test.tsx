import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getMultiShotGenerationOptions,
  getShots,
  startMultiShotGeneration,
} from "../../api/client";
import type {
  MultiShotGenerationOptionsResponse,
  ShotListResponse,
} from "../../api/types";
import { ShotsStageContent } from "./ShotsStageContent";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getShots: vi.fn(),
    getMultiShotGenerationOptions: vi.fn(),
    startMultiShotGeneration: vi.fn(),
  };
});

const mockGetShots = vi.mocked(getShots);
const mockGenerationOptions = vi.mocked(getMultiShotGenerationOptions);
const mockStartGeneration = vi.mocked(startMultiShotGeneration);

const shotList: ShotListResponse = {
  project_id: "LEE柠檬",
  status: "WAITING_REVIEW",
  aggregation: {
    total: 3,
    approved: 1,
    waiting_review: 1,
    generating: 1,
    not_started: 0,
    failed: 0,
  },
  shots: [
    {
      shot_id: "shot_01",
      order: 1,
      title: "建立产品清爽外观",
      status: "WAITING_REVIEW",
      prompt_status: "READY",
      video_status: "READY",
      review_status: "WAITING_REVIEW",
      official_version: 2,
      pending_review_version: 3,
      version_count: 3,
      generation_count: 3,
    },
    {
      shot_id: "shot_02",
      order: 2,
      title: "展示核心卖点",
      status: "APPROVED",
      prompt_status: "READY",
      video_status: "READY",
      review_status: "APPROVED",
      official_version: 1,
      pending_review_version: null,
      version_count: 1,
      generation_count: 1,
    },
    {
      shot_id: "shot_03",
      order: 3,
      title: "完成品牌收束",
      status: "GENERATING",
      prompt_status: "READY",
      video_status: "GENERATING",
      review_status: "NOT_STARTED",
      official_version: null,
      pending_review_version: null,
      version_count: 0,
      generation_count: 0,
    },
  ],
};

const generationOptions: MultiShotGenerationOptionsResponse = {
  project_id: "LEE柠檬",
  status: "READY",
  max_parallel: 2,
  aggregation: {
    total: 3,
    queued: 0,
    running: 0,
    waiting_review: 1,
    approved: 1,
    failed: 0,
    not_started: 1,
  },
  shots: [
    {
      shot_id: "shot_01",
      order: 1,
      title: "建立产品清爽外观",
      status: "WAITING_REVIEW",
      prompt_ready: true,
      video_status: "READY",
      available: false,
    },
    {
      shot_id: "shot_02",
      order: 2,
      title: "展示核心卖点",
      status: "APPROVED",
      prompt_ready: true,
      video_status: "READY",
      available: false,
    },
    {
      shot_id: "shot_03",
      order: 3,
      title: "完成品牌收束",
      status: "READY",
      prompt_ready: true,
      video_status: "NOT_STARTED",
      available: true,
    },
  ],
};

function renderContent(stageKey: "shots" | "assembly" = "shots") {
  return render(
    <MemoryRouter>
      <ShotsStageContent projectId="LEE柠檬" stageKey={stageKey} />
    </MemoryRouter>,
  );
}

describe("ShotsStageContent", () => {
  beforeEach(() => {
    mockGetShots.mockReset();
    mockGetShots.mockResolvedValue({ data: shotList, correlationId: "req_shots" });
    mockGenerationOptions.mockReset();
    mockGenerationOptions.mockResolvedValue({
      data: generationOptions,
      correlationId: "req_generation_options",
    });
    mockStartGeneration.mockReset();
    mockStartGeneration.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        status: "IN_PROGRESS",
        max_parallel: 2,
        shots: [
          {
            shot_id: "shot_03",
            task_id: "task_0123456789abcdef0123456789abcdef",
            operation: "SHOT_GENERATE",
            status: "QUEUED",
          },
        ],
        aggregation: {
          ...generationOptions.aggregation,
          queued: 1,
          not_started: 0,
        },
      },
      correlationId: "req_generation_start",
    });
  });

  it("shows all persisted Shots and their count", async () => {
    renderContent();
    const section = (await screen.findByRole("heading", { name: "镜头列表" })).closest("section");
    expect(section).not.toBeNull();
    expect(within(section!).getByRole("heading", { name: "Shot 01" })).toBeInTheDocument();
    expect(within(section!).getByRole("heading", { name: "Shot 02" })).toBeInTheDocument();
    expect(within(section!).getByRole("heading", { name: "Shot 03" })).toBeInTheDocument();
    expect(within(section!).getAllByRole("link", { name: "查看镜头" })).toHaveLength(3);
    expect(within(section!).getByText("建立产品清爽外观")).toBeInTheDocument();
    expect(within(section!).getByText("展示核心卖点")).toBeInTheDocument();
  });

  it("keeps failed attempts out of initial batch and links to explicit recovery", async () => {
    mockGetShots.mockResolvedValue({ data: { ...shotList, shots: [{
      ...shotList.shots[2], status: "FAILED", video_status: "FAILED", review_status: "FAILED",
      generation_count: 1, version_count: 1,
    }] }, correlationId: null });
    mockGenerationOptions.mockResolvedValue({ data: { ...generationOptions, shots: [{
      ...generationOptions.shots[2], status: "FAILED", video_status: "FAILED", available: false,
    }] }, correlationId: null });
    renderContent();
    const heading = await screen.findByRole("heading", { name: "Shot 03" });
    const card = heading.closest("article")!;
    expect(within(card).getByText("生成失败")).toBeInTheDocument();
    expect(within(card).getByText("Video").parentElement).toHaveTextContent("执行失败");
    expect(within(card).getByText("Prompt").parentElement).toHaveTextContent("已就绪");
    await waitFor(() => expect(screen.getAllByRole("link", { name: "查看镜头 / 调整配置后重试" })).toHaveLength(2));
    const links = screen.getAllByRole("link", { name: "查看镜头 / 调整配置后重试" });
    expect(links[0].getAttribute("href")).toContain("/stages/shots/shot_03");
    expect(screen.getByRole("checkbox")).toBeDisabled();
    expect(screen.getByRole("button", { name: "开始生成所选镜头" })).toBeDisabled();
    expect(mockStartGeneration).not.toHaveBeenCalled();
  });

  it("preserves the Backend-provided Shot order", async () => {
    mockGetShots.mockResolvedValue({
      data: {
        ...shotList,
        shots: [
          { ...shotList.shots[1]!, order: 1 },
          { ...shotList.shots[0]!, order: 2 },
          shotList.shots[2]!,
        ],
      },
      correlationId: "req_ordered_shots",
    });
    renderContent();
    await screen.findByRole("heading", { name: "Shot 02" });
    const cards = document.querySelectorAll(".shot-summary-card");
    expect(cards[0]).toHaveTextContent("Shot 02");
    expect(cards[1]).toHaveTextContent("Shot 01");
    expect(cards[2]).toHaveTextContent("Shot 03");
  });

  it("shows the Backend aggregation without calculating it in the browser", async () => {
    renderContent();
    await screen.findByRole("heading", { name: "Shot 01" });
    const aggregation = screen.getByLabelText("镜头状态汇总");
    expect(within(aggregation).getByText("镜头总数").nextSibling).toHaveTextContent("3");
    expect(within(aggregation).getByText("已审核").nextSibling).toHaveTextContent("1");
    expect(within(aggregation).getByText("等待审核").nextSibling).toHaveTextContent("1");
    expect(within(aggregation).getByText("生成中").nextSibling).toHaveTextContent("1");
  });

  it("shows Prompt, Video, and Review states for every Shot", async () => {
    renderContent();
    const shotOne = (await screen.findByRole("heading", { name: "Shot 01" })).closest("article");
    expect(shotOne).not.toBeNull();
    expect(within(shotOne!).getAllByText("已就绪")).toHaveLength(2);
    expect(within(shotOne!).getAllByText("等待审核")).toHaveLength(2);
  });

  it("shows the official version", async () => {
    renderContent();
    expect(await screen.findByText("Video v2")).toBeInTheDocument();
  });

  it("shows a pending review version without an action", async () => {
    renderContent();
    expect(await screen.findByText("Video v3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /审核|批准|拒绝/ })).not.toBeInTheDocument();
  });

  it("safely shows no pending version", async () => {
    renderContent();
    await screen.findByRole("heading", { name: "Shot 02" });
    expect(screen.getAllByText("无")).toHaveLength(2);
  });

  it("opens a Shot Detail URL with an encoded project ID", async () => {
    renderContent();
    const links = await screen.findAllByRole("link", { name: "查看镜头" });
    expect(links[0]).toHaveAttribute(
      "href",
      "/projects/LEE%E6%9F%A0%E6%AA%AC/stages/shots/shot_01",
    );
  });

  it("selects only Backend-available Shots and submits one project plan", async () => {
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    renderContent();
    const panel = (await screen.findByRole("heading", { name: "多镜头生成计划" })).closest("section");
    expect(panel).not.toBeNull();
    const choices = await within(panel!).findAllByRole("checkbox");
    expect(choices[0]).toBeDisabled();
    expect(choices[1]).toBeDisabled();
    expect(choices[2]).toBeEnabled();
    fireEvent.click(choices[2]!);
    expect(within(panel!).getByText("可生成 1 个 · 已选择 1 个")).toBeInTheDocument();
    fireEvent.click(within(panel!).getByRole("button", { name: "开始生成所选镜头" }));
    expect(confirm).toHaveBeenCalledTimes(1);
    await waitFor(() => {
      expect(mockStartGeneration).toHaveBeenCalledWith("LEE柠檬", {
        shots: ["shot_03"],
        confirm_paid_call: true,
      });
    });
    confirm.mockRestore();
  });

  it("shows Backend project progress and concurrency without browser aggregation", async () => {
    renderContent();
    const progress = await screen.findByLabelText("项目镜头生成进度");
    expect(within(progress).getByText("总数").nextSibling).toHaveTextContent("3");
    expect(within(progress).getByText("等待审核").nextSibling).toHaveTextContent("1");
    expect(screen.getByText("最多同时生成 2 个镜头")).toBeInTheDocument();
  });

  it("shows content loading independently", () => {
    mockGetShots.mockReturnValue(
      new Promise<Awaited<ReturnType<typeof getShots>>>(() => undefined),
    );
    renderContent();
    expect(screen.getByText("正在加载镜头内容…")).toBeInTheDocument();
  });

  it("shows a safe content error and retries", async () => {
    mockGetShots
      .mockRejectedValueOnce(
        new ApiClientError({
          message: "D:\\private API_KEY",
          code: "NETWORK_ERROR",
          correlationId: "req_retry_shots",
        }),
      )
      .mockResolvedValueOnce({ data: shotList, correlationId: "req_shots" });
    renderContent();
    expect(await screen.findByText("镜头内容暂时无法读取")).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_retry_shots")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
    fireEvent.click(screen.getByRole("button", { name: "重试镜头内容" }));
    expect(await screen.findByRole("heading", { name: "Shot 01" })).toBeInTheDocument();
    expect(mockGetShots).toHaveBeenCalledTimes(2);
  });

  it("does not load or render on another Stage", () => {
    renderContent("assembly");
    expect(mockGetShots).not.toHaveBeenCalled();
    expect(mockGenerationOptions).not.toHaveBeenCalled();
    expect(screen.queryByRole("heading", { name: "镜头列表" })).not.toBeInTheDocument();
  });
});
