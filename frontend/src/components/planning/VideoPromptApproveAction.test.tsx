import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, approveVideoPrompts } from "../../api/client";
import type {
  AvailableAction,
  ProjectWorkflowResponse,
} from "../../api/types";
import { VideoPromptApproveAction } from "./VideoPromptApproveAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, approveVideoPrompts: vi.fn() };
});

const mockApprove = vi.mocked(approveVideoPrompts);

function approvedWorkflow(): ProjectWorkflowResponse {
  return {
    project_id: "project-a",
    workflow_phase: "VIDEO_GENERATION",
    status: "APPROVED",
    stages: {
      creative: { status: "APPROVED" },
      storyboard: { status: "APPROVED" },
      video_prompt: { status: "APPROVED" },
      shots: { status: "NOT_STARTED", approved: 0, total: 3 },
      assembly: { status: "NOT_STARTED", needs_update: false, version: null },
      voice: { status: "NOT_STARTED", version: null },
      subtitle: { status: "NOT_STARTED", version: null },
      music: { status: "NOT_STARTED", version: null },
      export: { status: "NOT_STARTED", version: null, created_at: null, stale: false },
    },
    available_actions: ["GENERATE_SHOTS"],
    updated_at: "2026-08-19T15:00:00+08:00",
  };
}

function renderAction(
  availableActions: AvailableAction[] = [
    "APPROVE_VIDEO_PROMPTS",
    "REVISE_VIDEO_PROMPTS",
    "REGENERATE_VIDEO_PROMPTS",
  ],
  refresh = vi.fn().mockResolvedValue(undefined),
  disabled = false,
) {
  return {
    refresh,
    ...render(
      <MemoryRouter>
        <VideoPromptApproveAction
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

describe("VideoPromptApproveAction", () => {
  beforeEach(() => {
    mockApprove.mockReset();
    mockApprove.mockResolvedValue({
      data: approvedWorkflow(),
      correlationId: "req_video_prompt_approve",
    });
  });

  it("shows approval only while Video Prompts wait for review", () => {
    const waiting = renderAction();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(screen.getByText(/确认当前所有镜头的视频提示词/)).toBeInTheDocument();
    waiting.unmount();

    const notStarted = renderAction(["GENERATE_VIDEO_PROMPTS"]);
    expect(notStarted.container).toBeEmptyDOMElement();
    notStarted.unmount();

    renderAction(["GENERATE_SHOTS"]);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    expect(screen.getByText("视频提示词已审核通过。")).toBeInTheDocument();
  });

  it("opens confirmation without POST and cancel changes nothing", () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("确认通过当前视频提示词？");
    expect(screen.getByRole("dialog")).toHaveTextContent("不会自动生成视频");
    expect(mockApprove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(mockApprove).not.toHaveBeenCalled();
  });

  it("cannot approve while a project task is active", () => {
    renderAction(undefined, undefined, true);
    const approve = screen.getByRole("button", { name: "审核通过" });
    expect(approve).toBeDisabled();
    fireEvent.click(approve);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockApprove).not.toHaveBeenCalled();
  });

  it("submits exactly once and disables rapid double confirmation", async () => {
    const pending = deferred<Awaited<ReturnType<typeof approveVideoPrompts>>>();
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
        correlationId: "req_video_prompt_approve",
      });
      await pending.promise;
    });
  });

  it("refreshes durable state, hides approval, and only navigates to Shots", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    renderAction(undefined, refresh);
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByText("视频提示词已审核通过。")).toBeInTheDocument();
    expect(refresh).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往镜头" })).toHaveAttribute(
      "href",
      "/projects/project-a/stages/shots",
    );
    for (const copy of ["QUEUED", "RUNNING", "排队中", "正在生成镜头"]) {
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
        correlationId: "req_safe_video_prompt_error",
      }),
    );
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(copy);
    expect(screen.getByText("错误编号：req_safe_video_prompt_error")).toBeInTheDocument();
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
      "视频提示词审核请求暂时无法处理",
    );
  });
});
