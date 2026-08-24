import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  executeFinalExport,
  getExport,
  getExportHistory,
  getProjectTasks,
  getProjectWorkflow,
  getTask,
  preflightFinalExport,
} from "../api/client";
import type {
  ExportDetail,
  ExportHistoryResponse,
  FinalExportPreflightResponse,
  TaskRecord,
} from "../api/types";
import { FinalExportAction } from "./FinalExportAction";


vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    executeFinalExport: vi.fn(),
    getExport: vi.fn(),
    getExportHistory: vi.fn(),
    getProjectTasks: vi.fn(),
    getProjectWorkflow: vi.fn(),
    getTask: vi.fn(),
    preflightFinalExport: vi.fn(),
  };
});

const mockExecuteFinalExport = vi.mocked(executeFinalExport);
const mockGetExport = vi.mocked(getExport);
const mockGetExportHistory = vi.mocked(getExportHistory);
const mockGetProjectTasks = vi.mocked(getProjectTasks);
const mockGetProjectWorkflow = vi.mocked(getProjectWorkflow);
const mockGetTask = vi.mocked(getTask);
const mockPreflightFinalExport = vi.mocked(preflightFinalExport);

const projectId = "SSS三明治";
const token = `exp_${"a".repeat(64)}`;

const mix = {
  base_volume: 0.2,
  ducking_enabled: true,
  ducking_ratio: 0.4,
  duck_attack_seconds: 0.25,
  duck_release_seconds: 0.35,
  fade_in_seconds: 0.8,
  fade_out_seconds: 1.2,
  loop_music: false,
  ducking_status: "ENABLED",
};

const detail: ExportDetail = {
  project_id: projectId,
  status: "STALE",
  version: 1,
  created_at: "2026-08-24T12:00:00+08:00",
  stale: true,
  stale_reasons: ["MUSIC_MIX_CHANGED"],
  video_available: true,
  assembly_version: 1,
  voice_version: 1,
  subtitle_version: 1,
  music_version: 1,
  voice_timing: null,
  music_mix: mix,
};

const preflight: FinalExportPreflightResponse = {
  project_id: projectId,
  ready: true,
  execution_required: true,
  next_export_version: 2,
  active_export_version: 1,
  inputs: {
    assembly_version: 2,
    voice_version: 3,
    subtitle_version: 4,
    music_version: 1,
  },
  voice_timing: {
    status: "PASS",
    accepted: false,
    track_start: 1.305,
    actual_audio_duration: 4.895,
    actual_end: 6.2,
  },
  subtitle: {
    semantic_type: "NARRATION_CAPTION",
    source_voice_version: 3,
    voice_aligned: true,
  },
  music_mix: mix,
  existing_export_version: null,
  stale: true,
  stale_reasons: ["MUSIC_MIX_CHANGED"],
  issues: [],
  confirmation_token: token,
};

const history: ExportHistoryResponse = {
  project_id: projectId,
  active_version: 1,
  versions: [{
    version: 1,
    created_at: detail.created_at,
    assembly_version: 1,
    voice_version: 1,
    subtitle_version: 1,
    music_version: 1,
    audio_muxed: true,
    subtitle_burned: true,
    duration_seconds: 12,
    video_available: true,
    is_active: true,
    stale: true,
    stale_reasons: ["MUSIC_MIX_CHANGED"],
  }],
};

function task(
  status: TaskRecord["status"] = "QUEUED",
  operation: TaskRecord["operation"] = "FINAL_EXPORT",
): TaskRecord {
  const terminal = ["SUCCEEDED", "FAILED", "INTERRUPTED", "CANCELLED"].includes(status);
  const failed = status === "FAILED" || status === "INTERRUPTED";
  return {
    task_id: `task_${"1".repeat(32)}`,
    project_id: projectId,
    operation,
    target_id: "export_v002",
    status,
    created_at: "2026-08-24T12:01:00Z",
    started_at: status === "QUEUED" ? null : "2026-08-24T12:01:01Z",
    finished_at: terminal ? "2026-08-24T12:01:02Z" : null,
    correlation_id: "req_export",
    error: failed ? {
      code: status === "INTERRUPTED" ? "TASK_INTERRUPTED" : "FINAL_EXPORT_FAILED",
      message: status === "INTERRUPTED" ? "任务已中断。" : "本地导出失败。",
      retryable: status === "FAILED",
    } : null,
    result: status === "SUCCEEDED" ? {
      resource_type: "FINAL_EXPORT",
      resource_id: "export_v002",
      version: 2,
    } : null,
  };
}

