import { act, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, approveCreative } from "../../api/client";
import type {
  AvailableAction,
  ProjectWorkflowResponse,
} from "../../api/types";
import { CreativeApproveAction } from "./CreativeApproveAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return { ...actual, approveCreative: vi.fn() };
});

const mockApprove = vi.mocked(approveCreative);

function approvedWorkflow(): ProjectWorkflowResponse {
  return {
    project_id: "project-a",
    workflow_phase: "STORYBOARD",
    status: "APPROVED",
    stages: {
      creative: { status: "APPROVED" },
      storyboard: { status: "NOT_STARTED" },
      video_prompt: { status: "NOT_STARTED" },
      shots: { status: "NOT_STARTED", approved: 0, total: 0 },
      assembly: { status: "NOT_STARTED", needs_update: false, version: null },
      voice: { status: "NOT_STARTED", version: null },
      subtitle: { status: "NOT_STARTED", version: null },
      music: { status: "NOT_STARTED", version: null },
      export: { status: "NOT_STARTED", version: null, created_at: null, stale: false },
    },
    available_actions: ["GENERATE_STORYBOARD"],
    updated_at: "2026-08-18T14:30:00+08:00",
  };
}

function renderAction(
  availableActions: AvailableAction[] = [
    "APPROVE_CREATIVE",
    "REVISE_CREATIVE",
    "REGENERATE_CREATIVE",
  ],
  refresh = vi.fn().mockResolvedValue(undefined),
) {
  return {
    refresh,
    ...render(
      <MemoryRouter>
        <CreativeApproveAction
          projectId="project-a"
          availableActions={availableActions}
          onApprovedRefresh={refresh}
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

describe("CreativeApproveAction", () => {
  beforeEach(() => {
    mockApprove.mockReset();
    mockApprove.mockResolvedValue({
      data: approvedWorkflow(),
      correlationId: "req_approve",
    });
  });

  it("shows the real approve button only when the action is available", () => {
    renderAction();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "修改创意" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新生成创意" })).not.toBeInTheDocument();
  });

  it("opens a lightweight confirmation without submitting immediately", () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("确认 Creative 审核通过？");
    expect(mockApprove).not.toHaveBeenCalled();
  });

  it("cancels confirmation without calling Backend", () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockApprove).not.toHaveBeenCalled();
  });

  it("submits exactly once and disables confirmation while pending", async () => {
    const pending = deferred<Awaited<ReturnType<typeof approveCreative>>>();
    mockApprove.mockReturnValue(pending.promise);
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    const confirm = screen.getByRole("button", { name: "确认通过" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(mockApprove).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "审核中…" })).toBeDisabled();
    await act(async () => {
      pending.resolve({ data: approvedWorkflow(), correlationId: "req_approve" });
      await pending.promise;
    });
  });

  it("refreshes durable Project, Workflow and Creative state through its callback", async () => {
    const refresh = vi.fn().mockResolvedValue(undefined);
    renderAction(undefined, refresh);
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    await screen.findByText("Creative 已审核通过。");
    expect(mockApprove).toHaveBeenCalledWith("project-a");
    expect(refresh).toHaveBeenCalledTimes(1);
  });

  it("shows Storyboard as navigation only after success", async () => {
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    const link = await screen.findByRole("link", { name: "前往 Storyboard" });
    expect(link).toHaveAttribute("href", "/projects/project-a/stages/storyboard");
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    expect(document.body).toHaveTextContent("不会自动生成分镜");
  });

  it.each([
    ["ACTION_NOT_ALLOWED", "当前项目状态不允许审核通过"],
    ["PROJECT_BUSY", "项目当前正在执行其他任务"],
    ["PROJECT_NOT_FOUND", "项目不存在或已被删除"],
    ["NETWORK_ERROR", "无法连接本地 Backend"],
  ])("shows a safe %s error with correlation ID", async (code, copy) => {
    mockApprove.mockRejectedValue(
      new ApiClientError({
        message: "D:\\private API_KEY=hidden",
        code,
        correlationId: "req_safe_error",
      }),
    );
    renderAction();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(copy);
    expect(screen.getByText("错误编号：req_safe_error")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("API_KEY");
  });

  it("renders nothing when approval and Storyboard continuation are unavailable", () => {
    const { container } = renderAction(["REVISE_CREATIVE"]);
    expect(container).toBeEmptyDOMElement();
  });

  it("restores an approved reload from Backend actions without local storage", () => {
    renderAction(["GENERATE_STORYBOARD"]);
    expect(screen.getByText("Creative 已审核通过。")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "前往 Storyboard" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/localStorage|sessionStorage/);
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
      "Creative 审核请求暂时无法处理",
    );
  });
});
