import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  acceptVoiceTiming,
  ApiClientError,
  generateVoice,
  getProjectTasks,
  getTask,
  getVoice,
  getVoiceHistory,
  getVoiceOptions,
  preflightVoice,
  regenerateVoice,
} from "../api/client";
import type {
  TaskRecord,
  VoiceDetail,
  VoiceHistoryResponse,
  VoiceOptionsResponse,
  VoicePreflightResponse,
} from "../api/types";
import { VoiceGenerationAction } from "./VoiceGenerationAction";


vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    acceptVoiceTiming: vi.fn(),
    generateVoice: vi.fn(),
    getProjectTasks: vi.fn(),
    getTask: vi.fn(),
    getVoice: vi.fn(),
    getVoiceHistory: vi.fn(),
    getVoiceOptions: vi.fn(),
    preflightVoice: vi.fn(),
    regenerateVoice: vi.fn(),
  };
});

const mockAcceptVoiceTiming = vi.mocked(acceptVoiceTiming);
const mockGenerateVoice = vi.mocked(generateVoice);
const mockGetProjectTasks = vi.mocked(getProjectTasks);
const mockGetTask = vi.mocked(getTask);
const mockGetVoice = vi.mocked(getVoice);
const mockGetVoiceHistory = vi.mocked(getVoiceHistory);
const mockGetVoiceOptions = vi.mocked(getVoiceOptions);
const mockPreflightVoice = vi.mocked(preflightVoice);
const mockRegenerateVoice = vi.mocked(regenerateVoice);

const projectId = "LEE柠檬";

const detail: VoiceDetail = {
  project_id: projectId,
  status: "COMPLETED",
  version: 1,
  created_at: "2026-08-24T10:00:00+08:00",
  script: "每一颗柠檬，都带着阳光。",
  script_source: "compiled_storyboard",
  provider: "xfyun_tts",
  model: "online-tts-v2",
  voice: "xiaoyan",
  language: "zh-CN",
  audio_available: true,
  planned_narration_duration: 8,
  planned_first_voice_start: 2,
  planned_last_voice_end: 10,
  planned_voice_span: 8,
  actual_audio_duration: 10,
  voice_track_start: 2,
  actual_voice_end: 12,
  total_video_duration: 18,
  duration_difference_seconds: 2,
  duration_difference_ratio: 0.25,
  timing_mode: "whole_track",
  cue_level_alignment: false,
  script_matches_storyboard: true,
  calibration_status: "OUT_OF_TOLERANCE",
  timing_acceptance: null,
};

const emptyDetail: VoiceDetail = {
  ...detail,
  status: "NOT_STARTED",
  version: null,
  created_at: null,
  script: null,
  script_source: null,
  provider: null,
  model: null,
  voice: null,
  language: null,
  audio_available: false,
  planned_narration_duration: null,
  planned_first_voice_start: null,
  planned_last_voice_end: null,
  planned_voice_span: null,
  actual_audio_duration: null,
  voice_track_start: null,
  actual_voice_end: null,
  total_video_duration: null,
  duration_difference_seconds: null,
  duration_difference_ratio: null,
  timing_mode: null,
  cue_level_alignment: null,
  script_matches_storyboard: null,
  calibration_status: "NOT_APPLICABLE",
};

const provider = {
  provider_id: "xfyun_tts",
  display_name: "讯飞 TTS",
  model: "online-tts-v2",
  default_voice: "xiaoyan",
  language: "zh-CN",
  supported_languages: ["zh-CN"],
  allowed_voices: [],
  available: true,
};

const options: VoiceOptionsResponse = {
  project_id: projectId,
  enabled: true,
  has_active_voice: true,
  active_version: 1,
  next_version: 2,
  script: {
    source: "compiled_storyboard",
    text: "每一颗柠檬，都带着阳光。",
    character_count: 14,
    cue_count: 1,
  },
  planned_timing: {
    first_start: 2,
    last_end: 10,
    span: 8,
    narration_duration: 8,
  },
  providers: [provider],
  default_provider: "xfyun_tts",
  default_voice: "xiaoyan",
  default_language: "zh-CN",
  manual_script_required: false,
};

const emptyOptions: VoiceOptionsResponse = {
  ...options,
  has_active_voice: false,
  active_version: null,
  next_version: 1,
};

const history: VoiceHistoryResponse = {
  project_id: projectId,
  active_version: 1,
  versions: [{
    version: 1,
    created_at: detail.created_at,
    provider: detail.provider,
    model: detail.model,
    voice: detail.voice,
    language: detail.language,
    script_source: detail.script_source,
    duration_seconds: detail.actual_audio_duration,
    calibration_status: detail.calibration_status,
    timing_acceptance: null,
    audio_available: true,
    is_active: true,
  }],
};

