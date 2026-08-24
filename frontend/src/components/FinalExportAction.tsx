import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  executeFinalExport,
  getExport,
  getExportHistory,
  getExportVideoUrl,
  getExportVersionVideoUrl,
  getProjectTasks,
  getProjectWorkflow,
  preflightFinalExport,
} from "../api/client";
import type {
  ExportDetail,
  ExportHistoryResponse,
  FinalExportPreflightResponse,
  MusicMixDetail,
  TaskRecord,
} from "../api/types";
import {
  isActiveTaskStatus,
  useProjectTaskPolling,
} from "../hooks/useProjectTaskPolling";


interface FinalExportActionProps {
  projectId: string;
  detail: ExportDetail;
  onDetailChange: (detail: ExportDetail) => void;
}

const STALE_REASON_LABELS: Record<string, string> = {
  ASSEMBLY_CHANGED: "合片版本已更新",
  VOICE_CHANGED: "配音已更新",
  SUBTITLE_CHANGED: "字幕已更新",
  MUSIC_CHANGED: "背景音乐已更新",
  MUSIC_MIX_CHANGED: "混音设置已更新",
  EXPORT_INPUT_UNKNOWN: "旧导出缺少完整输入记录",
};

const TASK_STATUS_LABELS: Record<TaskRecord["status"], string> = {
  QUEUED: "最终导出任务排队中…",
  RUNNING: "正在导出最终视频…",
  SUCCEEDED: "最终导出已完成",
  FAILED: "最终导出失败",
  INTERRUPTED: "上次最终导出任务中断",
  CANCELLED: "最终导出任务已取消",
};

function versionLabel(value: number | null): string {
  return value === null ? "无" : `v${String(value).padStart(3, "0")}`;
}

function seconds(value: number | null): string {
  return value === null
    ? "未记录"
    : `${value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}s`;
}

function percent(value: number | null): string {
  return value === null ? "未记录" : `${Math.round(value * 100)}%`;
}

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) return "最终导出操作失败，请重试。";
  return {
    EXPORT_CONFIRMATION_REQUIRED: "必须明确确认本地最终导出。",
    EXPORT_PREFLIGHT_STALE: "导出输入已变化，请重新检查后再次确认。",
    EXPORT_NOT_READY: "当前输入尚不能执行最终导出。",
    EXPORT_ALREADY_CURRENT: "当前最终视频已经是最新版本。",
    PROJECT_BUSY: "项目当前正在执行其他任务，请稍后重试。",
    TASK_RUNNER_UNAVAILABLE: "本地任务服务暂时不可用。",
    ACCEPTED_TASK_STATUS_UNREADABLE: "导出请求已经被 Backend 接受，但任务状态暂时不可读。请勿重复提交。",
    NETWORK_ERROR: "无法连接本地 Backend。",
  }[error.code] ?? "最终导出操作失败，请重试。";
}

function MixSummary({ mix }: { mix: MusicMixDetail | null }) {
  if (!mix) return <p className="postproduction-empty-copy">无 Music Mix</p>;
  return (
    <dl className="postproduction-facts final-export-mix-summary">
      <div><dt>Base Volume</dt><dd>{percent(mix.base_volume)}</dd></div>
      <div><dt>Ducking</dt><dd>{mix.ducking_enabled ? "开启" : "关闭"}</dd></div>
      <div><dt>Ratio</dt><dd>{percent(mix.ducking_ratio)}</dd></div>
      <div><dt>Attack</dt><dd>{seconds(mix.duck_attack_seconds)}</dd></div>
      <div><dt>Release</dt><dd>{seconds(mix.duck_release_seconds)}</dd></div>
      <div><dt>Fade In</dt><dd>{seconds(mix.fade_in_seconds)}</dd></div>
      <div><dt>Fade Out</dt><dd>{seconds(mix.fade_out_seconds)}</dd></div>
    </dl>
  );
}

