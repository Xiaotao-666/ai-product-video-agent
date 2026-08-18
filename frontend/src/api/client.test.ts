import { afterEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  createProject,
  getAssembly,
  getAssemblyVideoUrl,
  getCapabilities,
  getCreativeContent,
  getHealth,
  getExport,
  getExportVideoUrl,
  getMusic,
  getMusicAudioUrl,
  getProject,
  getProjectTasks,
  getProjects,
  getProjectWorkflow,
  getShot,
  getShots,
  getShotVideoUrl,
  getStoryboardContent,
  getSubtitle,
  getTask,
  getVideoPrompts,
  getVoice,
  getVoiceAudioUrl,
} from "./client";

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
        shots: [
          {
            shot_id: "shot_01",
            status: "APPROVED",
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
      status: "APPROVED",
      official_version: 2,
      pending_review_version: 3,
      version_count: 3,
      generation_count: 3,
    });
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/api/projects/LEE%E6%9F%A0%E6%AA%AC/shots"),
      expect.objectContaining({ method: "GET" }),
    );
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
});
