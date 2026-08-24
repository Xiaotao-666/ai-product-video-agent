import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  generateSubtitle,
  getSubtitle,
  getSubtitleHistory,
  getSubtitleOptions,
  getSubtitleVersion,
  regenerateSubtitle,
} from "../api/client";
import type {
  SubtitleDetail,
  SubtitleHistoryResponse,
  SubtitleOptionsResponse,
} from "../api/types";
import { SubtitleGenerationAction } from "./SubtitleGenerationAction";


vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    generateSubtitle: vi.fn(),
    getSubtitle: vi.fn(),
    getSubtitleHistory: vi.fn(),
    getSubtitleOptions: vi.fn(),
    getSubtitleVersion: vi.fn(),
    regenerateSubtitle: vi.fn(),
  };
});

const mockGenerateSubtitle = vi.mocked(generateSubtitle);
const mockGetSubtitle = vi.mocked(getSubtitle);
const mockGetSubtitleHistory = vi.mocked(getSubtitleHistory);
const mockGetSubtitleOptions = vi.mocked(getSubtitleOptions);
const mockGetSubtitleVersion = vi.mocked(getSubtitleVersion);
const mockRegenerateSubtitle = vi.mocked(regenerateSubtitle);

const projectId = "LEE柠檬";

const detail: SubtitleDetail = {
  project_id: projectId,
  status: "COMPLETED",
  version: 1,
  source: "active_voice",
  timing_source: "voice_audio_duration",
  semantic_type: "NARRATION_CAPTION",
  source_voice_version: 1,
  actual_audio_duration: 6.389,
  voice_track_start: 1.305,
  actual_voice_end: 7.694,
  cue_level_alignment: false,
  provider: "script_subtitle",
  model: "deterministic-local-v1",
  language: "zh-CN",
  duration_seconds: 12,
  created_at: "2026-08-24T10:00:00+08:00",
  cue_count: 2,
  content_available: true,
  cues: [
    { index: 1, start: "00:00:01,000", end: "00:00:02,500", text: "清爽开场" },
    { index: 2, start: "00:00:06,500", end: "00:00:08,000", text: "年轻有活力" },
  ],
};

const emptyDetail: SubtitleDetail = {
  ...detail,
  status: "NOT_STARTED",
  version: null,
  source: null,
  timing_source: null,
  semantic_type: null,
  source_voice_version: null,
  actual_audio_duration: null,
  voice_track_start: null,
  actual_voice_end: null,
  cue_level_alignment: null,
  provider: null,
  model: null,
  language: null,
  duration_seconds: null,
  created_at: null,
  cue_count: 0,
  content_available: false,
  cues: [],
};

const options: SubtitleOptionsResponse = {
  project_id: projectId,
  applicable: true,
  ready: true,
  stale: false,
  stale_reason: null,
  active_version: 1,
  next_version: 2,
  source: {
    type: "active_voice",
    label: "Voice v001",
    cue_count: 2,
    timing_source: "voice_audio_duration",
    voice_version: 1,
    semantic_type: "NARRATION_CAPTION",
    script: "实际 Voice 脚本。",
    actual_audio_duration: 6.389,
    voice_track_start: 1.305,
    actual_voice_end: 7.694,
    cue_level_alignment: false,
  },
  issues: [],
};

const emptyOptions: SubtitleOptionsResponse = {
  ...options,
  active_version: null,
  next_version: 1,
};

const history: SubtitleHistoryResponse = {
  project_id: projectId,
  active_version: 1,
  versions: [{
    version: 1,
    created_at: detail.created_at,
    provider: detail.provider,
    model: detail.model,
    language: detail.language,
    duration_seconds: detail.duration_seconds,
    cue_count: detail.cue_count,
    source: detail.source,
    timing_source: detail.timing_source,
    semantic_type: detail.semantic_type,
    source_voice_version: detail.source_voice_version,
    actual_audio_duration: detail.actual_audio_duration,
    voice_track_start: detail.voice_track_start,
    actual_voice_end: detail.actual_voice_end,
    cue_level_alignment: detail.cue_level_alignment,
    is_active: true,
  }],
};

