import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  approveShot,
  getProject,
  getProjectTasks,
  getReferenceAssets,
  getShot,
  getShotGenerationOptions,
  getShotGenerationStatus,
} from "../api/client";
import type { ProjectDetail, ShotDetail } from "../api/types";
import { ShotDetailPage } from "./ShotDetailPage";


vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    approveShot: vi.fn(),
    getProject: vi.fn(),
    getShot: vi.fn(),
    getReferenceAssets: vi.fn(),
    getShotGenerationOptions: vi.fn(),
    getProjectTasks: vi.fn(),
    getShotGenerationStatus: vi.fn(),
  };
});

const mockGetProject = vi.mocked(getProject);
const mockApproveShot = vi.mocked(approveShot);
const mockGetShot = vi.mocked(getShot);
const mockGetReferenceAssets = vi.mocked(getReferenceAssets);
const mockGetShotGenerationOptions = vi.mocked(getShotGenerationOptions);
const mockGetProjectTasks = vi.mocked(getProjectTasks);
const mockGetShotGenerationStatus = vi.mocked(getShotGenerationStatus);

const project: ProjectDetail = {
  project_id: "LEE柠檬",
  name: "LEE柠檬",
  request: {
    product_name: "LEE柠檬",
    product_description: "清爽饮料",
    user_notes: null,
    duration_seconds: 18,
    video_style: "清爽",
    video_purpose: "推广",
  },
  workflow: {
    workflow_phase: "COMPLETED",
    status: "COMPLETED",
    stages: {
      creative: { status: "APPROVED" },
      storyboard: { status: "APPROVED" },
      video_prompt: { status: "APPROVED" },
      shots: { status: "COMPLETED", approved: 3, total: 3 },
      assembly: { status: "COMPLETED", needs_update: false, version: 1 },
      voice: { status: "NOT_STARTED", version: null },
      subtitle: { status: "NOT_STARTED", version: null },
      music: { status: "NOT_STARTED", version: null },
      export: { status: "NOT_STARTED", version: null, created_at: null, stale: false },
    },
    available_actions: [],
  },
  assembly: { status: "COMPLETED", needs_update: false, version: 1 },
  post_production: {
    status: "NOT_STARTED",
    voice: { status: "NOT_STARTED", version: null },
    subtitle: { status: "NOT_STARTED", version: null },
    music: { status: "NOT_STARTED", version: null },
  },
  final_export: { status: "NOT_STARTED", version: null, created_at: null, stale: false },
  updated_at: "2026-08-18T18:00:00+08:00",
};

const shot: ShotDetail = {
  project_id: "LEE柠檬",
  shot_id: "shot_01",
  status: "APPROVED",
  official_version: 2,
  pending_review_version: 3,
  version_count: 3,
  generation_count: 3,
  versions: [
    {
      version: 3,
      role: "PENDING_REVIEW",
      review_status: "WAITING_REVIEW",
      created_at: "2026-08-18T12:03:00+08:00",
      prompt: {
        version: 4,
        source: "ai_revision",
        visual_prompt_core: null,
        final_prompt: "pending final prompt four",
      },
      generation: { model: "MiniMax-H3", visual_input_mode: "FIRST_FRAME" },
      video_available: true,
    },
    {
      version: 2,
      role: "OFFICIAL",
      review_status: "APPROVED",
      created_at: "2026-08-18T12:02:00+08:00",
      prompt: {
        version: 2,
        source: "ai_revision",
        visual_prompt_core: "official visual core",
        final_prompt: "official final prompt two",
      },
      generation: { model: "MiniMax-H3", visual_input_mode: "REFERENCE_ASSET" },
      video_available: true,
    },
    {
      version: 1,
      role: "HISTORY",
      review_status: "REJECTED",
      created_at: "2026-08-18T12:01:00+08:00",
      prompt: {
        version: 1,
        source: "ai_generated",
        visual_prompt_core: null,
        final_prompt: "history final prompt one",
      },
      generation: { model: "MiniMax-Hailuo-2.3", visual_input_mode: "NONE" },
      video_available: false,
    },
  ],
};

