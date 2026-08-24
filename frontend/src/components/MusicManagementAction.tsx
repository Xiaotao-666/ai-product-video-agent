import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  getMusic,
  getMusicAudioUrl,
  getMusicHistory,
  getMusicOptions,
  getMusicVersionAudioUrl,
  resetMusicMix,
  updateMusicMix,
  uploadMusic,
} from "../api/client";
import type {
  MusicDetail,
  MusicHistoryResponse,
  MusicMixDetail,
  MusicMixUpdateRequest,
  MusicOptionsResponse,
} from "../api/types";


interface MusicManagementActionProps {
  projectId: string;
  detail: MusicDetail;
  onDetailChange: (detail: MusicDetail) => void;
}

type EditableMix = {
  base_volume: number;
  ducking_enabled: boolean;
  ducking_ratio: number;
  duck_attack_seconds: number;
  duck_release_seconds: number;
  fade_in_seconds: number;
  fade_out_seconds: number;
};

type EditableMixKey = keyof EditableMix;

const MUSIC_ACCEPT = ".wav,.mp3,.flac,.ogg,.m4a,.aac";

function versionLabel(version: number | null): string {
  return version === null ? "尚无" : `v${String(version).padStart(3, "0")}`;
}

function secondsLabel(value: number | null): string {
  return value === null ? "未记录" : `${value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}s`;
}

function fileSizeLabel(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function displayFilename(value: string): string {
  const basename = value.replace(/\\/g, "/").split("/").pop()?.trim() ?? "";
  return basename || "未命名音频";
}

function fileExtension(value: string): string {
  return displayFilename(value).split(".").pop()?.toLowerCase() ?? "";
}

function editableMix(value: MusicMixDetail): EditableMix {
  return {
    base_volume: value.base_volume ?? 0.25,
    ducking_enabled: value.ducking_enabled ?? true,
    ducking_ratio: value.ducking_ratio ?? 0.4,
    duck_attack_seconds: value.duck_attack_seconds ?? 0.25,
    duck_release_seconds: value.duck_release_seconds ?? 0.35,
    fade_in_seconds: value.fade_in_seconds ?? 0.8,
    fade_out_seconds: value.fade_out_seconds ?? 1.2,
  };
}

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) return "背景音乐操作失败，请重试。";
  return {
    MUSIC_FILE_REQUIRED: "请先选择背景音乐文件。",
    MUSIC_FORMAT_UNSUPPORTED: "仅支持 WAV、MP3、FLAC、OGG、M4A 和 AAC 音频。",
    MUSIC_FILE_TOO_LARGE: "音乐文件超过允许的大小限制。",
    MUSIC_FILE_INVALID: "音乐文件为空、已损坏或格式不匹配。",
    MUSIC_UPLOAD_FAILED: "音乐上传未完成，原有版本保持不变。",
    MUSIC_STATE_CHANGED: "Music 版本已发生变化，请刷新后重试。",
    MUSIC_MIX_INVALID: "Music Mix 设置无效，请检查数值。",
    PROJECT_BUSY: "项目当前正在执行其他操作，请稍后重试。",
    ACTION_NOT_ALLOWED: "请先完成 Assembly 并上传背景音乐。",
    NETWORK_ERROR: "无法连接本地 Backend。",
  }[error.code] ?? "背景音乐操作失败，请重试。";
}