const basePreflight: VoicePreflightResponse = {
  project_id: projectId,
  ready: true,
  intent: "GENERATE",
  next_voice_version: 1,
  script: emptyOptions.script,
  provider,
  planned_timing: emptyOptions.planned_timing,
  issues: [],
  warnings: [{ code: "VOICE_EXTERNAL_COST_POSSIBLE", message: "可能产生费用。" }],
  external_call_required: true,
  external_cost_possible: true,
  preflight_fingerprint: `voice_pf_${"a".repeat(64)}`,
};

const queuedTask: TaskRecord = {
  task_id: "task_0123456789abcdef0123456789abcdef",
  project_id: projectId,
  operation: "VOICE_GENERATE",
  target_id: "voice_v001",
  status: "QUEUED",
  created_at: "2026-08-24T10:01:00Z",
  started_at: null,
  finished_at: null,
  correlation_id: "req_voice",
  error: null,
  result: null,
};

function result<T>(data: T) {
  return { data, correlationId: "req_voice" };
}

function renderAction(current: VoiceDetail = detail) {
  const onDetailChange = vi.fn();
  const rendered = render(
    <VoiceGenerationAction
      projectId={projectId}
      detail={current}
      onDetailChange={onDetailChange}
    />,
  );
  return { ...rendered, onDetailChange };
}

async function showEmptyPreparation() {
  mockGetVoiceOptions.mockResolvedValue(result(emptyOptions));
  mockGetVoiceHistory.mockResolvedValue(result({ ...history, active_version: null, versions: [] }));
  renderAction(emptyDetail);
  await screen.findByRole("button", { name: "检查配音配置" });
}

async function runEmptyPreflight() {
  await showEmptyPreparation();
  fireEvent.click(screen.getByRole("button", { name: "检查配音配置" }));
  await screen.findByRole("heading", { name: "配音配置检查通过" });
}

async function openConfirm() {
  await runEmptyPreflight();
  fireEvent.click(screen.getByRole("button", { name: "生成配音" }));
  await screen.findByRole("dialog");
}

