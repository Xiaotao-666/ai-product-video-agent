import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  generateStoryboard,
  getProjectTasks,
  getTask,
} from "../../api/client";
import type {
  AvailableAction,
  TaskOperation,
  TaskRecord,
  TaskStatus,
} from "../../api/types";
import { StoryboardGenerateAction } from "./StoryboardGenerateAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    generateStoryboard: vi.fn(),
    getProjectTasks: vi.fn(),
    getTask: vi.fn(),
  };
});

const mockGenerate = vi.mocked(generateStoryboard);
const mockProjectTasks = vi.mocked(getProjectTasks);
const mockTask = vi.mocked(getTask);
const projectId = "project-a";

function record(
  status: TaskStatus,
  operation: TaskOperation = "STORYBOARD_GENERATE",
  taskId = `task_${"s".repeat(32)}`,
): TaskRecord {
  const active = status === "QUEUED" || status === "RUNNING";
  return {
    task_id: taskId,
    project_id: projectId,
    operation,
    status,
    created_at: "2026-08-19T12:00:00Z",
    started_at: status === "QUEUED" ? null : "2026-08-19T12:00:01Z",
    finished_at: active ? null : "2026-08-19T12:00:02Z",
    correlation_id: "req_storyboard_task",
    error:
      status === "FAILED"
        ? {
            code: "STORYBOARD_OUTPUT_INVALID",
            message: "分镜生成结果未通过校验。",
            retryable: true,
          }
        : status === "INTERRUPTED"
          ? {
              code: "TASK_INTERRUPTED",
              message: "任务已中断。",
              retryable: false,
            }
          : null,
    result:
      status === "SUCCEEDED"
        ? { resource_type: "STORYBOARD", resource_id: projectId, version: null }
        : null,
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
  });
}

async function advancePoll(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2_000);
  });
}

function renderAction(
  availableActions: AvailableAction[] = ["GENERATE_STORYBOARD"],
  hasStoryboard: boolean | null = false,
) {
  const onTerminalRefresh = vi.fn().mockResolvedValue(undefined);
  const view = render(
    <StoryboardGenerateAction
      projectId={projectId}
      availableActions={availableActions}
      hasStoryboard={hasStoryboard}
      onTerminalRefresh={onTerminalRefresh}
    />,
  );
  const rerenderStoryboard = (nextHasStoryboard: boolean | null) => {
    view.rerender(
      <StoryboardGenerateAction
        projectId={projectId}
        availableActions={availableActions}
        hasStoryboard={nextHasStoryboard}
        onTerminalRefresh={onTerminalRefresh}
      />,
    );
  };
  return { ...view, onTerminalRefresh, rerenderStoryboard };
}

