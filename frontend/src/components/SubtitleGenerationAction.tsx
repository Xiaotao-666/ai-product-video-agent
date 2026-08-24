import { useCallback, useEffect, useRef, useState } from "react";

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


interface SubtitleGenerationActionProps {
  projectId: string;
  detail: SubtitleDetail;
  onDetailChange: (detail: SubtitleDetail) => void;
}

const SOURCE_LABELS: Record<string, string> = {
  active_voice: "Active Voice 旁白字幕",
  compiled_storyboard: "Legacy Storyboard 屏幕文案",
  voice_script: "Voice Script",
};

const TIMING_LABELS: Record<string, string> = {
  compiled_storyboard_global_timeline: "Global AV Timeline",
  "audio.wav": "Voice WAV Duration",
  voice_audio_duration: "Voice WAV 绝对时轴",
};

function versionLabel(version: number | null): string {
  return version === null ? "尚无" : `v${String(version).padStart(3, "0")}`;
}

function sourceLabel(source: string | null): string {
  return source ? SOURCE_LABELS[source] ?? source : "未记录";
}

function timingLabel(source: string | null): string {
  return source ? TIMING_LABELS[source] ?? source : "未记录";
}

function secondsLabel(value: number | null): string {
  return value == null ? "未记录" : `${value.toFixed(3)}s`;
}

function semanticLabel(value: string | null): string {
  if (value === "NARRATION_CAPTION") return "旁白字幕";
  if (value === "LEGACY_SCREEN_TEXT") return "屏幕文案（旧语义）";
  return value ?? "未记录";
}

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) return "字幕操作失败，请重试。";
  return {
    PROJECT_BUSY: "项目当前正在执行其他操作，请稍后重试。",
    PROJECT_NOT_READY: "项目尚未完成可用的 Assembly。",
    SUBTITLE_SOURCE_UNAVAILABLE: "当前没有可用的字幕来源。",
    ACTIVE_VOICE_REQUIRED: "请先完成并激活一个 Voice 版本。",
    SUBTITLE_NOT_APPLICABLE: "当前项目未启用旁白，不适用旁白字幕。",
    SUBTITLE_SOURCE_CHANGED: "Active Voice 已变更，请刷新后重试。",
    SUBTITLE_SOURCE_INVALID: "当前字幕来源无法安全读取。",
    SUBTITLE_GENERATION_FAILED: "字幕未能安全生成，旧版本保持不变。",
    ACTION_NOT_ALLOWED: "字幕版本状态已变化，请刷新后重试。",
    NETWORK_ERROR: "无法连接本地 Backend。",
  }[error.code] ?? "字幕操作失败，请重试。";
}

function CueList({ detail }: { detail: SubtitleDetail }) {
  if (!detail.content_available) {
    return <p className="media-unavailable">字幕文件不可用</p>;
  }
  return (
    <ol className="subtitle-cue-list">
      {detail.cues.map((cue) => (
        <li key={`${cue.index}-${cue.start}`}>
          <span>{cue.start} → {cue.end}</span>
          <p>{cue.text}</p>
        </li>
      ))}
    </ol>
  );
}

