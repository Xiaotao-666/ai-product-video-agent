import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  adoptPromptRevisionDraft,
  ApiClientError,
  getProjectTasks,
  getPromptRevisionDraft,
  getTask,
  submitPromptRevisionDraft,
} from "../../api/client";
import type {
  PromptRevisionDraftAdoptResponse,
  PromptRevisionDraftResponse,
  TaskRecord,
} from "../../api/types";
import { ShotPromptRevisionDraftAction } from "./ShotPromptRevisionDraftAction";


vi.mock("../../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../api/client")>();
  return {
    ...actual,
    adoptPromptRevisionDraft: vi.fn(),
    getProjectTasks: vi.fn(),
    getPromptRevisionDraft: vi.fn(),
    getTask: vi.fn(),
    submitPromptRevisionDraft: vi.fn(),
  };
});

const mockProjectTasks = vi.mocked(getProjectTasks);
const mockAdopt = vi.mocked(adoptPromptRevisionDraft);
const mockGetDraft = vi.mocked(getPromptRevisionDraft);
const mockGetTask = vi.mocked(getTask);
const mockSubmit = vi.mocked(submitPromptRevisionDraft);

const queuedTask: TaskRecord = {
  task_id: "task_0123456789abcdef0123456789abcdef",
  project_id: "project-a",
  operation: "SHOT_PROMPT_REVISION_DRAFT",
  target_id: "shot_01",
  status: "QUEUED",
  created_at: "2026-08-20T00:00:00Z",
  started_at: null,
  finished_at: null,
  correlation_id: "req_prompt_revision",
  error: null,
  result: null,
};

const succeededTask: TaskRecord = {
  ...queuedTask,
  status: "SUCCEEDED",
  started_at: "2026-08-20T00:00:01Z",
  finished_at: "2026-08-20T00:00:02Z",
  result: {
    resource_type: "PROMPT_REVISION_DRAFT",
    resource_id: "shot_01",
    version: null,
  },
};

const draft: PromptRevisionDraftResponse = {
  base_prompt_version: 2,
  original_prompt: "original Prompt with deterministic blocks",
  draft_prompt: "cinematic revised Prompt with deterministic blocks",
  feedback: "增强电影感",
  created_at: "2026-08-20T00:00:02Z",
};

const adoption: PromptRevisionDraftAdoptResponse = {
  project_id: "project-a",
  shot_id: "shot_01",
  prompt_version: 3,
  parent_version: 2,
  source: "ai_revision",
  active_prompt_version: 3,
  approved_prompt_version: 2,
  created_at: "2026-08-20T00:01:00+00:00",
};

const missingDraft = new ApiClientError({
  code: "PROMPT_REVISION_DRAFT_NOT_FOUND",
  message: "missing",
  status: 404,
});

function renderAction() {
  return render(
    <ShotPromptRevisionDraftAction
      projectId="project-a"
      shotId="shot_01"
      basePromptVersion={2}
    />,
  );
}

