import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, getShots } from "../../api/client";
import type { ShotListResponse } from "../../api/types";
import { ShotsStageContent } from "./ShotsStageContent";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, getShots: vi.fn() };
});

const mockGetShots = vi.mocked(getShots);

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
    expect(screen.queryByRole("heading", { name: "镜头列表" })).not.toBeInTheDocument();
  });
});
