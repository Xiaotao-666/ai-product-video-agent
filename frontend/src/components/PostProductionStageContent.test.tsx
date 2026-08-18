import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getAssembly,
  getExport,
  getMusic,
  getSubtitle,
  getVoice,
} from "../api/client";
import type {
  AssemblyDetail,
  ExportDetail,
  MusicDetail,
  SubtitleDetail,
  VoiceDetail,
} from "../api/types";
import type { StageKey } from "../stageDefinitions";
import { PostProductionStageContent } from "./PostProductionStageContent";


vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getAssembly: vi.fn(),
    getVoice: vi.fn(),
    getSubtitle: vi.fn(),
    getMusic: vi.fn(),
    getExport: vi.fn(),
  };
});

const mockGetAssembly = vi.mocked(getAssembly);
const mockGetVoice = vi.mocked(getVoice);
const mockGetSubtitle = vi.mocked(getSubtitle);
const mockGetMusic = vi.mocked(getMusic);
const mockGetExport = vi.mocked(getExport);

const assembly: AssemblyDetail = {
  project_id: "LEE柠檬",
  status: "COMPLETED",
  current_version: 2,
  needs_update: false,
  changed_shot_id: null,
  created_at: "2026-08-18T10:00:00+08:00",
  total_duration: 18.5,
  video_available: true,
  shots: [
    { shot_id: 1, video_version: 2 },
    { shot_id: 2, video_version: 1 },
  ],
};

const voice: VoiceDetail = {
  project_id: "LEE柠檬",
  status: "COMPLETED",
  version: 1,
  created_at: "2026-08-18T10:10:00+08:00",
  script: "新鲜看得见，LEE柠檬点亮每一天。",
  script_source: "compiled_storyboard",
  model: "online-tts-v2",
  voice: "xiaoyan",
  language: "zh-CN",
  audio_available: true,
  planned_narration_duration: 12,
  planned_first_voice_start: 2,
  planned_last_voice_end: 14,
  planned_voice_span: 12,
  actual_audio_duration: 10.5,
  voice_track_start: 2,
  actual_voice_end: 12.5,
  timing_mode: "whole_track",
  cue_level_alignment: false,
  script_matches_storyboard: true,
  calibration_status: "OUT_OF_TOLERANCE",
};

const subtitle: SubtitleDetail = {
  project_id: "LEE柠檬",
  status: "COMPLETED",
  version: 1,
  source: "compiled_storyboard",
  timing_source: "compiled_storyboard_global_timeline",
  created_at: "2026-08-18T10:20:00+08:00",
  cue_count: 2,
  content_available: true,
  cues: [
    { index: 1, start: "00:00:02,000", end: "00:00:04,500", text: "新鲜看得见" },
    { index: 2, start: "00:00:09,000", end: "00:00:12,000", text: "LEE柠檬，点亮每一天" },
  ],
};

const music: MusicDetail = {
  project_id: "LEE柠檬",
  status: "COMPLETED",
  version: 1,
  created_at: "2026-08-18T10:30:00+08:00",
  audio_available: true,
  format: "mp3",
  duration_seconds: 30,
  music_mix: {
    base_volume: 0.25,
    ducking_enabled: true,
    ducking_ratio: 0.4,
    duck_attack_seconds: 0.25,
    duck_release_seconds: 0.35,
    fade_in_seconds: 0.8,
    fade_out_seconds: 1.2,
    loop_music: false,
    ducking_status: "ENABLED",
  },
};

const finalExport: ExportDetail = {
  project_id: "LEE柠檬",
  status: "COMPLETED",
  version: 1,
  created_at: "2026-08-18T10:40:00+08:00",
  stale: false,
  video_available: true,
  assembly_version: 2,
  voice_version: 1,
  subtitle_version: 1,
  music_version: 1,
  voice_timing: {
    timing_mode: "whole_track",
    voice_track_start: 2,
    actual_audio_duration: 10.5,
    actual_voice_end: 12.5,
    calibration_status: "OUT_OF_TOLERANCE",
    cue_level_alignment: false,
  },
  music_mix: music.music_mix,
};

function result<T>(data: T) {
  return Promise.resolve({ data, correlationId: "req_detail" });
}

function renderStage(stageKey: StageKey, projectId = "LEE柠檬") {
  return render(
    <PostProductionStageContent projectId={projectId} stageKey={stageKey} />,
  );
}

