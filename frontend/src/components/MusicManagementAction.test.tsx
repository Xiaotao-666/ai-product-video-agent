import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiClientError,
  getMusic,
  getMusicHistory,
  getMusicOptions,
  resetMusicMix,
  updateMusicMix,
  uploadMusic,
} from "../api/client";
import type {
  MusicDetail,
  MusicHistoryResponse,
  MusicOptionsResponse,
} from "../api/types";
import { MusicManagementAction } from "./MusicManagementAction";


vi.mock("../api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/client")>();
  return {
    ...actual,
    getMusic: vi.fn(),
    getMusicHistory: vi.fn(),
    getMusicOptions: vi.fn(),
    resetMusicMix: vi.fn(),
    updateMusicMix: vi.fn(),
    uploadMusic: vi.fn(),
  };
});

const mockGetMusic = vi.mocked(getMusic);
const mockGetMusicHistory = vi.mocked(getMusicHistory);
const mockGetMusicOptions = vi.mocked(getMusicOptions);
const mockResetMusicMix = vi.mocked(resetMusicMix);
const mockUpdateMusicMix = vi.mocked(updateMusicMix);
const mockUploadMusic = vi.mocked(uploadMusic);

const projectId = "SSS三明治";

const mix = {
  base_volume: 0.25,
  ducking_enabled: true,
  ducking_ratio: 0.4,
  duck_attack_seconds: 0.25,
  duck_release_seconds: 0.35,
  fade_in_seconds: 0.8,
  fade_out_seconds: 1.2,
  loop_music: false,
  ducking_status: null,
};

const detail: MusicDetail = {
  project_id: projectId,
  status: "COMPLETED",
  version: 1,
  created_at: "2026-08-24T10:00:00+08:00",
  audio_available: true,
  format: "wav",
  duration_seconds: 4.25,
  music_mix: mix,
};

const emptyDetail: MusicDetail = {
  ...detail,
  status: "NOT_STARTED",
  version: null,
  created_at: null,
  audio_available: false,
  format: null,
  duration_seconds: null,
  music_mix: null,
};

const options: MusicOptionsResponse = {
  project_id: projectId,
  has_music: true,
  active_version: 1,
  next_version: 2,
  allowed_extensions: ["aac", "flac", "m4a", "mp3", "ogg", "wav"],
  max_file_size_bytes: 500 * 1024 * 1024,
  mix,
  capabilities: { ducking: true, fade: true, loop: false },
};

const emptyOptions: MusicOptionsResponse = {
  ...options,
  has_music: false,
  active_version: null,
  next_version: 1,
};

const history: MusicHistoryResponse = {
  project_id: projectId,
  active_version: 1,
  versions: [{
    version: 1,
    created_at: detail.created_at,
    format: "wav",
    duration_seconds: 4.25,
    audio_available: true,
    is_active: true,
  }],
};

function result<T>(data: T) {
  return { data, correlationId: "req_music" };
}

function renderAction(current: MusicDetail = detail) {
  const onDetailChange = vi.fn();
  const rendered = render(
    <MusicManagementAction
      projectId={projectId}
      detail={current}
      onDetailChange={onDetailChange}
    />,
  );
  return { ...rendered, onDetailChange };
}

async function renderEmpty() {
  mockGetMusicOptions.mockResolvedValue(result(emptyOptions));
  mockGetMusicHistory.mockResolvedValue(result({ ...history, active_version: null, versions: [] }));
  const rendered = renderAction(emptyDetail);
  await waitFor(() => expect(mockGetMusicOptions).toHaveBeenCalledWith(projectId));
  return rendered;
}

function chooseFile(name = "bgm.wav", type = "audio/wav") {
  const input = screen.getByLabelText("选择音乐文件") as HTMLInputElement;
  const file = new File(["RIFF0000WAVEdata"], name, { type });
  fireEvent.change(input, { target: { files: [file] } });
  return file;
}

