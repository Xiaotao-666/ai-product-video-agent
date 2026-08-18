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
  status: "COMPLETED",
  shots: [
    {
      shot_id: "shot_01",
      status: "APPROVED",
      official_version: 2,
      pending_review_version: 3,
      version_count: 3,
      generation_count: 3,
    },
    {
      shot_id: "shot_02",
      status: "APPROVED",
      official_version: 1,
      pending_review_version: null,
      version_count: 1,
      generation_count: 1,
    },
    {
      shot_id: "shot_03",
      status: "APPROVED",
      official_version: 1,
      pending_review_version: null,
      version_count: 1,
      generation_count: 1,
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
