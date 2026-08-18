import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  generateCreative,
  getProjectTasks,
  getTask,
  regenerateCreative,
  reviseCreative,
} from "../../api/client";
import type {
  AvailableAction,
  TaskOperation,
  TaskRecord,
  TaskStatus,
} from "../../api/types";
import { CreativeGenerateAction } from "./CreativeGenerateAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    generateCreative: vi.fn(),
    getProjectTasks: vi.fn(),
    getTask: vi.fn(),
    regenerateCreative: vi.fn(),
    reviseCreative: vi.fn(),
  };
});

const mockGenerate = vi.mocked(generateCreative);
const mockProjectTasks = vi.mocked(getProjectTasks);
const mockTask = vi.mocked(getTask);
const mockRegenerate = vi.mocked(regenerateCreative);
const mockRevise = vi.mocked(reviseCreative);
const projectId = "project-a";

function record(
  status: TaskStatus,
  operation: TaskOperation = "CREATIVE_GENERATE",
  taskId = `task_${"a".repeat(32)}`,
): TaskRecord {
  const active = status === "QUEUED" || status === "RUNNING";
  const failed = status === "FAILED" || status === "INTERRUPTED";
  return {
    task_id: taskId,
    project_id: projectId,
    operation,
    status,
    created_at: "2026-08-18T12:00:00Z",
    started_at: status === "QUEUED" ? null : "2026-08-18T12:00:01Z",
    finished_at: active ? null : "2026-08-18T12:00:02Z",
    correlation_id: "req_creative_task",
    error: failed
      ? {
          code: status === "INTERRUPTED" ? "TASK_INTERRUPTED" : "PROVIDER_REQUEST_FAILED",
          message: status === "INTERRUPTED" ? "任务已中断。" : "创意生成服务暂时不可用。",
          retryable: status === "FAILED",
        }
      : null,
    result:
      status === "SUCCEEDED"
        ? { resource_type: "CREATIVE", resource_id: projectId, version: null }
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
  availableActions: AvailableAction[] = ["GENERATE_CREATIVE"],
  hasCreative: boolean | null = false,
) {
  const onTerminalRefresh = vi.fn().mockResolvedValue(undefined);
  const onActiveTaskChange = vi.fn();
  const view = render(
    <CreativeGenerateAction
      projectId={projectId}
      availableActions={availableActions}
      hasCreative={hasCreative}
      onTerminalRefresh={onTerminalRefresh}
      onActiveTaskChange={onActiveTaskChange}
    />,
  );
  const rerenderCreative = (nextHasCreative: boolean | null) => {
    view.rerender(
      <CreativeGenerateAction
        projectId={projectId}
        availableActions={availableActions}
        hasCreative={nextHasCreative}
        onTerminalRefresh={onTerminalRefresh}
        onActiveTaskChange={onActiveTaskChange}
      />,
    );
  };
  return { ...view, onTerminalRefresh, onActiveTaskChange, rerenderCreative };
}

describe("CreativeGenerateAction", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockGenerate.mockReset();
    mockProjectTasks.mockReset();
    mockTask.mockReset();
    mockRegenerate.mockReset();
    mockRevise.mockReset();
    mockProjectTasks.mockResolvedValue({
      data: { project_id: projectId, tasks: [] },
      correlationId: "req_tasks",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("shows the real Generate button only when Backend allows it", async () => {
    renderAction();
    await flush();
    expect(screen.getByRole("button", { name: "生成创意" })).toBeInTheDocument();
    expect(screen.getByText("未开始")).toBeInTheDocument();
  });

  it("does not expose executable Generate without GENERATE_CREATIVE", async () => {
    renderAction(["APPROVE_CREATIVE"]);
    await flush();
    expect(screen.queryByRole("button", { name: "生成创意" })).not.toBeInTheDocument();
    expect(screen.getByText("当前项目状态不允许生成创意。")).toBeInTheDocument();
  });

  it.each([
    ["WAITING_REVIEW", ["APPROVE_CREATIVE"] satisfies AvailableAction[]],
    ["APPROVED", ["GENERATE_STORYBOARD"] satisfies AvailableAction[]],
  ])(
    "uses persisted Creative content for %s instead of available actions",
    async (_reviewState, actions) => {
      renderAction(actions, true);
      await flush();
      expect(screen.getByText("已生成")).toBeInTheDocument();
      expect(screen.queryByText("未开始")).not.toBeInTheDocument();
      expect(
        screen.queryByText("当前项目状态不允许生成创意。"),
      ).not.toBeInTheDocument();
    },
  );

  it("submits the correct project and displays QUEUED", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: "req_post" });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    expect(mockGenerate).toHaveBeenCalledWith(projectId);
    expect(screen.getByText("排队中…")).toBeInTheDocument();
  });

  it("disables immediately and double click sends only one POST", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof generateCreative>>) => void;
    mockGenerate.mockReturnValue(
      new Promise((resolve) => {
        resolveSubmit = resolve;
      }),
    );
    renderAction();
    await flush();
    const button = screen.getByRole("button", { name: "生成创意" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled();
    expect(mockGenerate).toHaveBeenCalledTimes(1);
    resolveSubmit({ data: record("QUEUED"), correlationId: "req_post" });
    await flush();
  });

  it("polls while RUNNING and never displays fake percentages", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("RUNNING"), correlationId: null });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    await advancePoll();
    expect(screen.getByText("正在生成创意…")).toBeInTheDocument();
    expect(mockTask).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("continues polling for active states", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("RUNNING"), correlationId: null });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    await advancePoll();
    await advancePoll();
    expect(mockTask).toHaveBeenCalledTimes(2);
  });

  it("stops polling on SUCCEEDED and refreshes business state", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("SUCCEEDED"), correlationId: null });
    const { onTerminalRefresh } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    await advancePoll();
    await flush();
    expect(screen.getByText("生成成功")).toBeInTheDocument();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(mockTask).toHaveBeenCalledTimes(1);
  });

  it("shows persisted Generated after a SUCCEEDED refresh", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("SUCCEEDED"), correlationId: null });
    const { rerenderCreative } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    await advancePoll();
    await flush();
    rerenderCreative(true);
    expect(screen.getByText("已生成")).toBeInTheDocument();
    expect(screen.queryByText("未开始")).not.toBeInTheDocument();
  });

  it("stops polling on FAILED and displays only safe task error fields", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("FAILED"), correlationId: null });
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    await advancePoll();
    expect(screen.getByText("生成失败")).toBeInTheDocument();
    expect(screen.getByText("创意生成失败。")).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_creative_task")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("Traceback");
  });

  it("handles INTERRUPTED by refreshing and never auto-posting", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("INTERRUPTED"), correlationId: null });
    const { onTerminalRefresh } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    await advancePoll();
    await flush();
    expect(screen.getByText("任务中断")).toBeInTheDocument();
    expect(screen.getByText("上次生成任务被中断。")).toBeInTheDocument();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    expect(mockGenerate).toHaveBeenCalledTimes(1);
  });

  it("prefers persisted Creative over an INTERRUPTED task record", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("INTERRUPTED"), correlationId: null });
    const { rerenderCreative } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    await advancePoll();
    await flush();
    rerenderCreative(true);
    expect(screen.getByText("已生成")).toBeInTheDocument();
    expect(screen.queryByText("任务中断")).not.toBeInTheDocument();
    expect(screen.queryByText("上次生成任务被中断。")).not.toBeInTheDocument();
  });

  it("prefers persisted Creative over a FAILED task record", async () => {
    mockGenerate.mockResolvedValue({ data: record("QUEUED"), correlationId: null });
    mockTask.mockResolvedValue({ data: record("FAILED"), correlationId: null });
    const { rerenderCreative } = renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    await advancePoll();
    await flush();
    rerenderCreative(true);
    expect(screen.getByText("已生成")).toBeInTheDocument();
    expect(screen.queryByText("生成失败")).not.toBeInTheDocument();
    expect(screen.queryByText("创意生成失败。")).not.toBeInTheDocument();
  });

  it("recovers an active Creative task after page refresh and resumes polling", async () => {
    mockProjectTasks.mockResolvedValue({
      data: { project_id: projectId, tasks: [record("RUNNING")] },
      correlationId: null,
    });
    mockTask.mockResolvedValue({ data: record("SUCCEEDED"), correlationId: null });
    const { onTerminalRefresh } = renderAction();
    await flush();
    expect(screen.getByText("正在生成创意…")).toBeInTheDocument();
    expect(mockGenerate).not.toHaveBeenCalled();
    await advancePoll();
    await flush();
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
  });

  it("PROJECT_BUSY attaches to an existing active Creative task", async () => {
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: { project_id: projectId, tasks: [record("RUNNING")] },
        correlationId: null,
      });
    mockGenerate.mockRejectedValue(
      new ApiClientError({ message: "busy raw", status: 409, code: "PROJECT_BUSY" }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    expect(screen.getByText("正在生成创意…")).toBeInTheDocument();
    expect(screen.queryByText("项目当前正在执行其他任务。")).not.toBeInTheDocument();
  });

  it("PROJECT_BUSY from another operation shows the safe Busy state", async () => {
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: { project_id: projectId, tasks: [record("RUNNING", "ASSEMBLY")] },
        correlationId: null,
      });
    mockGenerate.mockRejectedValue(
      new ApiClientError({
        message: "D:\\private provider raw",
        status: 409,
        code: "PROJECT_BUSY",
        correlationId: "req_busy",
      }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    expect(screen.getByText("项目当前正在执行其他任务。")).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_busy")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
  });

  it("maps network failures to safe copy and correlation ID", async () => {
    mockGenerate.mockRejectedValue(
      new ApiClientError({
        message: "D:\\secret API_KEY raw fetch error",
        code: "NETWORK_ERROR",
        correlationId: "req_network",
      }),
    );
    renderAction();
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "生成创意" }));
    await flush();
    expect(screen.getByText(/无法连接本地 Backend/)).toBeInTheDocument();
    expect(screen.getByText("错误编号：req_network")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(document.body).not.toHaveTextContent("raw fetch");
  });

  it("unmount clears polling timer without cancelling the Backend task", async () => {
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

  it("shows distinct Revise and Regenerate actions only while review allows them", async () => {
    const { rerender } = renderAction(
      ["APPROVE_CREATIVE", "REVISE_CREATIVE", "REGENERATE_CREATIVE"],
      true,
    );
    await flush();
    expect(screen.getByRole("button", { name: "修改创意" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成创意" })).toBeInTheDocument();

    rerender(
      <CreativeGenerateAction
        projectId={projectId}
        availableActions={["GENERATE_STORYBOARD"]}
        hasCreative
        onTerminalRefresh={vi.fn().mockResolvedValue(undefined)}
      />,
    );
    expect(screen.queryByRole("button", { name: "修改创意" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "重新生成创意" })).not.toBeInTheDocument();
  });

  it("opens and cancels the lightweight feedback panel without POST", async () => {
    renderAction(["REVISE_CREATIVE", "REGENERATE_CREATIVE"], true);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改创意" }));
    const feedback = screen.getByRole("textbox", { name: "修改意见" });
    expect(feedback).toHaveAttribute("maxlength", "4000");
    expect(screen.getByRole("button", { name: "提交修改" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("textbox", { name: "修改意见" })).not.toBeInTheDocument();
    expect(mockRevise).not.toHaveBeenCalled();
  });

  it("trims feedback, submits Revise once, and locks editing immediately", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof reviseCreative>>) => void;
    mockRevise.mockReturnValue(
      new Promise((resolve) => {
        resolveSubmit = resolve;
      }),
    );
    renderAction(["REVISE_CREATIVE", "REGENERATE_CREATIVE"], true);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改创意" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修改意见" }), {
      target: { value: "  保留主题，不要人物  " },
    });
    const submit = screen.getByRole("button", { name: "提交修改" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    expect(mockRevise).toHaveBeenCalledTimes(1);
    expect(mockRevise).toHaveBeenCalledWith(projectId, "保留主题，不要人物");
    expect(screen.getByRole("textbox", { name: "修改意见" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled();
    resolveSubmit({
      data: record("QUEUED", "CREATIVE_REVISE"),
      correlationId: "req_revise",
    });
    await flush();
    expect(screen.queryByRole("textbox", { name: "修改意见" })).not.toBeInTheDocument();
    expect(screen.getByText("修改任务已提交，排队中…")).toBeInTheDocument();
  });

  it("opens Regenerate confirmation and cancel performs no POST", async () => {
    renderAction(["REVISE_CREATIVE", "REGENERATE_CREATIVE"], true);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "重新生成创意" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("确认重新生成 Creative？");
    expect(screen.getByRole("dialog")).not.toHaveTextContent("可随时恢复");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockRegenerate).not.toHaveBeenCalled();
  });

  it("confirms Regenerate once without sending feedback", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof regenerateCreative>>) => void;
    mockRegenerate.mockReturnValue(
      new Promise((resolve) => {
        resolveSubmit = resolve;
      }),
    );
    renderAction(["REVISE_CREATIVE", "REGENERATE_CREATIVE"], true);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "重新生成创意" }));
    const confirm = screen.getByRole("button", { name: "确认重新生成" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(mockRegenerate).toHaveBeenCalledTimes(1);
    expect(mockRegenerate).toHaveBeenCalledWith(projectId);
    expect(screen.getByRole("button", { name: "正在提交…" })).toBeDisabled();
    resolveSubmit({
      data: record("QUEUED", "CREATIVE_REGENERATE"),
      correlationId: "req_regenerate",
    });
    await flush();
    expect(screen.getByText("重新生成任务已提交，排队中…")).toBeInTheDocument();
  });

  it.each([
    ["CREATIVE_REVISE", "正在修改创意…"],
    ["CREATIVE_REGENERATE", "正在重新生成创意…"],
  ] satisfies Array<[TaskOperation, string]>) (
    "recovers an active %s task after refresh with operation-specific copy",
    async (operation, copy) => {
      mockProjectTasks.mockResolvedValue({
        data: { project_id: projectId, tasks: [record("RUNNING", operation)] },
        correlationId: null,
      });
      mockTask.mockResolvedValue({
        data: record("SUCCEEDED", operation),
        correlationId: null,
      });
      const { onTerminalRefresh, onActiveTaskChange } = renderAction(
        ["REVISE_CREATIVE", "REGENERATE_CREATIVE"],
        true,
      );
      await flush();
      expect(screen.getByText(copy)).toBeInTheDocument();
      expect(screen.getByText("正在生成新的创意方案…")).toBeInTheDocument();
      expect(onActiveTaskChange).toHaveBeenCalledWith(true);
      expect(mockRevise).not.toHaveBeenCalled();
      expect(mockRegenerate).not.toHaveBeenCalled();
      await advancePoll();
      await flush();
      expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    },
  );

  it("Revise success stops polling and refreshes durable business state", async () => {
    mockRevise.mockResolvedValue({
      data: record("QUEUED", "CREATIVE_REVISE"),
      correlationId: null,
    });
    mockTask.mockResolvedValue({
      data: record("SUCCEEDED", "CREATIVE_REVISE"),
      correlationId: null,
    });
    const { onTerminalRefresh } = renderAction(["REVISE_CREATIVE"], true);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改创意" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修改意见" }), {
      target: { value: "调整为产品微距" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    await flush();
    await advancePoll();
    await flush();
    expect(screen.getAllByText("创意修改完成。").length).toBeGreaterThan(0);
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    await act(async () => vi.advanceTimersByTimeAsync(10_000));
    expect(mockTask).toHaveBeenCalledTimes(1);
  });

  it("Revise failure stops polling while persisted old Creative remains visible", async () => {
    mockRevise.mockResolvedValue({
      data: record("QUEUED", "CREATIVE_REVISE"),
      correlationId: null,
    });
    mockTask.mockResolvedValue({
      data: record("FAILED", "CREATIVE_REVISE"),
      correlationId: null,
    });
    renderAction(["REVISE_CREATIVE", "REGENERATE_CREATIVE"], true);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改创意" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修改意见" }), {
      target: { value: "调整" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    await flush();
    await advancePoll();
    expect(screen.getByText("已生成")).toBeInTheDocument();
    expect(screen.getAllByText("修改创意失败。").length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toMatch(/\d+%/);
  });

  it("Interrupted Regenerate refreshes state without automatic resubmission", async () => {
    mockProjectTasks.mockResolvedValue({
      data: {
        project_id: projectId,
        tasks: [record("RUNNING", "CREATIVE_REGENERATE")],
      },
      correlationId: null,
    });
    mockTask.mockResolvedValue({
      data: record("INTERRUPTED", "CREATIVE_REGENERATE"),
      correlationId: null,
    });
    const { onTerminalRefresh } = renderAction(
      ["REVISE_CREATIVE", "REGENERATE_CREATIVE"],
      true,
    );
    await flush();
    await advancePoll();
    await flush();
    expect(screen.getAllByText("重新生成任务已中断。").length).toBeGreaterThan(0);
    expect(onTerminalRefresh).toHaveBeenCalledTimes(1);
    expect(mockRegenerate).not.toHaveBeenCalled();
  });

  it("PROJECT_BUSY Revise attaches to an existing Creative Regenerate task", async () => {
    mockProjectTasks
      .mockResolvedValueOnce({ data: { project_id: projectId, tasks: [] }, correlationId: null })
      .mockResolvedValueOnce({
        data: {
          project_id: projectId,
          tasks: [record("RUNNING", "CREATIVE_REGENERATE")],
        },
        correlationId: null,
      });
    mockRevise.mockRejectedValue(
      new ApiClientError({
        message: "busy",
        status: 409,
        code: "PROJECT_BUSY",
      }),
    );
    renderAction(["REVISE_CREATIVE", "REGENERATE_CREATIVE"], true);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改创意" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修改意见" }), {
      target: { value: "调整" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    await flush();
    expect(screen.getByText("正在重新生成创意…")).toBeInTheDocument();
    expect(screen.queryByText("项目当前正在执行其他任务。")).not.toBeInTheDocument();
  });

  it("does not use browser storage or put feedback into a URL", async () => {
    mockRevise.mockResolvedValue({
      data: record("QUEUED", "CREATIVE_REVISE"),
      correlationId: null,
    });
    renderAction(["REVISE_CREATIVE"], true);
    await flush();
    fireEvent.click(screen.getByRole("button", { name: "修改创意" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修改意见" }), {
      target: { value: "URL中不能出现的反馈" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));
    await flush();
    expect(document.body.textContent).not.toMatch(/localStorage|sessionStorage/);
    expect(window.location.href).not.toContain("URL%E4%B8%AD");
  });
});
