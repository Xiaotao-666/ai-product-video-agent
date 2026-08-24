import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  approveCreative,
  approveStoryboard,
  approveVideoPrompts,
  generateCreative,
  generateStoryboard,
  getAssembly,
  getCreativeContent,
  getExport,
  getMusic,
  getProject,
  getProjectWorkflow,
  getProjectTasks,
  getShots,
  getStoryboardContent,
  getSubtitle,
  getVideoPrompts,
  getVoice,
  getVoiceHistory,
  getVoiceOptions,
  getTask,
  regenerateCreative,
  regenerateStoryboard,
  reviseCreative,
  reviseStoryboard,
} from "../api/client";
import type {
  CreativeContentResponse,
  ProjectDetail,
  ProjectWorkflowResponse,
  StoryboardContentResponse,
  VideoPromptsContentResponse,
  WorkflowState,
} from "../api/types";
import { ProjectStagePage } from "./ProjectStagePage";

vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getAssembly: vi.fn(),
    getCreativeContent: vi.fn(),
    getExport: vi.fn(),
    getMusic: vi.fn(),
    getProject: vi.fn(),
    getProjectWorkflow: vi.fn(),
    getProjectTasks: vi.fn(),
    getShots: vi.fn(),
    getStoryboardContent: vi.fn(),
    getSubtitle: vi.fn(),
    getVideoPrompts: vi.fn(),
    getVoice: vi.fn(),
    getVoiceHistory: vi.fn(),
    getVoiceOptions: vi.fn(),
    generateCreative: vi.fn(),
    generateStoryboard: vi.fn(),
    getTask: vi.fn(),
    approveCreative: vi.fn(),
    approveStoryboard: vi.fn(),
    approveVideoPrompts: vi.fn(),
    regenerateCreative: vi.fn(),
    regenerateStoryboard: vi.fn(),
    reviseCreative: vi.fn(),
    reviseStoryboard: vi.fn(),
  };
});

const mockGetProject = vi.mocked(getProject);
const mockGetProjectWorkflow = vi.mocked(getProjectWorkflow);
const mockGetProjectTasks = vi.mocked(getProjectTasks);
const mockGetAssembly = vi.mocked(getAssembly);
const mockGetExport = vi.mocked(getExport);
const mockGetMusic = vi.mocked(getMusic);
const mockGetShots = vi.mocked(getShots);
const mockGetCreativeContent = vi.mocked(getCreativeContent);
const mockGetStoryboardContent = vi.mocked(getStoryboardContent);
const mockGetSubtitle = vi.mocked(getSubtitle);
const mockGetVideoPrompts = vi.mocked(getVideoPrompts);
const mockGetVoice = vi.mocked(getVoice);
const mockGetVoiceHistory = vi.mocked(getVoiceHistory);
const mockGetVoiceOptions = vi.mocked(getVoiceOptions);
const mockGenerateCreative = vi.mocked(generateCreative);
const mockGenerateStoryboard = vi.mocked(generateStoryboard);
const mockGetTask = vi.mocked(getTask);
const mockApproveCreative = vi.mocked(approveCreative);
const mockApproveStoryboard = vi.mocked(approveStoryboard);
const mockApproveVideoPrompts = vi.mocked(approveVideoPrompts);
const mockRegenerateCreative = vi.mocked(regenerateCreative);
const mockRegenerateStoryboard = vi.mocked(regenerateStoryboard);
const mockReviseCreative = vi.mocked(reviseCreative);
const mockReviseStoryboard = vi.mocked(reviseStoryboard);

function storyboardContentResponse(
  status = "WAITING_REVIEW",
): StoryboardContentResponse {
  return {
    project_id: "LEE柠檬",
    status,
    content: {
      total_duration_seconds: 18,
      shots: [
        {
          shot_id: 1,
          duration_seconds: 6,
          purpose: "建立产品视觉",
          visual: "明亮黄色背景中的柠檬产品微距",
          camera: "缓慢推近",
          voiceover_cues: [
            { text: "新鲜看得见", start_offset: 2, end_offset: 4, position: null },
          ],
          subtitle_cues: [],
          video_constraints: {
            reserve_subtitle_space: true,
            subtitle_safe_area: "bottom_center",
          },
        },
      ],
    },
  };
}

