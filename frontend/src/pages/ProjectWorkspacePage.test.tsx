import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getProject,
  getProjectWorkflow,
} from "../api/client";
import type {
  ProjectDetail,
  ProjectWorkflowResponse,
  WorkflowState,
} from "../api/types";
import { ProjectWorkspacePage } from "./ProjectWorkspacePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getProject: vi.fn(),
    getProjectWorkflow: vi.fn(),
  };
});

const mockGetProject = vi.mocked(getProject);
const mockGetProjectWorkflow = vi.mocked(getProjectWorkflow);

function workflow(
  overrides: Partial<ProjectWorkflowResponse> = {},
): ProjectWorkflowResponse {
  return {
    project_id: "LEE柠檬",
    workflow_phase: "COMPLETED",
    status: "COMPLETED",
    stages: {
      creative: { status: "APPROVED" },
      storyboard: { status: "APPROVED" },
      video_prompt: { status: "APPROVED" },
      shots: { status: "COMPLETED", approved: 3, total: 3 },
      assembly: { status: "COMPLETED", needs_update: false, version: 2 },
      voice: { status: "COMPLETED", version: 1 },
      subtitle: { status: "NOT_STARTED", version: null },
      music: { status: "COMPLETED", version: 2 },
      export: {
        status: "COMPLETED",
        version: 3,
        created_at: "2026-08-18T14:20:00+08:00",
        stale: false,
      },
    },
    available_actions: [],
    updated_at: "2026-08-18T14:30:00+08:00",
    ...overrides,
  };
}

function detail(currentWorkflow = workflow()): ProjectDetail {
  const workflowState: WorkflowState = {
    workflow_phase: currentWorkflow.workflow_phase,
    status: currentWorkflow.status,
    stages: currentWorkflow.stages,
    available_actions: currentWorkflow.available_actions,
  };
  return {
    project_id: currentWorkflow.project_id,
    name: "LEE柠檬清爽饮品",
    request: {
      product_name: "LEE柠檬",
      product_description: "一款强调新鲜口感与明亮视觉的柠檬饮料。",
      user_notes: "避免出现人物，突出气泡与冰块。",
      duration_seconds: 18,
      video_style: "清爽、明亮、年轻",
      video_purpose: "用于新品社交媒体推广",
    },
    workflow: workflowState,
    assembly: currentWorkflow.stages.assembly,
    post_production: {
      status: "RUNNING",
      voice: currentWorkflow.stages.voice,
      subtitle: currentWorkflow.stages.subtitle,
      music: currentWorkflow.stages.music,
    },
    final_export: currentWorkflow.stages.export,
    updated_at: currentWorkflow.updated_at,
  };
}

function resolveWorkspace(currentWorkflow = workflow(), currentDetail = detail(currentWorkflow)) {
  mockGetProject.mockResolvedValue({
    data: currentDetail,
    correlationId: "req_detail",
  });
  mockGetProjectWorkflow.mockResolvedValue({
    data: currentWorkflow,
    correlationId: "req_workflow",
  });
}

