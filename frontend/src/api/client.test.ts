import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  approveCreative,
  approveShot,
  setOfficialShotVersion,
  approveStoryboard,
  approveVideoPrompts,
  createProject,
  generateCreative,
  generateStoryboard,
  generateVideoPrompts,
  getAssembly,
  getAssemblyVideoUrl,
  getCapabilities,
  getCreativeContent,
  getHealth,
  getExport,
  getExportVideoUrl,
  getMusic,
  getMusicAudioUrl,
  getMultiShotGenerationOptions,
  getProject,
  getProjectTasks,
  getProjects,
  getProjectWorkflow,
  getReferenceAssets,
  getReferenceImageUrl,
  getShot,
  getShotGenerationOptions,
  getShotGenerationStatus,
  getShots,
  getShotVideoUrl,
  getStoryboardContent,
  getSubtitle,
  getTask,
  getVideoPrompts,
  getVoice,
  getVoiceAudioUrl,
  generateShotWithPromptVersion,
  regenerateCreative,
  regenerateShotGeneration,
  regenerateStoryboard,
  regenerateVideoPrompts,
  preflightShotGeneration,
  resumeShotGeneration,
  startShotGeneration,
  startMultiShotGeneration,
  retryCreative,
  reviseCreative,
  reviseStoryboard,
  reviseVideoPrompts,
  uploadReferenceAsset,
} from "./client";
import { TASK_OPERATIONS } from "./types";

function responseOf(
  payload: unknown,
  status = 200,
  headers: Record<string, string> = {},
): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers(headers),
    json: vi.fn().mockResolvedValue(payload),
  } as unknown as Response;
}

const projectListPayload = {
  projects: [
    {
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
    },
  ],
};

const createRequest = {
  product_name: "测试柠檬",
  product_description: "新鲜柠檬饮料",
  user_notes: "不要出现人物",
  duration_seconds: 18,
  video_style: "清爽、年轻",
  video_purpose: "提升产品知名度",
};

const createResponse = {
  project_id: "0123456789abcdef0123456789abcdef",
  name: "测试柠檬",
  workflow_phase: "CREATIVE",
  status: "NOT_STARTED",
  created_at: "2026-08-18T10:00:00+08:00",
  updated_at: "2026-08-18T10:00:00+08:00",
};

const taskPayload = {
  task_id: "task_0123456789abcdef0123456789abcdef",
  project_id: "project-1",
  operation: "CREATIVE_GENERATE",
  target_id: null,
  status: "QUEUED",
  created_at: "2026-08-18T12:00:00Z",
  started_at: null,
  finished_at: null,
  correlation_id: "req_task",
  error: null,
  result: null,
};

const workflowStagesPayload = {
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
};

const projectWorkflowPayload = {
  project_id: "LEE柠檬",
  workflow_phase: "COMPLETED",
  status: "COMPLETED",
  stages: workflowStagesPayload,
  available_actions: [],
  updated_at: "2026-08-18T14:30:00+08:00",
};

const projectDetailPayload = {
  project_id: "LEE柠檬",
  name: "LEE柠檬清爽饮品",
  request: {
    product_name: "LEE柠檬",
    product_description: "新鲜柠檬饮料",
    user_notes: "不要出现人物",
    duration_seconds: 18,
    video_style: "清爽、年轻",
    video_purpose: "提升产品知名度",
  },
  workflow: {
    workflow_phase: "COMPLETED",
    status: "COMPLETED",
    stages: workflowStagesPayload,
    available_actions: [],
  },
  assembly: workflowStagesPayload.assembly,
  post_production: {
    status: "RUNNING",
    voice: workflowStagesPayload.voice,
    subtitle: workflowStagesPayload.subtitle,
    music: workflowStagesPayload.music,
  },
  final_export: workflowStagesPayload.export,
  updated_at: "2026-08-18T14:30:00+08:00",
};

const creativeContentPayload = {
  project_id: "LEE柠檬",
  status: "APPROVED",
  content: {
    creative_concept: "明亮柠檬世界",
    target_audience: "年轻消费者",
    key_message: "新鲜看得见",
    visual_direction: "黄色插画",
    narrative_arc: "品牌收束",
    narration_plan: {
      enabled: true,
      tone: "年轻活泼",
      full_script: "新鲜看得见，酸甜刚刚好。",
      target_duration_seconds: 12,
    },
    subtitle_strategy: {
      enabled: true,
      tone: "简洁明快",
      density: "low",
      max_lines: 1,
      preferred_position: "bottom_center",
      principles: ["不遮挡产品"],
    },
    global_constraints: { must: [], must_not: ["people"] },
    av_timeline_constraints: {
      forbidden_windows: [{ start: 0, end: 3, tracks: ["voiceover"] }],
    },
  },
};

const storyboardContentPayload = {
  project_id: "LEE柠檬",
  status: "APPROVED",
  content: {
    total_duration_seconds: 6,
    shots: [
      {
        shot_id: 1,
        duration_seconds: 6,
        purpose: "开场",
        visual: "柠檬轮廓",
        camera: "平稳推近",
        voiceover_cues: [
          { text: "新鲜", start_offset: 1, end_offset: 2, position: null },
        ],
        subtitle_cues: [
          {
            text: "LEE柠檬",
            start_offset: 2,
            end_offset: 4,
            position: "bottom_center",
          },
        ],
        video_constraints: {
          reserve_subtitle_space: true,
          subtitle_safe_area: "bottom_center",
        },
      },
    ],
  },
};

const videoPromptsContentPayload = {
  project_id: "LEE柠檬",
  status: "APPROVED",
  content: {
    shots: [
      {
        shot_id: 1,
        prompt_version: 2,
        prompt_source: "ai_revision",
        visual_prompt_core: "bright lemon core",
        prompt_text: "approved final prompt",
      },
    ],
  },
};