describe("StoryboardGenerateAction", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockGenerate.mockReset();
    mockProjectTasks.mockReset();
    mockTask.mockReset();
    mockProjectTasks.mockResolvedValue({
      data: { project_id: projectId, tasks: [] },
      correlationId: "req_tasks",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows Generate only when Backend allows it and explains the review boundary", async () => {
    renderAction();
    await flush();
    expect(screen.getByRole("button", { name: "生成分镜" })).toBeInTheDocument();
    expect(screen.getByText("未开始")).toBeInTheDocument();
    expect(screen.getByText(/生成完成后仍需人工审核/)).toBeInTheDocument();
    expect(screen.getByText(/不会自动进入视频提示词阶段/)).toBeInTheDocument();
  });

  it("does not expose an executable action when GENERATE_STORYBOARD is absent", async () => {
    renderAction(["APPROVE_STORYBOARD"]);
    await flush();
    expect(screen.queryByRole("button", { name: "生成分镜" })).not.toBeInTheDocument();
    expect(screen.getByText("当前项目状态不允许生成分镜。")).toBeInTheDocument();
  });

  it("submits the canonical project exactly once and displays QUEUED", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof generateStoryboard>>) => void;
    mockGenerate.mockReturnValue(new Promise((resolve) => {
      resolveSubmit = resolve;
    }));
    renderAction();
    await flush();
    const button = screen.getByRole("button", { name: "生成分镜" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled();
    expect(mockGenerate).toHaveBeenCalledTimes(1);
    expect(mockGenerate).toHaveBeenCalledWith(projectId);
    resolveSubmit({ data: record("QUEUED"), correlationId: "req_post" });
    await flush();
    expect(screen.getByText("排队中…")).toBeInTheDocument();
  });

  it("polls QUEUED and RUNNING without fake progress", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("RUNNING"), correlationId: null });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成分镜" }));
    await flush();
    await advancePoll();
    expect(screen.getByText("正在生成分镜…")).toBeInTheDocument();
    expect(screen.getByText("分镜生成任务正在执行。")).toBeInTheDocument();
    expect(mockTask).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("stops on SUCCEEDED and refreshes durable Project, Workflow, and Storyboard state", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("SUCCEEDED"), correlationId: null });
    const { onTerminalRefresh, rerenderStoryboard } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成分镜" }));
    await flush();
    await advancePoll();
    await flush();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    rerenderStoryboard(true);
    expect(screen.getByText("已生成")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(mockTask).toHaveBeenCalledTimes(1);
  });

  it("shows safe FAILED details and does not auto-retry", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("FAILED"), correlationId: null });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成分镜" }));
    await flush();
    await advancePoll();
    expect(screen.getByText("生成失败")).toBeInTheDocument();
    expect(screen.getByText("分镜生成结果未通过校验。")).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_storyboard_task")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(mockGenerate).toHaveBeenCalledTimes(1);
    expect(document.body).not.toHaveTextContent("Traceback");
  });

  it("refreshes INTERRUPTED state without resubmitting", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("INTERRUPTED"), correlationId: null });
    const { onTerminalRefresh } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成分镜" }));
    await flush();
    await advancePoll();
    await flush();
    expect(screen.getByText("任务中断")).toBeInTheDocument();
    expect(screen.getByText(/不会自动再次提交/)).toBeInTheDocument();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    expect(mockGenerate).toHaveBeenCalledTimes(1);
  });

  it("recovers an active Storyboard task after F5 and resumes polling without POST", async () => {
    mockProjectTasks.mockResolvedValue({
      data: { project_id: projectId, tasks: [record("RUNNING")] },
      correlationId: null,
    });
    mockTask.mockResolvedValue({ data: record("SUCCEEDED"), correlationId: null });
    const { onTerminalRefresh } = renderAction();
    await flush();
    expect(screen.getByText("正在生成分镜…")).toBeInTheDocument();
    expect(mockGenerate).not.toHaveBeenCalled();
    await advancePoll();
    await flush();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
  });

  it("attaches to an existing Storyboard task on PROJECT_BUSY", async () => {
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: { project_id: projectId, tasks: [record("RUNNING")] },
        correlationId: null,
      });
    mockGenerate.mockRejectedValue(
      new ApiClientError({ message: "busy", status: 409, code: "PROJECT_BUSY" }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成分镜" }));
    await flush();
    expect(screen.getByText("正在生成分镜…")).toBeInTheDocument();
    expect(screen.queryByText("项目当前正在执行其他任务。")).not.toBeInTheDocument();
  });

  it("does not attach to a different operation on PROJECT_BUSY", async () => {
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: { project_id: projectId, tasks: [record("RUNNING", "ASSEMBLY")] },
        correlationId: null,
      });
    mockGenerate.mockRejectedValue(
      new ApiClientError({
        message: "D:\\private raw provider response",
        status: 409,
        code: "PROJECT_BUSY",
        correlationId: "req_busy",
      }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成分镜" }));
    await flush();
    expect(screen.getByText("项目当前正在执行其他任务。")).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_busy")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
  });

  it("maps network errors to safe copy and never renders raw internals", async () => {
    mockGenerate.mockRejectedValue(
      new ApiClientError({
        message: "API_KEY secret raw fetch error",
        code: "NETWORK_ERROR",
        correlationId: "req_network",
      }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成分镜" }));
    await flush();
    expect(screen.getByText(/无法连接本地 Backend/)).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_network")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(document.body).not.toHaveTextContent("raw fetch");
  });

  it("clears polling on unmount without cancelling the durable task", async () => {
    mockProjectTasks.mockResolvedValue({
      data: { project_id: projectId, tasks: [record("RUNNING")] },
      correlationId: null,
    });
    const { unmount } = renderAction();
    await flush();
    unmount();
    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(mockTask).not.toHaveBeenCalled();
    expect(mockGenerate).not.toHaveBeenCalled();
  });
});
