import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getCreativeContent,
  getProject,
  getProjectWorkflow,
  getShots,
  getStoryboardContent,
  getVideoPrompts,
} from "../api/client";
import type {
  ProjectDetail,
  ProjectWorkflowResponse,
  WorkflowState,
} from "../api/types";
import { ProjectStagePage } from "./ProjectStagePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getCreativeContent: vi.fn(),
    getProject: vi.fn(),
    getProjectWorkflow: vi.fn(),
    getShots: vi.fn(),
    getStoryboardContent: vi.fn(),
    getVideoPrompts: vi.fn(),
  };
});

const mockGetProject = vi.mocked(getProject);
const mockGetProjectWorkflow = vi.mocked(getProjectWorkflow);
const mockGetShots = vi.mocked(getShots);
const mockGetCreativeContent = vi.mocked(getCreativeContent);
const mockGetStoryboardContent = vi.mocked(getStoryboardContent);
const mockGetVideoPrompts = vi.mocked(getVideoPrompts);

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
      product_description: "新鲜柠檬饮料",
      user_notes: null,
      duration_seconds: 18,
      video_style: "清爽",
      video_purpose: "新品推广",
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

function resolveStage(currentWorkflow = workflow(), currentDetail = detail(currentWorkflow)) {
  mockGetProject.mockResolvedValue({
    data: currentDetail,
    correlationId: "req_detail",
  });
  mockGetProjectWorkflow.mockResolvedValue({
    data: currentWorkflow,
    correlationId: "req_workflow",
  });
}