function result<T>(data: T) {
  return { data, correlationId: "req_export" };
}

function renderAction(current: ExportDetail = detail) {
  const onDetailChange = vi.fn();
  const rendered = render(
    <FinalExportAction
      projectId={projectId}
      detail={current}
      onDetailChange={onDetailChange}
    />,
  );
  return { ...rendered, onDetailChange };
}

async function prepared() {
  const rendered = renderAction();
  await screen.findByText("最终视频需要导出");
  return rendered;
}

async function openConfirmation() {
  await prepared();
  fireEvent.click(screen.getByRole("button", { name: "执行最终导出" }));
  await screen.findByRole("dialog");
}

describe("FinalExportAction", () => {
  beforeEach(() => {
    mockExecuteFinalExport.mockReset();
    mockGetExport.mockReset();
    mockGetExportHistory.mockReset();
    mockGetProjectTasks.mockReset();
    mockGetProjectWorkflow.mockReset();
    mockGetTask.mockReset();
    mockPreflightFinalExport.mockReset();
    mockExecuteFinalExport.mockResolvedValue(result(task()));
    mockGetExport.mockResolvedValue(result(detail));
    mockGetExportHistory.mockResolvedValue(result(history));
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [] }));
    mockGetProjectWorkflow.mockResolvedValue(result({} as never));
    mockGetTask.mockResolvedValue(result(task("SUCCEEDED")));
    mockPreflightFinalExport.mockResolvedValue(result(preflight));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("01 renders Final Export Preparation", async () => {
    renderAction();
    expect(await screen.findByLabelText("Final Export Preparation")).toBeInTheDocument();
  });

  it("02 displays Assembly Version", async () => {
    await prepared();
    expect(screen.getAllByText("v002").length).toBeGreaterThan(0);
  });

  it("03 displays Voice Version", async () => {
    await prepared();
    expect(screen.getAllByText("v003").length).toBeGreaterThan(0);
  });

  it("04 displays Subtitle Version", async () => {
    await prepared();
    expect(screen.getAllByText("v004").length).toBeGreaterThan(0);
  });

  it("05 displays Music Version", async () => {
    await prepared();
    expect(screen.getAllByText("v001").length).toBeGreaterThan(0);
  });

  it("06 displays Music Mix", async () => {
    await prepared();
    expect(screen.getByText("20%")).toBeInTheDocument();
    expect(screen.getAllByText("40%").length).toBeGreaterThan(0);
  });

  it("07 displays Voice Timing", async () => {
    await prepared();
    expect(screen.getByText("1.305s")).toBeInTheDocument();
    expect(screen.getByText("6.2s")).toBeInTheDocument();
  });

  it("08 displays Subtitle Voice lineage", async () => {
    await prepared();
    expect(screen.getByText("NARRATION_CAPTION")).toBeInTheDocument();
    expect(screen.getByText("一致")).toBeInTheDocument();
  });

  it("09 renders fresh state", async () => {
    mockPreflightFinalExport.mockResolvedValue(result({ ...preflight, execution_required: false, existing_export_version: 1, stale: false, stale_reasons: [], confirmation_token: null }));
    renderAction();
    expect(await screen.findByText("最终视频已经是最新版本")).toBeInTheDocument();
  });

  it("10 renders stale state", async () => {
    await prepared();
    expect(screen.getAllByText("最终视频需要重新导出").length).toBeGreaterThan(0);
  });

  it.each([
    ["11", "ASSEMBLY_CHANGED", "合片版本已更新"],
    ["12", "VOICE_CHANGED", "配音已更新"],
    ["13", "SUBTITLE_CHANGED", "字幕已更新"],
    ["14", "MUSIC_CHANGED", "背景音乐已更新"],
    ["15", "MUSIC_MIX_CHANGED", "混音设置已更新"],
  ])("%s renders %s reason", async (_number, code, label) => {
    mockPreflightFinalExport.mockResolvedValue(result({ ...preflight, stale_reasons: [code] }));
    renderAction();
    expect(await screen.findByText(label)).toBeInTheDocument();
  });

  it("16 renders multiple stale reasons", async () => {
    mockPreflightFinalExport.mockResolvedValue(result({ ...preflight, stale_reasons: ["VOICE_CHANGED", "MUSIC_MIX_CHANGED"] }));
    renderAction();
    expect(await screen.findByText("配音已更新")).toBeInTheDocument();
    expect(screen.getByText("混音设置已更新")).toBeInTheDocument();
  });

  it("17 renders Subtitle mismatch blocker", async () => {
    mockPreflightFinalExport.mockResolvedValue(result({ ...preflight, ready: false, confirmation_token: null, issues: [{ code: "SUBTITLE_VOICE_MISMATCH", message: "当前旁白字幕与配音版本不一致，请先重新生成字幕。" }] }));
    renderAction();
    expect(await screen.findByText(/旁白字幕与配音版本不一致/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "执行最终导出" })).not.toBeInTheDocument();
  });

  it("18 renders Legacy subtitle blocker", async () => {
    mockPreflightFinalExport.mockResolvedValue(result({ ...preflight, ready: false, confirmation_token: null, issues: [{ code: "LEGACY_SUBTITLE_NOT_ALIGNED", message: "当前字幕是旧版屏幕文字，请先生成旁白字幕。" }] }));
    renderAction();
    expect(await screen.findByText(/旧版屏幕文字/)).toBeInTheDocument();
  });

  it("19 renders Voice timing blocker", async () => {
    mockPreflightFinalExport.mockResolvedValue(result({ ...preflight, ready: false, confirmation_token: null, issues: [{ code: "VOICE_OUT_OF_BOUNDS", message: "配音超出视频时长，无法导出。" }] }));
    renderAction();
    expect(await screen.findByText(/配音超出视频时长/)).toBeInTheDocument();
  });

  it("20 opens confirmation modal", async () => {
    await openConfirmation();
    expect(screen.getByText("确认执行最终导出")).toBeInTheDocument();
  });

  it("21 explains local FFmpeg work", async () => {
    await openConfirmation();
    expect(screen.getByText(/本机执行 FFmpeg/)).toBeInTheDocument();
  });

  it("22 has no paid warning", async () => {
    await openConfirmation();
    expect(screen.queryByText(/可能产生费用|付费|收费/)).not.toBeInTheDocument();
    expect(screen.getByText(/不会产生外部 API 费用/)).toBeInTheDocument();
  });

  it("23 cancel makes zero execute POST", async () => {
    await openConfirmation();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(mockExecuteFinalExport).not.toHaveBeenCalled();
  });

  it("24 confirm makes exactly one execute POST", async () => {
    await openConfirmation();
    fireEvent.click(screen.getByRole("button", { name: "确认并导出" }));
    await waitFor(() => expect(mockExecuteFinalExport).toHaveBeenCalledTimes(1));
    expect(mockExecuteFinalExport).toHaveBeenCalledWith(projectId, { confirmation_token: token, confirm_local_export: true }, 2);
  });

  it("25 guards confirmation double click", async () => {
    let resolveTask!: (value: ReturnType<typeof result<TaskRecord>>) => void;
    mockExecuteFinalExport.mockReturnValue(new Promise((resolve) => { resolveTask = resolve; }));
    await openConfirmation();
    const confirm = screen.getByRole("button", { name: "确认并导出" });
    fireEvent.click(confirm);
    fireEvent.click(confirm);
    expect(mockExecuteFinalExport).toHaveBeenCalledTimes(1);
    resolveTask(result(task()));
  });

  it("26 polls FINAL_EXPORT task", async () => {
    await openConfirmation();
    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "确认并导出" }));
    await act(async () => { await Promise.resolve(); });
    await act(async () => { await vi.advanceTimersByTimeAsync(2_100); });
    expect(mockGetTask).toHaveBeenCalled();
  });

  it("27 F5-style mount attaches active FINAL_EXPORT", async () => {
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [task("RUNNING")] }));
    renderAction();
    expect(await screen.findByText("正在导出最终视频…")).toBeInTheDocument();
    expect(mockExecuteFinalExport).not.toHaveBeenCalled();
  });

  it("28 ignores unrelated active task", async () => {
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [task("RUNNING", "VOICE_GENERATE")] }));
    await prepared();
    expect(screen.queryByText("正在导出最终视频…")).not.toBeInTheDocument();
  });

  it("29 locks after accepted-but-unreadable 202", async () => {
    mockExecuteFinalExport.mockRejectedValue(new ApiClientError({ message: "accepted", code: "ACCEPTED_TASK_STATUS_UNREADABLE", requestAccepted: true, correlationId: "req_export" }));
    await openConfirmation();
    fireEvent.click(screen.getByRole("button", { name: "确认并导出" }));
    expect(await screen.findByText(/任务状态暂时不可读/)).toBeInTheDocument();
    expect(mockExecuteFinalExport).toHaveBeenCalledTimes(1);
  });

  it("30 success refreshes Export, History, Preflight, and Workflow", async () => {
    mockExecuteFinalExport.mockResolvedValue(result(task("SUCCEEDED")));
    await openConfirmation();
    fireEvent.click(screen.getByRole("button", { name: "确认并导出" }));
    await waitFor(() => expect(mockGetExport).toHaveBeenCalledWith(projectId));
    expect(mockGetExportHistory).toHaveBeenCalled();
    expect(mockPreflightFinalExport).toHaveBeenCalled();
    expect(mockGetProjectWorkflow).toHaveBeenCalledWith(projectId);
  });

  it("31 displays Final Export v001", async () => {
    await prepared();
    expect(screen.getAllByText("Final Export v001").length).toBeGreaterThan(0);
  });

  it("32 plays current final video", async () => {
    const { container } = await prepared();
    expect(container.querySelector('video[src*="/export/video"]')).toBeInTheDocument();
  });

  it("33 displays export history", async () => {
    await prepared();
    expect(screen.getAllByText("Final Export v001").length).toBeGreaterThan(1);
  });

  it("34 plays historical export video", async () => {
    await prepared();
    expect(screen.getByLabelText("Final Export v001 历史视频")).toHaveAttribute("src", expect.stringContaining("/export/versions/1/video"));
  });

  it("35 displays historical lineage", async () => {
    await prepared();
    expect(screen.getAllByText("Assembly").length).toBeGreaterThan(1);
    expect(screen.getAllByText("Voice").length).toBeGreaterThan(1);
  });

  it("36 displays task failure safely", async () => {
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [task("FAILED")] }));
    renderAction();
    expect(await screen.findByText("最终导出失败")).toBeInTheDocument();
    expect(screen.getByText("本地导出失败。")).toBeInTheDocument();
  });

  it("37 displays interrupted task without resume claim", async () => {
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [task("INTERRUPTED")] }));
    renderAction();
    expect(await screen.findByText("上次最终导出任务中断")).toBeInTheDocument();
    expect(screen.getByText(/不会自动重新执行 FFmpeg/)).toBeInTheDocument();
  });

  it("38 retry button performs a new preflight", async () => {
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [task("INTERRUPTED")] }));
    renderAction();
    const button = await screen.findByRole("button", { name: "重新检查当前输入" });
    const before = mockPreflightFinalExport.mock.calls.length;
    fireEvent.click(button);
    await waitFor(() => expect(mockPreflightFinalExport.mock.calls.length).toBeGreaterThan(before));
  });

  it("39 never automatically retries execution", async () => {
    mockGetProjectTasks.mockResolvedValue(result({ project_id: projectId, tasks: [task("FAILED")] }));
    renderAction();
    await screen.findByText("最终导出失败");
    expect(mockExecuteFinalExport).not.toHaveBeenCalled();
  });

  it("40 fresh state hides normal execute", async () => {
    mockPreflightFinalExport.mockResolvedValue(result({ ...preflight, execution_required: false, existing_export_version: 1, stale: false, stale_reasons: [], confirmation_token: null }));
    renderAction();
    await screen.findByText("最终视频已经是最新版本");
    expect(screen.queryByRole("button", { name: "执行最终导出" })).not.toBeInTheDocument();
  });

  it("41 does not expose force re-export", async () => {
    await prepared();
    expect(screen.queryByText(/force|强制重新导出/i)).not.toBeInTheDocument();
  });

  it("42 has no read-only label", async () => {
    await prepared();
    expect(screen.queryByText(/只读|READ-ONLY/i)).not.toBeInTheDocument();
  });

  it("43 does not render Voice generation controls", async () => {
    await prepared();
    expect(screen.queryByText("Voice Generation")).not.toBeInTheDocument();
  });

  it("44 does not render Subtitle generation controls", async () => {
    await prepared();
    expect(screen.queryByRole("button", { name: /生成旁白字幕/ })).not.toBeInTheDocument();
  });

  it("45 does not render Music upload controls", async () => {
    await prepared();
    expect(screen.queryByLabelText("选择音乐文件")).not.toBeInTheDocument();
  });

  it("46 does not render Assembly execution controls", async () => {
    await prepared();
    expect(screen.queryByRole("button", { name: /执行合片|重新合片/ })).not.toBeInTheDocument();
  });

  it("47 displays no path, command, fingerprint, or hash", async () => {
    const { container } = await prepared();
    const text = container.textContent ?? "";
    expect(text).not.toMatch(/[A-Z]:\\|ffprobe command|input_fingerprint|sha256|staging/i);
  });
});
