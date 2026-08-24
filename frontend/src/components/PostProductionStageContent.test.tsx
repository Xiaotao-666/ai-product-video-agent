import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  createAssemblyPlan,
  executeAssembly,
  getAssembly,
  getAssemblyReadiness,
  getExport,
  getExportHistory,
  getMusic,
  getMusicHistory,
  getMusicOptions,
  getProjectTasks,
  getProjectWorkflow,
  getSubtitle,
  getSubtitleHistory,
  getSubtitleOptions,
  getSubtitleVersion,
  generateSubtitle,
  regenerateSubtitle,
  getTask,
  getVoice,
  getVoiceHistory,
  getVoiceOptions,
  resumeAssembly,
  preflightFinalExport,
  resetMusicMix,
  updateMusicMix,
  uploadMusic,
} from "../api/client";
import type {
  AssemblyDetail,
  AssemblyPlan,
  AssemblyReadiness,
  ExportDetail,
  ExportHistoryResponse,
  FinalExportPreflightResponse,
  MusicDetail,
  SubtitleDetail,
  TaskRecord,
  VoiceDetail,
} from "../api/types";
import type { StageKey } from "../stageDefinitions";
import { PostProductionStageContent } from "./PostProductionStageContent";


vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    createAssemblyPlan: vi.fn(),
    executeAssembly: vi.fn(),
    getAssembly: vi.fn(),
    getAssemblyReadiness: vi.fn(),
    getVoice: vi.fn(),
    getVoiceHistory: vi.fn(),
    getVoiceOptions: vi.fn(),
    getSubtitle: vi.fn(),
    getSubtitleHistory: vi.fn(),
    getSubtitleOptions: vi.fn(),
    getSubtitleVersion: vi.fn(),
    generateSubtitle: vi.fn(),
    regenerateSubtitle: vi.fn(),
    getMusic: vi.fn(),
    getMusicHistory: vi.fn(),
    getMusicOptions: vi.fn(),
    getExport: vi.fn(),
    getExportHistory: vi.fn(),
    getProjectTasks: vi.fn(),
    getProjectWorkflow: vi.fn(),
    getTask: vi.fn(),
    resumeAssembly: vi.fn(),
    preflightFinalExport: vi.fn(),
    resetMusicMix: vi.fn(),
    updateMusicMix: vi.fn(),
    uploadMusic: vi.fn(),
  };
});

const mockGetAssembly = vi.mocked(getAssembly);
const mockGetAssemblyReadiness = vi.mocked(getAssemblyReadiness);
const mockCreateAssemblyPlan = vi.mocked(createAssemblyPlan);
const mockExecuteAssembly = vi.mocked(executeAssembly);
const mockResumeAssembly = vi.mocked(resumeAssembly);
const mockGetProjectTasks = vi.mocked(getProjectTasks);
const mockGetTask = vi.mocked(getTask);
const mockGetVoice = vi.mocked(getVoice);
const mockGetVoiceHistory = vi.mocked(getVoiceHistory);
const mockGetVoiceOptions = vi.mocked(getVoiceOptions);
const mockGetSubtitle = vi.mocked(getSubtitle);
const mockGetSubtitleHistory = vi.mocked(getSubtitleHistory);
const mockGetSubtitleOptions = vi.mocked(getSubtitleOptions);
const mockGetSubtitleVersion = vi.mocked(getSubtitleVersion);
const mockGenerateSubtitle = vi.mocked(generateSubtitle);
const mockRegenerateSubtitle = vi.mocked(regenerateSubtitle);
const mockGetMusic = vi.mocked(getMusic);
const mockGetMusicHistory = vi.mocked(getMusicHistory);
const mockGetMusicOptions = vi.mocked(getMusicOptions);
const mockResetMusicMix = vi.mocked(resetMusicMix);
const mockUpdateMusicMix = vi.mocked(updateMusicMix);
const mockUploadMusic = vi.mocked(uploadMusic);
const mockGetExport = vi.mocked(getExport);
const mockGetExportHistory = vi.mocked(getExportHistory);
const mockGetProjectWorkflow = vi.mocked(getProjectWorkflow);
const mockPreflightFinalExport = vi.mocked(preflightFinalExport);

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
  current_plan: null,
  final_videos: [
    {
      final_video_version: 2,
      assembly_version: 2,
      created_at: "2026-08-18T10:00:00+08:00",
      total_duration: 18.5,
      video_available: true,
      is_current: true,
      shots: [
        { shot_id: 1, order: 1, video_version: 2, prompt_version: 3 },
        { shot_id: 2, order: 2, video_version: 1, prompt_version: 1 },
      ],
    },
    {
      final_video_version: 1,
      assembly_version: 1,
      created_at: "2026-08-17T10:00:00+08:00",
      total_duration: 17,
      video_available: true,
      is_current: false,
      shots: [
        { shot_id: 1, order: 1, video_version: 1, prompt_version: 1 },
        { shot_id: 2, order: 2, video_version: 1, prompt_version: 1 },
      ],
    },
  ],
};

