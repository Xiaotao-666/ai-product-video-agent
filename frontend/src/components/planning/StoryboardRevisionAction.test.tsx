import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getProjectTasks,
  getTask,
  regenerateStoryboard,
  reviseStoryboard,
} from "../../api/client";
import type {
  AvailableAction,
  TaskRecord,
  TaskStatus,
} from "../../api/types";
import { StoryboardRevisionAction } from "./StoryboardRevisionAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    getProjectTasks: vi.fn(),
    getTask: vi.fn(),
    reviseStoryboard: vi.fn(),
    regenerateStoryboard: vi.fn(),
  };
});

const mockProjectTasks = vi.mocked(getProjectTasks);
const mockTask = vi.mocked(getTask);
const mockRevise = vi.mocked(reviseStoryboard);
const mockRegenerate = vi.mocked(regenerateStoryboard);
const projectId = "project-a";

function record(
  status: TaskStatus,
  operation: "STORYBOARD_REVISE" | "STORYBOARD_REGENERATE",
): TaskRecord {
  const active = status === "QUEUED" || status === "RUNNING";
  return {
    task_id: `task_${operation === "STORYBOARD_REVISE" ? "a" : "b".repeat(32)}`.replace(
      "task_a",
      `task_${"a".repeat(32)}`,
    ),
    project_id: projectId,
    operation,
    status,
    created_at: "2026-08-19T12:00:00Z",
    started_at: status === "QUEUED" ? null : "2026-08-19T12:00:01Z",
    finished_at: active ? null : "2026-08-19T12:00:02Z",
    correlation_id: "req_storyboard_revision",
    error:
      status === "FAILED"
        ? {
            code: "STORYBOARD_OUTPUT_INVALID",
            message: "分镜结果无法使用。",
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
  availableActions: AvailableAction[] = [
    "REVISE_STORYBOARD",
    "REGENERATE_STORYBOARD",
  ],
) {
  const onTerminalRefresh = vi.fn().mockResolvedValue(undefined);
  const onActiveTaskChange = vi.fn();
  const view = render(
    <StoryboardRevisionAction
      projectId={projectId}
      availableActions={availableActions}
      onTerminalRefresh={onTerminalRefresh}
      onActiveTaskChange={onActiveTaskChange}
    />,
  );
  return { ...view, onTerminalRefresh, onActiveTaskChange };
}

describe("StoryboardRevisionAction", () => {
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

  it("shows both review actions only while Backend allows them", async () => {
    renderAction();
    await flush();
    expect(screen.getByRole("button", { name: "修改分镜" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成分镜" })).toBeInTheDocument();

    const approved = renderAction([]);
    await flush();
    expect(approved.container).toBeEmptyDOMElement();
  });

  it("opens feedback, blocks empty input, and Cancel never posts", async () => {
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改分镜" }));
    expect(screen.getByLabelText("修改意见")).toHaveAttribute("maxlength", "4000");
    expect(screen.getByRole("button", { name: "提交修改" })).toBeDisabled();
    expect(screen.getByPlaceholderText(/希望保留、删除或调整的镜头/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(mockRevise).not.toHaveBeenCalled();
  });

  it("submits trimmed revise feedback once and reports QUEUED/RUNNING", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof reviseStoryboard>>) => void;
    mockRevise.mockReturnValue(new Promise((resolve) => {
      resolveSubmit = resolve;
    }));
    mockTask.mockResolvedValue({
      data: record("RUNNING", "STORYBOARD_REVISE"),
      correlationId: null,
    });
    const { onActiveTaskChange } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改分镜" }));
    fireEvent.change(screen.getByLabelText("修改意见"), {
      target: { value: "  保留镜头数量，减少旁白  " },
    });
    const submit = screen.getByRole("button", { name: "提交修改" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(mockRevise).toHaveBeenCalledTimes(1);
    expect(mockRevise).toHaveBeenCalledWith(projectId, "保留镜头数量，减少旁白");
    resolveSubmit({
      data: record("QUEUED", "STORYBOARD_REVISE"),
      correlationId: null,
    });
    await flush();
    expect(screen.getByText("修改任务已提交，排队中…")).toBeInTheDocument();
    expect(screen.getByText(/当前 Storyboard 会继续保留并显示/)).toBeInTheDocument();
    await advancePoll();
    expect(screen.getAllByText("正在修改分镜…").length).toBeGreaterThan(0);
    expect(onActiveTaskChange).toHaveBeenLastCalledWith(true);
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("requires regenerate confirmation, supports Cancel, and posts once", async () => {
    mockRegenerate.mockResolvedValue({
      data: record("QUEUED", "STORYBOARD_REGENERATE"),
      correlationId: null,
    });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "重新生成分镜" }));
    expect(screen.getByText(/已审核Creative和原项目需求/)).toBeInTheDocument();
    expect(screen.getByText(/重新执行Timeline规划/)).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("旧版本可恢复");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(mockRegenerate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "重新生成分镜" }));
    const confirm = screen.getByRole("button", { name: "确认重新生成" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await flush();
    expect(mockRegenerate).toHaveBeenCalledTimes(1);
    expect(mockRegenerate).toHaveBeenCalledWith(projectId);
  });

  it.each(["STORYBOARD_REVISE", "STORYBOARD_REGENERATE"] as const)(
    "recovers active %s after F5 without POST",
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
          operation === "STORYBOARD_REVISE"
            ? "正在修改分镜…"
            : "正在重新生成分镜…",
        ).length,
      ).toBeGreaterThan(0);
      expect(mockRevise).not.toHaveBeenCalled();
      expect(mockRegenerate).not.toHaveBeenCalled();
      await advancePoll();
      await flush();
      expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    },
  );

  it("attaches to a Storyboard revision on PROJECT_BUSY", async () => {
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: {
          project_id: projectId,
          tasks: [record("RUNNING", "STORYBOARD_REGENERATE")],
        },
        correlationId: null,
      });
    mockRevise.mockRejectedValue(
      new ApiClientError({ message: "busy", code: "PROJECT_BUSY", status: 409 }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改分镜" }));
    fireEvent.change(screen.getByLabelText("修改意见"), { target: { value: "调整" } });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    await flush();
    expect(screen.getAllByText("正在重新生成分镜…").length).toBeGreaterThan(0);
    expect(screen.queryByText("项目当前正在执行其他任务。")).not.toBeInTheDocument();
  });

  it("attaches to an existing Storyboard generate task on PROJECT_BUSY", async () => {
    const generateTask: TaskRecord = {
      ...record("RUNNING", "STORYBOARD_REVISE"),
      operation: "STORYBOARD_GENERATE",
    };
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: { project_id: projectId, tasks: [generateTask] },
        correlationId: null,
      });
    mockRegenerate.mockRejectedValue(
      new ApiClientError({ message: "busy", code: "PROJECT_BUSY", status: 409 }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "重新生成分镜" }));
    fireEvent.click(screen.getByRole("button", { name: "确认重新生成" }));
    await flush();
    expect(screen.getByText("正在生成分镜…")).toBeInTheDocument();
    expect(screen.queryByText("项目当前正在执行其他任务。")).not.toBeInTheDocument();
  });

  it("does not attach to another operation and never renders raw internals", async () => {
    const other = {
      ...record("RUNNING", "STORYBOARD_REVISE"),
      operation: "ASSEMBLY" as const,
    };
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: { project_id: projectId, tasks: [other] },
        correlationId: null,
      });
    mockRegenerate.mockRejectedValue(
      new ApiClientError({
        message: "D:\\private API_KEY provider raw response",
        code: "PROJECT_BUSY",
        status: 409,
        correlationId: "req_busy",
      }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "重新生成分镜" }));
    fireEvent.click(screen.getByRole("button", { name: "确认重新生成" }));
    await flush();
    expect(screen.getByText("项目当前正在执行其他任务。")).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_busy")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("API_KEY");
  });

  it("refreshes once on success and stops polling", async () => {
    mockRevise.mockResolvedValue({
      data: record("QUEUED", "STORYBOARD_REVISE"),
      correlationId: null,
    });
    mockTask.mockResolvedValue({
      data: record("SUCCEEDED", "STORYBOARD_REVISE"),
      correlationId: null,
    });
    const { onTerminalRefresh } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改分镜" }));
    fireEvent.change(screen.getByLabelText("修改意见"), { target: { value: "调整" } });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    await flush();
    await advancePoll();
    await flush();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    expect(screen.getByText("新 Storyboard 已载入，仍需再次人工审核。")).toBeInTheDocument();
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(mockTask).toHaveBeenCalledTimes(1);
  });

  it.each(["FAILED", "INTERRUPTED"] as const)(
    "stops on %s, keeps safe copy, and never posts again",
    async (status) => {
      mockProjectTasks.mockResolvedValue({
        data: {
          project_id: projectId,
          tasks: [record("RUNNING", "STORYBOARD_REVISE")],
        },
        correlationId: null,
      });
      mockTask.mockResolvedValue({
        data: record(status, "STORYBOARD_REVISE"),
        correlationId: null,
      });
      const { onTerminalRefresh } = renderAction([]);
      await flush();
      await advancePoll();
      await flush();
      expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
      expect(mockRevise).not.toHaveBeenCalled();
      expect(mockRegenerate).not.toHaveBeenCalled();
      if (status === "FAILED") {
        expect(screen.getAllByText("修改分镜失败。").length).toBeGreaterThan(0);
        expect(screen.getByText("分镜结果无法使用。")).toBeInTheDocument();
      } else {
        expect(screen.getByText(/不会自动再次提交/)).toBeInTheDocument();
      }
    },
  );
});
