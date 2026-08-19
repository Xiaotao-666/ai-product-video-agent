import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  generateVideoPrompts,
  getProjectTasks,
  getTask,
} from "../../api/client";
import type {
  AvailableAction,
  TaskOperation,
  TaskRecord,
  TaskStatus,
} from "../../api/types";
import { VideoPromptGenerateAction } from "./VideoPromptGenerateAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    generateVideoPrompts: vi.fn(),
    getProjectTasks: vi.fn(),
    getTask: vi.fn(),
  };
});

const mockGenerate = vi.mocked(generateVideoPrompts);
const mockProjectTasks = vi.mocked(getProjectTasks);
const mockTask = vi.mocked(getTask);
const projectId = "project-a";

function record(
  status: TaskStatus,
  operation: TaskOperation = "VIDEO_PROMPT_GENERATE",
  errorCode = "VIDEO_PROMPT_OUTPUT_INVALID",
): TaskRecord {
  const active = status === "QUEUED" || status === "RUNNING";
  return {
    task_id: `task_${"v".repeat(32)}`,
    project_id: projectId,
    operation,
    status,
    created_at: "2026-08-19T12:00:00Z",
    started_at: status === "QUEUED" ? null : "2026-08-19T12:00:01Z",
    finished_at: active ? null : "2026-08-19T12:00:02Z",
    correlation_id: "req_video_prompt_task",
    error:
      status === "FAILED"
        ? {
            code: errorCode,
            message: "D:\\private\\raw response API_KEY secret",
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
        ? { resource_type: "VIDEO_PROMPTS", resource_id: projectId, version: null }
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
  availableActions: AvailableAction[] = ["GENERATE_VIDEO_PROMPTS"],
  videoPromptStatus = "NOT_STARTED",
  hasVideoPrompts: boolean | null = false,
) {
  const onTerminalRefresh = vi.fn().mockResolvedValue(undefined);
  const view = render(
    <VideoPromptGenerateAction
      projectId={projectId}
      availableActions={availableActions}
      videoPromptStatus={videoPromptStatus}
      hasVideoPrompts={hasVideoPrompts}
      onTerminalRefresh={onTerminalRefresh}
    />,
  );
  const rerenderState = (
    nextStatus: string,
    nextHasVideoPrompts: boolean | null,
    nextActions: AvailableAction[] = availableActions,
  ) => {
    view.rerender(
      <VideoPromptGenerateAction
        projectId={projectId}
        availableActions={nextActions}
        videoPromptStatus={nextStatus}
        hasVideoPrompts={nextHasVideoPrompts}
        onTerminalRefresh={onTerminalRefresh}
      />,
    );
  };
  return { ...view, onTerminalRefresh, rerenderState };
}

describe("VideoPromptGenerateAction", () => {
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

  it("shows Generate only for the Backend action and explains review boundaries", async () => {
    renderAction();
    await flush();
    expect(screen.getByRole("button", { name: "生成视频提示词" })).toBeInTheDocument();
    expect(screen.getByText(/根据已审核分镜，为每个镜头生成/)).toBeInTheDocument();
    expect(screen.getByText(/不会自动生成镜头视频/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("does not expose Generate before Storyboard approval", async () => {
    renderAction([], "NOT_STARTED", false);
    await flush();
    expect(screen.queryByRole("button", { name: "生成视频提示词" })).not.toBeInTheDocument();
    expect(screen.getByText("当前项目状态不允许生成视频提示词。")).toBeInTheDocument();
  });

  it("submits exactly once and displays QUEUED", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof generateVideoPrompts>>) => void;
    mockGenerate.mockReturnValue(new Promise((resolve) => {
      resolveSubmit = resolve;
    }));
    renderAction();
    await flush();
    const button = screen.getByRole("button", { name: "生成视频提示词" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mockGenerate).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled();
    resolveSubmit({ data: record("QUEUED"), correlationId: "req_post" });
    await flush();
    expect(screen.getByText("排队中…")).toBeInTheDocument();
  });

  it("polls RUNNING without fake progress", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("RUNNING"), correlationId: null });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成视频提示词" }));
    await flush();
    await advancePoll();
    expect(screen.getByText("正在生成视频提示词…")).toBeInTheDocument();
    expect(screen.getByText("视频提示词生成任务正在执行。")).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("stops on success, refreshes durable state, and hides Generate", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("SUCCEEDED"), correlationId: null });
    const { onTerminalRefresh, rerenderState } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成视频提示词" }));
    await flush();
    await advancePoll();
    await flush();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    rerenderState("WAITING_REVIEW", true, ["APPROVE_VIDEO_PROMPTS"]);
    expect(screen.getByText("已生成")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成视频提示词" })).not.toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(mockTask).toHaveBeenCalledTimes(1);
  });

  it("shows safe invalid-output failure and an explicit manual retry", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("FAILED"), correlationId: null });
    const { rerenderState } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成视频提示词" }));
    await flush();
    await advancePoll();
    await flush();
    rerenderState("FAILED", false);
    expect(screen.getByText("视频提示词生成失败。")).toBeInTheDocument();
    expect(screen.getByText(/部分镜头的视频提示词未通过校验/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新尝试生成" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(mockGenerate).toHaveBeenCalledTimes(1);
  });

  it("recovers an active task after F5 without POST", async () => {
    mockProjectTasks.mockResolvedValue({
      data: { project_id: projectId, tasks: [record("RUNNING")] },
      correlationId: null,
    });
    mockTask.mockResolvedValue({ data: record("SUCCEEDED"), correlationId: null });
    const { onTerminalRefresh } = renderAction([], "RUNNING", false);
    await flush();
    expect(screen.getByText("正在生成视频提示词…")).toBeInTheDocument();
    expect(mockGenerate).not.toHaveBeenCalled();
    await advancePoll();
    await flush();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
  });

  it("recovers INTERRUPTED terminal state without automatic POST", async () => {
    mockProjectTasks.mockResolvedValue({
      data: { project_id: projectId, tasks: [record("INTERRUPTED")] },
      correlationId: null,
    });
    renderAction(["GENERATE_VIDEO_PROMPTS"], "RUNNING", false);
    await flush();
    expect(screen.getByText("任务中断")).toBeInTheDocument();
    expect(screen.getByText(/不会自动重新提交/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新尝试生成" })).toBeInTheDocument();
    expect(mockGenerate).not.toHaveBeenCalled();
  });

  it("attaches to an existing Video Prompt task on PROJECT_BUSY", async () => {
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
    fireEvent.click(screen.getByRole("button", { name: "生成视频提示词" }));
    await flush();
    expect(screen.getByText("正在生成视频提示词…")).toBeInTheDocument();
    expect(screen.queryByText("项目当前正在执行其他任务。")).not.toBeInTheDocument();
  });

  it("reports another operation as busy without leaking raw errors", async () => {
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: { project_id: projectId, tasks: [record("RUNNING", "ASSEMBLY")] },
        correlationId: null,
      });
    mockGenerate.mockRejectedValue(
      new ApiClientError({
        message: "D:\\secret API_KEY raw",
        status: 409,
        code: "PROJECT_BUSY",
        correlationId: "req_busy",
      }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成视频提示词" }));
    await flush();
    expect(screen.getByText("项目当前正在执行其他任务。")).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_busy")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\secret");
    expect(document.body).not.toHaveTextContent("API_KEY");
  });
});