const assemblyPlan: AssemblyPlan = {
  project_id: "LEE柠檬",
  assembly_version: 3,
  status: "READY",
  created_at: "2026-08-20T10:00:00+08:00",
  total_duration: 14,
  shots: [
    {
      shot_id: 1,
      order: 1,
      approved_video_version: 2,
      prompt_version: 3,
      duration: 6,
      resolution: "768P",
    },
    {
      shot_id: 2,
      order: 2,
      approved_video_version: 1,
      prompt_version: 1,
      duration: 8,
      resolution: "768P",
    },
  ],
};

const assemblyReadiness: AssemblyReadiness = {
  project_id: "LEE柠檬",
  status: "READY",
  ready: true,
  shot_count: 2,
  ready_count: 2,
  total_duration: 14,
  shots: assemblyPlan.shots,
  issues: [],
  current_plan: null,
};

const assemblyTask: TaskRecord = {
  task_id: "task_0123456789abcdef0123456789abcdef",
  project_id: "LEE柠檬",
  operation: "ASSEMBLY_EXECUTE",
  target_id: "assembly_v003",
  status: "QUEUED",
  created_at: "2026-08-20T10:01:00+08:00",
  started_at: null,
  finished_at: null,
  correlation_id: "req_0123456789abcdef0123456789abcdef",
  error: null,
  result: null,
};

const voice: VoiceDetail = {
  project_id: "LEE柠檬",
  status: "COMPLETED",
  version: 1,
  created_at: "2026-08-18T10:10:00+08:00",
  script: "新鲜看得见，LEE柠檬点亮每一天。",
  script_source: "compiled_storyboard",
  provider: "xfyun_tts",
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
  total_video_duration: 18,
  duration_difference_seconds: -1.5,
  duration_difference_ratio: -0.125,
  timing_mode: "whole_track",
  cue_level_alignment: false,
  script_matches_storyboard: true,
  calibration_status: "OUT_OF_TOLERANCE",
  timing_acceptance: null,
};