function result<T>(data: T) {
  return { data, correlationId: "req_subtitle" };
}

function renderAction(current: SubtitleDetail = detail) {
  const onDetailChange = vi.fn();
  const rendered = render(
    <SubtitleGenerationAction
      projectId={projectId}
      detail={current}
      onDetailChange={onDetailChange}
    />,
  );
  return { ...rendered, onDetailChange };
}

async function renderEmpty() {
  mockGetSubtitleOptions.mockResolvedValue(result(emptyOptions));
  mockGetSubtitleHistory.mockResolvedValue(result({ ...history, active_version: null, versions: [] }));
  return renderAction(emptyDetail);
}

describe("SubtitleGenerationAction", () => {
  beforeEach(() => {
    mockGenerateSubtitle.mockReset();
    mockGetSubtitle.mockReset();
    mockGetSubtitleHistory.mockReset();
    mockGetSubtitleOptions.mockReset();
    mockGetSubtitleVersion.mockReset();
    mockRegenerateSubtitle.mockReset();
    mockGetSubtitle.mockResolvedValue(result(detail));
    mockGetSubtitleOptions.mockResolvedValue(result(options));
    mockGetSubtitleHistory.mockResolvedValue(result(history));
    mockGetSubtitleVersion.mockResolvedValue(result(detail));
    mockGenerateSubtitle.mockResolvedValue(result(detail));
    mockRegenerateSubtitle.mockResolvedValue(result({ ...detail, version: 2 }));
  });

  it("01 shows Generate when no active Subtitle exists", async () => {
    await renderEmpty();
    expect(await screen.findByRole("button", { name: "生成字幕" })).toBeInTheDocument();
  });

  it("02 loads Subtitle options", async () => {
    renderAction();
    await waitFor(() => expect(mockGetSubtitleOptions).toHaveBeenCalledWith(projectId));
  });

  it("03 shows active Voice source", async () => {
    renderAction();
    expect(await screen.findByText("Voice v001")).toBeInTheDocument();
  });

  it("04 shows Voice timing and script", async () => {
    mockGetSubtitleOptions.mockResolvedValue(result({
      ...emptyOptions,
      source: {
        ...options.source!,
        type: "active_voice",
        label: "Voice v002",
        cue_count: 3,
        timing_source: "voice_audio_duration",
        voice_version: 2,
        script: "唯一的 Voice Script。",
      },
    }));
    renderAction(emptyDetail);
    expect(await screen.findByText("Voice v002")).toBeInTheDocument();
    expect(screen.getAllByText("Voice WAV 绝对时轴").length).toBeGreaterThan(0);
    expect(screen.getByText("唯一的 Voice Script。")).toBeInTheDocument();
  });

  it("05 shows a structured no-source issue", async () => {
    mockGetSubtitleOptions.mockResolvedValue(result({
      ...emptyOptions,
      ready: false,
      source: null,
      issues: [{ code: "SUBTITLE_SOURCE_UNAVAILABLE", message: "当前没有可用字幕来源。" }],
    }));
    renderAction(emptyDetail);
    expect(await screen.findByText("当前没有可用字幕来源。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成字幕" })).toBeDisabled();
  });

  it("06 shows synchronous generation loading", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof generateSubtitle>>) => void;
    mockGenerateSubtitle.mockImplementation(() => new Promise((resolve) => { resolveSubmit = resolve; }));
    await renderEmpty();
    fireEvent.click(await screen.findByRole("button", { name: "生成字幕" }));
    expect(screen.getByRole("button", { name: "正在生成字幕…" })).toBeDisabled();
    await act(async () => resolveSubmit(result(detail)));
  });

  it("07 Generate sends exactly one POST", async () => {
    await renderEmpty();
    fireEvent.click(await screen.findByRole("button", { name: "生成字幕" }));
    await waitFor(() => expect(mockGenerateSubtitle).toHaveBeenCalledTimes(1));
  });

  it("08 double click cannot send a second POST", async () => {
    let resolveSubmit!: (value: Awaited<ReturnType<typeof generateSubtitle>>) => void;
    mockGenerateSubtitle.mockImplementation(() => new Promise((resolve) => { resolveSubmit = resolve; }));
    await renderEmpty();
    const button = await screen.findByRole("button", { name: "生成字幕" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mockGenerateSubtitle).toHaveBeenCalledTimes(1);
    await act(async () => resolveSubmit(result(detail)));
  });

  it("09 success refreshes durable current, options, and history", async () => {
    await renderEmpty();
    fireEvent.click(await screen.findByRole("button", { name: "生成字幕" }));
    await waitFor(() => expect(mockGetSubtitle).toHaveBeenCalledWith(projectId));
    expect(mockGetSubtitleHistory).toHaveBeenCalledTimes(2);
    expect(mockGetSubtitleOptions).toHaveBeenCalledTimes(2);
  });

  it("10 displays active Subtitle v001 after success", async () => {
    const { onDetailChange } = await renderEmpty();
    fireEvent.click(await screen.findByRole("button", { name: "生成字幕" }));
    await waitFor(() => expect(onDetailChange).toHaveBeenCalledWith(detail));
    expect((await screen.findAllByText("v001")).length).toBeGreaterThan(0);
  });

  it("11 renders Cue text", async () => {
    renderAction();
    expect(await screen.findByText("清爽开场")).toBeInTheDocument();
    expect(screen.getByText("年轻有活力")).toBeInTheDocument();
  });

  it("12 renders Core global times unchanged", async () => {
    renderAction();
    expect(await screen.findByText("00:00:01,000 → 00:00:02,500")).toBeInTheDocument();
  });

  it("13 Regenerate sends the regenerate request", async () => {
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "重新生成字幕" }));
    await waitFor(() => expect(mockRegenerateSubtitle).toHaveBeenCalledTimes(1));
    expect(mockGenerateSubtitle).not.toHaveBeenCalled();
  });

  it("14 shows the immutable vNext notice before Regenerate", async () => {
    renderAction();
    expect(await screen.findByText(/Subtitle v002/)).toBeInTheDocument();
    expect(screen.getByText(/当前版本会保留在历史中/)).toBeInTheDocument();
  });

  it("15 displays v002 as active after Regenerate", async () => {
    const v2 = { ...detail, version: 2 };
    mockGetSubtitle.mockResolvedValue(result(v2));
    mockGetSubtitleHistory.mockResolvedValue(result({
      ...history,
      active_version: 2,
      versions: [
        { ...history.versions[0], version: 2, is_active: true },
        { ...history.versions[0], is_active: false },
      ],
    }));
    const { onDetailChange } = renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "重新生成字幕" }));
    await waitFor(() => expect(onDetailChange).toHaveBeenCalledWith(v2));
  });

  it("16 keeps v001 in History after Regenerate", async () => {
    mockGetSubtitleHistory.mockResolvedValue(result({
      ...history,
      active_version: 2,
      versions: [
        { ...history.versions[0], version: 2, is_active: true },
        { ...history.versions[0], is_active: false },
      ],
    }));
    renderAction({ ...detail, version: 2 });
    expect((await screen.findAllByText("Subtitle v001")).length).toBeGreaterThan(0);
  });

  it("17 lists immutable History", async () => {
    renderAction();
    expect(await screen.findByRole("heading", { name: "历史版本" })).toBeInTheDocument();
    expect(screen.getByText("Subtitle v001")).toBeInTheDocument();
  });

  it("18 loads historical Cue detail on demand", async () => {
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "查看 Cue" }));
    await waitFor(() => expect(mockGetSubtitleVersion).toHaveBeenCalledWith(projectId, 1));
    expect(screen.getByRole("heading", { name: "Subtitle v001 Cue Preview" })).toBeInTheDocument();
  });

  it("19 marks the active History version", async () => {
    renderAction();
    expect(await screen.findByText("当前 active")).toBeInTheDocument();
  });

  it("20 exposes no Approve action", async () => {
    renderAction();
    await screen.findByText("Voice v001");
    expect(screen.queryByText(/Approve|审核通过/i)).not.toBeInTheDocument();
  });

  it("21 exposes no Candidate or Pending state", async () => {
    renderAction();
    await screen.findByText("Voice v001");
    expect(screen.queryByText(/Candidate|Pending/i)).not.toBeInTheDocument();
  });

  it("22 exposes no Task UI", async () => {
    renderAction();
    await screen.findByText("Voice v001");
    expect(screen.queryByText(/任务|QUEUED|RUNNING/)).not.toBeInTheDocument();
  });

  it("23 renders PROJECT_BUSY safely", async () => {
    mockRegenerateSubtitle.mockRejectedValue(new ApiClientError({
      message: "raw internal path",
      code: "PROJECT_BUSY",
      correlationId: "req_busy",
    }));
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "重新生成字幕" }));
    expect(await screen.findByText(/正在执行其他操作/)).toBeInTheDocument();
    expect(screen.queryByText(/raw internal path/)).not.toBeInTheDocument();
  });

  it("24 renders generation errors without raw paths", async () => {
    mockRegenerateSubtitle.mockRejectedValue(new ApiClientError({
      message: "D:\\secret\\subtitle.srt",
      code: "SUBTITLE_GENERATION_FAILED",
    }));
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "重新生成字幕" }));
    expect(await screen.findByText(/旧版本保持不变/)).toBeInTheDocument();
    expect(screen.queryByText(/secret/)).not.toBeInTheDocument();
  });

  it("25 F5-style mount reads durable state only", async () => {
    renderAction();
    await waitFor(() => expect(mockGetSubtitleHistory).toHaveBeenCalledWith(projectId));
    expect(mockGenerateSubtitle).not.toHaveBeenCalled();
    expect(mockRegenerateSubtitle).not.toHaveBeenCalled();
  });

  it("26 never automatically POSTs after a read error", async () => {
    mockGetSubtitleOptions.mockRejectedValue(new ApiClientError({ code: "NETWORK_ERROR", message: "offline" }));
    renderAction();
    expect(await screen.findByText(/无法连接本地 Backend/)).toBeInTheDocument();
    expect(mockGenerateSubtitle).not.toHaveBeenCalled();
    expect(mockRegenerateSubtitle).not.toHaveBeenCalled();
  });

  it("27 sends the expected active Voice version", async () => {
    await renderEmpty();
    fireEvent.click(await screen.findByRole("button", { name: "生成字幕" }));
    await waitFor(() => expect(mockGenerateSubtitle).toHaveBeenCalledWith(
      projectId,
      {
        expected_active_version: null,
        expected_next_version: 1,
        expected_voice_version: 1,
      },
    ));
  });

  it("28 shows stale Voice warning without automatic POST", async () => {
    mockGetSubtitleOptions.mockResolvedValue(result({
      ...options,
      stale: true,
      stale_reason: "VOICE_VERSION_CHANGED",
    }));
    renderAction();
    expect(await screen.findByText(/Active Voice 已变更/)).toBeInTheDocument();
    expect(mockRegenerateSubtitle).not.toHaveBeenCalled();
  });

  it("29 labels legacy screen-text history", async () => {
    mockGetSubtitleHistory.mockResolvedValue(result({
      ...history,
      versions: [{
        ...history.versions[0],
        source: "compiled_storyboard",
        semantic_type: "LEGACY_SCREEN_TEXT",
        source_voice_version: null,
      }],
    }));
    renderAction();
    expect(await screen.findByText(/屏幕文案（旧语义）/)).toBeInTheDocument();
  });

  it("30 labels narration history with its Voice lineage", async () => {
    renderAction();
    expect(await screen.findByText(/旁白字幕 · Voice v001/)).toBeInTheDocument();
  });

  it("31 exposes no manual Subtitle text editor", async () => {
    renderAction();
    await screen.findByText("实际 Voice 脚本。");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/编辑字幕/)).not.toBeInTheDocument();
  });
});