export function MusicManagementAction({
  projectId,
  detail,
  onDetailChange,
}: MusicManagementActionProps) {
  const [current, setCurrent] = useState(detail);
  const [options, setOptions] = useState<MusicOptionsResponse | null>(null);
  const [history, setHistory] = useState<MusicHistoryResponse | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [mixDraft, setMixDraft] = useState<EditableMix | null>(null);
  const [dirtyMix, setDirtyMix] = useState<Set<EditableMixKey>>(new Set());
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [savingMix, setSavingMix] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const uploadGuard = useRef(false);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setCurrent(detail);
  }, [detail]);

  const applyOptions = useCallback((value: MusicOptionsResponse) => {
    setOptions(value);
    setMixDraft(editableMix(value.mix));
    setDirtyMix(new Set());
  }, []);

  const loadMetadata = useCallback(async () => {
    const [optionsResult, historyResult] = await Promise.all([
      getMusicOptions(projectId),
      getMusicHistory(projectId),
    ]);
    applyOptions(optionsResult.data);
    setHistory(historyResult.data);
  }, [applyOptions, projectId]);

  const refresh = useCallback(async () => {
    const [detailResult, optionsResult, historyResult] = await Promise.all([
      getMusic(projectId),
      getMusicOptions(projectId),
      getMusicHistory(projectId),
    ]);
    setCurrent(detailResult.data);
    applyOptions(optionsResult.data);
    setHistory(historyResult.data);
    onDetailChange(detailResult.data);
  }, [applyOptions, onDetailChange, projectId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadMetadata()
      .then(() => { if (!cancelled) setError(null); })
      .catch((caught) => { if (!cancelled) setError(errorMessage(caught)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [loadMetadata]);

  const selectFile = (file: File | null) => {
    setError(null);
    setNotice(null);
    if (!file) {
      setSelectedFile(null);
      return;
    }
    const extension = fileExtension(file.name);
    if (options && !options.allowed_extensions.includes(extension)) {
      setSelectedFile(null);
      setError("仅支持 WAV、MP3、FLAC、OGG、M4A 和 AAC 音频。");
      return;
    }
    if (options && file.size > options.max_file_size_bytes) {
      setSelectedFile(null);
      setError("音乐文件超过允许的大小限制。");
      return;
    }
    setSelectedFile(file);
  };

  const submitUpload = async () => {
    if (!selectedFile || !options || uploadGuard.current || uploading) return;
    uploadGuard.current = true;
    setUploading(true);
    setError(null);
    setNotice(null);
    const targetVersion = options.next_version;
    try {
      await uploadMusic(projectId, selectedFile, {
        expected_active_version: options.active_version,
        expected_next_version: targetVersion,
      });
      await refresh();
      setSelectedFile(null);
      if (fileInput.current) fileInput.current.value = "";
      setNotice(`Music ${versionLabel(targetVersion)} 已上传并成为 active。`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      uploadGuard.current = false;
      setUploading(false);
    }
  };

  const changeMix = <K extends EditableMixKey,>(field: K, value: EditableMix[K]) => {
    setMixDraft((currentDraft) => currentDraft ? { ...currentDraft, [field]: value } : currentDraft);
    setDirtyMix((currentDirty) => new Set(currentDirty).add(field));
    setNotice(null);
  };

  const saveMix = async () => {
    if (!mixDraft || dirtyMix.size === 0 || savingMix) return;
    const payload: MusicMixUpdateRequest = {};
    dirtyMix.forEach((field) => {
      Object.assign(payload, { [field]: mixDraft[field] });
    });
    const versionBefore = current.version;
    setSavingMix(true);
    setError(null);
    setNotice(null);
    try {
      const result = await updateMusicMix(projectId, payload);
      if (result.data.version !== versionBefore) {
        throw new ApiClientError({
          message: "Mix 修改意外改变了 Music 版本。",
          code: "INVALID_RESPONSE",
        });
      }
      await refresh();
      setNotice(`Music ${versionLabel(versionBefore)} 的 Mix 已保存，音乐版本未变。`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSavingMix(false);
    }
  };

  const resetMix = async () => {
    if (savingMix) return;
    const versionBefore = current.version;
    setSavingMix(true);
    setError(null);
    setNotice(null);
    try {
      const result = await resetMusicMix(projectId);
      if (result.data.version !== versionBefore) {
        throw new ApiClientError({
          message: "Mix Reset 意外改变了 Music 版本。",
          code: "INVALID_RESPONSE",
        });
      }
      await refresh();
      setNotice(`Music ${versionLabel(versionBefore)} 的 Mix 已重置，音乐版本未变。`);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSavingMix(false);
    }
  };

  return (
    <div className="music-management-action">
      <div className="postproduction-title-row">
        <h3>当前背景音乐</h3>
        <strong>Music {versionLabel(current.version)}</strong>
      </div>

      {current.version === null ? (
        <p className="postproduction-empty-copy">尚未添加背景音乐。</p>
      ) : (
        <>
          <dl className="postproduction-facts">
            <div><dt>正式版本</dt><dd>{versionLabel(current.version)}</dd></div>
            <div><dt>格式</dt><dd>{current.format?.toUpperCase() ?? "未记录"}</dd></div>
            <div><dt>素材时长</dt><dd>{secondsLabel(current.duration_seconds)}</dd></div>
          </dl>
          <div className="postproduction-media-card">
            <h3>当前音乐</h3>
            {current.audio_available ? (
              <audio controls preload="metadata" src={getMusicAudioUrl(projectId)} />
            ) : <p className="media-unavailable">音频文件不可用</p>}
          </div>
        </>
      )}

      <div className="postproduction-subsection music-upload-panel">
        <h3>{current.version === null ? "添加背景音乐" : "替换背景音乐"}</h3>
        <p className="stage-readonly-note">
          支持 WAV / MP3 / FLAC / OGG / M4A / AAC。文件只会上传到受控的本地处理区。
        </p>
        <label className="music-file-field">
          <span>选择音乐文件</span>
          <input
            ref={fileInput}
            type="file"
            accept={MUSIC_ACCEPT}
            disabled={loading || uploading}
            onChange={(event) => selectFile(event.target.files?.[0] ?? null)}
          />
        </label>
        {selectedFile && (
          <dl className="music-selected-file">
            <div><dt>文件名</dt><dd>{displayFilename(selectedFile.name)}</dd></div>
            <div><dt>大小</dt><dd>{fileSizeLabel(selectedFile.size)}</dd></div>
            <div><dt>格式</dt><dd>{fileExtension(selectedFile.name).toUpperCase()}</dd></div>
          </dl>
        )}
        {current.version !== null && options && (
          <p className="action-warning" role="status">
            将创建 Music {versionLabel(options.next_version)}，当前版本会保留在历史记录中。
          </p>
        )}
        <button
          className="primary-button"
          type="button"
          disabled={loading || uploading || !selectedFile || !options}
          onClick={() => { void submitUpload(); }}
        >
          {uploading ? "正在上传背景音乐…" : current.version === null ? "上传音乐" : "上传替换版本"}
        </button>
      </div>

      {current.version !== null && mixDraft && (
        <div className="postproduction-subsection music-mix-editor">
          <h3>Music Mix</h3>
          <label>
            <span>基础音量 <strong>{Math.round(mixDraft.base_volume * 100)}%</strong></span>
            <input
              aria-label="基础音量"
              type="range"
              min="0"
              max="100"
              value={Math.round(mixDraft.base_volume * 100)}
              onChange={(event) => changeMix("base_volume", Number(event.target.value) / 100)}
            />
          </label>
          <label className="music-checkbox-field">
            <input
              type="checkbox"
              checked={mixDraft.ducking_enabled}
              onChange={(event) => changeMix("ducking_enabled", event.target.checked)}
            />
            <span>开启 Ducking</span>
          </label>
          <p className="stage-readonly-note">
            旁白期间背景音乐降低至基础音量的 {Math.round(mixDraft.ducking_ratio * 100)}%。实际处理在最终导出时执行。
          </p>
          <label>
            <span>Ducking Ratio <strong>{Math.round(mixDraft.ducking_ratio * 100)}%</strong></span>
            <input
              aria-label="Ducking Ratio"
              type="range"
              min="0"
              max="100"
              value={Math.round(mixDraft.ducking_ratio * 100)}
              onChange={(event) => changeMix("ducking_ratio", Number(event.target.value) / 100)}
            />
          </label>
          <div className="music-time-grid">
            {([
              ["duck_attack_seconds", "Attack"],
              ["duck_release_seconds", "Release"],
              ["fade_in_seconds", "Fade In"],
              ["fade_out_seconds", "Fade Out"],
            ] as const).map(([field, label]) => (
              <label key={field}>
                <span>{label} (秒)</span>
                <input
                  aria-label={label}
                  type="number"
                  min="0"
                  step="0.05"
                  value={mixDraft[field]}
                  onChange={(event) => changeMix(field, Number(event.target.value))}
                />
              </label>
            ))}
          </div>
          <p className="music-loop-unavailable">循环播放：当前不支持</p>
          <div className="music-mix-actions">
            <button
              className="primary-button"
              type="button"
              disabled={savingMix || dirtyMix.size === 0}
              onClick={() => { void saveMix(); }}
            >
              {savingMix ? "正在保存 Mix…" : "保存 Mix"}
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={savingMix}
              onClick={() => { void resetMix(); }}
            >
              Reset Mix
            </button>
          </div>
        </div>
      )}

      {notice && <p className="action-success" role="status">{notice}</p>}
      {error && <p className="action-error" role="alert">{error}</p>}

      <div className="postproduction-subsection music-history">
        <h3>历史版本</h3>
        {history?.versions.length ? (
          <ul>
            {history.versions.map((version) => (
              <li key={version.version}>
                <div>
                  <strong>Music {versionLabel(version.version)}</strong>
                  <span>{version.format?.toUpperCase() ?? "未记录"} · {secondsLabel(version.duration_seconds)}</span>
                  {version.is_active && <em>当前 active</em>}
                </div>
                {version.audio_available ? (
                  <audio
                    aria-label={`Music ${versionLabel(version.version)} 历史音频`}
                    controls
                    preload="metadata"
                    src={getMusicVersionAudioUrl(projectId, version.version)}
                  />
                ) : <span className="media-unavailable">音频不可用</span>}
              </li>
            ))}
          </ul>
        ) : <p className="postproduction-empty-copy">暂无历史版本。</p>}
      </div>

      {current.version !== null && (
        <p className="stage-readonly-note">
          更换音乐或修改混音设置后，已有最终导出可能需要重新生成。
        </p>
      )}
    </div>
  );
}