const subtitle: SubtitleDetail = {
  project_id: "LEE柠檬",
  status: "COMPLETED",
  version: 1,
  source: "compiled_storyboard",
  timing_source: "compiled_storyboard_global_timeline",
  semantic_type: "LEGACY_SCREEN_TEXT",
  source_voice_version: null,
  actual_audio_duration: null,
  voice_track_start: null,
  actual_voice_end: null,
  cue_level_alignment: null,
  provider: "storyboard_subtitle",
  model: "compiled-storyboard-v1",
  language: "zh-CN",
  duration_seconds: 12,
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
  stale_reasons: [],
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

const exportPreflight: FinalExportPreflightResponse = {
  project_id: "LEE柠檬",
  ready: true,
  execution_required: false,
  next_export_version: 2,
  active_export_version: 1,
  inputs: {
    assembly_version: 2,
    voice_version: 1,
    subtitle_version: 1,
    music_version: 1,
  },
  voice_timing: {
    status: "OUT_OF_TOLERANCE",
    accepted: true,
    track_start: 2,
    actual_audio_duration: 10.5,
    actual_end: 12.5,
  },
  subtitle: {
    semantic_type: "NARRATION_CAPTION",
    source_voice_version: 1,
    voice_aligned: true,
  },
  music_mix: music.music_mix,
  existing_export_version: 1,
  stale: false,
  stale_reasons: [],
  issues: [],
  confirmation_token: null,
};

const exportHistory: ExportHistoryResponse = {
  project_id: "LEE柠檬",
  active_version: 1,
  versions: [{
    version: 1,
    created_at: finalExport.created_at,
    assembly_version: 2,
    voice_version: 1,
    subtitle_version: 1,
    music_version: 1,
    audio_muxed: true,
    subtitle_burned: true,
    duration_seconds: 18.5,
    video_available: true,
    is_active: true,
    stale: false,
    stale_reasons: [],
  }],
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
    mockGetAssemblyReadiness.mockReset();
    mockCreateAssemblyPlan.mockReset();
    mockExecuteAssembly.mockReset();
    mockResumeAssembly.mockReset();
    mockGetProjectTasks.mockReset();
    mockGetTask.mockReset();
    mockGetVoice.mockReset();
    mockGetVoiceHistory.mockReset();
    mockGetVoiceOptions.mockReset();
    mockGetSubtitle.mockReset();
    mockGetSubtitleHistory.mockReset();
    mockGetSubtitleOptions.mockReset();
    mockGetSubtitleVersion.mockReset();
    mockGenerateSubtitle.mockReset();
    mockRegenerateSubtitle.mockReset();
    mockGetMusic.mockReset();
    mockGetMusicHistory.mockReset();
    mockGetMusicOptions.mockReset();
    mockResetMusicMix.mockReset();
    mockUpdateMusicMix.mockReset();
    mockUploadMusic.mockReset();
    mockGetExport.mockReset();
    mockGetExportHistory.mockReset();
    mockGetProjectWorkflow.mockReset();
    mockPreflightFinalExport.mockReset();
    mockGetAssembly.mockResolvedValue({ data: assembly, correlationId: "req_a" });
    mockGetAssemblyReadiness.mockResolvedValue({ data: assemblyReadiness, correlationId: "req_ar" });
    mockCreateAssemblyPlan.mockResolvedValue({ data: assemblyPlan, correlationId: "req_ap" });
    mockExecuteAssembly.mockResolvedValue({ data: assemblyTask, correlationId: "req_ae" });
    mockResumeAssembly.mockResolvedValue({ data: assemblyTask, correlationId: "req_resume" });
    mockGetProjectTasks.mockResolvedValue({
      data: { project_id: "LEE柠檬", tasks: [] },
      correlationId: "req_tasks",
    });
    mockGetTask.mockResolvedValue({ data: assemblyTask, correlationId: "req_task" });
    mockGetVoice.mockResolvedValue({ data: voice, correlationId: "req_v" });
    mockGetVoiceHistory.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        active_version: 1,
        versions: [{
          version: 1,
          created_at: voice.created_at,
          provider: voice.provider,
          model: voice.model,
          voice: voice.voice,
          language: voice.language,
          script_source: voice.script_source,
          duration_seconds: voice.actual_audio_duration,
          calibration_status: voice.calibration_status,
          timing_acceptance: null,
          audio_available: true,
          is_active: true,
        }],
      },
      correlationId: "req_vh",
    });
    mockGetVoiceOptions.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        enabled: true,
        has_active_voice: true,
        active_version: 1,
        next_version: 2,
        script: {
          source: "compiled_storyboard",
          text: voice.script ?? "",
          character_count: voice.script?.length ?? 0,
          cue_count: 2,
        },
        planned_timing: {
          first_start: 2,
          last_end: 14,
          span: 12,
          narration_duration: 12,
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
      correlationId: "req_vo",
    });
    mockGetSubtitle.mockResolvedValue({ data: subtitle, correlationId: "req_s" });
    mockGetSubtitleOptions.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        applicable: true,
        ready: true,
        stale: true,
        stale_reason: "LEGACY_SCREEN_TEXT",
        active_version: 1,
        next_version: 2,
        source: {
          type: "active_voice",
          label: "Voice v001",
          cue_count: 2,
          timing_source: "voice_audio_duration",
          voice_version: 1,
          semantic_type: "NARRATION_CAPTION",
          script: voice.script ?? "",
          actual_audio_duration: 10.5,
          voice_track_start: 2,
          actual_voice_end: 12.5,
          cue_level_alignment: false,
        },
        issues: [],
      },
      correlationId: "req_so",
    });
    mockGetSubtitleHistory.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        active_version: 1,
        versions: [{
          version: 1,
          created_at: subtitle.created_at,
          provider: subtitle.provider,
          model: subtitle.model,
          language: subtitle.language,
          duration_seconds: subtitle.duration_seconds,
          cue_count: subtitle.cue_count,
          source: subtitle.source,
          timing_source: subtitle.timing_source,
          semantic_type: subtitle.semantic_type,
          source_voice_version: null,
          actual_audio_duration: null,
          voice_track_start: null,
          actual_voice_end: null,
          cue_level_alignment: null,
          is_active: true,
        }],
      },
      correlationId: "req_sh",
    });
    mockGetSubtitleVersion.mockResolvedValue({ data: subtitle, correlationId: "req_sv" });
    mockGenerateSubtitle.mockResolvedValue({ data: subtitle, correlationId: "req_sg" });
    mockRegenerateSubtitle.mockResolvedValue({ data: { ...subtitle, version: 2 }, correlationId: "req_sr" });
    mockGetMusic.mockResolvedValue({ data: music, correlationId: "req_m" });
    mockGetMusicOptions.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        has_music: true,
        active_version: 1,
        next_version: 2,
        allowed_extensions: ["aac", "flac", "m4a", "mp3", "ogg", "wav"],
        max_file_size_bytes: 500 * 1024 * 1024,
        mix: music.music_mix!,
        capabilities: { ducking: true, fade: true, loop: false },
      },
      correlationId: "req_mo",
    });
    mockGetMusicHistory.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        active_version: 1,
        versions: [{
          version: 1,
          created_at: music.created_at,
          format: music.format,
          duration_seconds: music.duration_seconds,
          audio_available: true,
          is_active: true,
        }],
      },
      correlationId: "req_mh",
    });
    mockResetMusicMix.mockResolvedValue({ data: music, correlationId: "req_mr" });
    mockUpdateMusicMix.mockResolvedValue({ data: music, correlationId: "req_mu" });
    mockUploadMusic.mockResolvedValue({ data: music, correlationId: "req_up" });
    mockGetExport.mockResolvedValue({ data: finalExport, correlationId: "req_e" });
    mockGetExportHistory.mockResolvedValue({ data: exportHistory, correlationId: "req_eh" });
    mockGetProjectWorkflow.mockResolvedValue({ data: {} as never, correlationId: "req_w" });
    mockPreflightFinalExport.mockResolvedValue({ data: exportPreflight, correlationId: "req_ep" });
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
    expect((await screen.findAllByText(/Legacy Storyboard 屏幕文案/)).length).toBeGreaterThan(0);
  });

  it("16b marks Subtitle as an executable workflow instead of read-only detail", async () => {
    renderStage("subtitle");
    expect(await screen.findByText("POST-PRODUCTION WORKFLOW")).toBeInTheDocument();
    expect(screen.getByText("可执行")).toBeInTheDocument();
    expect(screen.queryByText("PERSISTED READ-ONLY DETAIL")).not.toBeInTheDocument();
  });

  it("17 renders Subtitle NOT_STARTED", async () => {
    mockGetSubtitle.mockResolvedValue({ data: { ...subtitle, status: "NOT_STARTED", version: null, cue_count: 0, content_available: false, cues: [] }, correlationId: null });
    renderStage("subtitle");
    expect(await screen.findByText("字幕未生成。")).toBeInTheDocument();
  });

  it("18 renders Music version and format", async () => {
    renderStage("music");
    expect((await screen.findAllByText(/v001/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText("MP3").length).toBeGreaterThan(0);
  });

  it("19 renders Music audio controls", async () => {
    const { container } = renderStage("music");
    await screen.findByRole("heading", { name: "当前音乐" });
    expect(container.querySelector("audio")?.getAttribute("src")).toContain("/post-production/music/audio");
  });

  it("20 renders Music Mix config", async () => {
    renderStage("music");
    expect(await screen.findByText("25%")).toBeInTheDocument();
    expect(screen.getByLabelText("Fade Out")).toHaveValue(1.2);
  });

  it("21 renders Music ducking values", async () => {
    renderStage("music");
    expect(await screen.findByLabelText("Ducking Ratio")).toHaveValue("40");
    expect(screen.getByText(/旁白期间背景音乐降低/)).toBeInTheDocument();
  });

  it("22 renders Music NOT_STARTED", async () => {
    mockGetMusic.mockResolvedValue({ data: { ...music, status: "NOT_STARTED", version: null, audio_available: false, format: null, music_mix: null }, correlationId: null });
    mockGetMusicOptions.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        has_music: false,
        active_version: null,
        next_version: 1,
        allowed_extensions: ["aac", "flac", "m4a", "mp3", "ogg", "wav"],
        max_file_size_bytes: 500 * 1024 * 1024,
        mix: music.music_mix!,
        capabilities: { ducking: true, fade: true, loop: false },
      },
      correlationId: null,
    });
    mockGetMusicHistory.mockResolvedValue({
      data: { project_id: "LEE柠檬", active_version: null, versions: [] },
      correlationId: null,
    });
    renderStage("music");
    expect(await screen.findByText("尚未添加背景音乐。")).toBeInTheDocument();
  });

  it("23 renders Export version", async () => {
    renderStage("export");
    expect(await screen.findByRole("heading", { name: "最终导出", level: 2 })).toBeInTheDocument();
    expect((await screen.findAllByText("v001")).length).toBeGreaterThan(0);
  });

  it("24 renders Export video controls", async () => {
    const { container } = renderStage("export");
    await screen.findByRole("heading", { name: "当前最终视频" });
    expect(container.querySelector("video")?.getAttribute("src")).toContain("/export/video");
  });

  it("25 renders Export related component versions", async () => {
    renderStage("export");
    await screen.findByRole("heading", { name: "当前输入" });
    expect(screen.getAllByText("v002").length).toBeGreaterThan(0);
    expect(screen.getAllByText("v001").length).toBeGreaterThanOrEqual(4);
  });

  it("26 renders Export stale warning", async () => {
    mockGetExport.mockResolvedValue({ data: { ...finalExport, status: "STALE", stale: true, stale_reasons: ["ASSEMBLY_CHANGED"] }, correlationId: null });
    mockPreflightFinalExport.mockResolvedValue({
      data: {
        ...exportPreflight,
        execution_required: true,
        existing_export_version: null,
        stale: true,
        stale_reasons: ["ASSEMBLY_CHANGED"],
        confirmation_token: `exp_${"a".repeat(64)}`,
      },
      correlationId: null,
    });
    renderStage("export");
    expect(await screen.findByText("合片版本已更新")).toBeInTheDocument();
  });

  it("27 handles missing Export video", async () => {
    mockGetExport.mockResolvedValue({ data: { ...finalExport, video_available: false }, correlationId: null });
    renderStage("export");
    expect(await screen.findByText("最终视频不可用")).toBeInTheDocument();
  });

  it("28 renders Export NOT_STARTED", async () => {
    mockGetExport.mockResolvedValue({ data: { ...finalExport, status: "NOT_STARTED", version: null, video_available: false }, correlationId: null });
    renderStage("export");
    expect(await screen.findByText("尚未导出")).toBeInTheDocument();
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
    await screen.findByRole("heading", { name: "最终导出", level: 2 });
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

  it("36 marks Final Export executable without unrelated stage actions", async () => {
    renderStage("export");
    await screen.findByRole("heading", { name: "最终导出", level: 2 });
    expect(screen.getByText("可执行")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /生成配音|生成字幕|上传音乐|删除|批准|拒绝|切换/ })).not.toBeInTheDocument();
  });

  it("37 safely encodes a Chinese project ID in media URLs", async () => {
    const { container } = renderStage("music", "正式项目 柠檬");
    await screen.findByRole("heading", { name: "当前音乐" });
    expect(container.querySelector("audio")?.getAttribute("src")).toContain("%E6%AD%A3%E5%BC%8F%E9%A1%B9%E7%9B%AE%20%E6%9F%A0%E6%AA%AC");
  });

  it("38 keeps long Subtitle content readable", async () => {
    const longText = "很长的字幕内容".repeat(40);
    mockGetSubtitle.mockResolvedValue({ data: { ...subtitle, cue_count: 1, cues: [{ ...subtitle.cues[0], text: longText }] }, correlationId: null });
    renderStage("subtitle");
    const cue = await screen.findByText(longText);
    expect(cue.closest("ol")).toHaveClass("subtitle-cue-list");
  });

  it("39 renders Assembly readiness, ordered Shot versions, and duration", async () => {
    renderStage("assembly");
    expect(await screen.findByRole("heading", { name: "Assembly 计划" })).toBeInTheDocument();
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByText("14s")).toBeInTheDocument();
    expect(screen.getByText(/Video v002 · Prompt v003 · 6s · 768P/)).toBeInTheDocument();
    expect(screen.getByText(/Video v001 · Prompt v001 · 8s · 768P/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "创建 Assembly 计划" })).toBeEnabled();
  });

  it("40 shows readiness issues and does not offer plan creation", async () => {
    mockGetAssemblyReadiness.mockResolvedValue({
      data: {
        ...assemblyReadiness,
        status: "NOT_READY",
        ready: false,
        ready_count: 1,
        total_duration: null,
        shots: assemblyReadiness.shots.slice(0, 1),
        issues: [{ shot_id: 2, order: 2, reason: "WAITING_REVIEW" }],
      },
      correlationId: "req_issue",
    });
    renderStage("assembly");
    expect(await screen.findByText("Shot 02：镜头仍在等待审核。")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建 Assembly 计划" })).not.toBeInTheDocument();
  });

  it("41 creates only an Assembly plan and renders its version snapshot", async () => {
    renderStage("assembly");
    fireEvent.click(await screen.findByRole("button", { name: "创建 Assembly 计划" }));
    expect(await screen.findByText("Assembly 计划已创建，可以在确认后生成 Final Video。")).toBeInTheDocument();
    expect(mockCreateAssemblyPlan).toHaveBeenCalledTimes(1);
    expect(mockCreateAssemblyPlan).toHaveBeenCalledWith("LEE柠檬");
    expect(screen.getByText("v003")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "创建 Assembly 计划" })).not.toBeInTheDocument();
  });

  it("42 shows an OUTDATED plan without mutating its stored Shot snapshot", async () => {
    mockGetAssemblyReadiness.mockResolvedValue({
      data: {
        ...assemblyReadiness,
        current_plan: { ...assemblyPlan, status: "OUTDATED" },
      },
      correlationId: "req_outdated",
    });
    renderStage("assembly");
    expect(await screen.findByText("当前镜头版本已变化，需要重新生成 Assembly 计划")).toBeInTheDocument();
    expect(screen.getAllByText(/Video v002 · Prompt v003/).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "创建 Assembly 计划" })).toBeEnabled();
  });

  it("43 asks for explicit confirmation and cancel creates no Task", async () => {
    mockGetAssemblyReadiness.mockResolvedValue({
      data: { ...assemblyReadiness, current_plan: assemblyPlan },
      correlationId: "req_ready_plan",
    });
    renderStage("assembly");
    fireEvent.click(await screen.findByRole("button", { name: "执行合片" }));
    expect(screen.getByText("将根据当前Assembly Plan生成Final Video。不会修改Shot版本。")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(mockExecuteAssembly).not.toHaveBeenCalled();
  });

  it("44 confirms one Assembly execution Task with the current plan", async () => {
    mockGetAssemblyReadiness.mockResolvedValue({
      data: { ...assemblyReadiness, current_plan: assemblyPlan },
      correlationId: "req_ready_plan",
    });
    renderStage("assembly");
    fireEvent.click(await screen.findByRole("button", { name: "执行合片" }));
    fireEvent.click(screen.getByRole("button", { name: "确认并执行" }));
    expect(await screen.findByText("排队中")).toBeInTheDocument();
    expect(mockExecuteAssembly).toHaveBeenCalledTimes(1);
    expect(mockExecuteAssembly).toHaveBeenCalledWith("LEE柠檬", 3);
  });

  it("45 restores an active Assembly Task after refresh without another POST", async () => {
    mockGetAssemblyReadiness.mockResolvedValue({
      data: { ...assemblyReadiness, current_plan: assemblyPlan },
      correlationId: "req_ready_plan",
    });
    mockGetProjectTasks.mockResolvedValue({
      data: {
        project_id: "LEE柠檬",
        tasks: [{ ...assemblyTask, status: "RUNNING", started_at: "2026-08-20T10:02:00+08:00" }],
      },
      correlationId: "req_tasks",
    });
    renderStage("assembly");
    expect(await screen.findByText("正在生成最终视频")).toBeInTheDocument();
    expect(mockExecuteAssembly).not.toHaveBeenCalled();
  });

  it("46 renders historical Final Video versions with independent playback URLs", async () => {
    const { container } = renderStage("assembly");
    expect(await screen.findByRole("heading", { name: "Final Video 历史" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Final Video v001" })).toBeInTheDocument();
    const urls = Array.from(container.querySelectorAll("video")).map((video) => video.getAttribute("src"));
    expect(urls).toContain("http://127.0.0.1:8000/api/projects/LEE%E6%9F%A0%E6%AA%AC/assembly/versions/1/video");
  });

  it("47 offers durable resume for a retryable failed Assembly Task", async () => {
    const failed = {
      ...assemblyTask,
      status: "FAILED" as const,
      finished_at: "2026-08-20T10:03:00+08:00",
      error: { code: "ASSEMBLY_EXECUTION_FAILED", message: "安全错误", retryable: true },
    };
    mockGetAssemblyReadiness.mockResolvedValue({
      data: { ...assemblyReadiness, current_plan: assemblyPlan },
      correlationId: "req_ready_plan",
    });
    mockGetProjectTasks.mockResolvedValue({
      data: { project_id: "LEE柠檬", tasks: [failed] },
      correlationId: "req_tasks",
    });
    mockResumeAssembly.mockResolvedValue({
      data: { ...assemblyTask, task_id: "task_fedcba9876543210fedcba9876543210" },
      correlationId: "req_resume",
    });
    renderStage("assembly");
    fireEvent.click(await screen.findByRole("button", { name: "继续执行合片" }));
    await waitFor(() => expect(mockResumeAssembly).toHaveBeenCalledWith("LEE柠檬", 3));
    expect(mockExecuteAssembly).not.toHaveBeenCalled();
  });
});
