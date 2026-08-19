import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, approveStoryboard } from "../../api/client";
import type {
  AvailableAction,
  ProjectWorkflowResponse,
} from "../../api/types";
import { StoryboardApproveAction } from "./StoryboardApproveAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, approveStoryboard: vi.fn() };
});

const mockApprove = vi.mocked(approveStoryboard);

function approvedWorkflow(): ProjectWorkflowResponse {
  return {
    project_id: "project-a",
    workflow_phase: "VIDEO_PROMPT",
    status: "APPROVED",
    stages: {
      creative: { status: "APPROVED" },
      storyboard: { status: "APPROVED" },
      video_prompt: { status: "NOT_STARTED" },
      shots: { status: "NOT_STARTED", approved: 0, total: 0 },
      assembly: { status: "NOT_STARTED", needs_update: false, version: null },
      voice: { status: "NOT_STARTED", version: null },
      subtitle: { status: "NOT_STARTED", version: null },
      music: { status: "NOT_STARTED", version: null },
      export: { status: "NOT_STARTED", version: null, created_at: null, stale: false },
    },
    available_actions: ["GENERATE_VIDEO_PROMPTS"],
    updated_at: "2026-08-19T15:00:00+08:00",
  };
}

function renderAction(
  availableActions: AvailableAction[] = [
    "APPROVE_STORYBOARD",
    "REVISE_STORYBOARD",
    "REGENERATE_STORYBOARD",
  ],
  refresh = vi.fn().mockResolvedValue(undefined),
  disabled = false,
) {
  return {
    refresh,
    ...render(
      <MemoryRouter>
        <StoryboardApproveAction
          projectId="project-a"
          availableActions={availableActions}
          onApprovedRefresh={refresh}
          disabled={disabled}
        />
      </MemoryRouter>,
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

describe("StoryboardApproveAction", () => {
  beforeEach(() => {
    mockApprove.mockReset();
    mockApprove.mockResolvedValue({
      data: approvedWorkflow(),
      correlationId: "req_storyboard_approve",
    });
  });

  it("shows the real approval only for WAITING_REVIEW actions", () => {
    const waiting = renderAction();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(screen.getByText(/确认当前分镜方案/)).toBeInTheDocument();
    waiting.unmount();

    const notStarted = renderAction(["GENERATE_STORYBOARD"]);
    expect(notStarted.container).toBeEmptyDOMElement();
    notStarted.unmount();

    renderAction(["GENERATE_VIDEO_PROMPTS"]);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    expect(screen.getByText("Storyboard 已审核通过。")).toBeInTheDocument();
  });

  it("opens confirmation without POST and cancel leaves review unchanged", () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("确认通过当前分镜方案？");
    expect(mockApprove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(mockApprove).not.toHaveBeenCalled();
  });

  it("cannot approve while a Storyboard AI task is active", () => {
    renderAction(undefined, undefined, true);
    const approve = screen.getByRole("button", { name: "审核通过" });
    expect(approve).toBeDisabled();
    fireEvent.click(approve);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockApprove).not.toHaveBeenCalled();
  });

  it("submits exactly once and disables rapid double confirmation", async () => {
    const pending = deferred<Awaited<ReturnType<typeof approveStoryboard>>>();
    mockApprove.mockReturnValue(pending.promise);
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    const confirm = screen.getByRole("button", { name: "确认通过" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(mockApprove).toHaveBeenCalledTimes(1);
    expect(mockApprove).toHaveBeenCalledWith("project-a");
    expect(screen.getByRole("button", { name: "审核中…" })).toBeDisabled();
    await act(async () => {
      pending.resolve({
        data: approvedWorkflow(),
        correlationId: "req_storyboard_approve",
      });
      await pending.promise;
    });
  });

  it("refreshes durable Project, Workflow, and Storyboard through its callback", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    renderAction(undefined, refresh);
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByText("Storyboard 已审核通过。")).toBeInTheDocument();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
  });

  it("offers Video Prompt navigation only and exposes no task progress UI", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    const link = await screen.findByRole("link", { name: "前往视频提示词" });
    expect(link).toHaveAttribute("href", "/projects/project-a/stages/video-prompt");
    expect(document.body).toHaveTextContent("没有生成视频提示词");
    for (const copy of ["QUEUED", "RUNNING", "排队中", "生成中"]) {
      expect(document.body).not.toHaveTextContent(copy);
    }
  });

  it.each([
    ["ACTION_NOT_ALLOWED", "当前项目状态不允许审核通过"],
    ["PROJECT_BUSY", "项目当前正在执行其他任务"],
    ["PROJECT_NOT_FOUND", "项目不存在或已被删除"],
    ["INVALID_PROJECT_ID", "项目不存在或已被删除"],
    ["NETWORK_ERROR", "无法连接本地 Backend"],
  ])("shows a safe %s error and correlation ID", async (code, copy) => {
    mockApprove.mockRejectedValue(
      new ApiClientError({
        message: "D:\\private API_KEY=hidden raw exception",
        code,
        correlationId: "req_safe_storyboard_error",
      }),
    );
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(copy);
    expect(screen.getByText("错误编号：req_safe_storyboard_error")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(document.body).not.toHaveTextContent("raw exception");
  });

  it("rejects a mismatched project response safely", async () => {
    mockApprove.mockResolvedValue({
      data: { ...approvedWorkflow(), project_id: "other-project" },
      correlationId: "req_mismatch",
    });
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Storyboard 审核请求暂时无法处理",
    );
  });
});