describe("VoiceGenerationAction", () => {
  beforeEach(() => {
    mockAcceptVoiceTiming.mockReset();
    mockGenerateVoice.mockReset();
    mockGetProjectTasks.mockReset();
    mockGetTask.mockReset();
    mockGetVoice.mockReset();
    mockGetVoiceHistory.mockReset();
    mockGetVoiceOptions.mockReset();
    mockPreflightVoice.mockReset();
    mockRegenerateVoice.mockReset();
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [] }));
    mockGetVoiceOptions.mockResolvedValue(result(options));
    mockGetVoiceHistory.mockResolvedValue(result(history));
    mockGetVoice.mockResolvedValue(result(detail));
    mockPreflightVoice.mockImplementation((_project, payload) => Promise.resolve(result({
      ...basePreflight,
      intent: payload.intent,
      next_voice_version: payload.intent === "GENERATE" ? 1 : 2,
      script: { ...basePreflight.script!, text: payload.script_override ?? "" },
    })));
    mockGenerateVoice.mockResolvedValue(result(queuedTask));
    mockRegenerateVoice.mockResolvedValue(result({ ...queuedTask, target_id: "voice_v002" }));
    mockGetTask.mockResolvedValue(result(queuedTask));
    mockAcceptVoiceTiming.mockResolvedValue(result({
      ...detail,
      timing_acceptance: { accepted: true, accepted_at: "2026-08-24T10:02:00Z" },
    }));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("01 shows generation preparation when no active Voice exists", async () => {
    await showEmptyPreparation();
    expect(screen.getByRole("heading", { name: "Voice Generation" })).toBeInTheDocument();
  });

  it("02 shows the current Voice in immutable history", async () => {
    renderAction();
    expect(await screen.findByText("当前版本")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Voice v001" })).toBeInTheDocument();
  });

  it("03 renders Voice audio playback", async () => {
    const { container } = renderAction();
    await screen.findByRole("heading", { name: "Voice Version 历史" });
    expect(container.querySelector("audio")?.getAttribute("src")).toContain("/voice/versions/1/audio");
  });

  it("04 shows the script source", async () => {
    await showEmptyPreparation();
    expect(screen.getByText("Storyboard Planned")).toBeInTheDocument();
  });

  it("05 previews the compiled script", async () => {
    await showEmptyPreparation();
    expect(screen.getByLabelText("本次 Voice Script")).toHaveValue(options.script?.text);
  });

  it("06 shows only the safe Provider display name", async () => {
    await showEmptyPreparation();
    expect(screen.getByRole("option", { name: "讯飞 TTS" })).toBeInTheDocument();
  });

  it("07 shows Voice and Language values", async () => {
    await showEmptyPreparation();
    expect(screen.getByDisplayValue("xiaoyan")).toBeInTheDocument();
    expect(screen.getByDisplayValue("zh-CN")).toBeInTheDocument();
  });

  it("08 runs initial Generate preflight", async () => {
    await runEmptyPreflight();
    expect(mockPreflightVoice).toHaveBeenCalledWith(projectId, expect.objectContaining({ intent: "GENERATE" }));
  });

  it("09 runs Regenerate preflight", async () => {
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "重新生成新版本" }));
    fireEvent.click(screen.getByRole("button", { name: "检查配音配置" }));
    await waitFor(() => expect(mockPreflightVoice).toHaveBeenCalledWith(projectId, expect.objectContaining({ intent: "REGENERATE" })));
  });

  it("10 lets Regenerate edit the current script", async () => {
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "重新生成新版本" }));
    fireEvent.change(screen.getByLabelText("本次 Voice Script"), { target: { value: "编辑后的脚本" } });
    expect(screen.getByLabelText("本次 Voice Script")).toHaveValue("编辑后的脚本");
  });

  it("11 shows the next immutable version", async () => {
    await showEmptyPreparation();
    expect(screen.getByText("v001")).toBeInTheDocument();
  });

  it("12 shows the external TTS cost warning", async () => {
    await openConfirm();
    expect(screen.getByText(/可能产生费用/)).toBeInTheDocument();
  });

  it("13 cancel creates zero generation POST", async () => {
    await openConfirm();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(mockGenerateVoice).not.toHaveBeenCalled();
  });

  it("14 confirmation submits exactly one generation request", async () => {
    await openConfirm();
    fireEvent.click(screen.getByRole("button", { name: "确认并生成配音" }));
    await waitFor(() => expect(mockGenerateVoice).toHaveBeenCalledTimes(1));
  });

  it("15 double click cannot submit a second external TTS request", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof generateVoice>>) => void;
    mockGenerateVoice.mockImplementation(() => new Promise((resolve) => { resolveSubmit = resolve; }));
    await openConfirm();
    const button = screen.getByRole("button", { name: "确认并生成配音" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mockGenerateVoice).toHaveBeenCalledTimes(1);
    await act(async () => resolveSubmit(await result(queuedTask)));
  });

  it("16 polls an accepted VOICE_GENERATE task", async () => {
    await openConfirm();
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "确认并生成配音" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2100); });
    expect(mockGetTask).toHaveBeenCalledWith(queuedTask.task_id);
  });

  it("17 attaches an active Voice task after F5 without POST", async () => {
    mockGetProjectTasks.mockResolvedValue(result({
      project_id: projectId,
      tasks: [{ ...queuedTask, status: "RUNNING", started_at: "2026-08-24T10:01:01Z" }],
    }));
    renderAction(emptyDetail);
    expect(await screen.findByText("正在生成配音…")).toBeInTheDocument();
    expect(mockGenerateVoice).not.toHaveBeenCalled();
  });

  it("18 does not attach an unrelated active task", async () => {
    mockGetProjectTasks.mockResolvedValue(result({
      project_id: projectId,
      tasks: [{ ...queuedTask, operation: "ASSEMBLY_EXECUTE", target_id: "assembly_v001" }],
    }));
    await showEmptyPreparation();
    expect(screen.queryByText("配音任务排队中…")).not.toBeInTheDocument();
  });

  it("19 locks paid action after accepted-but-unreadable 202", async () => {
    mockGenerateVoice.mockRejectedValue(new ApiClientError({
      message: "accepted",
      code: "ACCEPTED_TASK_STATUS_UNREADABLE",
      status: 202,
      requestAccepted: true,
    }));
    await openConfirm();
    fireEvent.click(screen.getByRole("button", { name: "确认并生成配音" }));
    expect(await screen.findByText(/生成入口已锁定/)).toBeInTheDocument();
    expect(mockGenerateVoice).toHaveBeenCalledTimes(1);
  });

  it("20 refreshes business state after a terminal task", async () => {
    const succeeded = {
      ...queuedTask,
      status: "SUCCEEDED" as const,
      finished_at: "2026-08-24T10:02:00Z",
      result: { resource_type: "VOICE", resource_id: "voice_v001", version: 1 },
    };
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [succeeded] }));
    const { onDetailChange } = renderAction(emptyDetail);
    await waitFor(() => expect(onDetailChange).toHaveBeenCalledWith(detail));
  });

  it("21 displays the active vNext returned by durable business state", async () => {
    const v2 = { ...detail, version: 2 };
    mockGetVoice.mockResolvedValue(result(v2));
    mockGetProjectTasks.mockResolvedValue(result({
      project_id: projectId,
      tasks: [{
        ...queuedTask,
        target_id: "voice_v002",
        status: "SUCCEEDED",
        finished_at: "2026-08-24T10:02:00Z",
        result: { resource_type: "VOICE", resource_id: "voice_v002", version: 2 },
      }],
    }));
    const { onDetailChange } = renderAction();
    await waitFor(() => expect(onDetailChange).toHaveBeenCalledWith(v2));
  });

  it("22 renders immutable Voice history", async () => {
    renderAction();
    expect(await screen.findByRole("heading", { name: "Voice Version 历史" })).toBeInTheDocument();
  });

  it("23 plays a historical Voice version without selecting it active", async () => {
    mockGetVoiceHistory.mockResolvedValue(result({
      ...history,
      versions: [
        history.versions[0],
        { ...history.versions[0], version: 2, is_active: false },
      ],
    }));
    const { container } = renderAction();
    await screen.findByRole("heading", { name: "Voice v002" });
    const urls = Array.from(container.querySelectorAll("audio")).map((item) => item.getAttribute("src"));
    expect(urls.some((url) => url?.includes("/versions/2/audio"))).toBe(true);
    expect(screen.queryByRole("button", { name: /切换/ })).not.toBeInTheDocument();
  });

  it("24 shows PASS calibration without an acceptance action", async () => {
    renderAction({ ...detail, calibration_status: "PASS" });
    await screen.findByRole("heading", { name: "Voice Generation" });
    expect(screen.queryByRole("button", { name: "接受当前 Timing" })).not.toBeInTheDocument();
  });

  it("25 shows OUT_OF_TOLERANCE guidance", async () => {
    renderAction();
    expect(await screen.findByText(/当前时长超出建议范围/)).toBeInTheDocument();
  });

  it("26 submits local Timing Acceptance without a task", async () => {
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "接受当前 Timing" }));
    await waitFor(() => expect(mockAcceptVoiceTiming).toHaveBeenCalledWith(projectId, 1));
    expect(mockGenerateVoice).not.toHaveBeenCalled();
  });

  it("27 prevents acceptance for OUT_OF_BOUNDS", async () => {
    renderAction({ ...detail, calibration_status: "OUT_OF_BOUNDS" });
    expect(await screen.findByText(/不能直接接受/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "接受当前 Timing" })).not.toBeInTheDocument();
  });

  it("28 displays a safe Provider failure", async () => {
    mockGetProjectTasks.mockResolvedValue(result({
      project_id: projectId,
      tasks: [{
        ...queuedTask,
        status: "FAILED",
        finished_at: "2026-08-24T10:02:00Z",
        error: { code: "VOICE_PROVIDER_FAILED", message: "外部 TTS 服务未能完成配音生成；不会自动重试。", retryable: false },
      }],
    }));
    renderAction(emptyDetail);
    expect(await screen.findByText(/外部 TTS 服务未能完成/)).toBeInTheDocument();
  });

  it("29 displays interrupted safety guidance", async () => {
    mockGetProjectTasks.mockResolvedValue(result({
      project_id: projectId,
      tasks: [{
        ...queuedTask,
        status: "INTERRUPTED",
        finished_at: "2026-08-24T10:02:00Z",
        error: { code: "TASK_INTERRUPTED", message: "任务已中断。", retryable: false },
      }],
    }));
    renderAction(emptyDetail);
    expect(await screen.findByText(/无法确认上次外部 TTS 调用结果/)).toBeInTheDocument();
  });

  it("30 never automatically retries an interrupted Provider call", async () => {
    mockGetProjectTasks.mockResolvedValue(result({
      project_id: projectId,
      tasks: [{
        ...queuedTask,
        status: "INTERRUPTED",
        finished_at: "2026-08-24T10:02:00Z",
        error: { code: "TASK_INTERRUPTED", message: "任务已中断。", retryable: false },
      }],
    }));
    renderAction(emptyDetail);
    await screen.findByText(/无法确认上次外部 TTS 调用结果/);
    expect(mockGenerateVoice).not.toHaveBeenCalled();
    expect(mockRegenerateVoice).not.toHaveBeenCalled();
  });

  it("31 never displays paths, credentials, endpoints, or raw response", async () => {
    const { container } = renderAction();
    await screen.findByRole("heading", { name: "Voice Version 历史" });
    expect(container.textContent).not.toMatch(/[A-Z]:[\\/]|credential|endpoint|raw response|api key/i);
  });
});