describe("PostProductionStageContent", () => {
  beforeEach(() => {
    mockGetAssembly.mockReset();
    mockGetVoice.mockReset();
    mockGetSubtitle.mockReset();
    mockGetMusic.mockReset();
    mockGetExport.mockReset();
    mockGetAssembly.mockResolvedValue({ data: assembly, correlationId: "req_a" });
    mockGetVoice.mockResolvedValue({ data: voice, correlationId: "req_v" });
    mockGetSubtitle.mockResolvedValue({ data: subtitle, correlationId: "req_s" });
    mockGetMusic.mockResolvedValue({ data: music, correlationId: "req_m" });
    mockGetExport.mockResolvedValue({ data: finalExport, correlationId: "req_e" });
  });

  it("01 renders Assembly persisted detail", async () => {
    renderStage("assembly");
    expect(await screen.findByRole("heading", { name: "合片详情" })).toBeInTheDocument();
  });

  it("02 renders Assembly version and duration", async () => {
    renderStage("assembly");
    expect((await screen.findAllByText("v002")).length).toBeGreaterThan(0);
    expect(screen.getByText("18.5s")).toBeInTheDocument();
  });

  it("03 renders Assembly video with backend URL", async () => {
    const { container } = renderStage("assembly");
    await screen.findByRole("heading", { name: "合片视频" });
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/api/projects/LEE%E6%9F%A0%E6%AA%AC/assembly/video");
  });

  it("04 renders Assembly stale warning and changed Shot", async () => {
    mockGetAssembly.mockResolvedValue({ data: { ...assembly, needs_update: true, changed_shot_id: 2 }, correlationId: null });
    renderStage("assembly");
    expect(await screen.findByText("当前合片已过期，需要重新合片")).toBeInTheDocument();
    expect(screen.getAllByText("Shot 02").length).toBeGreaterThan(0);
  });

  it("05 handles missing Assembly video", async () => {
    mockGetAssembly.mockResolvedValue({ data: { ...assembly, video_available: false }, correlationId: null });
    renderStage("assembly");
    expect(await screen.findByText("视频文件不可用")).toBeInTheDocument();
  });

  it("06 renders Voice status and version", async () => {
    renderStage("voice");
    expect(await screen.findByRole("heading", { name: "当前正式配音" })).toBeInTheDocument();
    expect(screen.getByText("v001")).toBeInTheDocument();
  });

  it("07 renders persisted Voice script", async () => {
    renderStage("voice");
    expect(await screen.findByText(voice.script ?? "")).toBeInTheDocument();
  });

  it("08 maps Voice script source for users", async () => {
    renderStage("voice");
    expect(await screen.findByText("Storyboard Planned")).toBeInTheDocument();
  });

  it("09 renders Voice audio controls", async () => {
    const { container } = renderStage("voice");
    await screen.findByRole("heading", { name: "配音音频" });
    expect(container.querySelector("audio")?.getAttribute("src")).toContain("/post-production/voice/audio");
  });

  it("10 renders Voice timing values", async () => {
    renderStage("voice");
    expect(await screen.findByText("12.5s")).toBeInTheDocument();
    expect(screen.getByText("whole_track")).toBeInTheDocument();
  });

  it("11 maps Voice calibration status", async () => {
    renderStage("voice");
    expect(await screen.findByText("超出建议范围")).toBeInTheDocument();
  });

  it("12 handles missing Voice audio", async () => {
    mockGetVoice.mockResolvedValue({ data: { ...voice, audio_available: false }, correlationId: null });
    renderStage("voice");
    expect(await screen.findByText("音频文件不可用")).toBeInTheDocument();
  });

  it("13 renders Subtitle version", async () => {
    renderStage("subtitle");
    expect(await screen.findByText("v001")).toBeInTheDocument();
  });

  it("14 renders all Subtitle cues", async () => {
    renderStage("subtitle");
    expect(await screen.findByText("新鲜看得见")).toBeInTheDocument();
    expect(screen.getByText("LEE柠檬，点亮每一天")).toBeInTheDocument();
  });

  it("15 renders persisted Subtitle absolute times", async () => {
    renderStage("subtitle");
    expect(await screen.findByText("00:00:02,000 → 00:00:04,500")).toBeInTheDocument();
  });

  it("16 renders Subtitle source", async () => {
    renderStage("subtitle");
    expect(await screen.findByText("Storyboard Planned")).toBeInTheDocument();
  });

  it("17 renders Subtitle NOT_STARTED", async () => {
    mockGetSubtitle.mockResolvedValue({ data: { ...subtitle, status: "NOT_STARTED", version: null, cue_count: 0, content_available: false, cues: [] }, correlationId: null });
    renderStage("subtitle");
    expect(await screen.findByText("尚未生成字幕。")).toBeInTheDocument();
  });

  it("18 renders Music version and format", async () => {
    renderStage("music");
    expect(await screen.findByText("v001")).toBeInTheDocument();
    expect(screen.getByText("MP3")).toBeInTheDocument();
  });

  it("19 renders Music audio controls", async () => {
    const { container } = renderStage("music");
    await screen.findByRole("heading", { name: "原始正式音乐" });
    expect(container.querySelector("audio")?.getAttribute("src")).toContain("/post-production/music/audio");
  });

  it("20 renders Music Mix config", async () => {
    renderStage("music");
    expect(await screen.findByText("25%")).toBeInTheDocument();
    expect(screen.getByText("1.2s")).toBeInTheDocument();
  });

  it("21 renders Music ducking values", async () => {
    renderStage("music");
    expect(await screen.findByText("40%")).toBeInTheDocument();
    expect(screen.getByText("ENABLED")).toBeInTheDocument();
  });

  it("22 renders Music NOT_STARTED", async () => {
    mockGetMusic.mockResolvedValue({ data: { ...music, status: "NOT_STARTED", version: null, audio_available: false, format: null, music_mix: null }, correlationId: null });
    renderStage("music");
    expect(await screen.findByText("尚未设置音乐。")).toBeInTheDocument();
  });

  it("23 renders Export version", async () => {
    renderStage("export");
    expect(await screen.findByRole("heading", { name: "最终导出详情" })).toBeInTheDocument();
    expect(screen.getAllByText("v001").length).toBeGreaterThan(0);
  });

  it("24 renders Export video controls", async () => {
    const { container } = renderStage("export");
    await screen.findByRole("heading", { name: "最终成片" });
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/export/video");
  });

  it("25 renders Export related component versions", async () => {
    renderStage("export");
    await screen.findByRole("heading", { name: "使用的正式组件版本" });
    expect(screen.getByText("v002")).toBeInTheDocument();
    expect(screen.getAllByText("v001")).toHaveLength(4);
  });

  it("26 renders Export stale warning", async () => {
    mockGetExport.mockResolvedValue({ data: { ...finalExport, status: "STALE", stale: true }, correlationId: null });
    renderStage("export");
    expect(await screen.findByText("当前导出版本已过期")).toBeInTheDocument();
  });

  it("27 handles missing Export video", async () => {
    mockGetExport.mockResolvedValue({ data: { ...finalExport, video_available: false }, correlationId: null });
    renderStage("export");
    expect(await screen.findByText("视频文件不可用")).toBeInTheDocument();
  });

  it("28 renders Export NOT_STARTED", async () => {
    mockGetExport.mockResolvedValue({ data: { ...finalExport, status: "NOT_STARTED", version: null, video_available: false }, correlationId: null });
    renderStage("export");
    expect(await screen.findByText("尚未导出最终成片。")).toBeInTheDocument();
  });

  it("29 renders an explicit loading state", () => {
    mockGetAssembly.mockImplementation(() => new Promise<never>(() => undefined));
    renderStage("assembly");
    expect(screen.getByText("正在加载已持久化详情…")).toBeInTheDocument();
  });

  it("30 renders a safe network error", async () => {
    mockGetVoice.mockRejectedValue(new ApiClientError({ message: "network", code: "NETWORK_ERROR" }));
    renderStage("voice");
    expect(await screen.findByText(/无法连接本地 Backend/)).toBeInTheDocument();
  });

  it("31 retries a failed request", async () => {
    mockGetVoice
      .mockRejectedValueOnce(new ApiClientError({ message: "network", code: "NETWORK_ERROR" }))
      .mockImplementationOnce(() => result(voice));
    renderStage("voice");
    fireEvent.click(await screen.findByRole("button", { name: "重试" }));
    expect(await screen.findByText(voice.script ?? "")).toBeInTheDocument();
    expect(mockGetVoice).toHaveBeenCalledTimes(2);
  });

  it("32 never renders an absolute path", async () => {
    const { container } = renderStage("export");
    await screen.findByRole("heading", { name: "最终导出详情" });
    expect(container.textContent).not.toMatch(/[A-Z]:[\\/]|file:\/\//i);
  });

  it("33 never renders secrets or raw provider metadata", async () => {
    const { container } = renderStage("voice");
    await screen.findByRole("heading", { name: "配音详情" });
    expect(container.textContent).not.toMatch(/api key|credential|authorization|provider_task_id|raw response/i);
  });

  it("34 does not render or fetch on Planning stages", async () => {
    const { container } = renderStage("creative");
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(mockGetAssembly).not.toHaveBeenCalled();
  });

  it("35 does not render or fetch on Shots", async () => {
    const { container } = renderStage("shots");
    await waitFor(() => expect(container).toBeEmptyDOMElement());
    expect(mockGetVoice).not.toHaveBeenCalled();
  });

  it("36 exposes no generate, edit, delete, approve, or export action", async () => {
    renderStage("export");
    await screen.findByRole("heading", { name: "最终导出详情" });
    expect(screen.queryByRole("button", { name: /生成|编辑|删除|批准|拒绝|导出|切换/ })).not.toBeInTheDocument();
  });

  it("37 safely encodes a Chinese project ID in media URLs", async () => {
    const { container } = renderStage("music", "正式项目 柠檬");
    await screen.findByRole("heading", { name: "原始正式音乐" });
    expect(container.querySelector("audio")?.getAttribute("src")).toContain("%E6%AD%A3%E5%BC%8F%E9%A1%B9%E7%9B%AE%20%E6%9F%A0%E6%AA%AC");
  });

  it("38 keeps long Subtitle content readable", async () => {
    const longText = "很长的字幕内容".repeat(40);
    mockGetSubtitle.mockResolvedValue({ data: { ...subtitle, cue_count: 1, cues: [{ ...subtitle.cues[0], text: longText }] }, correlationId: null });
    renderStage("subtitle");
    const cue = await screen.findByText(longText);
    expect(cue.closest("ol")).toHaveClass("subtitle-cue-list");
  });
});
