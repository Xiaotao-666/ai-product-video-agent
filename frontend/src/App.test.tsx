import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import {
  createProject,
  generateCreative,
  getCapabilities,
  getCreativeContent,
  getHealth,
  getProject,
  getProjects,
  getProjectWorkflow,
  getProjectTasks,
  getShot,
  getShots,
  getStoryboardContent,
  getVideoPrompts,
  getTask,
} from "./api/client";
import type {
  CapabilitiesResponse,
  ProjectDetail,
  ProjectWorkflowResponse,
} from "./api/types";

vi.mock("./api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api/client")>();
  return {
    ...actual,
    getHealth: vi.fn(),
    getCapabilities: vi.fn(),
    getProjects: vi.fn(),
    getProject: vi.fn(),
    getProjectWorkflow: vi.fn(),
    getProjectTasks: vi.fn(),
    getShot: vi.fn(),
    getShots: vi.fn(),
    getCreativeContent: vi.fn(),
    getStoryboardContent: vi.fn(),
    getVideoPrompts: vi.fn(),
    createProject: vi.fn(),
    generateCreative: vi.fn(),
    getTask: vi.fn(),
  };
});

const mockGetHealth = vi.mocked(getHealth);
const mockGetCapabilities = vi.mocked(getCapabilities);
const mockGetProjects = vi.mocked(getProjects);
const mockGetProject = vi.mocked(getProject);
const mockGetProjectWorkflow = vi.mocked(getProjectWorkflow);
const mockGetProjectTasks = vi.mocked(getProjectTasks);
const mockGetShot = vi.mocked(getShot);
const mockGetShots = vi.mocked(getShots);
const mockGetCreativeContent = vi.mocked(getCreativeContent);
const mockGetStoryboardContent = vi.mocked(getStoryboardContent);
const mockGetVideoPrompts = vi.mocked(getVideoPrompts);
const mockCreateProject = vi.mocked(createProject);
const mockGenerateCreative = vi.mocked(generateCreative);
const mockGetTask = vi.mocked(getTask);

function renderSystem() {
  return render(
    <MemoryRouter initialEntries={["/system"]}>
      <App />
    </MemoryRouter>,
  );
}

const capabilities: CapabilitiesResponse = {
  planning: { deepseek: { available: true } },
  video: {
    minimax_hailuo: { available: true },
    minimax_h3: { available: false },
  },
  voice: {
    aliyun_tts: { available: false },
    xfyun_tts: { available: true },
  },
  ffmpeg: { available: true },
};

const workspaceWorkflow: ProjectWorkflowResponse = {
  project_id: "LEE柠檬",
  workflow_phase: "CREATIVE",
  status: "NOT_STARTED",
  stages: {
    creative: { status: "NOT_STARTED" },
    storyboard: { status: "NOT_STARTED" },
    video_prompt: { status: "NOT_STARTED" },
    shots: { status: "NOT_STARTED", approved: 0, total: 0 },
    assembly: { status: "NOT_STARTED", needs_update: false, version: null },
    voice: { status: "NOT_STARTED", version: null },
    subtitle: { status: "NOT_STARTED", version: null },
    music: { status: "NOT_STARTED", version: null },
    export: {
      status: "NOT_STARTED",
      version: null,
      created_at: null,
      stale: false,
    },
  },
  available_actions: ["GENERATE_CREATIVE"],
  updated_at: "2026-08-18T10:00:00+08:00",
};

const workspaceDetail: ProjectDetail = {
  project_id: "LEE柠檬",
  name: "LEE柠檬",
  request: {
    product_name: "LEE柠檬",
    product_description: "柠檬饮料",
    user_notes: null,
    duration_seconds: 15,
    video_style: "清爽",
    video_purpose: "产品推广",
  },
  workflow: {
    workflow_phase: workspaceWorkflow.workflow_phase,
    status: workspaceWorkflow.status,
    stages: workspaceWorkflow.stages,
    available_actions: workspaceWorkflow.available_actions,
  },
  assembly: workspaceWorkflow.stages.assembly,
  post_production: {
    status: "NOT_STARTED",
    voice: workspaceWorkflow.stages.voice,
    subtitle: workspaceWorkflow.stages.subtitle,
    music: workspaceWorkflow.stages.music,
  },
  final_export: workspaceWorkflow.stages.export,
  updated_at: workspaceWorkflow.updated_at,
};