export function FinalExportAction({
  projectId,
  detail,
  onDetailChange,
}: FinalExportActionProps) {
  const [current, setCurrent] = useState(detail);
  const [preflight, setPreflight] = useState<FinalExportPreflightResponse | null>(null);
  const [history, setHistory] = useState<ExportHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [acceptedStatusUncertain, setAcceptedStatusUncertain] = useState(false);
  const [acceptedCorrelationId, setAcceptedCorrelationId] = useState<string | null>(null);
  const [acceptedTargetId, setAcceptedTargetId] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const submitGuard = useRef(false);

  useEffect(() => {
    setCurrent(detail);
  }, [detail]);

  const isFinalExportTask = useCallback(
    (candidate: TaskRecord) => candidate.operation === "FINAL_EXPORT",
    [],
  );

  const loadPreparation = useCallback(async () => {
    const [prepared, loadedHistory] = await Promise.all([
      preflightFinalExport(projectId),
      getExportHistory(projectId),
    ]);
    setPreflight(prepared.data);
    setHistory(loadedHistory.data);
    return prepared.data;
  }, [projectId]);

  const refresh = useCallback(async () => {
    const [exportResult, prepared, loadedHistory] = await Promise.all([
      getExport(projectId),
      preflightFinalExport(projectId),
      getExportHistory(projectId),
      getProjectWorkflow(projectId),
    ]);
    setCurrent(exportResult.data);
    onDetailChange(exportResult.data);
    setPreflight(prepared.data);
    setHistory(loadedHistory.data);
    setConfirmOpen(false);
  }, [onDetailChange, projectId]);

  const {
    task,
    setTask,
    error: taskReadError,
    active,
    terminalRefreshPending,
    attachToExistingTask,
  } = useProjectTaskPolling({
    projectId,
    isTask: isFinalExportTask,
    onTerminalRefresh: refresh,
    recoverLatestTerminalTask: true,
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    loadPreparation()
      .then(() => { if (!cancelled) setActionError(null); })
      .catch((error) => { if (!cancelled) setActionError(errorMessage(error)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [loadPreparation]);

  const blocked = active || submitting || acceptedStatusUncertain;

  async function recheck() {
    if (checking || blocked) return;
    setChecking(true);
    setActionError(null);
    setConfirmOpen(false);
    try {
      await loadPreparation();
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setChecking(false);
    }
  }

  async function confirmExport() {
    if (
      submitGuard.current
      || blocked
      || !preflight?.ready
      || !preflight.execution_required
      || !preflight.confirmation_token
    ) return;
    submitGuard.current = true;
    setSubmitting(true);
    setActionError(null);
    const targetId = `export_v${String(preflight.next_export_version).padStart(3, "0")}`;
    try {
      const result = await executeFinalExport(
        projectId,
        {
          confirmation_token: preflight.confirmation_token,
          confirm_local_export: true,
        },
        preflight.next_export_version,
      );
      if (result.data.operation !== "FINAL_EXPORT" || result.data.target_id !== targetId) {
        throw new ApiClientError({
          message: "Final Export Task 与确认版本不匹配。",
          code: "INVALID_RESPONSE",
          requestAccepted: true,
        });
      }
      setTask(result.data);
      setConfirmOpen(false);
    } catch (error) {
      if (
        error instanceof ApiClientError
        && (error.requestAccepted || error.code === "ACCEPTED_TASK_STATUS_UNREADABLE")
      ) {
        setAcceptedStatusUncertain(true);
        setAcceptedCorrelationId(error.correlationId);
        setAcceptedTargetId(targetId);
        setConfirmOpen(false);
      } else if (
        error instanceof ApiClientError
        && error.code === "PROJECT_BUSY"
        && await attachToExistingTask().catch(() => false)
      ) {
        setConfirmOpen(false);
      } else {
        if (error instanceof ApiClientError && error.code === "EXPORT_PREFLIGHT_STALE") {
          setPreflight(null);
          setConfirmOpen(false);
        }
        setActionError(errorMessage(error));
      }
    } finally {
      submitGuard.current = false;
      setSubmitting(false);
    }
  }

  async function recheckAcceptedTask() {
    if (!acceptedStatusUncertain || reconciling) return;
    setReconciling(true);
    try {
      const result = await getProjectTasks(projectId);
      const matches = result.data.tasks.filter((candidate) =>
        candidate.operation === "FINAL_EXPORT"
        && candidate.target_id === acceptedTargetId
        && (
          acceptedCorrelationId === null
          || candidate.correlation_id === acceptedCorrelationId
        ));
      if (matches.length !== 1) return;
      setTask(matches[0]);
      setAcceptedStatusUncertain(false);
      setAcceptedCorrelationId(null);
      setAcceptedTargetId(null);
      setActionError(null);
      if (!isActiveTaskStatus(matches[0].status)) await refresh();
    } catch {
      // Keep execution locked; reconciliation remains GET-only.
    } finally {
      setReconciling(false);
    }
  }

  return (
    <section className="final-export-action" aria-label="Final Export Preparation">
      <div className="postproduction-title-row">
        <h3>最终导出</h3>
        <strong>{current.version === null ? "尚未导出" : `Final Export ${versionLabel(current.version)}`}</strong>
      </div>

      {current.version !== null && (
        <div className="postproduction-media-card final-export-current">
          <div className="postproduction-title-row">
            <h3>当前最终视频</h3>
            <span>{current.stale ? "需要重新导出" : "当前版本"}</span>
          </div>
          {current.video_available ? (
            <video controls preload="metadata" src={getExportVideoUrl(projectId)} />
          ) : <p className="media-unavailable">最终视频不可用</p>}
        </div>
      )}

      {task && (
        <div className="assembly-task-status" role="status">
          <strong>{TASK_STATUS_LABELS[task.status]}</strong>
          <span>{task.target_id ?? "Final Export"}</span>
          {terminalRefreshPending && <span>正在刷新 Export Bundle 与 Workflow…</span>}
          {task.status === "INTERRUPTED" && (
            <span>不会自动重新执行 FFmpeg。请重新检查当前输入并再次确认。</span>
          )}
          {task.error && <span>{task.error.message}</span>}
          {(task.status === "FAILED" || task.status === "INTERRUPTED") && !active && (
            <button className="secondary-button" type="button" disabled={checking} onClick={() => { void recheck(); }}>
              {checking ? "正在重新检查…" : "重新检查当前输入"}
            </button>
          )}
        </div>
      )}

      {taskReadError && (
        <p className="postproduction-error" role="alert">无法读取 Final Export Task 状态。</p>
      )}
      {acceptedStatusUncertain && (
        <div className="postproduction-error" role="alert">
          <p>导出请求已被 Backend 接受，但任务状态暂时不可读。执行入口已锁定，避免重复 FFmpeg。</p>
          <button className="secondary-button" type="button" disabled={reconciling} onClick={() => { void recheckAcceptedTask(); }}>
            {reconciling ? "正在重新读取…" : "重新读取任务状态"}
          </button>
        </div>
      )}

      {loading ? (
        <p className="postproduction-empty-copy">正在准备 Final Export…</p>
      ) : preflight ? (
        <section className={`preflight-summary ${preflight.ready ? "preflight-summary-ready" : "preflight-summary-not-ready"}`}>
          <p className="page-kicker">FINAL EXPORT PREFLIGHT</p>
          <h3>
            {!preflight.ready
              ? "当前无法导出"
              : preflight.execution_required
                ? "最终视频需要导出"
                : "最终视频已经是最新版本"}
          </h3>

          <div className="postproduction-subsection">
            <h3>当前输入</h3>
            <ul className="component-version-list component-version-grid">
              <li><span>Assembly</span><strong>{versionLabel(preflight.inputs.assembly_version)}</strong></li>
              <li><span>Voice</span><strong>{versionLabel(preflight.inputs.voice_version)}</strong></li>
              <li><span>Subtitle</span><strong>{versionLabel(preflight.inputs.subtitle_version)}</strong></li>
              <li><span>Music</span><strong>{versionLabel(preflight.inputs.music_version)}</strong></li>
            </ul>
          </div>

          <div className="postproduction-subsection">
            <h3>Music Mix</h3>
            <MixSummary mix={preflight.music_mix} />
          </div>

          <div className="postproduction-subsection final-export-lineage-grid">
            <div>
              <h3>Voice Timing</h3>
              <dl className="postproduction-facts">
                <div><dt>状态</dt><dd>{preflight.voice_timing.status}</dd></div>
                <div><dt>已接受</dt><dd>{preflight.voice_timing.accepted ? "是" : "否"}</dd></div>
                <div><dt>轨道开始</dt><dd>{seconds(preflight.voice_timing.track_start)}</dd></div>
                <div><dt>实际结束</dt><dd>{seconds(preflight.voice_timing.actual_end)}</dd></div>
              </dl>
            </div>
            <div>
              <h3>Subtitle Lineage</h3>
              <dl className="postproduction-facts">
                <div><dt>语义</dt><dd>{preflight.subtitle.semantic_type ?? "无字幕"}</dd></div>
                <div><dt>Source Voice</dt><dd>{versionLabel(preflight.subtitle.source_voice_version)}</dd></div>
                <div><dt>Voice 对齐</dt><dd>{preflight.subtitle.voice_aligned === null ? "不适用" : preflight.subtitle.voice_aligned ? "一致" : "不一致"}</dd></div>
              </dl>
            </div>
          </div>

          {preflight.stale_reasons.length > 0 && (
            <div className="stale-warning" role="status">
              <strong>最终视频需要重新导出</strong>
              <ul>
                {preflight.stale_reasons.map((reason) => (
                  <li key={reason}>{STALE_REASON_LABELS[reason] ?? reason}</li>
                ))}
              </ul>
            </div>
          )}

          {preflight.issues.length > 0 && (
            <ul className="generation-issues-list final-export-issues">
              {preflight.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}
            </ul>
          )}

          {preflight.ready && !preflight.execution_required && (
            <p className="action-success" role="status">
              当前 Final Export {versionLabel(preflight.existing_export_version)} 与全部输入一致，无需重新导出。
            </p>
          )}

          {preflight.ready && preflight.execution_required && preflight.confirmation_token && (
            <button className="primary-button" type="button" disabled={blocked} onClick={() => setConfirmOpen(true)}>
              执行最终导出
            </button>
          )}
        </section>
      ) : (
        <button className="secondary-button" type="button" disabled={checking || blocked} onClick={() => { void recheck(); }}>
          {checking ? "正在重新检查…" : "重新检查最终导出"}
        </button>
      )}

      {confirmOpen && preflight?.ready && preflight.execution_required && preflight.confirmation_token && (
        <div className="generation-confirm-backdrop" role="presentation">
          <section className="generation-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="final-export-confirm-title">
            <p className="page-kicker">LOCAL FINAL EXPORT</p>
            <h3 id="final-export-confirm-title">确认执行最终导出</h3>
            <dl>
              <div><dt>将创建</dt><dd>Final Export {versionLabel(preflight.next_export_version)}</dd></div>
              <div><dt>Assembly</dt><dd>{versionLabel(preflight.inputs.assembly_version)}</dd></div>
              <div><dt>Voice</dt><dd>{versionLabel(preflight.inputs.voice_version)}</dd></div>
              <div><dt>Subtitle</dt><dd>{versionLabel(preflight.inputs.subtitle_version)}</dd></div>
              <div><dt>Music</dt><dd>{versionLabel(preflight.inputs.music_version)}</dd></div>
            </dl>
            <MixSummary mix={preflight.music_mix} />
            <p className="local-export-warning">
              最终导出会在本机执行 FFmpeg，可能需要一些时间和磁盘空间。不会调用外部 Provider，也不会产生外部 API 费用。
            </p>
            <div className="generation-confirm-actions">
              <button className="secondary-button" type="button" disabled={submitting} onClick={() => setConfirmOpen(false)}>取消</button>
              <button className="primary-button" type="button" disabled={submitting || acceptedStatusUncertain} onClick={() => { void confirmExport(); }}>
                {submitting ? "正在提交…" : "确认并导出"}
              </button>
            </div>
          </section>
        </div>
      )}

      {actionError && <p className="postproduction-error" role="alert">{actionError}</p>}

      <div className="postproduction-subsection final-export-history">
        <h3>Export Version 历史</h3>
        {history?.versions.length ? (
          <div className="assembly-version-history">
            {history.versions.map((version) => (
              <article key={version.version} className="postproduction-media-card">
                <div className="postproduction-title-row">
                  <h3>Final Export {versionLabel(version.version)}</h3>
                  <span>{version.is_active ? "当前 active" : version.stale ? "历史 · stale" : "历史版本"}</span>
                </div>
                <ul className="component-version-list component-version-grid">
                  <li><span>Assembly</span><strong>{versionLabel(version.assembly_version)}</strong></li>
                  <li><span>Voice</span><strong>{versionLabel(version.voice_version)}</strong></li>
                  <li><span>Subtitle</span><strong>{versionLabel(version.subtitle_version)}</strong></li>
                  <li><span>Music</span><strong>{versionLabel(version.music_version)}</strong></li>
                </ul>
                {version.video_available && (
                  <video
                    aria-label={`Final Export ${versionLabel(version.version)} 历史视频`}
                    controls
                    preload="metadata"
                    src={getExportVersionVideoUrl(projectId, version.version)}
                  />
                )}
              </article>
            ))}
          </div>
        ) : <p className="postproduction-empty-copy">暂无 Export 历史版本。</p>}
      </div>
    </section>
  );
}