function renderStage(path = "/projects/LEE%E6%9F%A0%E6%AA%AC/stages/shots") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/projects/:projectId/stages/:stageKey"
          element={<ProjectStagePage />}
        />
        <Route
          path="/projects/:projectId"
          element={<h1>Workspace Overview Test Route</h1>}
        />
        <Route path="/projects" element={<h1>Projects Test Route</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

function summarySection(): HTMLElement {
  const heading = screen.getByRole("heading", { name: "阶段摘要" });
  const section = heading.closest("section");
  if (!section) {
    throw new Error("Stage summary section is missing");
  }
  return section;
}

describe("ProjectStagePage", () => {
  beforeEach(() => {
    mockGetProject.mockReset();
    mockGetProjectWorkflow.mockReset();
    mockGetShots.mockReset();
    mockGetCreativeContent.mockReset();
    mockGetStoryboardContent.mockReset();
    mockGetVideoPrompts.mockReset();
    mockGetCreativeContent.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "APPROVED", content: null },
      correlationId: "req_creative",
    });
    mockGetStoryboardContent.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "APPROVED", content: null },
      correlationId: "req_storyboard",
    });
    mockGetVideoPrompts.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "APPROVED", content: null },
      correlationId: "req_prompts",
    });
    mockGetShots.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        status: "COMPLETED",
        shots: [
          {
            shot_id: "shot_01",
            status: "APPROVED",
            official_version: 2,
            pending_review_version: null,
            version_count: 2,
            generation_count: 2,
          },
          {
            shot_id: "shot_02",
            status: "APPROVED",
            official_version: 1,
            pending_review_version: null,
            version_count: 1,
            generation_count: 1,
          },
          {
            shot_id: "shot_03",
            status: "APPROVED",
            official_version: 1,
            pending_review_version: null,
            version_count: 1,
            generation_count: 1,
          },
        ],
      },
      correlationId: "req_shots",
    });
    resolveStage();
  });

  it("loads a direct Stage deep link without previous page state", async () => {
    renderStage();
    expect(
      await screen.findByRole("heading", { name: "镜头" }),
    ).toBeInTheDocument();
    expect(mockGetProject).toHaveBeenCalledWith("LEE柠檬");
    expect(mockGetProjectWorkflow).toHaveBeenCalledWith("LEE柠檬");
    expect(screen.getAllByText("LEE柠檬清爽饮品").length).toBeGreaterThan(0);
    expect(screen.getByText("2026/08/18 14:30")).toBeInTheDocument();
  });

  it("supports a UUID project ID on a direct Stage URL", async () => {
    const currentWorkflow = workflow({
      project_id: "0123456789abcdef0123456789abcdef",
    });
    resolveStage(currentWorkflow, detail(currentWorkflow));
    renderStage(
      "/projects/0123456789abcdef0123456789abcdef/stages/creative",
    );
    await screen.findByRole("heading", { name: "创意策划" });
    expect(mockGetProject).toHaveBeenCalledWith(
      "0123456789abcdef0123456789abcdef",
    );
  });

  it("keeps the active Stage in the URL and marks its NavLink active", async () => {
    renderStage();
    await screen.findByRole("heading", { name: "镜头" });
    const shotsLink = within(
      screen.getByRole("navigation", { name: "Workflow stages" }),
    ).getByRole("link", { name: /镜头/ });
    expect(shotsLink).toHaveClass("stage-nav-link-active");
    expect(shotsLink).toHaveAttribute("aria-current", "page");
    expect(shotsLink).toHaveAttribute(
      "href",
      "/projects/LEE%E6%9F%A0%E6%AA%AC/stages/shots",
    );
  });

  it("allows switching directly to another Stage URL", async () => {
    renderStage();
    await screen.findByRole("heading", { name: "镜头" });
    fireEvent.click(screen.getByRole("link", { name: /最终导出/ }));
    expect(
      await screen.findByRole("heading", { name: "最终导出" }),
    ).toBeInTheDocument();
    expect(mockGetProject).toHaveBeenCalledTimes(2);
    expect(mockGetProjectWorkflow).toHaveBeenCalledTimes(2);
  });

  it("keeps a NOT_STARTED Stage viewable", async () => {
    const currentWorkflow = workflow({
      workflow_phase: "CREATIVE",
      status: "NOT_STARTED",
      available_actions: ["GENERATE_CREATIVE"],
    });
    currentWorkflow.stages.creative.status = "NOT_STARTED";
    resolveStage(currentWorkflow, detail(currentWorkflow));
    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/creative");
    expect(
      await screen.findByRole("heading", { name: "创意策划" }),
    ).toBeInTheDocument();
    expect(within(summarySection()).getAllByText("未开始").length).toBeGreaterThan(0);
    expect(await screen.findByText("创意策划尚未生成。")).toBeInTheDocument();
  });

  it("shows Creative status without inventing Creative content", async () => {
    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/creative");
    await screen.findByRole("heading", { name: "创意策划" });
    expect(within(summarySection()).getAllByText("已审核").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/已持久化的正式创意内容/),
    ).toBeInTheDocument();
  });

  it("shows the real Shots approved and total summary", async () => {
    renderStage();
    await screen.findByRole("heading", { name: "镜头" });
    expect(within(summarySection()).getByText("3 / 3 已审核")).toBeInTheDocument();
    expect(within(summarySection()).getByText("3 / 3")).toBeInTheDocument();
  });

  it("shows Assembly version and needs-update fields", async () => {
    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/assembly");
    await screen.findByRole("heading", { name: "视频合片" });
    const summary = within(summarySection());
    expect(summary.getByText("v2")).toBeInTheDocument();
    expect(summary.getByText("Needs Update")).toBeInTheDocument();
    expect(summary.getByText("否")).toBeInTheDocument();
  });

  it("shows Assembly Required without claiming it is current", async () => {
    const currentWorkflow = workflow({ workflow_phase: "ASSEMBLY_REQUIRED" });
    currentWorkflow.stages.assembly.needs_update = true;
    resolveStage(currentWorkflow, detail(currentWorkflow));
    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/assembly");
    await screen.findByRole("heading", { name: "视频合片" });
    expect(within(summarySection()).getByText("需要重新合片 · v2")).toBeInTheDocument();
    expect(within(summarySection()).getByText("是")).toBeInTheDocument();
  });

  it("shows Export version and stale fields", async () => {
    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/export");
    await screen.findByRole("heading", { name: "最终导出" });
    const summary = within(summarySection());
    expect(summary.getByText("v3")).toBeInTheDocument();
    expect(summary.getByText("Stale")).toBeInTheDocument();
    expect(summary.getByText("否")).toBeInTheDocument();
  });

  it("shows a stale Export as requiring re-export", async () => {
    const currentWorkflow = workflow({ workflow_phase: "FINAL_EXPORT" });
    currentWorkflow.stages.export.stale = true;
    currentWorkflow.stages.export.status = "STALE";
    resolveStage(currentWorkflow, detail(currentWorkflow));
    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/export");
    await screen.findByRole("heading", { name: "最终导出" });
    expect(within(summarySection()).getByText("需要重新导出 · v3")).toBeInTheDocument();
    expect(within(summarySection()).getByText("是")).toBeInTheDocument();
  });

  it("filters available actions to the current Stage and keeps them read-only", async () => {
    const currentWorkflow = workflow({
      workflow_phase: "CREATIVE_REVIEW",
      available_actions: [
        "APPROVE_CREATIVE",
        "REVISE_CREATIVE",
        "REGENERATE_CREATIVE",
      ],
    });
    resolveStage(currentWorkflow, detail(currentWorkflow));
    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/creative");
    await screen.findByRole("heading", { name: "创意策划" });
    expect(screen.getByText("审核创意")).toBeInTheDocument();
    expect(screen.getByText("修改创意")).toBeInTheDocument();
    expect(screen.getByText("重新生成创意")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /创意/ })).not.toBeInTheDocument();
    expect(screen.getByText(/不会执行任何操作/)).toBeInTheDocument();
  });

  it("handles an invalid Stage locally without requesting Backend", () => {
    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/banana");
    expect(screen.getByRole("heading", { name: "阶段不存在" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "返回项目总览" })).toHaveAttribute(
      "href",
      "/projects/LEE%E6%9F%A0%E6%AA%AC",
    );
    expect(mockGetProject).not.toHaveBeenCalled();
    expect(mockGetProjectWorkflow).not.toHaveBeenCalled();
  });

  it("shows a safe PROJECT_NOT_FOUND error", async () => {
    mockGetProject.mockRejectedValue(
      new ApiClientError({
        message: "not found",
        status: 404,
        code: "PROJECT_NOT_FOUND",
      }),
    );
    renderStage();
    expect(await screen.findByText("项目不存在或已被删除")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("not found");
  });

  it.each(["PROJECT_DATA_CORRUPT", "PROJECT_DATA_UNSUPPORTED"])(
    "shows a safe project data error for %s",
    async (code) => {
      mockGetProject.mockRejectedValue(
        new ApiClientError({ message: "parser D:\\private", code }),
      );
      renderStage();
      expect(
        await screen.findByText("项目数据暂时无法读取"),
      ).toBeInTheDocument();
      expect(document.body).not.toHaveTextContent("D:\\private");
    },
  );

  it("shows a safe Network Error", async () => {
    mockGetProject.mockRejectedValue(
      new ApiClientError({
        message: "D:\\secret API_KEY",
        code: "NETWORK_ERROR",
      }),
    );
    renderStage();
    expect(await screen.findByText("无法连接 Backend")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\secret");
    expect(document.body).not.toHaveTextContent("API_KEY");
  });

  it("retries Detail and Workflow without refreshing the browser", async () => {
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
    renderStage();
    expect(await screen.findByText("错误编号：req_retry")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(
      await screen.findByRole("heading", { name: "镜头" }),
    ).toBeInTheDocument();
    expect(mockGetProject).toHaveBeenCalledTimes(2);
    expect(mockGetProjectWorkflow).toHaveBeenCalledTimes(2);
  });

  it("shows a loading state while both GET requests are pending", () => {
    mockGetProject.mockReturnValue(
      new Promise<Awaited<ReturnType<typeof getProject>>>(() => undefined),
    );
    mockGetProjectWorkflow.mockReturnValue(
      new Promise<Awaited<ReturnType<typeof getProjectWorkflow>>>(() => undefined),
    );
    renderStage();
    expect(screen.getByText("正在加载项目阶段…")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
  });

  it("does not render path, secret, raw-error, or Candidate extensions", async () => {
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
    renderStage();
    await screen.findByRole("heading", { name: "镜头" });
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("MINIMAX_API_KEY");
    expect(document.body).not.toHaveTextContent("raw_error");
    expect(document.body).not.toHaveTextContent(/candidate/i);
    expect(document.body).not.toHaveTextContent("Bearer hidden");
  });

  it("returns to the Workspace overview with a semantic Link", async () => {
    renderStage();
    await screen.findByRole("heading", { name: "镜头" });
    fireEvent.click(screen.getByRole("link", { name: "← 返回项目总览" }));
    expect(
      await screen.findByRole("heading", { name: "Workspace Overview Test Route" }),
    ).toBeInTheDocument();
  });
});