describe("App", () => {
  beforeEach(() => {
    mockGetHealth.mockResolvedValue({
      data: {
        status: "ok",
        service: "ai-product-video-agent",
        api_version: "v1",
      },
      correlationId: "req_health",
    });
    mockGetCapabilities.mockResolvedValue({
      data: capabilities,
      correlationId: "req_capabilities",
    });
    mockGetProjects.mockResolvedValue({
      data: { projects: [] },
      correlationId: "req_projects",
    });
    mockGetProject.mockResolvedValue({
      data: workspaceDetail,
      correlationId: "req_project",
    });
    mockGetProjectWorkflow.mockResolvedValue({
      data: workspaceWorkflow,
      correlationId: "req_workflow",
    });
    mockGetProjectTasks.mockResolvedValue({
      data: { project_id: "LEE柠檬", tasks: [] },
      correlationId: "req_tasks",
    });
    mockGenerateCreative.mockReset();
    mockGetTask.mockReset();
    mockGetShots.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        status: "NOT_STARTED",
        aggregation: {
          total: 0,
          approved: 0,
          waiting_review: 0,
          generating: 0,
          not_started: 0,
          failed: 0,
        },
        shots: [],
      },
      correlationId: "req_shots",
    });
    mockGetShot.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        shot_id: "shot_01",
        status: "NOT_STARTED",
        official_version: null,
        pending_review_version: null,
        version_count: 0,
        generation_count: 0,
        versions: [],
      },
      correlationId: "req_shot",
    });
    mockGetCreativeContent.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "NOT_STARTED", content: null },
      correlationId: "req_creative",
    });
    mockGetStoryboardContent.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "NOT_STARTED", content: null },
      correlationId: "req_storyboard",
    });
    mockGetVideoPrompts.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "NOT_STARTED", content: null },
      correlationId: "req_prompts",
    });
    mockCreateProject.mockResolvedValue({
      data: {
        project_id: "project-new",
        name: "测试项目",
        workflow_phase: "CREATIVE",
        status: "NOT_STARTED",
        created_at: "2026-08-18T10:00:00+08:00",
        updated_at: "2026-08-18T10:00:00+08:00",
      },
      correlationId: "req_create",
    });
  });

  it("renders the application shell", () => {
    renderSystem();
    expect(
      screen.getByRole("heading", { name: "System Status" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Projects")).toBeInTheDocument();
  });

  it("shows Connected and API version when health succeeds", async () => {
    renderSystem();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("v1")).toBeInTheDocument();
  });

  it("shows Offline without crashing when health fails", async () => {
    mockGetHealth.mockRejectedValue(new Error("network unavailable"));
    mockGetCapabilities.mockRejectedValue(new Error("network unavailable"));
    renderSystem();
    expect(await screen.findByText("Offline")).toBeInTheDocument();
    expect(screen.getByText("Backend 未连接")).toBeInTheDocument();
    expect(screen.getByText(/uvicorn web_backend\.app:app/)).toBeInTheDocument();
  });

  it("renders backend capability availability", async () => {
    renderSystem();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    expect(screen.getByText("DeepSeek")).toBeInTheDocument();
    expect(screen.getByText("MiniMax Hailuo")).toBeInTheDocument();
    expect(screen.getByText("MiniMax H3")).toBeInTheDocument();
    expect(screen.getByText("XFYUN TTS")).toBeInTheDocument();
    expect(screen.getByText("FFmpeg")).toBeInTheDocument();
    expect(screen.getAllByText("Available")).toHaveLength(4);
    expect(screen.getAllByText("Unavailable")).toHaveLength(2);
  });

  it("never renders secret-bearing internal errors", async () => {
    mockGetHealth.mockRejectedValue(
      new Error("MINIMAX_API_KEY=must-not-render"),
    );
    mockGetCapabilities.mockRejectedValue(
      new Error("XFYUN_API_SECRET=must-not-render"),
    );
    renderSystem();
    await screen.findByText("Offline");
    expect(document.body).not.toHaveTextContent("must-not-render");
    expect(document.body).not.toHaveTextContent("API_KEY");
    expect(document.body).not.toHaveTextContent("API_SECRET");
  });

  it("keeps the page usable when capabilities fail", async () => {
    mockGetCapabilities.mockRejectedValue(new Error("invalid response"));
    renderSystem();
    expect(await screen.findByText("Connected")).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getAllByText("Unavailable")).toHaveLength(6);
    });
    expect(
      screen.getByRole("heading", { name: "Capabilities" }),
    ).toBeInTheDocument();
  });

  it("keeps the System Status route and active navigation", async () => {
    renderSystem();
    expect(
      screen.getByRole("heading", { name: "System Status" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: /System Status/ }),
    ).toHaveClass("nav-item-active");
    expect(screen.getByRole("link", { name: /Projects/ })).not.toHaveClass(
      "nav-item-active",
    );
  });

  it("routes the root path to Projects and marks navigation active", async () => {
    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("heading", { name: "Projects" }),
    ).toBeInTheDocument();
    const primaryNavigation = screen.getByRole("navigation", {
      name: "Primary navigation",
    });
    expect(
      within(primaryNavigation).getByRole("link", { name: "Projects" }),
    ).toHaveClass("nav-item-active");
  });

  it("renders /projects/new and keeps Projects navigation active", () => {
    render(
      <MemoryRouter initialEntries={["/projects/new"]}>
        <App />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("heading", { name: "新建视频项目" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Projects" })).toHaveClass(
      "nav-item-active",
    );
    expect(screen.getByRole("link", { name: /System Status/ })).not.toHaveClass(
      "nav-item-active",
    );
  });

  it("renders /projects/:id and keeps Projects navigation active", async () => {
    render(
      <MemoryRouter initialEntries={["/projects/LEE%E6%9F%A0%E6%AA%AC"]}>
        <App />
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("heading", { name: "LEE柠檬" }),
    ).toBeInTheDocument();
    expect(mockGetProject).toHaveBeenCalledWith("LEE柠檬");
    expect(screen.getByRole("link", { name: "Projects" })).toHaveClass(
      "nav-item-active",
    );
    expect(screen.getByRole("link", { name: /System Status/ })).not.toHaveClass(
      "nav-item-active",
    );
  });

  it("renders a Stage deep link and keeps Projects navigation active", async () => {
    render(
      <MemoryRouter
        initialEntries={["/projects/LEE%E6%9F%A0%E6%AA%AC/stages/creative"]}
      >
        <App />
      </MemoryRouter>,
    );
    expect(
      await screen.findByRole("heading", { name: "创意策划" }),
    ).toBeInTheDocument();
    expect(mockGetProject).toHaveBeenCalledWith("LEE柠檬");
    const primaryNavigation = screen.getByRole("navigation", {
      name: "Primary navigation",
    });
    expect(
      within(primaryNavigation).getByRole("link", { name: "Projects" }),
    ).toHaveClass("nav-item-active");
    expect(screen.getByRole("link", { name: /System Status/ })).not.toHaveClass(
      "nav-item-active",
    );
  });
});
