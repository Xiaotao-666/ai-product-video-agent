import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiClientError, getProjects } from "../api/client";
import type { ProjectSummary } from "../api/types";
import { ProjectsPage } from "./ProjectsPage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return { ...actual, getProjects: vi.fn() };
});

const mockGetProjects = vi.mocked(getProjects);

function project(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    project_id: "project-1",
    name: "LEE柠檬",
    workflow_phase: "COMPLETED",
    status: "COMPLETED",
    updated_at: "2026-08-17T20:58:53+08:00",
    assembly: {
      status: "COMPLETED",
      needs_update: false,
      version: 2,
    },
    final_export: {
      status: "COMPLETED",
      version: 1,
      created_at: "2026-08-17T20:57:00+08:00",
      stale: false,
    },
    ...overrides,
  };
}

function resolveProjects(projects: ProjectSummary[]) {
  mockGetProjects.mockResolvedValue({
    data: { projects },
    correlationId: "req_projects",
  });
}

describe("ProjectsPage", () => {
  beforeEach(() => {
    mockGetProjects.mockReset();
    resolveProjects([project()]);
  });

  it("renders the Projects page", async () => {
    render(<ProjectsPage />);
    expect(
      screen.getByRole("heading", { name: "Projects" }),
    ).toBeInTheDocument();
    expect(await screen.findByText("LEE柠檬")).toBeInTheDocument();
  });

  it("renders a project returned by getProjects", async () => {
    render(<ProjectsPage />);
    expect(await screen.findByText("LEE柠檬")).toBeInTheDocument();
    expect(screen.getByText("Backend Connected")).toBeInTheDocument();
  });

  it("keeps backend order and renders multiple projects", async () => {
    resolveProjects([
      project({ project_id: "newer", name: "最新项目" }),
      project({ project_id: "older", name: "较早项目" }),
    ]);
    render(<ProjectsPage />);
    expect(await screen.findByText("最新项目")).toBeInTheDocument();
    expect(screen.getByText("较早项目")).toBeInTheDocument();
    const cards = screen.getAllByRole("article");
    expect(cards[0]).toHaveTextContent("最新项目");
    expect(cards[1]).toHaveTextContent("较早项目");
  });

  it("maps workflow phases to user-facing Chinese labels", async () => {
    resolveProjects([
      project({ workflow_phase: "STORYBOARD_REVIEW", status: "WAITING_REVIEW" }),
    ]);
    render(<ProjectsPage />);
    expect(await screen.findByText("分镜审核")).toBeInTheDocument();
    expect(screen.queryByText("STORYBOARD_REVIEW")).not.toBeInTheDocument();
  });

  it("shows a completed project status", async () => {
    render(<ProjectsPage />);
    expect((await screen.findAllByText("已完成")).length).toBeGreaterThan(0);
  });

  it("shows Assembly Required without offering workspace actions", async () => {
    resolveProjects([
      project({
        workflow_phase: "ASSEMBLY_REQUIRED",
        status: "RUNNING",
        assembly: {
          status: "COMPLETED",
          needs_update: true,
          version: 2,
        },
      }),
    ]);
    render(<ProjectsPage />);
    expect(await screen.findByText("需要重新合片")).toBeInTheDocument();
    expect(screen.getByText(/需要更新/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /打开项目/ })).toBeDisabled();
  });

  it("shows the final export version", async () => {
    render(<ProjectsPage />);
    expect(await screen.findByText("已完成 · v1")).toBeInTheDocument();
  });

  it("shows a loading state and disables refresh", () => {
    mockGetProjects.mockReturnValue(
      new Promise<Awaited<ReturnType<typeof getProjects>>>(() => undefined),
    );
    render(<ProjectsPage />);
    expect(screen.getByText("正在加载项目…")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "刷新中…" })).toBeDisabled();
  });

  it("shows an empty state without calling create", async () => {
    resolveProjects([]);
    render(<ProjectsPage />);
    expect(await screen.findByText("还没有项目")).toBeInTheDocument();
    expect(screen.getByText("创建你的第一个 AI 产品视频项目。")).toBeInTheDocument();
    expect(
      screen
        .getAllByRole("button", { name: /新建项目/ })
        .every((button) => button.hasAttribute("disabled")),
    ).toBe(true);
  });

  it("shows a safe offline state", async () => {
    mockGetProjects.mockRejectedValue(
      new ApiClientError({ message: "无法连接", code: "NETWORK_ERROR" }),
    );
    render(<ProjectsPage />);
    expect(await screen.findByText("无法连接 Backend")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("TypeError");
    expect(document.body).not.toHaveTextContent("fetch failed");
  });

  it("retries the API request without reloading the browser", async () => {
    mockGetProjects
      .mockRejectedValueOnce(
        new ApiClientError({
          message: "暂时不可用",
          code: "HTTP_ERROR",
          correlationId: "req_retry",
        }),
      )
      .mockResolvedValueOnce({
        data: { projects: [project({ name: "重试成功项目" })] },
        correlationId: "req_success",
      });
    render(<ProjectsPage />);
    expect(await screen.findByText("错误编号：req_retry")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByText("重试成功项目")).toBeInTheDocument();
    expect(mockGetProjects).toHaveBeenCalledTimes(2);
  });

  it("does not crash on an invalid timestamp", async () => {
    resolveProjects([project({ updated_at: "invalid-date" })]);
    render(<ProjectsPage />);
    expect(await screen.findByText("更新于 时间未知")).toBeInTheDocument();
  });

  it("does not render a backend absolute-path extension field", async () => {
    const item = Object.assign(project(), {
      local_path: "D:\\private\\project.json",
    });
    resolveProjects([item]);
    render(<ProjectsPage />);
    await screen.findByText("LEE柠檬");
    expect(document.body).not.toHaveTextContent("D:\\private");
  });

  it("does not render credential extension fields", async () => {
    const item = Object.assign(project(), {
      credential_env_name: "MINIMAX_API_KEY",
    });
    resolveProjects([item]);
    render(<ProjectsPage />);
    await screen.findByText("LEE柠檬");
    expect(document.body).not.toHaveTextContent("credential_env_name");
    expect(document.body).not.toHaveTextContent("MINIMAX_API_KEY");
  });

  it("does not render Candidate internal terminology", async () => {
    const item = Object.assign(project(), {
      candidate_state: "CANDIDATE_APPROVE",
    });
    resolveProjects([item]);
    render(<ProjectsPage />);
    await screen.findByText("LEE柠檬");
    expect(document.body).not.toHaveTextContent(/candidate/i);
  });
});
