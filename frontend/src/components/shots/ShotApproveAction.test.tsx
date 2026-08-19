import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, approveShot } from "../../api/client";
import type { ShotDetail } from "../../api/types";
import { ShotApproveAction } from "./ShotApproveAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, approveShot: vi.fn() };
});

const mockApprove = vi.mocked(approveShot);

const approvedShot: ShotDetail = {
  project_id: "project-a",
  shot_id: "shot_01",
  status: "APPROVED",
  official_version: 1,
  pending_review_version: null,
  version_count: 1,
  generation_count: 1,
  versions: [
    {
      version: 1,
      role: "OFFICIAL",
      review_status: "APPROVED",
      created_at: "2026-08-19T12:00:00+08:00",
      prompt: {
        version: 2,
        source: "ai_revision",
        visual_prompt_core: "visual core",
        final_prompt: "final prompt",
      },
      generation: {
        model: "MiniMax Hailuo 2.3",
        visual_input_mode: "NONE",
      },
      video_available: true,
    },
  ],
};

function renderAction(refresh = vi.fn().mockResolvedValue(undefined)) {
  return {
    refresh,
    ...render(
      <ShotApproveAction
        projectId="project-a"
        shotId="shot_01"
        version={1}
        onApprovedRefresh={refresh}
      />,
    ),
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("ShotApproveAction", () => {
  beforeEach(() => {
    mockApprove.mockReset();
    mockApprove.mockResolvedValue({
      data: approvedShot,
      correlationId: "req_shot_approve",
    });
  });

  it("opens the required confirmation without POST and cancel changes nothing", () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("确认通过当前视频版本？");
    expect(screen.getByRole("dialog")).toHaveTextContent("通过后该版本将成为正式版本。");
    expect(mockApprove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(mockApprove).not.toHaveBeenCalled();
  });

  it("submits exactly one synchronous POST under rapid double confirmation", async () => {
    const pending = deferred<Awaited<ReturnType<typeof approveShot>>>();
    mockApprove.mockReturnValue(pending.promise);
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    const confirm = screen.getByRole("button", { name: "确认通过" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(mockApprove).toHaveBeenCalledTimes(1);
    expect(mockApprove).toHaveBeenCalledWith("project-a", "shot_01");
    expect(screen.getByRole("button", { name: "审核中…" })).toBeDisabled();
    await act(async () => {
      pending.resolve({ data: approvedShot, correlationId: "req_shot_approve" });
      await pending.promise;
    });
  });

  it("refreshes the durable Shot and exposes no task or provider flow", async () => {
    const { refresh } = renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByText("Video v1 已成为正式版本。")).toBeInTheDocument();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    for (const unsafeFlow of ["QUEUED", "RUNNING", "MiniMax", "DeepSeek", "FFmpeg"] ) {
      expect(document.body).not.toHaveTextContent(unsafeFlow);
    }
  });

  it.each([
    ["ACTION_NOT_ALLOWED", "当前镜头状态不允许审核通过"],
    ["PROJECT_BUSY", "项目当前正在执行其他任务"],
    ["SHOT_NOT_FOUND", "镜头不存在或已被删除"],
    ["INVALID_SHOT_ID", "镜头不存在或已被删除"],
    ["NETWORK_ERROR", "无法连接本地 Backend"],
  ])("shows a safe %s error with correlation ID", async (code, copy) => {
    mockApprove.mockRejectedValue(
      new ApiClientError({
        message: "D:\\private API_KEY=hidden raw provider response",
        code,
        correlationId: "req_safe_shot_error",
      }),
    );
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(copy);
    expect(screen.getByText("错误编号：req_safe_shot_error")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(document.body).not.toHaveTextContent("raw provider response");
  });

  it("rejects a mismatched approval response without hiding the action", async () => {
    mockApprove.mockResolvedValue({
      data: { ...approvedShot, official_version: 2 },
      correlationId: "req_mismatch",
    });
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "镜头审核请求暂时无法处理",
    );
    expect(screen.getByRole("button", { name: "确认通过" })).toBeInTheDocument();
  });
});