function creativeContentResponse(
  status = "APPROVED",
  concept = "明亮柠檬世界",
): CreativeContentResponse {
  return {
    project_id: "LEE柠檬",
    status,
    content: {
      creative_concept: concept,
      target_audience: "年轻消费者",
      key_message: "自然清爽",
      visual_direction: "高明度黄色品牌视觉",
      narrative_arc: "产品亮相到品牌收束",
      narration_plan: {
        enabled: false,
        tone: null,
        full_script: null,
        target_duration_seconds: null,
      },
      subtitle_strategy: {
        enabled: false,
        tone: null,
        density: null,
        max_lines: null,
        preferred_position: null,
        principles: [],
      },
      global_constraints: { must: [], must_not: [] },
      av_timeline_constraints: { forbidden_windows: [] },
    },
  };
}

function videoPromptsContentResponse(
  status = "WAITING_REVIEW",
): VideoPromptsContentResponse {
  return {
    project_id: "LEE柠檬",
    status,
    content: {
      shots: [1, 2, 3].map((shotId) => ({
        shot_id: shotId,
        prompt_version: 1,
        prompt_source: "ai_generated",
        visual_prompt_core: `visual prompt core ${shotId}`,
        prompt_text: `final video prompt ${shotId}\n[Composition Constraint]`,
      })),
    },
  };
}

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
      <RouteLocation />
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