describe("API client", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads health and preserves the response correlation ID", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            status: "ok",
            service: "ai-product-video-agent",
            api_version: "v1",
          },
          200,
          { "X-Correlation-ID": "req_test" },
        ),
      ),
    );
    const result = await getHealth();
    expect(result.data.api_version).toBe("v1");
    expect(result.correlationId).toBe("req_test");
  });

  it("maps the backend safe error DTO", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "PROJECT_ERROR",
              code: "PROJECT_BUSY",
              message: "项目当前正在执行其他操作，请稍后重试。",
              retryable: true,
              correlation_id: "req_busy",
            },
          },
          409,
        ),
      ),
    );
    await expect(getCapabilities()).rejects.toMatchObject({
      status: 409,
      code: "PROJECT_BUSY",
      correlationId: "req_busy",
      retryable: true,
    });
  });

  it("converts network failures into a non-sensitive error", async () => {
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockRejectedValue(new Error("D:\\private MINIMAX_API_KEY=secret")),
    );
    const error = await getHealth().catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: null });
    expect((error as Error).message).not.toContain("MINIMAX_API_KEY");
    expect((error as Error).message).not.toContain("D:\\");
  });

  it("reads and validates the projects response", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(
        responseOf(projectListPayload, 200, {
          "X-Correlation-ID": "req_projects",
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getProjects();

    expect(result.data.projects).toHaveLength(1);
    expect(result.data.projects[0]?.name).toBe("LEE柠檬");
    expect(result.correlationId).toBe("req_projects");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/projects"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("maps the backend safe error DTO for the projects request", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "PROJECT_ERROR",
              code: "PROJECT_LIST_UNAVAILABLE",
              message: "项目列表暂时不可用。",
              retryable: true,
              correlation_id: "req_projects_error",
            },
          },
          503,
        ),
      ),
    );

    await expect(getProjects()).rejects.toMatchObject({
      status: 503,
      code: "PROJECT_LIST_UNAVAILABLE",
      message: "项目列表暂时不可用。",
      correlationId: "req_projects_error",
      retryable: true,
    });
  });

  it("converts a projects network failure into a safe error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("D:\\secret\\project.json")),
    );

    const error = await getProjects().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: null });
    expect((error as Error).message).not.toContain("D:\\");
  });

  it("rejects malformed projects JSON and preserves correlation ID", async () => {
    const response = responseOf(null, 200, {
      "X-Correlation-ID": "req_bad_json",
    });
    vi.mocked(response.json).mockRejectedValue(new SyntaxError("bad JSON"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(getProjects()).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
      correlationId: "req_bad_json",
      message: "Backend 返回了无法读取的响应。",
    });
  });

  it("rejects an invalid projects DTO without exposing its fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            projects: [
              {
                project_id: "project-1",
                local_path: "D:\\private\\project.json",
              },
            ],
          },
          200,
          { "X-Correlation-ID": "req_invalid_projects" },
        ),
      ),
    );

    const error = await getProjects().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({
      code: "INVALID_RESPONSE",
      correlationId: "req_invalid_projects",
    });
    expect((error as Error).message).not.toContain("D:\\private");
  });

  it("gets and validates a project detail with an encoded Chinese ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(
        {
          ...projectDetailPayload,
          local_path: "D:\\private\\project.json",
          credential_env_name: "MINIMAX_API_KEY",
        },
        200,
        { "X-Correlation-ID": "req_detail" },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getProject("LEE柠檬");

    expect(result.data.name).toBe("LEE柠檬清爽饮品");
    expect(result.data.request.duration_seconds).toBe(18);
    expect(result.correlationId).toBe("req_detail");
    expect(result.data).not.toHaveProperty("local_path");
    expect(result.data).not.toHaveProperty("credential_env_name");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/projects/LEE%E6%9F%A0%E6%AA%AC"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("gets and validates project workflow with an encoded Chinese ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(
        {
          ...projectWorkflowPayload,
          candidate_state: "CANDIDATE_APPROVE",
          raw_error: "hidden",
        },
        200,
        { "X-Correlation-ID": "req_workflow" },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getProjectWorkflow("LEE柠檬");

    expect(result.data.workflow_phase).toBe("COMPLETED");
    expect(result.data.stages.shots).toEqual({
      status: "COMPLETED",
      approved: 3,
      total: 3,
    });
    expect(result.correlationId).toBe("req_workflow");
    expect(result.data).not.toHaveProperty("candidate_state");
    expect(result.data).not.toHaveProperty("raw_error");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/projects/LEE%E6%9F%A0%E6%AA%AC/workflow",
      ),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it.each([
    [404, "PROJECT_NOT_FOUND"],
    [422, "PROJECT_DATA_CORRUPT"],
  ])("maps project detail HTTP %i with code %s", async (status, code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "PROJECT_ERROR",
              code,
              message: "安全项目错误",
              retryable: false,
              correlation_id: `req_detail_${status}`,
            },
          },
          status,
        ),
      ),
    );

    await expect(getProject("project-1")).rejects.toMatchObject({
      status,
      code,
      correlationId: `req_detail_${status}`,
    });
  });

  it.each([
    [404, "PROJECT_NOT_FOUND"],
    [422, "PROJECT_DATA_UNSUPPORTED"],
  ])("maps project workflow HTTP %i with code %s", async (status, code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "PROJECT_ERROR",
              code,
              message: "安全工作流错误",
              retryable: false,
              correlation_id: `req_workflow_${status}`,
            },
          },
          status,
        ),
      ),
    );

    await expect(getProjectWorkflow("project-1")).rejects.toMatchObject({
      status,
      code,
      correlationId: `req_workflow_${status}`,
    });
  });

  it.each([
    ["project detail", () => getProject("project-1")],
    ["project workflow", () => getProjectWorkflow("project-1")],
  ])("converts %s network failure into a safe error", async (_name, call) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("D:\\private API_KEY=hidden")),
    );

    const error = await call().catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: null });
    expect((error as Error).message).not.toContain("D:\\");
    expect((error as Error).message).not.toContain("API_KEY");
  });

  it.each([
    ["project detail", () => getProject("project-1")],
    ["project workflow", () => getProjectWorkflow("project-1")],
  ])("rejects malformed %s JSON with correlation ID", async (_name, call) => {
    const response = responseOf(null, 200, {
      "X-Correlation-ID": "req_malformed_workspace",
    });
    vi.mocked(response.json).mockRejectedValue(new SyntaxError("bad JSON"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(call()).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
      correlationId: "req_malformed_workspace",
    });
  });

  it("gets and validates Creative content with an encoded project ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(
        {
          ...creativeContentPayload,
          provider_task_id: "hidden",
          content: {
            ...creativeContentPayload.content,
            debug_path: "D:\\private\\raw.json",
          },
        },
        200,
        { "X-Correlation-ID": "req_creative" },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await getCreativeContent("LEE柠檬");
    expect(result.data.content?.creative_concept).toBe("明亮柠檬世界");
    expect(result.data.content?.narration_plan.full_script).toContain("酸甜");
    expect(result.data).not.toHaveProperty("provider_task_id");
    expect(result.data.content).not.toHaveProperty("debug_path");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/creative",
      ),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("gets and validates Storyboard shots, cues, and constraints", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(responseOf(storyboardContentPayload)),
    );
    const result = await getStoryboardContent("LEE柠檬");
    const shot = result.data.content?.shots[0];
    expect(shot?.duration_seconds).toBe(6);
    expect(shot?.voiceover_cues[0]?.start_offset).toBe(1);
    expect(shot?.subtitle_cues[0]?.position).toBe("bottom_center");
    expect(shot?.video_constraints.reserve_subtitle_space).toBe(true);
  });

  it("gets and validates official Video Prompt versions and text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          ...videoPromptsContentPayload,
          content: {
            shots: [
              {
                ...videoPromptsContentPayload.content.shots[0],
                provider_task_id: "must-not-escape",
                file_id: "hidden-file",
                candidate_state: "hidden",
              },
            ],
          },
        }),
      ),
    );
    const result = await getVideoPrompts("LEE柠檬");
    const shot = result.data.content?.shots[0];
    expect(shot).toEqual(videoPromptsContentPayload.content.shots[0]);
    expect(shot).not.toHaveProperty("provider_task_id");
    expect(shot).not.toHaveProperty("file_id");
    expect(shot).not.toHaveProperty("candidate_state");
  });

  it.each([
    ["Creative", getCreativeContent, "/planning/creative"],
    ["Storyboard", getStoryboardContent, "/planning/storyboard"],
    ["Video Prompt", getVideoPrompts, "/planning/video-prompts"],
  ])("accepts a null %s content response", async (_name, call, path) => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf({ project_id: "project-1", status: "NOT_STARTED", content: null }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const result = await call("project-1");
    expect(result.data.content).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(path),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("rejects malformed Planning DTOs without exposing raw fields", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          project_id: "LEE柠檬",
          status: "APPROVED",
          content: {
            shots: [{ local_path: "D:\\private", provider_secret: "hidden" }],
          },
        }),
      ),
    );
    const error = await getStoryboardContent("LEE柠檬").catch(
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "INVALID_RESPONSE" });
    expect((error as Error).message).not.toContain("D:\\private");
    expect((error as Error).message).not.toContain("provider_secret");
  });

  it("hides a path or credential marker inside projected Planning text", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          ...creativeContentPayload,
          content: {
            ...creativeContentPayload.content,
            visual_direction: "file://D:/private/raw.txt",
            narration_plan: {
              ...creativeContentPayload.content.narration_plan,
              full_script: "API_KEY=hidden",
            },
          },
        }),
      ),
    );
    const result = await getCreativeContent("LEE柠檬");
    expect(result.data.content?.visual_direction).toBe("[敏感内容已隐藏]");
    expect(result.data.content?.narration_plan.full_script).toBe(
      "[敏感内容已隐藏]",
    );
  });

  it("gets and safely projects the Shot list", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf({
        project_id: "LEE柠檬",
        status: "COMPLETED",
        aggregation: {
          total: 1,
          approved: 0,
          waiting_review: 1,
          generating: 0,
          not_started: 0,
          failed: 0,
        },
        shots: [
          {
            shot_id: "shot_01",
            order: 1,
            title: "产品清爽亮相",
            status: "WAITING_REVIEW",
            prompt_status: "READY",
            video_status: "READY",
            review_status: "WAITING_REVIEW",
            official_version: 2,
            pending_review_version: 3,
            version_count: 3,
            generation_count: 3,
            local_path: "D:\\private\\shot.json",
            provider_task_id: "hidden",
            candidate_state: "hidden",
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getShots("LEE柠檬");

    expect(result.data.shots[0]).toEqual({
      shot_id: "shot_01",
      order: 1,
      title: "产品清爽亮相",
      status: "WAITING_REVIEW",
      prompt_status: "READY",
      video_status: "READY",
      review_status: "WAITING_REVIEW",
      official_version: 2,
      pending_review_version: 3,
      version_count: 3,
      generation_count: 3,
    });
    expect(result.data.aggregation).toEqual({
      total: 1,
      approved: 0,
      waiting_review: 1,
      generating: 0,
      not_started: 0,
      failed: 0,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/projects/LEE%E6%9F%A0%E6%AA%AC/shots"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("parses Backend-owned multi-Shot options and ordering", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf({
        project_id: "LEE柠檬",
        status: "READY",
        max_parallel: 2,
        aggregation: {
          total: 2,
          queued: 0,
          running: 0,
          waiting_review: 0,
          approved: 1,
          failed: 0,
          not_started: 1,
        },
        shots: [
          {
            shot_id: "shot_01",
            order: 1,
            title: "First",
            status: "APPROVED",
            prompt_ready: true,
            video_status: "READY",
            available: false,
          },
          {
            shot_id: "shot_02",
            order: 2,
            title: "Second",
            status: "READY",
            prompt_ready: true,
            video_status: "NOT_STARTED",
            available: true,
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getMultiShotGenerationOptions("LEE柠檬");

    expect(result.data.max_parallel).toBe(2);
    expect(result.data.shots.map((shot) => shot.shot_id)).toEqual([
      "shot_01",
      "shot_02",
    ]);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/shots/generation/options"),
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("submits one multi-Shot plan without inventing a project task", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(
        {
          project_id: "LEE柠檬",
          status: "IN_PROGRESS",
          max_parallel: 2,
          shots: [
            {
              shot_id: "shot_01",
              task_id: "task_0123456789abcdef0123456789abcdef",
              operation: "SHOT_GENERATE",
              status: "QUEUED",
            },
            {
              shot_id: "shot_03",
              task_id: "task_abcdef0123456789abcdef0123456789",
              operation: "SHOT_GENERATE",
              status: "QUEUED",
            },
          ],
          aggregation: {
            total: 3,
            queued: 2,
            running: 0,
            waiting_review: 0,
            approved: 0,
            failed: 0,
            not_started: 1,
          },
        },
        202,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await startMultiShotGeneration("LEE柠檬", {
      shots: ["shot_01", "shot_03"],
      confirm_paid_call: true,
    });

    expect(result.data.shots).toHaveLength(2);
    expect(result.data.shots.every((shot) => shot.operation === "SHOT_GENERATE")).toBe(true);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      shots: ["shot_01", "shot_03"],
      confirm_paid_call: true,
    });
  });

  it("gets Shot Detail with explicit roles and bound Prompt versions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          project_id: "LEE柠檬",
          shot_id: "shot_01",
          status: "APPROVED",
          official_version: 2,
          pending_review_version: 3,
          version_count: 2,
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
                final_prompt: "bound prompt four",
                task_id: "hidden",
              },
              generation: {
                model: "MiniMax-H3",
                visual_input_mode: "FIRST_FRAME",
                credential_env_name: "MINIMAX_API_KEY",
              },
              video_available: true,
            },
            {
              version: 2,
              role: "OFFICIAL",
              review_status: "APPROVED",
              created_at: null,
              prompt: {
                version: 2,
                source: "ai_revision",
                visual_prompt_core: "official core",
                final_prompt: "official final prompt",
              },
              generation: {
                model: "MiniMax-H3",
                visual_input_mode: "REFERENCE_ASSET",
              },
              video_available: true,
            },
          ],
          provider_task_id: "hidden",
          candidate_state: "hidden",
        }),
      ),
    );

    const result = await getShot("LEE柠檬", "shot_01");

    expect(result.data.versions[0]).toMatchObject({
      version: 3,
      role: "PENDING_REVIEW",
      prompt: { version: 4, final_prompt: "bound prompt four" },
    });
    expect(result.data).not.toHaveProperty("provider_task_id");
    expect(result.data.versions[0].prompt).not.toHaveProperty("task_id");
    expect(result.data.versions[0].generation).not.toHaveProperty(
      "credential_env_name",
    );
  });

  it("posts Shot approval once and parses the approved public DTO", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf({
        project_id: "LEE柠檬",
        shot_id: "shot_01",
        status: "APPROVED",
        official_version: 1,
        pending_review_version: null,
        version_count: 1,
        generation_count: 1,
        versions: [
          {
            version: 1,
            role: "OFFICIAL",
            review_status: "APPROVED",
            created_at: "2026-08-19T12:00:00+08:00",
            prompt: {
              version: 2,
              source: "ai_revision",
              visual_prompt_core: "visual core",
              final_prompt: "final prompt",
            },
            generation: {
              model: "MiniMax-Hailuo-2.3",
              visual_input_mode: "NONE",
            },
            video_available: true,
          },
        ],
        provider_task_id: "must-not-survive",
        local_path: "D:\\private\\video.mp4",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await approveShot("LEE柠檬", "shot_01");

    expect(result.data).toMatchObject({
      status: "APPROVED",
      official_version: 1,
      pending_review_version: null,
    });
    expect(result.data).not.toHaveProperty("provider_task_id");
    expect(result.data).not.toHaveProperty("local_path");
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/projects/LEE%E6%9F%A0%E6%AA%AC/shots/shot_01/approve",
      ),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("posts one historical set-official request and parses history metadata", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf({
        project_id: "LEE柠檬",
        shot_id: "shot_01",
        status: "APPROVED",
        official_version: 1,
        pending_review_version: null,
        version_count: 2,
        generation_count: 3,
        versions: [
          {
            version: 1,
            role: "OFFICIAL",
            review_status: "APPROVED",
            history_reason: null,
            created_at: null,
            prompt: { version: 1, source: "ai_generated", visual_prompt_core: null, final_prompt: "p1" },
            generation: { model: "MiniMax-Hailuo-2.3", visual_input_mode: "NONE" },
            video_available: true,
          },
          {
            version: 3,
            role: "HISTORY",
            review_status: "APPROVED",
            history_reason: "PREVIOUSLY_APPROVED",
            created_at: null,
            prompt: { version: 2, source: "ai_revision", visual_prompt_core: null, final_prompt: "p2" },
            generation: { model: "MiniMax-Hailuo-2.3", visual_input_mode: "NONE" },
            video_available: true,
          },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await setOfficialShotVersion("LEE柠檬", "shot_01", 1);

    expect(result.data.official_version).toBe(1);
    expect(result.data.versions[1].history_reason).toBe("PREVIOUSLY_APPROVED");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining(
        "/api/projects/LEE%E6%9F%A0%E6%AA%AC/shots/shot_01/versions/1/set-official",
      ),
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("rejects malformed or inconsistent Shot DTOs", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          project_id: "LEE柠檬",
          shot_id: "../shot_01",
          status: "APPROVED",
          official_version: 2,
          pending_review_version: null,
          version_count: 1,
          generation_count: 1,
          versions: [],
          local_path: "D:\\private",
        }),
      ),
    );

    await expect(getShot("LEE柠檬", "shot_01")).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  });

  it("constructs an encoded Backend video URL without downloading the MP4", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    expect(getShotVideoUrl("LEE柠檬", "shot_01", 2)).toBe(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/shots/shot_01/versions/2/video",
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(() => getShotVideoUrl("LEE柠檬", "shot_01", 0)).toThrow(
      ApiClientError,
    );
  });

  it("gets and safely projects Assembly detail", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseOf({
      project_id: "LEE柠檬", status: "COMPLETED", current_version: 2,
      needs_update: false, changed_shot_id: null,
      created_at: "2026-08-18T10:00:00+08:00", total_duration: 18.5,
      video_available: true, shots: [{ shot_id: 1, video_version: 2 }],
      final_video_path: "D:\\private\\final.mp4", ffmpeg_command: "hidden",
    })));
    const payload = (await getAssembly("LEE柠檬")).data;
    expect(payload.current_version).toBe(2);
    expect(payload.shots).toEqual([{ shot_id: 1, video_version: 2 }]);
    expect(payload).not.toHaveProperty("final_video_path");
  });

  it("gets and safely projects Voice detail and calibration", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseOf({
      project_id: "LEE柠檬", status: "COMPLETED", version: 1,
      created_at: null, script: "正式配音脚本", script_source: "compiled_storyboard",
      model: "online-tts-v2", voice: "xiaoyan", language: "zh-CN",
      audio_available: true, planned_narration_duration: 12,
      planned_first_voice_start: 2, planned_last_voice_end: 14,
      planned_voice_span: 12, actual_audio_duration: 10.5,
      voice_track_start: 2, actual_voice_end: 12.5, timing_mode: "whole_track",
      cue_level_alignment: false, script_matches_storyboard: true,
      calibration_status: "OUT_OF_TOLERANCE", provider_task_id: "hidden",
    })));
    const payload = (await getVoice("LEE柠檬")).data;
    expect(payload.calibration_status).toBe("OUT_OF_TOLERANCE");
    expect(payload.script).toBe("正式配音脚本");
    expect(payload).not.toHaveProperty("provider_task_id");
  });

  it("gets and validates structured Subtitle cues", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseOf({
      project_id: "LEE柠檬", status: "COMPLETED", version: 1,
      source: "compiled_storyboard", timing_source: "global_timeline",
      created_at: null, cue_count: 1, content_available: true,
      cues: [{ index: 1, start: "00:00:02,000", end: "00:00:04,500", text: "新鲜看得见" }],
      subtitle_path: "D:\\private\\subtitle.srt",
    })));
    const payload = (await getSubtitle("LEE柠檬")).data;
    expect(payload.cues[0]).toMatchObject({ start: "00:00:02,000", text: "新鲜看得见" });
    expect(payload).not.toHaveProperty("subtitle_path");
  });

  it("gets Music detail with explicit Mix projection", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseOf({
      project_id: "LEE柠檬", status: "COMPLETED", version: 1,
      created_at: null, audio_available: true, format: "mp3", duration_seconds: 30,
      music_mix: { base_volume: 0.25, ducking_enabled: true, ducking_ratio: 0.4,
        duck_attack_seconds: 0.25, duck_release_seconds: 0.35,
        fade_in_seconds: 0.8, fade_out_seconds: 1.2, loop_music: false,
        ducking_status: "ENABLED", raw_filtergraph: "hidden" },
    })));
    const payload = (await getMusic("LEE柠檬")).data;
    expect(payload.music_mix?.ducking_ratio).toBe(0.4);
    expect(payload.music_mix).not.toHaveProperty("raw_filtergraph");
  });

  it("gets Export detail without fingerprint or render internals", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseOf({
      project_id: "LEE柠檬", status: "COMPLETED", version: 1,
      created_at: null, stale: false, video_available: true,
      assembly_version: 2, voice_version: 1, subtitle_version: 1, music_version: 1,
      voice_timing: { timing_mode: "whole_track", voice_track_start: 2,
        actual_audio_duration: 10.5, actual_voice_end: 12.5,
        calibration_status: "PASS", cue_level_alignment: false },
      music_mix: null, input_fingerprint_sha256: "hidden", render_config: { codec: "hidden" },
    })));
    const payload = (await getExport("LEE柠檬")).data;
    expect(payload.assembly_version).toBe(2);
    expect(payload.voice_timing?.calibration_status).toBe("PASS");
    expect(payload).not.toHaveProperty("input_fingerprint_sha256");
  });

  it("constructs all encoded media URLs without fetch", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    expect(getAssemblyVideoUrl("LEE柠檬")).toContain("/LEE%E6%9F%A0%E6%AA%AC/assembly/video");
    expect(getVoiceAudioUrl("LEE柠檬")).toContain("/post-production/voice/audio");
    expect(getMusicAudioUrl("LEE柠檬")).toContain("/post-production/music/audio");
    expect(getExportVideoUrl("LEE柠檬")).toContain("/LEE%E6%9F%A0%E6%AA%AC/export/video");
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("rejects malformed post-production DTOs", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseOf({
      project_id: "LEE柠檬", status: "COMPLETED", version: 1,
      created_at: null, stale: "false", video_available: true,
      assembly_version: 1, voice_version: null, subtitle_version: null,
      music_version: null, voice_timing: null, music_mix: null,
    })));
    await expect(getExport("LEE柠檬")).rejects.toMatchObject({ code: "INVALID_RESPONSE" });
  });

  it("creates a project with POST JSON and preserves correlation ID", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(createResponse, 201, {
        "X-Correlation-ID": "req_create",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await createProject(createRequest);

    expect(result.data).toEqual(createResponse);
    expect(result.correlationId).toBe("req_create");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toContain("/api/projects");
    expect(init.method).toBe("POST");
    expect(init.headers).toMatchObject({
      Accept: "application/json",
      "Content-Type": "application/json",
    });
    expect(JSON.parse(String(init.body))).toEqual(createRequest);
  });

  it.each([
    [422, "INVALID_VIDEO_DURATION"],
    [409, "PROJECT_BUSY"],
    [500, "PROJECT_CREATE_FAILED"],
  ])("maps create error HTTP %i with code %s", async (status, code) => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: status === 422 ? "VALIDATION_ERROR" : "PROJECT_ERROR",
              code,
              message: "安全错误消息",
              retryable: status === 409,
              correlation_id: `req_${status}`,
            },
          },
          status,
        ),
      ),
    );

    await expect(createProject(createRequest)).rejects.toMatchObject({
      status,
      code,
      message: "安全错误消息",
      correlationId: `req_${status}`,
      retryable: status === 409,
    });
  });

  it("converts create network failure into a safe error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("D:\\private API_KEY=hidden")),
    );

    const error = await createProject(createRequest).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(ApiClientError);
    expect(error).toMatchObject({ code: "NETWORK_ERROR", status: null });
    expect((error as Error).message).not.toContain("D:\\");
    expect((error as Error).message).not.toContain("API_KEY");
  });

  it("rejects malformed create JSON and keeps the correlation ID", async () => {
    const response = responseOf(null, 201, {
      "X-Correlation-ID": "req_bad_create_json",
    });
    vi.mocked(response.json).mockRejectedValue(new SyntaxError("bad JSON"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(createProject(createRequest)).rejects.toMatchObject({
      status: 201,
      code: "INVALID_RESPONSE",
      correlationId: "req_bad_create_json",
    });
  });

  it("sanitizes create response extensions", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            ...createResponse,
            local_path: "D:\\private\\project.json",
            provider_secret: "hidden",
          },
          201,
        ),
      ),
    );

    const result = await createProject(createRequest);

    expect(result.data).toEqual(createResponse);
    expect(result.data).not.toHaveProperty("local_path");
    expect(result.data).not.toHaveProperty("provider_secret");
  });

  it("gets a durable task and preserves response correlation ID", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(taskPayload, 200, { "X-Correlation-ID": "req_task_get" }),
      ),
    );

    const result = await getTask(taskPayload.task_id);

    expect(result.data).toEqual(taskPayload);
    expect(result.correlationId).toBe("req_task_get");
  });

  it("submits Creative generation with POST and accepts a 202 task", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "CREATIVE_GENERATE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(queuedTask, 202, { "X-Correlation-ID": "req_generate" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await generateCreative("LEE柠檬");

    expect(result.data).toEqual(queuedTask);
    expect(result.correlationId).toBe("req_generate");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/creative/generate",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("submits failed Creative Retry with its explicit operation", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "CREATIVE_RETRY",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(queuedTask, 202, { "X-Correlation-ID": "req_retry" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await retryCreative("LEE柠檬");

    expect(result.data.operation).toBe("CREATIVE_RETRY");
    expect(result.correlationId).toBe("req_retry");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/creative/retry",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("submits Storyboard generation with POST and accepts a 202 task", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "STORYBOARD_GENERATE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(queuedTask, 202, { "X-Correlation-ID": "req_storyboard" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await generateStoryboard("LEE柠檬");

    expect(result.data.operation).toBe("STORYBOARD_GENERATE");
    expect(result.correlationId).toBe("req_storyboard");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/storyboard/generate",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.not.objectContaining({ body: expect.anything() }),
    );
  });

  it("submits Storyboard feedback in JSON and never places it in the URL", async () => {
    const feedback = "保留3个镜头，第二镜头减少旁白";
    const queuedTask = {
      ...taskPayload,
      operation: "STORYBOARD_REVISE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(queuedTask, 202));
    vi.stubGlobal("fetch", fetchMock);

    const result = await reviseStoryboard("LEE柠檬", feedback);

    expect(result.data.operation).toBe("STORYBOARD_REVISE");
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/storyboard/revise",
    );
    expect(url).not.toContain("%E4%BF%9D%E7%95%99");
    expect(options).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ feedback }),
      }),
    );
  });

  it("submits Storyboard regeneration without a request body", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "STORYBOARD_REGENERATE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(queuedTask, 202));
    vi.stubGlobal("fetch", fetchMock);

    const result = await regenerateStoryboard("LEE柠檬");

    expect(result.data.operation).toBe("STORYBOARD_REGENERATE");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/storyboard/regenerate",
      expect.not.objectContaining({ body: expect.anything() }),
    );
  });

  it("submits Video Prompt generation to the canonical plural endpoint", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "VIDEO_PROMPT_GENERATE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(queuedTask, 202));
    vi.stubGlobal("fetch", fetchMock);

    const result = await generateVideoPrompts("LEE柠檬");

    expect(result.data.operation).toBe("VIDEO_PROMPT_GENERATE");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/video-prompts/generate",
      expect.not.objectContaining({ body: expect.anything() }),
    );
  });

  it("submits Video Prompt feedback in JSON and never in the URL", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "VIDEO_PROMPT_REVISE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(queuedTask, 202));
    vi.stubGlobal("fetch", fetchMock);
    const feedback = "减少镜头运动，保持无人物";

    const result = await reviseVideoPrompts("LEE柠檬", feedback);

    expect(result.data.operation).toBe("VIDEO_PROMPT_REVISE");
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/video-prompts/revise",
    );
    expect(url).not.toContain("%E5%87%8F%E5%B0%91");
    expect(options).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ feedback }),
      }),
    );
  });

  it("submits Video Prompt regeneration without a request body", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "VIDEO_PROMPT_REGENERATE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(queuedTask, 202));
    vi.stubGlobal("fetch", fetchMock);

    const result = await regenerateVideoPrompts("LEE柠檬");

    expect(result.data.operation).toBe("VIDEO_PROMPT_REGENERATE");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/video-prompts/regenerate",
      expect.not.objectContaining({ body: expect.anything() }),
    );
  });

  it("submits trimmed Creative feedback in a JSON body, never the URL", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "CREATIVE_REVISE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(queuedTask, 202));
    vi.stubGlobal("fetch", fetchMock);

    const result = await reviseCreative("LEE柠檬", "保留主题，不要人物");

    expect(result.data.operation).toBe("CREATIVE_REVISE");
    const [url, options] = fetchMock.mock.calls[0];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/creative/revise",
    );
    expect(url).not.toContain("%E4%BF%9D%E7%95%99");
    expect(options).toEqual(
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ feedback: "保留主题，不要人物" }),
      }),
    );
  });

  it("submits Creative regeneration without a feedback body", async () => {
    const queuedTask = {
      ...taskPayload,
      operation: "CREATIVE_REGENERATE",
      status: "QUEUED",
      started_at: null,
      finished_at: null,
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(queuedTask, 202));
    vi.stubGlobal("fetch", fetchMock);

    const result = await regenerateCreative("LEE柠檬");

    expect(result.data.operation).toBe("CREATIVE_REGENERATE");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/creative/regenerate",
      expect.not.objectContaining({ body: expect.anything() }),
    );
  });

  it("approves Creative with POST and validates the returned workflow", async () => {
    const approvedWorkflow = {
      ...projectWorkflowPayload,
      workflow_phase: "STORYBOARD",
      status: "APPROVED",
      stages: {
        ...workflowStagesPayload,
        creative: { status: "APPROVED" },
        storyboard: { status: "NOT_STARTED" },
      },
      available_actions: ["GENERATE_STORYBOARD"],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(approvedWorkflow, 200, {
        "X-Correlation-ID": "req_approve",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await approveCreative("LEE柠檬");

    expect(result.data.workflow_phase).toBe("STORYBOARD");
    expect(result.data.available_actions).toEqual(["GENERATE_STORYBOARD"]);
    expect(result.correlationId).toBe("req_approve");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/creative/approve",
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("approves Storyboard synchronously without creating a task request", async () => {
    const approvedWorkflow = {
      ...projectWorkflowPayload,
      workflow_phase: "VIDEO_PROMPT",
      status: "APPROVED",
      stages: {
        ...workflowStagesPayload,
        storyboard: { status: "APPROVED" },
        video_prompt: { status: "NOT_STARTED" },
      },
      available_actions: ["GENERATE_VIDEO_PROMPTS"],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(approvedWorkflow, 200, {
        "X-Correlation-ID": "req_storyboard_approve",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await approveStoryboard("LEE柠檬");

    expect(result.data.workflow_phase).toBe("VIDEO_PROMPT");
    expect(result.data.stages.storyboard.status).toBe("APPROVED");
    expect(result.data.stages.video_prompt.status).toBe("NOT_STARTED");
    expect(result.correlationId).toBe("req_storyboard_approve");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/storyboard/approve",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.not.objectContaining({ body: expect.anything() }),
    );
  });

  it("approves Video Prompts synchronously without creating a task request", async () => {
    const approvedWorkflow = {
      ...projectWorkflowPayload,
      workflow_phase: "VIDEO_GENERATION",
      status: "APPROVED",
      stages: {
        ...workflowStagesPayload,
        video_prompt: { status: "APPROVED" },
        shots: { status: "NOT_STARTED", approved: 0, total: 3 },
      },
      available_actions: ["GENERATE_SHOTS"],
    };
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf(approvedWorkflow, 200, {
        "X-Correlation-ID": "req_video_prompt_approve",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await approveVideoPrompts("LEE柠檬");

    expect(result.data.workflow_phase).toBe("VIDEO_GENERATION");
    expect(result.data.stages.video_prompt.status).toBe("APPROVED");
    expect(result.data.stages.shots.status).toBe("NOT_STARTED");
    expect(result.data.available_actions).toEqual(["GENERATE_SHOTS"]);
    expect(result.correlationId).toBe("req_video_prompt_approve");
    expect(fetchMock).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/planning/video-prompts/approve",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.any(String),
      expect.not.objectContaining({ body: expect.anything() }),
    );
  });

  it("maps task not found without exposing backend internals", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf(
          {
            error: {
              type: "TASK_ERROR",
              code: "TASK_NOT_FOUND",
              message: "任务不存在。",
              retryable: false,
              correlation_id: "req_missing_task",
            },
          },
          404,
        ),
      ),
    );

    await expect(getTask(taskPayload.task_id)).rejects.toMatchObject({
      status: 404,
      code: "TASK_NOT_FOUND",
      correlationId: "req_missing_task",
    });
  });

  it("gets a project's durable task list", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({ project_id: "project-1", tasks: [taskPayload] }),
      ),
    );

    const result = await getProjectTasks("project-1");

    expect(result.data.project_id).toBe("project-1");
    expect(result.data.tasks).toEqual([taskPayload]);
  });

  it("maps task query network failure to the shared safe error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockRejectedValue(new Error("D:\\private API_KEY=hidden")),
    );

    await expect(getTask(taskPayload.task_id)).rejects.toMatchObject({
      code: "NETWORK_ERROR",
      status: null,
      message: "无法连接本地 Backend。",
    });
  });

  it("rejects malformed task lifecycle JSON", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          ...taskPayload,
          status: "RUNNING",
          started_at: null,
        }),
      ),
    );

    await expect(getTask(taskPayload.task_id)).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  });

  it("keeps correlation ID when task JSON is unreadable", async () => {
    const response = responseOf(null, 200, {
      "X-Correlation-ID": "req_bad_task_json",
    });
    vi.mocked(response.json).mockRejectedValue(new SyntaxError("bad JSON"));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));

    await expect(getTask(taskPayload.task_id)).rejects.toMatchObject({
      status: 200,
      code: "INVALID_RESPONSE",
      correlationId: "req_bad_task_json",
    });
  });

  it("projects task fields without absolute paths or secrets", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          ...taskPayload,
          status: "FAILED",
          finished_at: "2026-08-18T12:00:01Z",
          error: {
            code: "TASK_EXECUTION_FAILED",
            message: "D:\\private API_KEY=hidden",
            retryable: false,
          },
          local_path: "D:\\private\\task.json",
          provider_response: { authorization: "hidden" },
        }),
      ),
    );

    const payload = (await getTask(taskPayload.task_id)).data;

    expect(payload.error?.message).toBe("[敏感内容已隐藏]");
    expect(payload).not.toHaveProperty("local_path");
    expect(payload).not.toHaveProperty("provider_response");
    expect(JSON.stringify(payload)).not.toContain("D:\\");
    expect(JSON.stringify(payload).toLowerCase()).not.toContain("api_key");
  });

  it("parses local Shot generation options from the Backend capability response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          project_id: "中文项目",
          eligible: true,
          shot: { shot_id: "shot_01", duration_seconds: 6, prompt_version: 2, resolution: "768P" },
          selection_modes: ["AUTO", "MANUAL"],
          visual_input_modes: [
            { mode: "none", display_name: "不使用参考图", description: "完全根据提示词生成。", compatible_model_ids: ["MiniMax-Hailuo-2.3"] },
          ],
          models: [
            {
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
            },
          ],
          issues: [],
          paid_call_required: true,
        }),
      ),
    );
    const result = await getShotGenerationOptions("中文项目", "shot_01");
    expect(result.data.shot.prompt_version).toBe(2);
    expect(result.data.models[0].supported_visual_input_modes).toEqual(["none", "first_frame"]);
    expect(fetch).toHaveBeenCalledWith(
      "http://127.0.0.1:8000/api/projects/%E4%B8%AD%E6%96%87%E9%A1%B9%E7%9B%AE/shots/shot_01/generation/options",
      expect.objectContaining({ method: "GET" }),
    );
  });

  it("parses reference assets and builds a path-free encoded preview URL", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          project_id: "中文项目",
          assets: [{ asset_id: "ref_001", filename: "产品图.png", media_type: "image/png", width: 1024, height: 1024 }],
        }),
      ),
    );
    const result = await getReferenceAssets("中文项目");
    expect(result.data.assets[0].filename).toBe("产品图.png");
    expect(getReferenceImageUrl("中文项目", "ref_001")).toBe(
      "http://127.0.0.1:8000/api/projects/%E4%B8%AD%E6%96%87%E9%A1%B9%E7%9B%AE/references/ref_001/image",
    );
  });

  it("uploads one reference with FormData and lets the browser set its boundary", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      responseOf({
        asset_id: "ref_001",
        filename: "ref_001.png",
        media_type: "image/png",
        width: 640,
        height: 480,
        deduplicated: false,
      }, 201),
    );
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["png-bytes"], "product.png", { type: "image/png" });

    const result = await uploadReferenceAsset("中文项目", file);

    expect(result.data.asset_id).toBe("ref_001");
    expect(result.data.deduplicated).toBe(false);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(
      "http://127.0.0.1:8000/api/projects/%E4%B8%AD%E6%96%87%E9%A1%B9%E7%9B%AE/references",
    );
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const uploaded = (init.body as FormData).get("file") as File;
    expect(uploaded.name).toBe(file.name);
    expect(uploaded.size).toBe(file.size);
    expect(init.headers).toEqual({ Accept: "application/json" });
    expect(JSON.stringify(init.headers).toLowerCase()).not.toContain("content-type");
  });

  it("posts only the selected preflight configuration and parses the resolved route", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        responseOf({
          ready: true,
          shot: { shot_id: "shot_01", duration_seconds: 6, prompt_version: 2, resolution: "768P" },
          resolved: {
            provider: "minimax",
            provider_display_name: "MiniMax",
            model: "MiniMax-H3",
            model_display_name: "MiniMax H3",
            api_version: "v2",
            generation_mode: "reference_generation",
            generation_mode_display_name: "主体参考生成",
            visual_input_mode: "reference_asset",
            model_selection: "MANUAL",
          },
          provider_available: true,
          selected_asset_ids: ["ref_001"],
          issues: [],
          warnings: [],
          paid_call_required: true,
          preflight_fingerprint: "a".repeat(64),
        }),
      ),
    );
    const request = {
      model_selection: "MANUAL" as const,
      requested_model: "MiniMax-H3",
      visual_input: { mode: "reference_asset" as const, asset_ids: ["ref_001"] },
    };
    const result = await preflightShotGeneration("中文项目", "shot_01", request);
    expect(result.data.resolved?.model).toBe("MiniMax-H3");
    const init = vi.mocked(fetch).mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual(request);
    expect(String(init.body)).not.toContain("path");
    expect(String(init.body).toLowerCase()).not.toContain("api_key");
  });

  it("preserves manual Prompt intent, text, and Backend-owned next versions", async () => {
    const fetchMock = vi.fn().mockResolvedValue(responseOf({
      ready: true,
      shot: {
        shot_id: "shot_01",
        duration_seconds: 6,
        prompt_version: 1,
        resolution: "768P",
        official_video_version: 3,
        pending_video_version: null,
        next_video_version: 4,
        base_video_version: 3,
        next_prompt_version: 2,
      },
      resolved: {
        provider: "minimax",
        provider_display_name: "MiniMax",
        model: "MiniMax-Hailuo-2.3",
        model_display_name: "MiniMax Hailuo 2.3",
        api_version: "v1",
        generation_mode: "text_to_video",
        generation_mode_display_name: "纯文本生成",
        visual_input_mode: "none",
        model_selection: "AUTO",
      },
      provider_available: true,
      selected_asset_ids: [],
      issues: [],
      warnings: [],
      paid_call_required: true,
      preflight_fingerprint: "9".repeat(64),
    }));
    vi.stubGlobal("fetch", fetchMock);
    const payload = {
      intent: "REGENERATE_MANUAL_PROMPT" as const,
      base_prompt_version: 1,
      edited_prompt: "edited visual core",
      model_selection: "AUTO" as const,
      requested_model: null,
      visual_input: { mode: "none" as const, asset_ids: [] },
    };
    const result = await preflightShotGeneration("中文项目", "shot_01", payload);
    expect(result.data.shot).toMatchObject({
      base_video_version: 3,
      next_prompt_version: 2,
      next_video_version: 4,
    });
    const init = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual(payload);
  });

  it("starts and resumes Shot generation with only public request fields", async () => {
    const task = {
      task_id: "task_0123456789abcdef0123456789abcdef",
      project_id: "中文项目",
      operation: "SHOT_GENERATE",
      target_id: "shot_01",
      status: "QUEUED",
      created_at: "2026-08-19T00:00:00Z",
      started_at: null,
      finished_at: null,
      correlation_id: "req_generate",
      error: null,
      result: null,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(responseOf(task, 202))
      .mockResolvedValueOnce(responseOf({ ...task, operation: "SHOT_RESUME" }, 202));
    vi.stubGlobal("fetch", fetchMock);
    const payload = {
      model_selection: "AUTO" as const,
      requested_model: null,
      visual_input: { mode: "none" as const, asset_ids: [] },
      preflight_fingerprint: "b".repeat(64),
      confirm_paid_call: true,
    };
    expect((await startShotGeneration("中文项目", "shot_01", payload)).data.operation).toBe("SHOT_GENERATE");
    expect((await resumeShotGeneration("中文项目", "shot_01")).data.operation).toBe("SHOT_RESUME");
    const startInit = fetchMock.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(startInit.body))).toEqual(payload);
    expect(String(startInit.body)).not.toMatch(/provider_task_id|file_id|path|api_key/i);
    expect(fetchMock.mock.calls[1][1]).toEqual(expect.objectContaining({ method: "POST" }));
  });

  it("parses every canonical Task operation and preserves compatible target IDs", async () => {
    const tasks = TASK_OPERATIONS.map((operation, index) => ({
      task_id: `task_${index.toString(16).padStart(32, "0")}`,
      project_id: "中文项目",
      operation,
      target_id: index % 2 === 0 ? "shot_01" : null,
      status: "QUEUED",
      created_at: "2026-08-19T00:00:00Z",
      started_at: null,
      finished_at: null,
      correlation_id: `req_${index}`,
      error: null,
      result: null,
    }));
    const fetchMock = vi.fn();
    for (const task of tasks) fetchMock.mockResolvedValueOnce(responseOf(task));
    vi.stubGlobal("fetch", fetchMock);

    for (const task of tasks) {
      const parsed = await getTask(task.task_id);
      expect(parsed.data.operation).toBe(task.operation);
      expect(parsed.data.target_id).toBe(task.target_id);
    }
  });

  it("maps a legacy missing target_id to null and still rejects an unknown operation", async () => {
    const legacy = {
      task_id: "task_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      project_id: "中文项目",
      operation: "CREATIVE_GENERATE",
      status: "QUEUED",
      created_at: "2026-08-19T00:00:00Z",
      started_at: null,
      finished_at: null,
      correlation_id: "req_legacy",
      error: null,
      result: null,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(responseOf(legacy))
      .mockResolvedValueOnce(responseOf({ ...legacy, operation: "UNKNOWN_TASK" }));
    vi.stubGlobal("fetch", fetchMock);

    expect((await getTask(legacy.task_id)).data.target_id).toBeNull();
    await expect(getTask(legacy.task_id)).rejects.toMatchObject({
      code: "INVALID_RESPONSE",
    });
  });

  it("attaches a SHOT_REGENERATE task from a valid 202 response", async () => {
    const task = {
      task_id: "task_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      project_id: "中文项目",
      operation: "SHOT_REGENERATE",
      target_id: "shot_01",
      status: "QUEUED",
      created_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
      correlation_id: "req_regenerate",
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(
      task,
      202,
      {
        Location: `/api/tasks/${task.task_id}`,
        "X-Correlation-ID": task.correlation_id,
      },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const result = await regenerateShotGeneration("中文项目", "shot_01", {
      intent: "REGENERATE_CURRENT_PROMPT",
      model_selection: "AUTO",
      requested_model: null,
      visual_input: { mode: "none", asset_ids: [] },
      preflight_fingerprint: "c".repeat(64),
      confirm_paid_call: true,
    });

    expect(result.data).toMatchObject({ operation: "SHOT_REGENERATE", target_id: "shot_01" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("submits the exact adopted Prompt Version through its dedicated paid endpoint", async () => {
    const task = {
      task_id: "task_abababababababababababababababab",
      project_id: "中文项目",
      operation: "SHOT_PROMPT_VERSION_GENERATE",
      target_id: "shot_01",
      status: "QUEUED",
      created_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
      correlation_id: "req_selected_prompt",
      error: null,
      result: null,
    };
    const fetchMock = vi.fn().mockResolvedValue(responseOf(
      task,
      202,
      { Location: `/api/tasks/${task.task_id}` },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const payload = {
      intent: "GENERATE_WITH_PROMPT_VERSION" as const,
      model_selection: "AUTO" as const,
      requested_model: null,
      visual_input: { mode: "none" as const, asset_ids: [] },
      target_prompt_version: 3,
      preflight_fingerprint: "7".repeat(64),
      confirm_paid_call: true,
    };

    const result = await generateShotWithPromptVersion("中文项目", "shot_01", payload);

    expect(result.data).toMatchObject({
      operation: "SHOT_PROMPT_VERSION_GENERATE",
      target_id: "shot_01",
    });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toContain("/generation/prompt-version");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual(payload);
    expect(String(init.body)).not.toMatch(/provider_task_id|file_id|path|api_key|secret/i);
  });

  it("reconciles an unreadable paid 202 body through Location without another POST", async () => {
    const task = {
      task_id: "task_cccccccccccccccccccccccccccccccc",
      project_id: "中文项目",
      operation: "SHOT_REGENERATE",
      target_id: "shot_01",
      status: "QUEUED",
      created_at: new Date().toISOString(),
      started_at: null,
      finished_at: null,
      correlation_id: "req_location",
      error: null,
      result: null,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(responseOf(
        { ...task, operation: "UNREADABLE_NEW_OPERATION" },
        202,
        { Location: `/api/tasks/${task.task_id}`, "X-Correlation-ID": task.correlation_id },
      ))
      .mockResolvedValueOnce(responseOf(task));
    vi.stubGlobal("fetch", fetchMock);

    const result = await regenerateShotGeneration("中文项目", "shot_01", {
      intent: "REGENERATE_CURRENT_PROMPT",
      model_selection: "AUTO",
      requested_model: null,
      visual_input: { mode: "none", asset_ids: [] },
      preflight_fingerprint: "d".repeat(64),
      confirm_paid_call: true,
    });

    expect(result.data.task_id).toBe(task.task_id);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(fetchMock.mock.calls[1][0]).toBe(`http://127.0.0.1:8000/api/tasks/${task.task_id}`);
  });

  it("uses the project task list when Location GET fails and never repeats the paid POST", async () => {
    const task = {
      task_id: "task_dddddddddddddddddddddddddddddddd",
      project_id: "中文项目",
      operation: "SHOT_GENERATE",
      target_id: "shot_01",
      status: "RUNNING",
      created_at: new Date().toISOString(),
      started_at: new Date().toISOString(),
      finished_at: null,
      correlation_id: "req_project_fallback",
      error: null,
      result: null,
    };
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(responseOf(
        { ...task, operation: "BROKEN_OPERATION" },
        202,
        { Location: `/api/tasks/${task.task_id}`, "X-Correlation-ID": task.correlation_id },
      ))
      .mockResolvedValueOnce(responseOf({ error: {} }, 503))
      .mockResolvedValueOnce(responseOf({ project_id: "中文项目", tasks: [task] }));
    vi.stubGlobal("fetch", fetchMock);

    const result = await startShotGeneration("中文项目", "shot_01", {
      model_selection: "AUTO",
      requested_model: null,
      visual_input: { mode: "none", asset_ids: [] },
      preflight_fingerprint: "e".repeat(64),
      confirm_paid_call: true,
    });

    expect(result.data.task_id).toBe(task.task_id);
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it("marks an accepted paid request as status-unreadable when both GET paths fail", async () => {
    const taskId = "task_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
    const fetchMock = vi.fn()
      .mockResolvedValueOnce(responseOf(
        { operation: "BROKEN" },
        202,
        { Location: `/api/tasks/${taskId}`, "X-Correlation-ID": "req_uncertain" },
      ))
      .mockResolvedValueOnce(responseOf({ error: {} }, 503))
      .mockResolvedValueOnce(responseOf({ error: {} }, 503));
    vi.stubGlobal("fetch", fetchMock);

    await expect(startShotGeneration("中文项目", "shot_01", {
      model_selection: "AUTO",
      requested_model: null,
      visual_input: { mode: "none", asset_ids: [] },
      preflight_fingerprint: "f".repeat(64),
      confirm_paid_call: true,
    })).rejects.toMatchObject({
      code: "ACCEPTED_TASK_STATUS_UNREADABLE",
      status: 202,
      requestAccepted: true,
      correlationId: "req_uncertain",
    });
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(1);
  });

  it("parses safe generation status without provider identifiers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseOf({
      project_id: "中文项目",
      shot_id: "shot_01",
      state: "READY_TO_DOWNLOAD",
      resume_available: true,
      resume_kind: "DOWNLOAD_EXISTING_FILE",
      video_version: 1,
      provider_submission_known: true,
    })));
    const result = await getShotGenerationStatus("中文项目", "shot_01");
    expect(result.data.state).toBe("READY_TO_DOWNLOAD");
    expect(JSON.stringify(result.data)).not.toMatch(/provider_task_id|file_id|path|credential/i);
  });

  it("parses an APPROVED generation status as non-resumable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(responseOf({
      project_id: "中文项目",
      shot_id: "shot_01",
      state: "APPROVED",
      resume_available: false,
      resume_kind: null,
      video_version: 1,
      provider_submission_known: true,
    })));
    const result = await getShotGenerationStatus("中文项目", "shot_01");
    expect(result.data.state).toBe("APPROVED");
    expect(result.data.resume_available).toBe(false);
  });
});