describe("MusicManagementAction", () => {
  beforeEach(() => {
    mockGetMusic.mockReset();
    mockGetMusicHistory.mockReset();
    mockGetMusicOptions.mockReset();
    mockResetMusicMix.mockReset();
    mockUpdateMusicMix.mockReset();
    mockUploadMusic.mockReset();
    mockGetMusic.mockResolvedValue(result(detail));
    mockGetMusicHistory.mockResolvedValue(result(history));
    mockGetMusicOptions.mockResolvedValue(result(options));
    mockResetMusicMix.mockResolvedValue(result(detail));
    mockUpdateMusicMix.mockResolvedValue(result(detail));
    mockUploadMusic.mockResolvedValue(result(detail));
  });

  it("01 shows upload entry when Music is empty", async () => {
    await renderEmpty();
    expect(await screen.findByText("尚未添加背景音乐。")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "上传音乐" })).toBeInTheDocument();
  });

  it("02 uses an input type=file", async () => {
    await renderEmpty();
    expect(await screen.findByLabelText("选择音乐文件")).toHaveAttribute("type", "file");
  });

  it("03 has no filesystem path text field", async () => {
    await renderEmpty();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/C:\\|source_path|server path/i)).not.toBeInTheDocument();
  });

  it("04 exposes exactly the Core audio accept formats", async () => {
    await renderEmpty();
    expect(await screen.findByLabelText("选择音乐文件")).toHaveAttribute(
      "accept", ".wav,.mp3,.flac,.ogg,.m4a,.aac",
    );
  });

  it("05 shows selected basename, size, and format", async () => {
    await renderEmpty();
    chooseFile("summer.wav");
    expect(screen.getByText("summer.wav")).toBeInTheDocument();
    expect(screen.getByText("WAV")).toBeInTheDocument();
    expect(screen.getByText(/KB/)).toBeInTheDocument();
  });

  it("06 shows real upload loading without fake percentages", async () => {
    let resolveUpload!: (value: Awaited<ReturnType<typeof uploadMusic>>) => void;
    mockUploadMusic.mockImplementation(() => new Promise((resolve) => { resolveUpload = resolve; }));
    await renderEmpty();
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "上传音乐" }));
    expect(screen.getByRole("button", { name: "正在上传背景音乐…" })).toBeDisabled();
    expect(screen.queryByText(/37%|62%/)).not.toBeInTheDocument();
    await act(async () => resolveUpload(result(detail)));
  });

  it("07 guards upload double click", async () => {
    let resolveUpload!: (value: Awaited<ReturnType<typeof uploadMusic>>) => void;
    mockUploadMusic.mockImplementation(() => new Promise((resolve) => { resolveUpload = resolve; }));
    await renderEmpty();
    chooseFile();
    const button = screen.getByRole("button", { name: "上传音乐" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(mockUploadMusic).toHaveBeenCalledTimes(1);
    await act(async () => resolveUpload(result(detail)));
  });

  it("08 success refreshes detail, options, and history", async () => {
    await renderEmpty();
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "上传音乐" }));
    await waitFor(() => expect(mockGetMusic).toHaveBeenCalledWith(projectId));
    expect(mockGetMusicOptions).toHaveBeenCalledTimes(2);
    expect(mockGetMusicHistory).toHaveBeenCalledTimes(2);
  });

  it("09 displays Music v001", async () => {
    renderAction();
    expect(await screen.findByText("Music v001")).toBeInTheDocument();
  });

  it("10 renders current audio playback", async () => {
    const { container } = renderAction();
    await screen.findByText("当前音乐");
    expect(container.querySelector('audio[src*="post-production/music/audio"]')).toBeInTheDocument();
  });

  it("11 shows Replace entry for active Music", async () => {
    renderAction();
    expect(await screen.findByRole("heading", { name: "替换背景音乐" })).toBeInTheDocument();
  });

  it("12 shows immutable Replace version notice", async () => {
    renderAction();
    expect(await screen.findByText(/Music v002/)).toBeInTheDocument();
    expect(screen.getByText(/当前版本会保留在历史记录中/)).toBeInTheDocument();
  });

  it("13 Replace sends expected active and next versions", async () => {
    renderAction();
    await screen.findByText("当前 active");
    const file = chooseFile("replacement.wav");
    fireEvent.click(screen.getByRole("button", { name: "上传替换版本" }));
    await waitFor(() => expect(mockUploadMusic).toHaveBeenCalledWith(
      projectId, file, { expected_active_version: 1, expected_next_version: 2 },
    ));
  });

  it("14 lists Music history", async () => {
    renderAction();
    expect(await screen.findByRole("heading", { name: "历史版本" })).toBeInTheDocument();
    expect(screen.getAllByText(/Music v001/).length).toBeGreaterThan(0);
  });

  it("15 renders historical audio playback", async () => {
    const { container } = renderAction();
    await screen.findByText("当前 active");
    expect(container.querySelector('audio[src*="versions/1/audio"]')).toBeInTheDocument();
  });

  it("16 marks current history active", async () => {
    renderAction();
    expect(await screen.findByText("当前 active")).toBeInTheDocument();
  });

  it("17 renders base volume slider", async () => {
    renderAction();
    expect(await screen.findByLabelText("基础音量")).toHaveValue("25");
  });

  it("18 converts 0-100 volume UI to 0-1 API", async () => {
    renderAction();
    fireEvent.change(await screen.findByLabelText("基础音量"), { target: { value: "65" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Mix" }));
    await waitFor(() => expect(mockUpdateMusicMix).toHaveBeenCalledWith(projectId, { base_volume: 0.65 }));
  });

  it("19 updates Ducking toggle", async () => {
    renderAction();
    fireEvent.click(await screen.findByRole("checkbox", { name: "开启 Ducking" }));
    fireEvent.click(screen.getByRole("button", { name: "保存 Mix" }));
    await waitFor(() => expect(mockUpdateMusicMix).toHaveBeenCalledWith(projectId, { ducking_enabled: false }));
  });

  it("20 updates Ducking Ratio", async () => {
    renderAction();
    fireEvent.change(await screen.findByLabelText("Ducking Ratio"), { target: { value: "55" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Mix" }));
    await waitFor(() => expect(mockUpdateMusicMix).toHaveBeenCalledWith(projectId, { ducking_ratio: 0.55 }));
  });

  it("21 updates Attack and Release only", async () => {
    renderAction();
    fireEvent.change(await screen.findByLabelText("Attack"), { target: { value: "0.1" } });
    fireEvent.change(screen.getByLabelText("Release"), { target: { value: "0.2" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Mix" }));
    await waitFor(() => expect(mockUpdateMusicMix).toHaveBeenCalledWith(
      projectId, { duck_attack_seconds: 0.1, duck_release_seconds: 0.2 },
    ));
  });

  it("22 updates Fade In and Fade Out", async () => {
    renderAction();
    fireEvent.change(await screen.findByLabelText("Fade In"), { target: { value: "0.4" } });
    fireEvent.change(screen.getByLabelText("Fade Out"), { target: { value: "0.6" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Mix" }));
    await waitFor(() => expect(mockUpdateMusicMix).toHaveBeenCalledWith(
      projectId, { fade_in_seconds: 0.4, fade_out_seconds: 0.6 },
    ));
  });

  it("23 keeps loop unavailable and non-interactive", async () => {
    renderAction();
    expect(await screen.findByText("循环播放：当前不支持")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox", { name: /循环/ })).not.toBeInTheDocument();
  });

  it("24 sends PATCH as a partial update", async () => {
    renderAction();
    fireEvent.change(await screen.findByLabelText("基础音量"), { target: { value: "40" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Mix" }));
    await waitFor(() => expect(mockUpdateMusicMix).toHaveBeenCalledTimes(1));
    expect(mockUpdateMusicMix.mock.calls[0][1]).toEqual({ base_volume: 0.4 });
  });

  it("25 Reset calls the synchronous reset API", async () => {
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "Reset Mix" }));
    await waitFor(() => expect(mockResetMusicMix).toHaveBeenCalledWith(projectId));
  });

  it("26 Mix update keeps Music v001", async () => {
    renderAction();
    fireEvent.change(await screen.findByLabelText("基础音量"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Mix" }));
    expect(await screen.findByText(/Music v001 的 Mix 已保存/)).toBeInTheDocument();
  });

  it("27 Reset keeps Music v001", async () => {
    renderAction();
    fireEvent.click(await screen.findByRole("button", { name: "Reset Mix" }));
    expect(await screen.findByText(/Music v001 的 Mix 已重置/)).toBeInTheDocument();
  });

  it("28 renders upload errors without raw paths", async () => {
    mockUploadMusic.mockRejectedValue(new ApiClientError({
      message: "D:\\secret\\music.wav",
      code: "MUSIC_FILE_INVALID",
    }));
    await renderEmpty();
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "上传音乐" }));
    expect(await screen.findByText(/文件为空、已损坏/)).toBeInTheDocument();
    expect(screen.queryByText(/secret/)).not.toBeInTheDocument();
  });

  it("29 renders PROJECT_BUSY safely", async () => {
    mockUploadMusic.mockRejectedValue(new ApiClientError({ message: "busy", code: "PROJECT_BUSY" }));
    await renderEmpty();
    chooseFile();
    fireEvent.click(screen.getByRole("button", { name: "上传音乐" }));
    expect(await screen.findByText(/正在执行其他操作/)).toBeInTheDocument();
  });

  it("30 F5-style mount reads durable Music", async () => {
    renderAction();
    await waitFor(() => expect(mockGetMusicOptions).toHaveBeenCalledWith(projectId));
    expect(screen.getAllByText("Music v001").length).toBeGreaterThan(0);
  });

  it("31 never automatically uploads on mount", async () => {
    renderAction();
    await waitFor(() => expect(mockGetMusicHistory).toHaveBeenCalledWith(projectId));
    expect(mockUploadMusic).not.toHaveBeenCalled();
  });

  it("32 exposes no Task UI", async () => {
    renderAction();
    await screen.findByText("当前 active");
    expect(screen.queryByText(/QUEUED|RUNNING|任务进度/)).not.toBeInTheDocument();
  });

  it("33 exposes no paid confirmation", async () => {
    renderAction();
    await screen.findByText("当前 active");
    expect(screen.queryByText(/费用|付费|外部调用/)).not.toBeInTheDocument();
  });

  it("34 is executable rather than read-only", async () => {
    renderAction();
    await screen.findByText("当前 active");
    expect(screen.getByLabelText("选择音乐文件")).toBeEnabled();
    chooseFile();
    expect(screen.getByRole("button", { name: "上传替换版本" })).toBeEnabled();
    expect(screen.queryByText("只读")).not.toBeInTheDocument();
  });

  it("35 does not render Voice generation controls", async () => {
    renderAction();
    await screen.findByText("当前 active");
    expect(screen.queryByText(/生成配音|重新生成 Voice/)).not.toBeInTheDocument();
  });

  it("36 does not render Subtitle generation controls", async () => {
    renderAction();
    await screen.findByText("当前 active");
    expect(screen.queryByText(/生成字幕|重新生成字幕/)).not.toBeInTheDocument();
  });

  it("37 does not render Assembly execution controls", async () => {
    renderAction();
    await screen.findByText("当前 active");
    expect(screen.queryByText(/创建 Assembly 计划|生成最终视频/)).not.toBeInTheDocument();
  });

  it("38 does not display paths, hashes, secrets, or providers", async () => {
    renderAction();
    await screen.findByText("当前 active");
    expect(screen.queryByText(/sha256|asset_path|config_path|local_music|api[_ -]?key|C:\\/i)).not.toBeInTheDocument();
  });

  it("39 rejects unsupported extension before POST", async () => {
    await renderEmpty();
    chooseFile("malware.exe", "application/octet-stream");
    expect(await screen.findByText(/仅支持 WAV/)).toBeInTheDocument();
    expect(mockUploadMusic).not.toHaveBeenCalled();
  });

  it("40 explains deterministic Voice-window Ducking", async () => {
    renderAction();
    expect(await screen.findByText(/旁白期间背景音乐降低至基础音量的 40%/)).toBeInTheDocument();
    expect(screen.queryByText(/AI智能降噪/)).not.toBeInTheDocument();
  });
});