function RouteLocation() {
  const location = useLocation();
  return <output data-testid="route-location">{location.pathname}</output>;
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
    mockGetProjectTasks.mockReset();
    mockGetAssembly.mockReset();
    mockGetExport.mockReset();
    mockGetMusic.mockReset();
    mockGetShots.mockReset();
    mockGetCreativeContent.mockReset();
    mockGetStoryboardContent.mockReset();
    mockGetSubtitle.mockReset();
    mockGetVideoPrompts.mockReset();
    mockGetVoice.mockReset();
    mockGetVoiceHistory.mockReset();
    mockGetVoiceOptions.mockReset();
    mockGenerateCreative.mockReset();
    mockGenerateStoryboard.mockReset();
    mockGetTask.mockReset();
    mockApproveCreative.mockReset();
    mockApproveStoryboard.mockReset();
    mockApproveVideoPrompts.mockReset();
    mockRegenerateCreative.mockReset();
    mockRegenerateStoryboard.mockReset();
    mockReviseCreative.mockReset();
    mockReviseStoryboard.mockReset();
    mockGetProjectTasks.mockResolvedValue({
      data: { project_id: "LEE柠檬", tasks: [] },
      correlationId: "req_tasks",
    });
    mockGetAssembly.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        status: "COMPLETED",
        current_version: 2,
        needs_update: false,
        changed_shot_id: null,
        created_at: "2026-08-18T14:00:00+08:00",
        total_duration: 18,
        video_available: true,
        shots: [],
        current_plan: null,
        final_videos: [],
      },
      correlationId: "req_assembly",
    });
    mockGetVoice.mockResolvedValue({
      data: {
        project_id: "LEE柠檬", status: "NOT_STARTED", version: null,
        created_at: null, script: null, script_source: null, provider: null, model: null,
        voice: null, language: null, audio_available: false,
        planned_narration_duration: null, planned_first_voice_start: null,
        planned_last_voice_end: null, planned_voice_span: null,
        actual_audio_duration: null, voice_track_start: null,
        actual_voice_end: null, total_video_duration: null,
        duration_difference_seconds: null, duration_difference_ratio: null,
        timing_mode: null, cue_level_alignment: null,
        script_matches_storyboard: null, calibration_status: "NOT_APPLICABLE",
        timing_acceptance: null,
      },
      correlationId: "req_voice",
    });
    mockGetVoiceHistory.mockResolvedValue({
      data: { project_id: "LEE柠檬", active_version: null, versions: [] },
      correlationId: "req_voice_history",
    });
    mockGetVoiceOptions.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        enabled: true,
        has_active_voice: false,
        active_version: null,
        next_version: 1,
        script: {
          source: "compiled_storyboard",
          text: "新鲜看得见",
          character_count: 6,
          cue_count: 1,
        },
        planned_timing: {
          first_start: 2,
          last_end: 4,
          span: 2,
          narration_duration: 2,
        },
        providers: [{
          provider_id: "xfyun_tts",
          display_name: "讯飞 TTS",
          model: "online-tts-v2",
          default_voice: "xiaoyan",
          language: "zh-CN",
          supported_languages: ["zh-CN"],
          allowed_voices: [],
          available: true,
        }],
        default_provider: "xfyun_tts",
        default_voice: "xiaoyan",
        default_language: "zh-CN",
        manual_script_required: false,
      },
      correlationId: "req_voice_options",
    });
    mockGetSubtitle.mockResolvedValue({
      data: {
        project_id: "LEE柠檬", status: "NOT_STARTED", version: null,
        source: null, timing_source: null, created_at: null, cue_count: 0,
        content_available: false, cues: [],
      },
      correlationId: "req_subtitle",
    });
    mockGetMusic.mockResolvedValue({
      data: {
        project_id: "LEE柠檬", status: "NOT_STARTED", version: null,
        created_at: null, audio_available: false, format: null,
        duration_seconds: null, music_mix: null,
      },
      correlationId: "req_music",
    });
    mockGetExport.mockResolvedValue({
      data: {
        project_id: "LEE柠檬", status: "COMPLETED", version: 3,
        created_at: "2026-08-18T14:20:00+08:00", stale: false,
        video_available: true, assembly_version: 2, voice_version: 1,
        subtitle_version: null, music_version: 2, voice_timing: null,
        music_mix: null,
      },
      correlationId: "req_export",
    });
    mockGetCreativeContent.mockResolvedValue({
      data: creativeContentResponse(),
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
        aggregation: {
          total: 3, approved: 3, waiting_review: 0,
          generating: 0, not_started: 0, failed: 0,
        },
        shots: [
          {
            shot_id: "shot_01",
            order: 1,
            title: "Shot 01",
            status: "APPROVED",
            prompt_status: "READY",
            video_status: "READY",
            review_status: "APPROVED",
            official_version: 2,
            pending_review_version: null,
            version_count: 2,
            generation_count: 2,
          },
          {
            shot_id: "shot_02",
            order: 2,
            title: "Shot 02",
            status: "APPROVED",
            prompt_status: "READY",
            video_status: "READY",
            review_status: "APPROVED",
            official_version: 1,
            pending_review_version: null,
            version_count: 1,
            generation_count: 1,
          },
          {
            shot_id: "shot_03",
            order: 3,
            title: "Shot 03",
            status: "APPROVED",
            prompt_status: "READY",
            video_status: "READY",
            review_status: "APPROVED",
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
    mockGetCreativeContent.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "NOT_STARTED", content: null },
      correlationId: "req_creative_empty",
    });
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

  it("exposes all three explicit Creative review actions while waiting", async () => {
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
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(screen.queryByText("审核创意")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改创意" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成创意" })).toBeInTheDocument();
    expect(screen.getByText(/均以 Backend 当前状态为准/)).toBeInTheDocument();
  });

  it("refreshes Project, Workflow, and Creative after Revise success", async () => {
    const waiting = workflow({
      workflow_phase: "CREATIVE_REVIEW",
      status: "WAITING_REVIEW",
      available_actions: [
        "APPROVE_CREATIVE",
        "REVISE_CREATIVE",
        "REGENERATE_CREATIVE",
      ],
    });
    waiting.stages.creative.status = "WAITING_REVIEW";
    waiting.stages.storyboard.status = "NOT_STARTED";
    mockGetProject
      .mockResolvedValueOnce({ data: detail(waiting), correlationId: "req_initial" })
      .mockResolvedValue({ data: detail(waiting), correlationId: "req_refreshed" });
    mockGetProjectWorkflow
      .mockResolvedValueOnce({ data: waiting, correlationId: "req_initial" })
      .mockResolvedValue({ data: waiting, correlationId: "req_refreshed" });
    mockGetCreativeContent
      .mockResolvedValueOnce({
        data: creativeContentResponse("WAITING_REVIEW", "原始 Creative"),
        correlationId: "req_initial_creative",
      })
      .mockResolvedValue({
        data: creativeContentResponse("WAITING_REVIEW", "焕新后的产品微距创意"),
        correlationId: "req_refreshed_creative",
      });
    mockReviseCreative.mockResolvedValue({
      data: {
        task_id: `task_${"b".repeat(32)}`,
        project_id: "LEE柠檬",
        operation: "CREATIVE_REVISE",
        status: "SUCCEEDED",
        created_at: "2026-08-18T14:30:00Z",
        started_at: "2026-08-18T14:30:01Z",
        finished_at: "2026-08-18T14:30:02Z",
        correlation_id: "req_revise",
        error: null,
        result: {
          resource_type: "CREATIVE",
          resource_id: "LEE柠檬",
          version: null,
        },
      },
      correlationId: "req_revise",
    });

    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/creative");
    expect(await screen.findByText("原始 Creative")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修改创意" }));
    fireEvent.change(screen.getByRole("textbox", { name: "修改意见" }), {
      target: { value: "增加产品微距" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));

    expect(await screen.findByText("焕新后的产品微距创意")).toBeInTheDocument();
    expect(mockReviseCreative).toHaveBeenCalledWith("LEE柠檬", "增加产品微距");
    expect(mockGetProject).toHaveBeenCalledTimes(2);
    expect(mockGetProjectWorkflow).toHaveBeenCalledTimes(2);
    expect(mockGetCreativeContent).toHaveBeenCalledTimes(2);
    expect(screen.getByRole("button", { name: "审核通过" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "修改创意" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成创意" })).toBeInTheDocument();
    expect(screen.queryByText("自动生成分镜")).not.toBeInTheDocument();
  });

  it("disables Approve while an active Creative revision task is recovered", async () => {
    const waiting = workflow({
      workflow_phase: "CREATIVE_REVIEW",
      status: "WAITING_REVIEW",
      available_actions: [
        "APPROVE_CREATIVE",
        "REVISE_CREATIVE",
        "REGENERATE_CREATIVE",
      ],
    });
    waiting.stages.creative.status = "WAITING_REVIEW";
    resolveStage(waiting, detail(waiting));
    mockGetProjectTasks.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        tasks: [
          {
            task_id: `task_${"c".repeat(32)}`,
            project_id: "LEE柠檬",
            operation: "CREATIVE_REVISE",
            status: "RUNNING",
            created_at: "2026-08-18T14:30:00Z",
            started_at: "2026-08-18T14:30:01Z",
            finished_at: null,
            correlation_id: "req_running",
            error: null,
            result: null,
          },
        ],
      },
      correlationId: "req_tasks",
    });

    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/creative");
    const approve = await screen.findByRole("button", { name: "审核通过" });
    await waitFor(() => expect(approve).toBeDisabled());
    expect(screen.getByText(/完成前不能审核通过/)).toBeInTheDocument();
    expect(mockApproveCreative).not.toHaveBeenCalled();
  });

  it("re-fetches Project, Workflow, and Creative after approval", async () => {
    const waiting = workflow({
      workflow_phase: "CREATIVE_REVIEW",
      status: "WAITING_REVIEW",
      available_actions: [
        "APPROVE_CREATIVE",
        "REVISE_CREATIVE",
        "REGENERATE_CREATIVE",
      ],
    });
    waiting.stages.creative.status = "WAITING_REVIEW";
    waiting.stages.storyboard.status = "NOT_STARTED";
    const approved = workflow({
      workflow_phase: "STORYBOARD",
      status: "APPROVED",
      available_actions: ["GENERATE_STORYBOARD"],
    });
    approved.stages.creative.status = "APPROVED";
    approved.stages.storyboard.status = "NOT_STARTED";

    mockGetProject
      .mockResolvedValueOnce({ data: detail(waiting), correlationId: "req_initial" })
      .mockResolvedValue({ data: detail(approved), correlationId: "req_refreshed" });
    mockGetProjectWorkflow
      .mockResolvedValueOnce({ data: waiting, correlationId: "req_initial" })
      .mockResolvedValue({ data: approved, correlationId: "req_refreshed" });
    mockGetCreativeContent
      .mockResolvedValueOnce({
        data: creativeContentResponse("WAITING_REVIEW"),
        correlationId: "req_initial_creative",
      })
      .mockResolvedValue({
        data: creativeContentResponse("APPROVED"),
        correlationId: "req_refreshed_creative",
      });
    mockApproveCreative.mockResolvedValue({
      data: approved,
      correlationId: "req_approve",
    });

    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/creative");
    fireEvent.click(await screen.findByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));

    const storyboardLink = await screen.findByRole("link", {
      name: "前往 Storyboard",
    });
    expect(storyboardLink).toBeInTheDocument();
    await waitFor(() => {
      expect(mockGetProject).toHaveBeenCalledTimes(2);
      expect(mockGetProjectWorkflow).toHaveBeenCalledTimes(2);
      expect(mockGetCreativeContent).toHaveBeenCalledTimes(2);
    });
    expect(mockApproveCreative).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    expect(screen.getByText("已生成")).toBeInTheDocument();
    expect(screen.getByText("Creative 已审核通过。")).toBeInTheDocument();

    fireEvent.click(storyboardLink);
    expect(screen.getByText("正在加载项目阶段…")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "分镜规划" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("route-location")).toHaveTextContent(
      "/stages/storyboard",
    );
  });

  it("generates Storyboard through a durable task and refreshes canonical content", async () => {
    const ready = workflow({
      workflow_phase: "STORYBOARD",
      status: "APPROVED",
      available_actions: ["GENERATE_STORYBOARD"],
    });
    ready.stages.creative.status = "APPROVED";
    ready.stages.storyboard.status = "NOT_STARTED";
    const waiting = workflow({
      workflow_phase: "STORYBOARD_REVIEW",
      status: "WAITING_REVIEW",
      available_actions: [
        "APPROVE_STORYBOARD",
        "REVISE_STORYBOARD",
        "REGENERATE_STORYBOARD",
      ],
    });
    waiting.stages.creative.status = "APPROVED";
    waiting.stages.storyboard.status = "WAITING_REVIEW";

    mockGetProject
      .mockResolvedValueOnce({ data: detail(ready), correlationId: "req_initial" })
      .mockResolvedValue({ data: detail(waiting), correlationId: "req_refreshed" });
    mockGetProjectWorkflow
      .mockResolvedValueOnce({ data: ready, correlationId: "req_initial" })
      .mockResolvedValue({ data: waiting, correlationId: "req_refreshed" });
    mockGetStoryboardContent
      .mockResolvedValueOnce({
        data: { project_id: "LEE柠檬", status: "NOT_STARTED", content: null },
        correlationId: "req_initial_storyboard",
      })
      .mockResolvedValue({
        data: storyboardContentResponse(),
        correlationId: "req_refreshed_storyboard",
      });
    mockGenerateStoryboard.mockResolvedValue({
      data: {
        task_id: `task_${"d".repeat(32)}`,
        project_id: "LEE柠檬",
        operation: "STORYBOARD_GENERATE",
        status: "SUCCEEDED",
        created_at: "2026-08-19T14:30:00Z",
        started_at: "2026-08-19T14:30:01Z",
        finished_at: "2026-08-19T14:30:02Z",
        correlation_id: "req_storyboard_generate",
        error: null,
        result: {
          resource_type: "STORYBOARD",
          resource_id: "LEE柠檬",
          version: null,
        },
      },
      correlationId: "req_storyboard_generate",
    });

    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/storyboard");
    expect(await screen.findByText("分镜规划尚未生成。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "生成分镜" }));

    expect(await screen.findByText("明亮黄色背景中的柠檬产品微距")).toBeInTheDocument();
    expect(mockGenerateStoryboard).toHaveBeenCalledTimes(1);
    expect(mockGenerateStoryboard).toHaveBeenCalledWith("LEE柠檬");
    expect(mockGetProject).toHaveBeenCalledTimes(2);
    expect(mockGetProjectWorkflow).toHaveBeenCalledTimes(2);
    expect(mockGetStoryboardContent).toHaveBeenCalledTimes(2);
    expect(screen.getByText("已生成")).toBeInTheDocument();
    expect(screen.getAllByText("等待审核").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "生成分镜" })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改分镜" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成分镜" })).toBeInTheDocument();
    expect(screen.getByText(/修改、重新生成与审核操作均以 Backend/)).toBeInTheDocument();
  });

  it("keeps the old Storyboard visible during revise, disables Approve, then refreshes the new canonical", async () => {
    const waiting = workflow({
      workflow_phase: "STORYBOARD_REVIEW",
      status: "WAITING_REVIEW",
      available_actions: [
        "APPROVE_STORYBOARD",
        "REVISE_STORYBOARD",
        "REGENERATE_STORYBOARD",
      ],
    });
    waiting.stages.creative.status = "APPROVED";
    waiting.stages.storyboard.status = "WAITING_REVIEW";
    const updated = storyboardContentResponse();
    updated.content!.shots[0].visual = "重新调度后的产品近景";
    updated.content!.shots[0].voiceover_cues = [
      { text: "新的旁白", start_offset: 3, end_offset: 5, position: null },
    ];
    mockGetProject.mockResolvedValue({
      data: detail(waiting),
      correlationId: "req_storyboard_project",
    });
    mockGetProjectWorkflow.mockResolvedValue({
      data: waiting,
      correlationId: "req_storyboard_workflow",
    });
    mockGetStoryboardContent
      .mockResolvedValueOnce({
        data: storyboardContentResponse(),
        correlationId: "req_old_storyboard",
      })
      .mockResolvedValue({
        data: updated,
        correlationId: "req_new_storyboard",
      });
    let resolveRevise!: (
      value: Awaited<ReturnType<typeof reviseStoryboard>>,
    ) => void;
    mockReviseStoryboard.mockReturnValue(
      new Promise((resolve) => {
        resolveRevise = resolve;
      }),
    );

    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/storyboard");
    expect(
      await screen.findByText("明亮黄色背景中的柠檬产品微距"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "修改分镜" }));
    fireEvent.change(screen.getByLabelText("修改意见"), {
      target: { value: "第二镜头减少旁白" },
    });
    fireEvent.click(screen.getByRole("button", { name: "提交修改" }));

    expect(screen.getByText("明亮黄色背景中的柠檬产品微距")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "审核通过" })).toBeDisabled();
    expect(mockReviseStoryboard).toHaveBeenCalledWith(
      "LEE柠檬",
      "第二镜头减少旁白",
    );

    resolveRevise({
      data: {
        task_id: `task_${"r".repeat(32)}`,
        project_id: "LEE柠檬",
        operation: "STORYBOARD_REVISE",
        status: "SUCCEEDED",
        created_at: "2026-08-19T15:00:00Z",
        started_at: "2026-08-19T15:00:01Z",
        finished_at: "2026-08-19T15:00:02Z",
        correlation_id: "req_storyboard_revise",
        error: null,
        result: {
          resource_type: "STORYBOARD",
          resource_id: "LEE柠檬",
          version: null,
        },
      },
      correlationId: "req_storyboard_revise",
    });

    expect(await screen.findByText("重新调度后的产品近景")).toBeInTheDocument();
    expect(screen.getByText("新的旁白")).toBeInTheDocument();
    expect(screen.getAllByText("等待审核").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "审核通过" })).toBeEnabled();
    expect(mockGetStoryboardContent).toHaveBeenCalledTimes(2);
    expect(mockGenerateStoryboard).not.toHaveBeenCalled();
    expect(mockRegenerateStoryboard).not.toHaveBeenCalled();
  });

  it("approves Storyboard synchronously, refreshes durable state, and only navigates next", async () => {
    const waiting = workflow({
      workflow_phase: "STORYBOARD_REVIEW",
      status: "WAITING_REVIEW",
      available_actions: [
        "APPROVE_STORYBOARD",
        "REVISE_STORYBOARD",
        "REGENERATE_STORYBOARD",
      ],
    });
    waiting.stages.creative.status = "APPROVED";
    waiting.stages.storyboard.status = "WAITING_REVIEW";
    waiting.stages.video_prompt.status = "NOT_STARTED";
    const approved = workflow({
      workflow_phase: "VIDEO_PROMPT",
      status: "APPROVED",
      available_actions: ["GENERATE_VIDEO_PROMPTS"],
    });
    approved.stages.creative.status = "APPROVED";
    approved.stages.storyboard.status = "APPROVED";
    approved.stages.video_prompt.status = "NOT_STARTED";

    mockGetProject
      .mockResolvedValueOnce({ data: detail(waiting), correlationId: "req_initial" })
      .mockResolvedValue({ data: detail(approved), correlationId: "req_refreshed" });
    mockGetProjectWorkflow
      .mockResolvedValueOnce({ data: waiting, correlationId: "req_initial" })
      .mockResolvedValue({ data: approved, correlationId: "req_refreshed" });
    mockGetStoryboardContent.mockResolvedValue({
      data: storyboardContentResponse(),
      correlationId: "req_storyboard",
    });
    mockGetVideoPrompts.mockResolvedValue({
      data: { project_id: "LEE柠檬", status: "NOT_STARTED", content: null },
      correlationId: "req_video_prompt_empty",
    });
    mockApproveStoryboard.mockResolvedValue({
      data: approved,
      correlationId: "req_storyboard_approve",
    });

    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/storyboard");
    expect(await screen.findByText("明亮黄色背景中的柠檬产品微距")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "修改分镜" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新生成分镜" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("确认通过当前分镜方案？");
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));

    expect(await screen.findByText("Storyboard 已审核通过。")).toBeInTheDocument();
    expect(mockApproveStoryboard).toHaveBeenCalledTimes(1);
    expect(mockApproveStoryboard).toHaveBeenCalledWith("LEE柠檬");
    expect(mockGetProject).toHaveBeenCalledTimes(2);
    expect(mockGetProjectWorkflow).toHaveBeenCalledTimes(2);
    expect(mockGetStoryboardContent).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("已审核").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "前往视频提示词" }));
    expect(
      await screen.findByRole("heading", { name: "视频提示词" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("route-location")).toHaveTextContent(
      "/stages/video-prompt",
    );
    expect(await screen.findByText("视频提示词尚未生成。")).toBeInTheDocument();
    expect(mockGetVideoPrompts).toHaveBeenCalledTimes(1);
  });

  it("approves Video Prompts synchronously, refreshes content, and only navigates to Shots", async () => {
    const waiting = workflow({
      workflow_phase: "VIDEO_PROMPT_REVIEW",
      status: "WAITING_REVIEW",
      available_actions: [
        "APPROVE_VIDEO_PROMPTS",
        "REVISE_VIDEO_PROMPTS",
        "REGENERATE_VIDEO_PROMPTS",
      ],
    });
    waiting.stages.video_prompt.status = "WAITING_REVIEW";
    waiting.stages.shots = { status: "NOT_STARTED", approved: 0, total: 3 };
    const approved = workflow({
      workflow_phase: "VIDEO_GENERATION",
      status: "APPROVED",
      available_actions: ["GENERATE_SHOTS"],
    });
    approved.stages.video_prompt.status = "APPROVED";
    approved.stages.shots = { status: "NOT_STARTED", approved: 0, total: 3 };

    mockGetProject
      .mockResolvedValueOnce({ data: detail(waiting), correlationId: "req_initial" })
      .mockResolvedValue({ data: detail(approved), correlationId: "req_refreshed" });
    mockGetProjectWorkflow
      .mockResolvedValueOnce({ data: waiting, correlationId: "req_initial" })
      .mockResolvedValue({ data: approved, correlationId: "req_refreshed" });
    mockGetVideoPrompts
      .mockResolvedValueOnce({
        data: videoPromptsContentResponse(),
        correlationId: "req_prompts_waiting",
      })
      .mockResolvedValue({
        data: videoPromptsContentResponse("APPROVED"),
        correlationId: "req_prompts_approved",
      });
    mockApproveVideoPrompts.mockResolvedValue({
      data: approved,
      correlationId: "req_video_prompt_approve",
    });
    mockGetShots.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        status: "NOT_STARTED",
        aggregation: {
          total: 0, approved: 0, waiting_review: 0,
          generating: 0, not_started: 0, failed: 0,
        },
        shots: [],
      },
      correlationId: "req_empty_shots",
    });

    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/video-prompt");
    expect(await screen.findByText("visual prompt core 1")).toBeInTheDocument();
    expect(screen.getByText("visual prompt core 2")).toBeInTheDocument();
    expect(screen.getByText("visual prompt core 3")).toBeInTheDocument();
    expect(screen.getByText("修改视频提示词")).toBeInTheDocument();
    expect(screen.getByText("重新生成视频提示词")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    expect(screen.getByRole("dialog")).toHaveTextContent("不会自动生成视频");
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(mockApproveVideoPrompts).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "审核通过" }));
    fireEvent.click(screen.getByRole("button", { name: "确认通过" }));

    expect(await screen.findByText("视频提示词已审核通过。")).toBeInTheDocument();
    expect(mockApproveVideoPrompts).toHaveBeenCalledTimes(1);
    expect(mockApproveVideoPrompts).toHaveBeenCalledWith("LEE柠檬");
    expect(mockGetProject).toHaveBeenCalledTimes(2);
    expect(mockGetProjectWorkflow).toHaveBeenCalledTimes(2);
    expect(mockGetVideoPrompts).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("已审核").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "审核通过" })).not.toBeInTheDocument();
    expect(screen.getByText("visual prompt core 1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("link", { name: "前往镜头" }));
    expect(await screen.findByRole("heading", { name: "镜头" })).toBeInTheDocument();
    expect(screen.getByTestId("route-location")).toHaveTextContent("/stages/shots");
    expect(await screen.findByText("当前项目尚无可浏览镜头。")).toBeInTheDocument();
    expect(mockGetShots).toHaveBeenCalledTimes(1);
    // The review-action panel performs one read-only task query so it can
    // recover an in-flight Revise/Regenerate after F5. Approval itself still
    // creates no Web task.
    expect(mockGetProjectTasks).toHaveBeenCalledTimes(1);
    expect(mockGetProjectTasks).toHaveBeenCalledWith("LEE柠檬");
  });

  it("switches continuously through every Workflow Stage without a blank render", async () => {
    const stages = [
      ["creative", "创意策划"],
      ["storyboard", "分镜规划"],
      ["video-prompt", "视频提示词"],
      ["shots", "镜头"],
      ["assembly", "视频合片"],
      ["voice", "配音"],
      ["subtitle", "字幕"],
      ["music", "音乐"],
      ["export", "最终导出"],
      ["creative", "创意策划"],
    ] as const;

    renderStage("/projects/LEE%E6%9F%A0%E6%AA%AC/stages/creative");
    expect(
      await screen.findByRole("heading", { name: "创意策划" }),
    ).toBeInTheDocument();

    for (const [key, label] of stages.slice(1)) {
      const navigation = screen.getByRole("navigation", {
        name: "Workflow stages",
      });
      fireEvent.click(within(navigation).getByRole("link", { name: new RegExp(label) }));
      expect(screen.getByRole("main")).toBeInTheDocument();
      expect(document.body.textContent?.trim().length).toBeGreaterThan(0);
      expect(
        await screen.findByRole("heading", { name: label }),
      ).toBeInTheDocument();
      expect(screen.getByTestId("route-location")).toHaveTextContent(
        `/stages/${key}`,
      );
      const activeLink = within(
        screen.getByRole("navigation", { name: "Workflow stages" }),
      ).getByRole("link", { name: new RegExp(label) });
      expect(activeLink).toHaveClass("stage-nav-link-active");
      expect(activeLink).toHaveAttribute("aria-current", "page");
    }
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