const waitingInitialShot: ShotDetail = {
  project_id: "LEE柠檬",
  shot_id: "shot_01",
  status: "WAITING_REVIEW",
  official_version: null,
  pending_review_version: 1,
  version_count: 1,
  generation_count: 1,
  versions: [
    {
      version: 1,
      role: "PENDING_REVIEW",
      review_status: "WAITING_REVIEW",
      created_at: "2026-08-19T12:00:00+08:00",
      prompt: {
        version: 2,
        source: "ai_revision",
        visual_prompt_core: "initial visual core",
        final_prompt: "initial final prompt",
      },
      generation: { model: "MiniMax-Hailuo-2.3", visual_input_mode: "NONE" },
      video_available: true,
    },
  ],
};

const approvedInitialShot: ShotDetail = {
  ...waitingInitialShot,
  status: "APPROVED",
  official_version: 1,
  pending_review_version: null,
  versions: waitingInitialShot.versions.map((version) => ({
    ...version,
    role: "OFFICIAL",
    review_status: "APPROVED",
  })),
};

function renderPage(path = "/projects/LEE%E6%9F%A0%E6%AA%AC/stages/shots/shot_01") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/projects/:projectId/stages/shots/:shotId" element={<ShotDetailPage />} />
        <Route path="/projects/:projectId/stages/shots" element={<h1>Shot List Test Route</h1>} />
      </Routes>
    </MemoryRouter>,
  );
}