describe("ShotPromptRevisionDraftAction", () => {
  beforeEach(() => {
    mockProjectTasks.mockReset();
    mockAdopt.mockReset();
    mockGetDraft.mockReset();
    mockGetTask.mockReset();
    mockSubmit.mockReset();
    mockProjectTasks.mockResolvedValue({
      data: { project_id: "project-a", tasks: [] },
      correlationId: "req_tasks",
    });
    mockGetDraft.mockRejectedValue(missingDraft);
    mockSubmit.mockResolvedValue({
      data: queuedTask,
      correlationId: "req_prompt_revision",
    });
    mockAdopt.mockResolvedValue({
      data: adoption,
      correlationId: "req_adopt",
    });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("opens feedback and cancel sends no POST", async () => {
    renderAction();
    await waitFor(() => expect(mockProjectTasks).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "AI修改Prompt" }));
    expect(screen.getByLabelText("修改意见")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("修改意见"), {
      target: { value: "增强电影感" },
    });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByLabelText("修改意见")).not.toBeInTheDocument();
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("submits trimmed feedback once and binds only the draft operation", async () => {
    renderAction();
    await waitFor(() => expect(mockProjectTasks).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "AI修改Prompt" }));
    fireEvent.change(screen.getByLabelText("修改意见"), {
      target: { value: "  增强电影感  " },
    });
    const submit = screen.getByRole("button", { name: "生成修改建议" });
    fireEvent.click(submit);
    fireEvent.click(submit);
    await waitFor(() => expect(mockSubmit).toHaveBeenCalledTimes(1));
    expect(mockSubmit).toHaveBeenCalledWith("project-a", "shot_01", {
      feedback: "增强电影感",
    });
    expect((await screen.findAllByText("等待生成")).length).toBeGreaterThan(0);
    expect(document.body).not.toHaveTextContent(queuedTask.task_id);
  });

  it("polls the active task and displays the durable draft", async () => {
    mockGetDraft
      .mockRejectedValueOnce(missingDraft)
      .mockResolvedValue({ data: draft, correlationId: "req_draft" });
    mockGetTask.mockResolvedValue({
      data: succeededTask,
      correlationId: "req_prompt_revision",
    });
    renderAction();
    await waitFor(() => expect(mockProjectTasks).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "AI修改Prompt" }));
    fireEvent.change(screen.getByLabelText("修改意见"), {
      target: { value: "增强电影感" },
    });
    fireEvent.click(screen.getByRole("button", { name: "生成修改建议" }));
    await waitFor(
      () => expect(mockGetTask).toHaveBeenCalledWith(queuedTask.task_id),
      { timeout: 3_500 },
    );
    expect(await screen.findByText("原 Prompt")).toBeInTheDocument();
    expect(screen.getByText(draft.original_prompt)).toBeInTheDocument();
    expect(screen.getByText(draft.draft_prompt)).toBeInTheDocument();
  });

  it("recovers an active draft task after F5 without another POST", async () => {
    const running: TaskRecord = {
      ...queuedTask,
      status: "RUNNING",
      started_at: "2026-08-20T00:00:01Z",
    };
    mockProjectTasks.mockResolvedValue({
      data: { project_id: "project-a", tasks: [running] },
      correlationId: "req_tasks",
    });
    mockGetDraft
      .mockRejectedValueOnce(missingDraft)
      .mockResolvedValue({ data: draft, correlationId: "req_draft" });
    mockGetTask.mockResolvedValue({
      data: succeededTask,
      correlationId: "req_prompt_revision",
    });
    renderAction();
    await waitFor(
      () => expect(mockGetTask).toHaveBeenCalledWith(running.task_id),
      { timeout: 3_500 },
    );
    expect(mockSubmit).not.toHaveBeenCalled();
    expect(await screen.findByText(draft.draft_prompt)).toBeInTheDocument();
  });

  it("uses a durable draft after F5 and suppresses its stale interrupted task", async () => {
    const interrupted: TaskRecord = {
      ...queuedTask,
      status: "INTERRUPTED",
      started_at: "2026-08-20T00:00:01Z",
      finished_at: "2026-08-20T00:00:02Z",
      error: {
        code: "TASK_INTERRUPTED",
        message: "上一次Web任务已中断，请根据当前项目状态继续。",
        retryable: false,
      },
    };
    mockProjectTasks.mockResolvedValue({
      data: { project_id: "project-a", tasks: [interrupted] },
      correlationId: "req_tasks",
    });
    mockGetDraft.mockResolvedValue({ data: draft, correlationId: "req_draft" });

    renderAction();

    expect(await screen.findByText(draft.draft_prompt)).toBeInTheDocument();
    expect(screen.queryByText("修改任务已中断")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采用此修改" })).toBeEnabled();
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("uses a durable draft instead of displaying an old failed task error", async () => {
    const failed: TaskRecord = {
      ...queuedTask,
      status: "FAILED",
      started_at: "2026-08-20T00:00:01Z",
      finished_at: "2026-08-20T00:00:02Z",
      error: {
        code: "PROVIDER_FAILED",
        message: "AI Prompt修改服务暂时不可用，请稍后重试。",
        retryable: true,
      },
    };
    mockProjectTasks.mockResolvedValue({
      data: { project_id: "project-a", tasks: [failed] },
      correlationId: "req_tasks",
    });
    mockGetDraft.mockResolvedValue({ data: draft, correlationId: "req_draft" });

    renderAction();

    expect(await screen.findByText(draft.draft_prompt)).toBeInTheDocument();
    expect(
      screen.queryByText("AI Prompt修改服务暂时不可用，请稍后重试。"),
    ).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "采用此修改" })).toBeEnabled();
  });

  it("shows draft actions and cancel is local only", async () => {
    mockGetDraft.mockResolvedValue({ data: draft, correlationId: "req_draft" });
    renderAction();
    expect(await screen.findByText(draft.draft_prompt)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "采用此修改" }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", { name: "重新生成修改建议" }),
    ).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByText(draft.draft_prompt)).not.toBeInTheDocument();
    expect(mockSubmit).not.toHaveBeenCalled();
    expect(mockAdopt).not.toHaveBeenCalled();
  });

  it("requires confirmation and cancel creates zero Prompt versions", async () => {
    mockGetDraft.mockResolvedValue({ data: draft, correlationId: "req_draft" });
    renderAction();
    await screen.findByText(draft.draft_prompt);
    fireEvent.click(screen.getByRole("button", { name: "采用此修改" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("将创建新的Prompt Version");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockAdopt).not.toHaveBeenCalled();
  });

  it("adopts exactly once and shows the new Prompt without video generation", async () => {
    mockGetDraft.mockResolvedValue({ data: draft, correlationId: "req_draft" });
    renderAction();
    await screen.findByText(draft.draft_prompt);
    fireEvent.click(screen.getByRole("button", { name: "采用此修改" }));
    const confirm = screen.getByRole("button", { name: "确认采用" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    await waitFor(() => expect(mockAdopt).toHaveBeenCalledTimes(1));
    expect(mockAdopt).toHaveBeenCalledWith("project-a", "shot_01");
    expect(await screen.findByText("Prompt v3 已创建。")).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent(
      "原 Prompt v2 保持不变，本阶段不会生成视频",
    );
    expect(mockSubmit).not.toHaveBeenCalled();
  });

  it("ignores SHOT_GENERATE and SHOT_REGENERATE tasks", async () => {
    mockProjectTasks.mockResolvedValue({
      data: {
        project_id: "project-a",
        tasks: [
          { ...queuedTask, operation: "SHOT_GENERATE" },
          { ...queuedTask, operation: "SHOT_REGENERATE" },
        ],
      },
      correlationId: "req_tasks",
    });
    renderAction();
    expect(
      await screen.findByRole("button", { name: "AI修改Prompt" }),
    ).toBeEnabled();
    expect(mockGetTask).not.toHaveBeenCalled();
  });

  it("renders safe failure copy without raw provider content or task id", async () => {
    const failed: TaskRecord = {
      ...queuedTask,
      status: "FAILED",
      started_at: "2026-08-20T00:00:01Z",
      finished_at: "2026-08-20T00:00:02Z",
      error: {
        code: "PROVIDER_FAILED",
        message: "AI Prompt修改服务暂时不可用，请稍后重试。",
        retryable: true,
      },
    };
    mockProjectTasks.mockResolvedValue({
      data: { project_id: "project-a", tasks: [failed] },
      correlationId: "req_tasks",
    });
    renderAction();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("AI Prompt修改服务暂时不可用");
    expect(document.body).not.toHaveTextContent(failed.task_id);
    expect(document.body).not.toHaveTextContent("provider raw");
    expect(document.body).not.toHaveTextContent("API_KEY");
  });
});
