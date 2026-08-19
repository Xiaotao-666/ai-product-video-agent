import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getProjectTasks,
  getTask,
  regenerateVideoPrompts,
  reviseVideoPrompts,
} from "../../api/client";
import type { AvailableAction, TaskRecord, TaskStatus } from "../../api/types";
import { VideoPromptRevisionAction } from "./VideoPromptRevisionAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getProjectTasks: vi.fn(),
    getTask: vi.fn(),
    reviseVideoPrompts: vi.fn(),
    regenerateVideoPrompts: vi.fn(),
  };
});

const mockProjectTasks = vi.mocked(getProjectTasks);
const mockTask = vi.mocked(getTask);
const mockRevise = vi.mocked(reviseVideoPrompts);
const mockRegenerate = vi.mocked(regenerateVideoPrompts);
const projectId = "project-a";

function record(
  status: TaskStatus,
  operation: "VIDEO_PROMPT_REVISE" | "VIDEO_PROMPT_REGENERATE",
): TaskRecord {
  const active = status === "QUEUED" || status === "RUNNING";
  return {
    task_id: `task_${operation === "VIDEO_PROMPT_REVISE" ? "a" : "b".repeat(32)}`.replace(
      "task_a",
      `task_${"a".repeat(32)}`,
    ),
    project_id: projectId,
    operation,
    status,
    created_at: "2026-08-19T12:00:00Z",
    started_at: status === "QUEUED" ? null : "2026-08-19T12:00:01Z",
    finished_at: active ? null : "2026-08-19T12:00:02Z",
    correlation_id: "req_video_prompt_revision",
    error:
      status === "FAILED"
        ? {
            code: "VIDEO_PROMPT_OUTPUT_INVALID",
            message: "private provider detail",
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
  availableActions: AvailableAction[] = [
    "REVISE_VIDEO_PROMPTS",
    "REGENERATE_VIDEO_PROMPTS",
  ],
) {
  const onTerminalRefresh = vi.fn().mockResolvedValue(undefined);
  const onActiveTaskChange = vi.fn();
  const view = render(
    <VideoPromptRevisionAction
      projectId={projectId}
      availableActions={availableActions}
      onTerminalRefresh={onTerminalRefresh}
      onActiveTaskChange={onActiveTaskChange}
    />,
  );
  return { ...view, onTerminalRefresh, onActiveTaskChange };
}

describe("VideoPromptRevisionAction", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockProjectTasks.mockReset();
    mockTask.mockReset();
    mockRevise.mockReset();
    mockRegenerate.mockReset();
    mockProjectTasks.mockResolvedValue({
      data: { project_id: projectId, tasks: [] },
      correlationId: null,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows revise and regenerate only while review actions are allowed", async () => {
    renderAction();
    await flush();
    expect(screen.getByRole("button", { name: "修改视频提示词" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成视频提示词" })).toBeInTheDocument();
    const approved = renderAction([]);
    await flush();
    expect(approved.container).toBeEmptyDOMElement();
  });

  it("validates feedback, Cancel does not POST, and revise posts once", async () => {
    mockRevise.mockResolvedValue({
      data: record("QUEUED", "VIDEO_PROMPT_REVISE"),
      correlationId: null,
    });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改视频提示词" }));
    const input = screen.getByLabelText("修改意见");
    expect(input).toHaveAttribute("maxlength", "4000");
    expect(screen.getByRole("button", { name: "提交修改" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(mockRevise).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "修改视频提示词" }));
    fireEvent.change(screen.getByLabelText("修改意见"), {
      target: { value: "  减少运动，保持无人物  " },
    });
    const submit = screen.getByRole("button", { name: "提交修改" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    await flush();
    expect(mockRevise).toHaveBeenCalledTimes(1);
    expect(mockRevise).toHaveBeenCalledWith(projectId, "减少运动，保持无人物");
    expect(screen.getByText("修改任务已提交，排队中…")).toBeInTheDocument();
    expect(screen.getByText(/当前旧提示词会继续保留并显示/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("requires regenerate confirmation, Cancel does not POST, and confirms once", async () => {
    mockRegenerate.mockResolvedValue({
      data: record("QUEUED", "VIDEO_PROMPT_REGENERATE"),
      correlationId: null,
    });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "重新生成视频提示词" }));
    expect(screen.getByText(/重新执行每个镜头的Prompt生成与校验/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("旧Prompt可恢复");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(mockRegenerate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新生成视频提示词" }));
    const confirm = screen.getByRole("button", { name: "确认重新生成" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await flush();
    expect(mockRegenerate).toHaveBeenCalledTimes(1);
    expect(mockRegenerate).toHaveBeenCalledWith(projectId);
  });

  it.each(["VIDEO_PROMPT_REVISE", "VIDEO_PROMPT_REGENERATE"] as const)(
    "recovers active %s after F5 without a POST",
    async (operation) => {
      mockProjectTasks.mockResolvedValue({
        data: { project_id: projectId, tasks: [record("RUNNING", operation)] },
        correlationId: null,
      });
      mockTask.mockResolvedValue({
        data: record("SUCCEEDED", operation),
        correlationId: null,
      });
      const { onTerminalRefresh } = renderAction([]);
      await flush();
      expect(
        screen.getAllByText(
          operation === "VIDEO_PROMPT_REVISE"
            ? "正在修改视频提示词…"
            : "正在重新生成视频提示词…",
        ).length,
      ).toBeGreaterThan(0);
      expect(mockRevise).not.toHaveBeenCalled();
      expect(mockRegenerate).not.toHaveBeenCalled();
      await advancePoll();
      await flush();
      expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    },
  );

  it("attaches a Video Prompt task on PROJECT_BUSY but not another operation", async () => {
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: {
          project_id: projectId,
          tasks: [record("RUNNING", "VIDEO_PROMPT_REGENERATE")],
        },
        correlationId: null,
      });
    mockRevise.mockRejectedValue(
      new ApiClientError({ message: "busy", code: "PROJECT_BUSY", status: 409 }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改视频提示词" }));
    fireEvent.change(screen.getByLabelText("修改意见"), { target: { value: "调整" } });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    await flush();
    expect(screen.getAllByText("正在重新生成视频提示词…").length).toBeGreaterThan(0);
    expect(screen.queryByText("项目当前正在执行其他任务。")).not.toBeInTheDocument();
  });

  it("refreshes once on success, stops polling, and re-enables human review", async () => {
    mockRevise.mockResolvedValue({
      data: record("QUEUED", "VIDEO_PROMPT_REVISE"),
      correlationId: null,
    });
    mockTask.mockResolvedValue({
      data: record("SUCCEEDED", "VIDEO_PROMPT_REVISE"),
      correlationId: null,
    });
    const { onTerminalRefresh } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改视频提示词" }));
    fireEvent.change(screen.getByLabelText("修改意见"), { target: { value: "调整" } });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    await flush();
    await advancePoll();
    await flush();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByText("新提示词已载入，仍需再次人工审核。")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(mockTask).toHaveBeenCalledTimes(1);
  });

  it.each(["FAILED", "INTERRUPTED"] as const)(
    "stops on %s, keeps safe copy, and never auto-posts",
    async (status) => {
      mockProjectTasks.mockResolvedValue({
        data: {
          project_id: projectId,
          tasks: [record("RUNNING", "VIDEO_PROMPT_REVISE")],
        },
        correlationId: null,
      });
      mockTask.mockResolvedValue({
        data: record(status, "VIDEO_PROMPT_REVISE"),
        correlationId: null,
      });
      renderAction([]);
      await flush();
      await advancePoll();
      await flush();
      expect(mockRevise).not.toHaveBeenCalled();
      expect(mockRegenerate).not.toHaveBeenCalled();
      if (status === "FAILED") {
        expect(screen.getByText(/部分镜头的视频提示词未通过校验/)).toBeInTheDocument();
        expect(document.body).not.toHaveTextContent("private provider detail");
      } else {
        expect(screen.getByText(/不会自动再次提交/)).toBeInTheDocument();
      }
    },
  );
});