function renderWorkspace(path = "/projects/LEE%E6%9F%A0%E6%AA%AC") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/projects/:projectId"
          element={<ProjectWorkspacePage />}
        />
        <Route path="/projects" element={<h1>Projects Test Route</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

function stageCard(name: string): HTMLElement {
  const heading = screen.getByRole("heading", { name });
  const article = heading.closest("article");
  if (!article) {
    throw new Error(`Missing stage article for ${name}`);
  }
  return article;
}

describe("ProjectWorkspacePage", () => {
  beforeEach(() => {
    mockGetProject.mockReset();
    mockGetProjectWorkflow.mockReset();
    resolveWorkspace();
  });

  it("loads the decoded project ID and renders the read-only Workspace", async () => {
    renderWorkspace();
    expect(
      await screen.findByRole("heading", { name: "LEE柠檬清爽饮品" }),
    ).toBeInTheDocument();
    expect(mockGetProject).toHaveBeenCalledWith("LEE柠檬");
    expect(mockGetProjectWorkflow).toHaveBeenCalledWith("LEE柠檬");
    expect(screen.getByText("项目已完成")).toBeInTheDocument();
    expect(screen.getByText("2026/08/18 14:30")).toBeInTheDocument();
  });

  it("renders all six original request fields", async () => {
    renderWorkspace();
    await screen.findByRole("heading", { name: "项目需求" });
    for (const label of [
      "产品名称",
      "产品描述",
      "视频时长",
      "视觉风格",
      "视频目的",
      "补充要求",
    ]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByText("LEE柠檬")).toBeInTheDocument();
    expect(screen.getByText("18 秒")).toBeInTheDocument();
    expect(screen.getByText(/一款强调新鲜口感/)).toBeInTheDocument();
    expect(screen.getByText("清爽、明亮、年轻")).toBeInTheDocument();
    expect(screen.getByText("用于新品社交媒体推广")).toBeInTheDocument();
    expect(screen.getByText(/避免出现人物/)).toBeInTheDocument();
  });

  it("shows 未填写 for empty or absent optional request values", async () => {
    const currentWorkflow = workflow();
    const currentDetail = detail(currentWorkflow);
    currentDetail.request.user_notes = "";
    currentDetail.request.video_style = null;
    resolveWorkspace(currentWorkflow, currentDetail);
    renderWorkspace();
    expect((await screen.findAllByText("未填写")).length).toBeGreaterThanOrEqual(2);
  });

  it("renders the phase and all nine workflow stages from the DTO", async () => {
    renderWorkspace();
    expect((await screen.findAllByText("已完成")).length).toBeGreaterThan(0);
    for (const stage of [
      "创意策划",
      "分镜规划",
      "视频提示词",
      "镜头",
      "视频合片",
      "配音",
      "字幕",
      "音乐",
      "最终导出",
    ]) {
      expect(screen.getByRole("heading", { name: stage })).toBeInTheDocument();
    }
    expect(within(stageCard("创意策划")).getAllByText("已审核").length).toBeGreaterThan(0);
    expect(within(stageCard("分镜规划")).getAllByText("已审核").length).toBeGreaterThan(0);
    expect(within(stageCard("镜头")).getByText("3 / 3 已审核")).toBeInTheDocument();
    expect(within(stageCard("视频合片")).getByText("已完成 · v2")).toBeInTheDocument();
    expect(within(stageCard("配音")).getByText("已完成 · v1")).toBeInTheDocument();
    expect(within(stageCard("字幕")).getAllByText("未开始").length).toBeGreaterThan(0);
    expect(within(stageCard("音乐")).getByText("已完成 · v2")).toBeInTheDocument();
    expect(within(stageCard("最终导出")).getByText("已完成 · v3")).toBeInTheDocument();
  });

  it("shows partial shot approval without scanning project files", async () => {
    const currentWorkflow = workflow();
    currentWorkflow.stages.shots = {
      status: "WAITING_REVIEW",
      approved: 2,
      total: 3,
    };
    resolveWorkspace(currentWorkflow, detail(currentWorkflow));
    renderWorkspace();
    await screen.findByRole("heading", { name: "镜头" });
    expect(
      within(stageCard("镜头")).getByText("2 / 3 已审核"),
    ).toBeInTheDocument();
  });

  it("makes Assembly Required prominent even if stored project status is completed", async () => {
    const currentWorkflow = workflow({
      workflow_phase: "ASSEMBLY_REQUIRED",
      status: "COMPLETED",
    });
    currentWorkflow.stages.assembly = {
      status: "COMPLETED",
      needs_update: true,
      version: 2,
    };
    resolveWorkspace(currentWorkflow, detail(currentWorkflow));
    renderWorkspace();
    expect((await screen.findAllByText("需要重新合片")).length).toBeGreaterThan(0);
    expect(within(stageCard("视频合片")).getByText("需要重新合片 · v2")).toBeInTheDocument();
    expect(screen.queryByText("项目已完成")).not.toBeInTheDocument();
  });

  it("shows a stale export as requiring re-export with its version", async () => {
    const currentWorkflow = workflow({ workflow_phase: "FINAL_EXPORT" });
    currentWorkflow.stages.export = {
      status: "STALE",
      version: 4,
      created_at: "2026-08-18T14:20:00+08:00",
      stale: true,
    };
    resolveWorkspace(currentWorkflow, detail(currentWorkflow));
    renderWorkspace();
    expect((await screen.findAllByText("需要重新导出")).length).toBeGreaterThan(0);
    expect(within(stageCard("最终导出")).getByText("需要重新导出 · v4")).toBeInTheDocument();
  });

  it("presents available actions as text and never as executable controls", async () => {
    const currentWorkflow = workflow({
      workflow_phase: "CREATIVE",
      status: "NOT_STARTED",
      available_actions: ["GENERATE_CREATIVE"],
    });
    resolveWorkspace(currentWorkflow, detail(currentWorkflow));
    renderWorkspace();
    expect(await screen.findByText("生成创意")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "生成创意" })).not.toBeInTheDocument();
    expect(screen.getByText(/仅展示状态/)).toBeInTheDocument();
  });

  it("shows a non-blank loading state while both GET requests are pending", () => {
    mockGetProject.mockReturnValue(
      new Promise<Awaited<ReturnType<typeof getProject>>>(() => undefined),
    );
    mockGetProjectWorkflow.mockReturnValue(
      new Promise<Awaited<ReturnType<typeof getProjectWorkflow>>>(() => undefined),
    );
    renderWorkspace();
    expect(screen.getByText("正在加载项目…")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
  });

  it("treats a partial request failure as a full Workspace error", async () => {
    mockGetProjectWorkflow.mockRejectedValue(
      new ApiClientError({ message: "workflow failed", code: "HTTP_ERROR" }),
    );
    renderWorkspace();
    expect(await screen.findByText("暂时无法加载项目")).toBeInTheDocument();
    expect(screen.queryByText("LEE柠檬清爽饮品")).not.toBeInTheDocument();
  });

  it("shows a safe network error", async () => {
    mockGetProject.mockRejectedValue(
      new ApiClientError({ message: "D:\\secret API_KEY", code: "NETWORK_ERROR" }),
    );
    renderWorkspace();
    expect(await screen.findByText("无法连接 Backend")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\secret");
    expect(document.body).not.toHaveTextContent("API_KEY");
  });

  it("shows PROJECT_NOT_FOUND with a semantic return link", async () => {
    mockGetProject.mockRejectedValue(
      new ApiClientError({
        message: "not found",
        status: 404,
        code: "PROJECT_NOT_FOUND",
      }),
    );
    renderWorkspace();
    expect(await screen.findByText("项目不存在或已被删除")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回 Projects" })).toHaveAttribute(
      "href",
      "/projects",
    );
  });

  it.each(["PROJECT_DATA_CORRUPT", "PROJECT_DATA_UNSUPPORTED"])(
    "shows a safe data error for %s",
    async (code) => {
      mockGetProject.mockRejectedValue(
        new ApiClientError({ message: "parser D:\\private", code }),
      );
      renderWorkspace();
      expect(
        await screen.findByText("项目数据暂时无法读取"),
      ).toBeInTheDocument();
      expect(document.body).not.toHaveTextContent("parser");
      expect(document.body).not.toHaveTextContent("D:\\private");
    },
  );

  it("retries both GET requests without refreshing the browser", async () => {
    mockGetProject
      .mockRejectedValueOnce(
        new ApiClientError({
          message: "temporary",
          code: "HTTP_ERROR",
          correlationId: "req_retry",
        }),
      )
      .mockResolvedValueOnce({ data: detail(), correlationId: "req_detail" });
    mockGetProjectWorkflow.mockResolvedValue({
      data: workflow(),
      correlationId: "req_workflow",
    });
    renderWorkspace();
    expect(await screen.findByText("错误编号：req_retry")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(
      await screen.findByRole("heading", { name: "LEE柠檬清爽饮品" }),
    ).toBeInTheDocument();
    expect(mockGetProject).toHaveBeenCalledTimes(2);
    expect(mockGetProjectWorkflow).toHaveBeenCalledTimes(2);
  });

  it("shows a safe correlation ID but no response extension fields", async () => {
    const unsafeDetail = Object.assign(detail(), {
      local_path: "D:\\private\\project.json",
      credential_env_name: "MINIMAX_API_KEY",
      raw_error: "provider secret",
    });
    const unsafeWorkflow = Object.assign(workflow(), {
      candidate_state: "CANDIDATE_APPROVE",
      authorization: "Bearer hidden",
    });
    mockGetProject.mockResolvedValue({
      data: unsafeDetail,
      correlationId: "req_detail",
    });
    mockGetProjectWorkflow.mockResolvedValue({
      data: unsafeWorkflow,
      correlationId: "req_workflow",
    });
    renderWorkspace();
    await screen.findByRole("heading", { name: "LEE柠檬清爽饮品" });
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("MINIMAX_API_KEY");
    expect(document.body).not.toHaveTextContent(/candidate/i);
    expect(document.body).not.toHaveTextContent("Bearer hidden");
  });

  it("renders unknown stage statuses safely", async () => {
    const currentWorkflow = workflow();
    currentWorkflow.stages.subtitle.status = "NEW_BACKEND_STATUS";
    resolveWorkspace(currentWorkflow, detail(currentWorkflow));
    renderWorkspace();
    await waitFor(() => {
      expect(
        within(stageCard("字幕")).getAllByText("未知状态").length,
      ).toBeGreaterThan(0);
    });
  });
});