describe("ShotDetailPage", () => {
  beforeEach(() => {
    mockGetProject.mockReset();
    mockApproveShot.mockReset();
    mockGetShot.mockReset();
    mockGetReferenceAssets.mockReset();
    mockGetShotGenerationOptions.mockReset();
    mockGetProjectTasks.mockReset();
    mockGetShotGenerationStatus.mockReset();
    mockGetProject.mockResolvedValue({ data: project, correlationId: "req_project" });
    mockApproveShot.mockResolvedValue({ data: approvedInitialShot, correlationId: "req_approve" });
    mockGetShot.mockResolvedValue({ data: shot, correlationId: "req_shot" });
    mockGetReferenceAssets.mockResolvedValue({
      data: { project_id: "LEE柠檬", assets: [] },
      correlationId: "req_references",
    });
    mockGetProjectTasks.mockResolvedValue({
      data: { project_id: "LEE柠檬", tasks: [] },
      correlationId: "req_tasks",
    });
    mockGetShotGenerationStatus.mockResolvedValue({
      data: {
        project_id: "LEE柠檬", shot_id: "shot_01", state: "NOT_STARTED",
        resume_available: false, resume_kind: null, video_version: null,
        provider_submission_known: true,
      },
      correlationId: "req_status",
    });
    mockGetShotGenerationOptions.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        eligible: true,
        shot: { shot_id: "shot_01", duration_seconds: 6, prompt_version: 2, resolution: "768P" },
        selection_modes: ["AUTO", "MANUAL"],
        visual_input_modes: [
          { mode: "none", display_name: "不使用参考图", description: "完全根据提示词生成。", compatible_model_ids: ["MiniMax-Hailuo-2.3"] },
          { mode: "reference_asset", display_name: "主体参考", description: "保持主体身份。", compatible_model_ids: [] },
          { mode: "first_frame", display_name: "作为首帧", description: "作为第一帧。", compatible_model_ids: ["MiniMax-Hailuo-2.3"] },
        ],
        models: [{
          model_id: "MiniMax-Hailuo-2.3",
          display_name: "MiniMax Hailuo 2.3",
          provider: "minimax",
          provider_display_name: "MiniMax",
          api_version: "v1",
          available: true,
          supported_visual_input_modes: ["none", "first_frame"],
          supported_resolutions: ["768P"],
          supported_durations: [6, 10],
          min_duration: null,
          max_duration: null,
        }],
        issues: [],
        paid_call_required: true,
      },
      correlationId: "req_options",
    });
  });

  it("loads a Chinese project and Shot ID from the URL", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Shot 01", level: 1 })).toBeInTheDocument();
    expect(mockGetProject).toHaveBeenCalledWith("LEE柠檬");
    expect(mockGetShot).toHaveBeenCalledWith("LEE柠檬", "shot_01");
  });

  it("visually separates the current official version", async () => {
    renderPage();
    const heading = await screen.findByRole("heading", { name: "当前正式版本", level: 2 });
    const section = heading.closest("section");
    expect(section).toHaveClass("shot-version-section-official");
    expect(within(section!).getByRole("heading", { name: "Video v2 / Prompt v2" })).toBeInTheDocument();
  });

  it("shows a pending review version in its own area", async () => {
    renderPage();
    const section = (await screen.findByRole("heading", { name: "待审核新版本", level: 2 })).closest("section");
    expect(within(section!).getByRole("heading", { name: "Video v3 / Prompt v4" })).toBeInTheDocument();
    expect(within(section!).getByText("现有正式版本的候选审核将在后续阶段开放。")).toBeInTheDocument();
    expect(within(section!).queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
  });

  it("approves an initial v001, refreshes it as official, and keeps video controls", async () => {
    mockGetShot
      .mockResolvedValueOnce({ data: waitingInitialShot, correlationId: "req_waiting" })
      .mockResolvedValueOnce({ data: approvedInitialShot, correlationId: "req_approved" });
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: "审核通过" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("确认通过当前视频版本？");
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));
    await waitFor(() => expect(mockGetShot).toHaveBeenCalledTimes(2));
    const officialSection = screen.getByRole("heading", {
      name: "当前正式版本",
      level: 2,
    }).closest("section");
    expect(within(officialSection!).getByRole("heading", {
      name: "Video v1 / Prompt v2",
    })).toBeInTheDocument();
    expect(mockApproveShot).toHaveBeenCalledTimes(1);
    expect(mockApproveShot).toHaveBeenCalledWith("LEE柠檬", "shot_01");
    expect(mockGetShotGenerationStatus).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Video v1 预览")).toHaveAttribute("controls");
    expect(document.body).not.toHaveTextContent(/QUEUED|RUNNING|MiniMax 调用/);
  });

  it("restores the approved v001 directly from GET after F5", async () => {
    mockGetShot.mockResolvedValue({ data: approvedInitialShot, correlationId: "req_f5" });
    renderPage();
    expect(await screen.findByRole("heading", { name: "Video v1 / Prompt v2" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    expect(screen.getByLabelText("Video v1 预览")).toHaveAttribute("controls");
    expect(mockApproveShot).not.toHaveBeenCalled();
  });

  it("is safe when no pending version exists", async () => {
    mockGetShot.mockResolvedValue({
      data: { ...shot, pending_review_version: null, version_count: 2, versions: shot.versions.filter((item) => item.role !== "PENDING_REVIEW") },
      correlationId: "req_shot",
    });
    renderPage();
    await screen.findByRole("heading", { name: "Shot 01", level: 1 });
    expect(screen.queryByRole("heading", { name: "待审核新版本", level: 2 })).not.toBeInTheDocument();
  });

  it("shows rejected history instead of hiding it", async () => {
    renderPage();
    const section = (await screen.findByRole("heading", { name: "历史版本", level: 2 })).closest("section");
    expect(within(section!).getByText("已拒绝")).toBeInTheDocument();
    expect(within(section!).getByRole("heading", { name: "Video v1 / Prompt v1" })).toBeInTheDocument();
  });

  it("keeps the Video and bound Prompt versions together", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "Video v3 / Prompt v4" })).toBeInTheDocument();
    expect(screen.getByText("pending final prompt four")).toBeInTheDocument();
  });

  it("shows visual core and final prompt separately", async () => {
    renderPage();
    expect(await screen.findByText("official visual core")).toBeInTheDocument();
    expect(screen.getByText("official final prompt two")).toBeInTheDocument();
  });

  it("shows model and visual input metadata", async () => {
    renderPage();
    expect((await screen.findAllByText("MiniMax H3")).length).toBeGreaterThan(0);
    expect(screen.getByText("Reference Asset")).toBeInTheDocument();
    expect(screen.getByText("首帧")).toBeInTheDocument();
  });

  it("uses a Backend media URL on the video element", async () => {
    renderPage();
    const video = await screen.findByLabelText("Video v2 预览");
    expect(video).toHaveAttribute(
      "src",
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/shots/shot_01/versions/2/video",
    );
    expect(video).toHaveAttribute("controls");
  });

  it("shows missing video safely while retaining metadata", async () => {
    renderPage();
    expect(await screen.findByText("视频文件不可用")).toBeInTheDocument();
    expect(screen.getByText("history final prompt one")).toBeInTheDocument();
  });

  it("shows a safe SHOT_NOT_FOUND error", async () => {
    mockGetShot.mockRejectedValue(new ApiClientError({ message: "raw path", status: 404, code: "SHOT_NOT_FOUND" }));
    renderPage();
    expect(await screen.findByRole("heading", { name: "镜头不存在或已被删除" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("raw path");
  });

  it("shows a safe network error", async () => {
    mockGetShot.mockRejectedValue(new ApiClientError({ message: "D:\\private API_KEY", code: "NETWORK_ERROR" }));
    renderPage();
    expect(await screen.findByRole("heading", { name: "无法连接 Backend" })).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent("D:\\private");
  });

  it("retries without refreshing the browser", async () => {
    mockGetShot
      .mockRejectedValueOnce(new ApiClientError({ message: "temporary", code: "HTTP_ERROR", correlationId: "req_retry" }))
      .mockResolvedValueOnce({ data: shot, correlationId: "req_shot" });
    renderPage();
    expect(await screen.findByText("错误编号：req_retry")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重试" }));
    expect(await screen.findByRole("heading", { name: "Shot 01", level: 1 })).toBeInTheDocument();
    expect(mockGetShot).toHaveBeenCalledTimes(2);
  });

  it("shows a loading state while requests are pending", () => {
    mockGetProject.mockReturnValue(new Promise<Awaited<ReturnType<typeof getProject>>>(() => undefined));
    mockGetShot.mockReturnValue(new Promise<Awaited<ReturnType<typeof getShot>>>(() => undefined));
    renderPage();
    expect(screen.getByText("正在加载镜头详情…")).toBeInTheDocument();
    expect(screen.getByRole("main")).toHaveAttribute("aria-busy", "true");
  });

  it("contains no write action controls", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Shot 01", level: 1 });
    expect(screen.queryByRole("button", { name: /批准|拒绝|生成|选择正式|编辑|删除/ })).not.toBeInTheDocument();
  });

  it("does not render unsafe response extensions or internal terminology", async () => {
    const unsafe = Object.assign({}, shot, {
      local_path: "D:\\private\\video.mp4",
      credential_env_name: "MINIMAX_API_KEY",
      candidate_state: "WAITING_REVIEW",
      provider_task_id: "hidden",
    });
    mockGetShot.mockResolvedValue({ data: unsafe, correlationId: "req_shot" });
    renderPage();
    await screen.findByRole("heading", { name: "Shot 01", level: 1 });
    expect(document.body).not.toHaveTextContent("D:\\private");
    expect(document.body).not.toHaveTextContent("MINIMAX_API_KEY");
    expect(document.body).not.toHaveTextContent(/candidate/i);
    expect(document.body).not.toHaveTextContent("provider_task_id");
  });

  it("returns to the Shot list with a semantic Link", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "Shot 01", level: 1 });
    fireEvent.click(screen.getByRole("link", { name: "← 返回镜头列表" }));
    expect(await screen.findByRole("heading", { name: "Shot List Test Route" })).toBeInTheDocument();
  });

  it("shows an empty history state", async () => {
    mockGetShot.mockResolvedValue({
      data: { ...shot, version_count: 2, versions: shot.versions.filter((item) => item.role !== "HISTORY") },
      correlationId: "req_shot",
    });
    renderPage();
    expect(await screen.findByText("当前没有历史版本。")).toBeInTheDocument();
  });

  it("shows generation preparation only for an ungenerated Shot in GENERATE_SHOTS", async () => {
    mockGetProject.mockResolvedValue({
      data: {
        ...project,
        workflow: {
          ...project.workflow,
          workflow_phase: "VIDEO_GENERATION",
          status: "APPROVED",
          stages: {
            ...project.workflow.stages,
            video_prompt: { status: "APPROVED" },
            shots: { status: "NOT_STARTED", approved: 0, total: 1 },
          },
          available_actions: ["GENERATE_SHOTS"],
        },
      },
      correlationId: "req_project",
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
    renderPage();
    expect(await screen.findByRole("heading", { name: "生成设置" })).toBeInTheDocument();
    await waitFor(() => {
      expect(mockGetShotGenerationOptions).toHaveBeenCalledWith("LEE柠檬", "shot_01");
      expect(mockGetReferenceAssets).toHaveBeenCalledWith("LEE柠檬");
    });
    expect(screen.queryByRole("button", { name: "生成视频" })).not.toBeInTheDocument();
  });
});