export function SubtitleGenerationAction({
  projectId,
  detail,
  onDetailChange,
}: SubtitleGenerationActionProps) {
  const [current, setCurrent] = useState(detail);
  const [options, setOptions] = useState<SubtitleOptionsResponse | null>(null);
  const [history, setHistory] = useState<SubtitleHistoryResponse | null>(null);
  const [historyDetail, setHistoryDetail] = useState<SubtitleDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const submitGuard = useRef(false);

  useEffect(() => {
    setCurrent(detail);
  }, [detail]);

  const loadMetadata = useCallback(async () => {
    const [optionsResult, historyResult] = await Promise.all([
      getSubtitleOptions(projectId),
      getSubtitleHistory(projectId),
    ]);
    setOptions(optionsResult.data);
    setHistory(historyResult.data);
  }, [projectId]);

  const refresh = useCallback(async () => {
    const [detailResult, optionsResult, historyResult] = await Promise.all([
      getSubtitle(projectId),
      getSubtitleOptions(projectId),
      getSubtitleHistory(projectId),
    ]);
    setCurrent(detailResult.data);
    setOptions(optionsResult.data);
    setHistory(historyResult.data);
    setHistoryDetail(null);
    onDetailChange(detailResult.data);
  }, [onDetailChange, projectId]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadMetadata()
      .then(() => {
        if (!cancelled) setError(null);
      })
      .catch((loadError) => {
        if (!cancelled) setError(errorMessage(loadError));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [loadMetadata]);

  const submit = async () => {
    if (submitGuard.current || submitting || !options?.ready) return;
    submitGuard.current = true;
    setSubmitting(true);
    setError(null);
    setNotice(null);
    try {
      const payload = {
        expected_active_version: options.active_version,
        expected_next_version: options.next_version,
        expected_voice_version: options.source!.voice_version!,
      };
      if (current.version === null) {
        await generateSubtitle(projectId, payload);
      } else {
        await regenerateSubtitle(projectId, payload);
      }
      await refresh();
      setNotice(`Subtitle ${versionLabel(options.next_version)} 已生成。`);
    } catch (actionError) {
      setError(errorMessage(actionError));
    } finally {
      submitGuard.current = false;
      setSubmitting(false);
    }
  };

  const inspectHistory = async (version: number) => {
    setHistoryLoading(true);
    setError(null);
    try {
      const result = await getSubtitleVersion(projectId, version);
      setHistoryDetail(result.data);
    } catch (historyError) {
      setError(errorMessage(historyError));
    } finally {
      setHistoryLoading(false);
    }
  };

  return (
    <div className="subtitle-generation-action">
      <div className="postproduction-title-row">
        <h3>当前正式字幕</h3>
        <strong>{versionLabel(current.version)}</strong>
      </div>

      {current.version === null ? (
        <p className="postproduction-empty-copy">字幕未生成。</p>
      ) : (
        <>
          <dl className="postproduction-facts">
            <div><dt>来源</dt><dd>{sourceLabel(current.source)}</dd></div>
            <div><dt>语义</dt><dd>{semanticLabel(current.semantic_type)}</dd></div>
            <div><dt>Timing</dt><dd>{timingLabel(current.timing_source)}</dd></div>
            <div><dt>Cue 数量</dt><dd>{current.cue_count}</dd></div>
            <div><dt>Voice 来源</dt><dd>{versionLabel(current.source_voice_version)}</dd></div>
            <div><dt>Voice 时长</dt><dd>{secondsLabel(current.actual_audio_duration)}</dd></div>
            <div><dt>Voice 起点</dt><dd>{secondsLabel(current.voice_track_start)}</dd></div>
          </dl>
          <CueList detail={current} />
        </>
      )}

      <div className="postproduction-subsection subtitle-preparation">
        <h3>Subtitle Preparation</h3>
        <p className="stage-readonly-note">
          字幕将根据当前正式配音生成，与实际旁白内容保持一致。
        </p>
        {loading && <p role="status">正在读取字幕来源…</p>}
        {!loading && options?.source && (
          <>
            <dl className="postproduction-facts">
              <div><dt>本次语义</dt><dd>{semanticLabel(options.source.semantic_type)}</dd></div>
              <div><dt>来源</dt><dd>{options.source.label}</dd></div>
              <div><dt>Next Version</dt><dd>{versionLabel(options.next_version)}</dd></div>
              <div><dt>Cue 数量</dt><dd>{options.source.cue_count}</dd></div>
              <div><dt>Timing</dt><dd>{timingLabel(options.source.timing_source)}</dd></div>
              <div><dt>Voice 时长</dt><dd>{secondsLabel(options.source.actual_audio_duration)}</dd></div>
              <div><dt>Voice 起点</dt><dd>{secondsLabel(options.source.voice_track_start)}</dd></div>
            </dl>
            <div className="subtitle-script-preview">
              <h4>Active Voice Script</h4>
              <p>{options.source.script}</p>
            </div>
          </>
        )}
        {!loading && options?.stale && (
          <p className="action-warning" role="status">
            {options.stale_reason === "LEGACY_SCREEN_TEXT"
              ? "当前 active Subtitle 是 Legacy 屏幕文案，需要手动生成旁白字幕。"
              : "Active Voice 已变更，当前 Subtitle 已过期，需要手动重新生成。"}
          </p>
        )}
        {!loading && options && !options.ready && (
          <ul className="subtitle-issue-list" role="alert">
            {options.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}
          </ul>
        )}
        {current.version !== null && options?.ready && (
          <p className="stage-readonly-note">
            将创建新的 Subtitle {versionLabel(options.next_version)}，当前版本会保留在历史中。
          </p>
        )}
        <button
          className="primary-button"
          type="button"
          disabled={loading || submitting || !options?.ready}
          onClick={() => { void submit(); }}
        >
          {submitting
            ? "正在生成字幕…"
            : current.version === null ? "生成字幕" : "重新生成字幕"}
        </button>
      </div>

      {notice && <p className="action-success" role="status">{notice}</p>}
      {error && <p className="action-error" role="alert">{error}</p>}

      <div className="postproduction-subsection">
        <h3>历史版本</h3>
        {history?.versions.length ? (
          <ul className="subtitle-history-list">
            {history.versions.map((version) => (
              <li key={version.version}>
                <div>
                  <strong>Subtitle {versionLabel(version.version)}</strong>
                  <span>
                    {semanticLabel(version.semantic_type)}
                    {version.source_voice_version !== null
                      ? ` · Voice ${versionLabel(version.source_voice_version)}`
                      : ""}
                    {` · ${sourceLabel(version.source)} · ${timingLabel(version.timing_source)}`}
                  </span>
                  {version.is_active && <em>当前 active</em>}
                </div>
                <button
                  className="secondary-button"
                  type="button"
                  disabled={historyLoading}
                  onClick={() => { void inspectHistory(version.version); }}
                >
                  查看 Cue
                </button>
              </li>
            ))}
          </ul>
        ) : <p className="postproduction-empty-copy">暂无历史版本。</p>}
        {historyDetail && (
          <div className="subtitle-history-preview">
            <h4>Subtitle {versionLabel(historyDetail.version)} Cue Preview</h4>
            <CueList detail={historyDetail} />
          </div>
        )}
      </div>
    </div>
  );
}
