import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  acceptVoiceTiming,
  ApiClientError,
  generateVoice,
  getProjectTasks,
  getVoice,
  getVoiceHistory,
  getVoiceOptions,
  getVoiceVersionAudioUrl,
  preflightVoice,
  regenerateVoice,
} from "../api/client";
import type {
  TaskRecord,
  VoiceDetail,
  VoiceHistoryResponse,
  VoiceIntent,
  VoiceOptionsResponse,
  VoicePreflightResponse,
} from "../api/types";
import {
  isActiveTaskStatus,
  toTaskActionError,
  useProjectTaskPolling,
} from "../hooks/useProjectTaskPolling";


interface VoiceGenerationActionProps {
  projectId: string;
  detail: VoiceDetail;
  onDetailChange: (detail: VoiceDetail) => void;
}

const SOURCE_LABELS: Record<string, string> = {
  compiled_storyboard: "Storyboard Planned",
  storyboard_edited: "Storyboard Edited for this Voice Version",
  manual: "Manual Script",
};

const TASK_STATUS_COPY: Record<TaskRecord["status"], string> = {
  QUEUED: "配音任务排队中…",
  RUNNING: "正在生成配音…",
  SUCCEEDED: "配音任务已完成",
  FAILED: "配音生成失败",
  INTERRUPTED: "上次配音任务已中断",
  CANCELLED: "配音任务已取消",
};

function versionLabel(version: number | null): string {
  return version === null ? "尚无" : `v${String(version).padStart(3, "0")}`;
}

function seconds(value: number | null): string {
  return value === null ? "未记录" : `${value.toFixed(3).replace(/0+$/, "").replace(/\.$/, "")}s`;
}

function sourceLabel(source: string | null): string {
  return source ? SOURCE_LABELS[source] ?? source : "未记录";
}

function errorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) return "配音操作失败，请重试。";
  return {
    PROJECT_BUSY: "项目当前正在执行其他任务，请稍后重试。",
    VOICE_PREFLIGHT_STALE: "配音条件已变化，请重新检查后再次确认。",
    VOICE_PROVIDER_UNAVAILABLE: "TTS Provider 配置或本次输入尚未就绪。",
    VOICE_INPUT_INVALID: "配音脚本或配置无效。",
    VOICE_EXTERNAL_CONFIRMATION_REQUIRED: "必须明确确认外部 TTS 调用。",
    TASK_RUNNER_UNAVAILABLE: "本地任务服务暂时不可用。",
    ACCEPTED_TASK_STATUS_UNREADABLE: "请求已经被 Backend 接受，但当前无法读取任务状态。请勿重复提交。",
    NETWORK_ERROR: "无法连接本地 Backend。",
  }[error.code] ?? "配音操作失败，请重试。";
}

export function VoiceGenerationAction({
  projectId,
  detail,
  onDetailChange,
}: VoiceGenerationActionProps) {
  const [options, setOptions] = useState<VoiceOptionsResponse | null>(null);
  const [history, setHistory] = useState<VoiceHistoryResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [preparing, setPreparing] = useState(detail.version === null);
  const [intent, setIntent] = useState<VoiceIntent>(detail.version === null ? "GENERATE" : "REGENERATE");
  const [providerId, setProviderId] = useState("");
  const [voice, setVoice] = useState("");
  const [language, setLanguage] = useState("zh-CN");
  const [script, setScript] = useState(detail.script ?? "");
  const [checking, setChecking] = useState(false);
  const [preflight, setPreflight] = useState<VoicePreflightResponse | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [acceptedStatusUncertain, setAcceptedStatusUncertain] = useState(false);
  const [acceptedCorrelationId, setAcceptedCorrelationId] = useState<string | null>(null);
  const [acceptedTargetId, setAcceptedTargetId] = useState<string | null>(null);
  const [reconciling, setReconciling] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [acceptingTiming, setAcceptingTiming] = useState(false);
  const submitGuard = useRef(false);
  const initialized = useRef(false);

  const isVoiceTask = useCallback(
    (candidate: TaskRecord) => candidate.operation === "VOICE_GENERATE",
    [],
  );

  const refresh = useCallback(async () => {
    const [voiceResult, optionsResult, historyResult] = await Promise.all([
      getVoice(projectId),
      getVoiceOptions(projectId),
      getVoiceHistory(projectId),
    ]);
    onDetailChange(voiceResult.data);
    setOptions(optionsResult.data);
    setHistory(historyResult.data);
    if (voiceResult.data.version !== null) {
      setPreparing(false);
      setPreflight(null);
      setConfirmOpen(false);
    }
  }, [onDetailChange, projectId]);

  const {
    task,
    setTask,
    error: taskReadError,
    setError: setTaskReadError,
    active,
    terminalRefreshPending,
    attachToExistingTask,
  } = useProjectTaskPolling({
    projectId,
    isTask: isVoiceTask,
    onTerminalRefresh: refresh,
    recoverLatestTerminalTask: true,
  });

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([getVoiceOptions(projectId), getVoiceHistory(projectId)])
      .then(([optionsResult, historyResult]) => {
        if (cancelled) return;
        const loaded = optionsResult.data;
        setOptions(loaded);
        setHistory(historyResult.data);
        if (!initialized.current) {
          initialized.current = true;
          setProviderId(loaded.default_provider ?? loaded.providers[0]?.provider_id ?? "");
          setVoice(detail.voice ?? loaded.default_voice ?? "");
          setLanguage(detail.language ?? loaded.default_language);
          setScript(detail.script ?? loaded.script?.text ?? "");
        }
        setLoadError(null);
      })
      .catch((error) => {
        if (!cancelled) setLoadError(errorMessage(error));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => { cancelled = true; };
  }, [detail.language, detail.script, detail.voice, projectId]);

  const selectedProvider = useMemo(
    () => options?.providers.find((item) => item.provider_id === providerId) ?? null,
    [options, providerId],
  );
  const blocked = active || submitting || acceptedStatusUncertain;

  function clearPreflight() {
    setPreflight(null);
    setConfirmOpen(false);
    setActionError(null);
  }

  function begin(intentValue: VoiceIntent) {
    setIntent(intentValue);
    setScript(
      intentValue === "REGENERATE"
        ? detail.script ?? options?.script?.text ?? ""
        : options?.script?.text ?? "",
    );
    setPreparing(true);
    clearPreflight();
  }

  function selectProvider(nextProviderId: string) {
    const provider = options?.providers.find((item) => item.provider_id === nextProviderId);
    setProviderId(nextProviderId);
    if (provider?.default_voice) setVoice(provider.default_voice);
    if (provider?.language) setLanguage(provider.language);
    clearPreflight();
  }

  async function runPreflight() {
    if (checking || submitGuard.current || blocked) return;
    submitGuard.current = true;
    setChecking(true);
    setActionError(null);
    try {
      const result = await preflightVoice(projectId, {
        intent,
        provider: providerId || null,
        voice,
        language,
        script_override: script.trim() || null,
      });
      setPreflight(result.data);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      submitGuard.current = false;
      setChecking(false);
    }
  }

  async function confirmGeneration() {
    if (
      submitGuard.current
      || blocked
      || !preflight?.ready
      || !preflight.preflight_fingerprint
      || !preflight.provider
      || !preflight.script
    ) return;
    submitGuard.current = true;
    setSubmitting(true);
    setActionError(null);
    const targetId = `voice_v${String(preflight.next_voice_version).padStart(3, "0")}`;
    try {
      const payload = {
        intent,
        provider: preflight.provider.provider_id,
        voice: preflight.provider.default_voice === voice
          ? preflight.provider.default_voice ?? voice
          : voice,
        language,
        script_override: script.trim(),
        preflight_fingerprint: preflight.preflight_fingerprint,
        confirm_external_tts_call: true,
      };
      const result = intent === "GENERATE"
        ? await generateVoice(projectId, payload, preflight.next_voice_version)
        : await regenerateVoice(projectId, payload, preflight.next_voice_version);
      if (
        result.data.operation !== "VOICE_GENERATE"
        || result.data.target_id !== targetId
      ) {
        throw new ApiClientError({
          message: "Voice Task 与已确认版本不匹配。",
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
        setActionError(errorMessage(error));
      } else if (
        error instanceof ApiClientError
        && error.code === "PROJECT_BUSY"
        && await attachToExistingTask().catch(() => false)
      ) {
        setConfirmOpen(false);
      } else {
        if (error instanceof ApiClientError && error.code === "VOICE_PREFLIGHT_STALE") {
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
        candidate.operation === "VOICE_GENERATE"
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
      // Keep external TTS submission locked until one matching task is readable.
    } finally {
      setReconciling(false);
    }
  }

  async function acceptTiming() {
    if (detail.version === null || acceptingTiming) return;
    setAcceptingTiming(true);
    setActionError(null);
    try {
      const result = await acceptVoiceTiming(projectId, detail.version);
      onDetailChange(result.data);
      const historyResult = await getVoiceHistory(projectId);
      setHistory(historyResult.data);
    } catch (error) {
      setActionError(errorMessage(error));
    } finally {
      setAcceptingTiming(false);
    }
  }

  if (loading) return <p className="postproduction-empty-copy">正在加载 Voice Generation…</p>;
  if (loadError || !options) {
    return <p className="postproduction-error" role="alert">{loadError ?? "无法读取 Voice Generation。"}</p>;
  }

  return (
    <section className="postproduction-subsection voice-generation-action" aria-label="Voice Generation">
      <div className="postproduction-title-row">
        <h3>Voice Generation</h3>
        <span>External TTS</span>
      </div>

      {task && (
        <div className="assembly-task-status" role="status">
          <strong>{TASK_STATUS_COPY[task.status]}</strong>
          <span>{task.target_id ? `目标 ${task.target_id.replace("voice_", "")}` : "Voice"}</span>
          {terminalRefreshPending && <span>正在刷新 Voice Bundle…</span>}
          {task.status === "INTERRUPTED" && (
            <span>无法确认上次外部 TTS 调用结果；不会自动重试，请检查当前 Voice 后重新确认。</span>
          )}
          {task.error && <span>{task.error.message}</span>}
        </div>
      )}
      {taskReadError && (
        <p className="postproduction-error" role="alert">
          无法读取配音任务状态{taskReadError.correlationId ? `（${taskReadError.correlationId}）` : ""}。
        </p>
      )}
      {acceptedStatusUncertain && (
        <div className="postproduction-error" role="alert">
          <p>生成请求已经被 Backend 接受，但任务状态暂时不可读。为避免重复费用，生成入口已锁定。</p>
          <button className="secondary-button" type="button" disabled={reconciling} onClick={() => void recheckAcceptedTask()}>
            {reconciling ? "正在重新读取…" : "重新读取任务状态"}
          </button>
        </div>
      )}

      {!preparing && detail.version !== null && !active && !acceptedStatusUncertain && (
        <button className="primary-button" type="button" onClick={() => begin("REGENERATE")}>
          重新生成新版本
        </button>
      )}

      {preparing && (
        <div className="voice-generation-form">
          <dl className="postproduction-facts">
            <div><dt>操作</dt><dd>{intent === "GENERATE" ? "生成配音" : "重新生成新版本"}</dd></div>
            <div><dt>将创建</dt><dd>{versionLabel(options.next_version)}</dd></div>
            <div><dt>默认脚本来源</dt><dd>{sourceLabel(options.script?.source ?? null)}</dd></div>
            <div><dt>计划 Voice Span</dt><dd>{seconds(options.planned_timing.span)}</dd></div>
          </dl>
          <label className="generation-select-label">
            TTS Provider
            <select value={providerId} disabled={blocked || checking} onChange={(event) => selectProvider(event.target.value)}>
              {options.providers.map((provider) => (
                <option key={provider.provider_id} value={provider.provider_id}>
                  {provider.display_name}{provider.available ? "" : " · 未配置"}
                </option>
              ))}
            </select>
          </label>
          {selectedProvider?.allowed_voices.length ? (
            <label className="generation-select-label">
              Voice
              <select value={voice} disabled={blocked || checking} onChange={(event) => { setVoice(event.target.value); clearPreflight(); }}>
                {selectedProvider.allowed_voices.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            </label>
          ) : (
            <label className="generation-select-label">
              Voice
              <input value={voice} disabled={blocked || checking} onChange={(event) => { setVoice(event.target.value); clearPreflight(); }} />
            </label>
          )}
          <label className="generation-select-label">
            Language
            {selectedProvider?.supported_languages.length ? (
              <select value={language} disabled={blocked || checking} onChange={(event) => { setLanguage(event.target.value); clearPreflight(); }}>
                {selectedProvider.supported_languages.map((item) => <option key={item} value={item}>{item}</option>)}
              </select>
            ) : (
              <input value={language} disabled={blocked || checking} onChange={(event) => { setLanguage(event.target.value); clearPreflight(); }} />
            )}
          </label>
          <label className="generation-select-label">
            本次 Voice Script
            <textarea
              aria-label="本次 Voice Script"
              rows={8}
              value={script}
              disabled={blocked || checking}
              onChange={(event) => { setScript(event.target.value); clearPreflight(); }}
            />
          </label>
          {options.manual_script_required && !script.trim() && (
            <p className="generation-inline-warning">Storyboard 没有 Voice Cue，请输入手动配音脚本。</p>
          )}
          <div className="generation-confirm-actions">
            {detail.version !== null && (
              <button className="secondary-button" type="button" disabled={blocked || checking} onClick={() => { setPreparing(false); clearPreflight(); }}>
                取消编辑
              </button>
            )}
            <button className="primary-button" type="button" disabled={blocked || checking || !script.trim() || !voice.trim()} onClick={() => void runPreflight()}>
              {checking ? "正在本地检查…" : "检查配音配置"}
            </button>
          </div>
          <p className="paid-call-warning">配置检查只做本地校验，不会连接 TTS Provider。</p>
        </div>
      )}

      {preflight && (
        <section className={`preflight-summary ${preflight.ready ? "preflight-summary-ready" : "preflight-summary-not-ready"}`} aria-label="配音生成前确认摘要">
          <p className="page-kicker">VOICE PREFLIGHT</p>
          <h3>{preflight.ready ? "配音配置检查通过" : "配音配置尚未就绪"}</h3>
          <dl className="postproduction-facts">
            <div><dt>将创建</dt><dd>{versionLabel(preflight.next_voice_version)}</dd></div>
            <div><dt>脚本来源</dt><dd>{sourceLabel(preflight.script?.source ?? null)}</dd></div>
            <div><dt>字符数</dt><dd>{preflight.script?.character_count ?? 0}</dd></div>
            <div><dt>Provider</dt><dd>{preflight.provider?.display_name ?? "未解析"}</dd></div>
            <div><dt>Model</dt><dd>{preflight.provider?.model ?? "未解析"}</dd></div>
            <div><dt>Voice</dt><dd>{voice}</dd></div>
            <div><dt>Language</dt><dd>{language}</dd></div>
            <div><dt>计划开始</dt><dd>{seconds(preflight.planned_timing.first_start)}</dd></div>
            <div><dt>计划结束</dt><dd>{seconds(preflight.planned_timing.last_end)}</dd></div>
          </dl>
          {preflight.issues.length > 0 && <ul className="generation-issues-list">{preflight.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}</ul>}
          {preflight.ready && (
            <button className="primary-button" type="button" disabled={blocked} onClick={() => setConfirmOpen(true)}>
              {intent === "GENERATE" ? "生成配音" : "生成新的 Voice 版本"}
            </button>
          )}
        </section>
      )}

      {confirmOpen && preflight?.ready && preflight.provider && preflight.script && (
        <div className="generation-confirm-backdrop" role="presentation">
          <section className="generation-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="voice-confirm-title">
            <p className="page-kicker">FINAL CONFIRMATION</p>
            <h3 id="voice-confirm-title">确认并生成配音？</h3>
            <dl>
              <div><dt>Voice Version</dt><dd>将创建 {versionLabel(preflight.next_voice_version)}</dd></div>
              <div><dt>Script 来源</dt><dd>{sourceLabel(preflight.script.source)}</dd></div>
              <div><dt>字符数</dt><dd>{preflight.script.character_count}</dd></div>
              <div><dt>Provider</dt><dd>{preflight.provider.display_name}</dd></div>
              <div><dt>Voice</dt><dd>{voice}</dd></div>
              <div><dt>Language</dt><dd>{language}</dd></div>
              <div><dt>Planned Timing</dt><dd>{seconds(preflight.planned_timing.first_start)} → {seconds(preflight.planned_timing.last_end)}</dd></div>
            </dl>
            <div className="voice-confirm-script"><strong>Script</strong><pre>{preflight.script.text}</pre></div>
            <p className="paid-call-warning"><strong>确认后将调用外部 TTS 服务，可能产生费用。</strong></p>
            <div className="generation-confirm-actions">
              <button className="secondary-button" type="button" disabled={submitting} onClick={() => setConfirmOpen(false)}>取消</button>
              <button className="primary-button" type="button" disabled={submitting || acceptedStatusUncertain} onClick={() => void confirmGeneration()}>
                {submitting ? "正在提交…" : "确认并生成配音"}
              </button>
            </div>
          </section>
        </div>
      )}

      {actionError && <p className="postproduction-error" role="alert">{actionError}</p>}

      {detail.calibration_status === "OUT_OF_TOLERANCE" && detail.version !== null && (
        <div className="postproduction-error" role="status">
          <p>当前时长超出建议范围。可以重新生成，或由 Core 校验后明确接受此 Timing。</p>
          {detail.timing_acceptance?.accepted ? (
            <strong>已接受当前 Timing</strong>
          ) : (
            <button className="secondary-button" type="button" disabled={acceptingTiming || active} onClick={() => void acceptTiming()}>
              {acceptingTiming ? "正在保存…" : "接受当前 Timing"}
            </button>
          )}
        </div>
      )}
      {detail.calibration_status === "OUT_OF_BOUNDS" && (
        <div className="postproduction-error" role="status">
          当前配音超出视频可用时间，不能直接接受。请编辑脚本后重新生成。
        </div>
      )}

      {history && history.versions.length > 0 && (
        <div className="postproduction-subsection">
          <h3>Voice Version 历史</h3>
          <div className="assembly-version-history">
            {history.versions.map((version) => (
              <article key={version.version} className="postproduction-media-card">
                <div className="postproduction-title-row">
                  <h3>Voice {versionLabel(version.version)}</h3>
                  <span>{version.is_active ? "当前版本" : "历史版本"}</span>
                </div>
                <dl className="postproduction-facts">
                  <div><dt>Provider</dt><dd>{version.provider ?? "未记录"}</dd></div>
                  <div><dt>Voice</dt><dd>{version.voice ?? "未记录"}</dd></div>
                  <div><dt>Language</dt><dd>{version.language ?? "未记录"}</dd></div>
                  <div><dt>Script 来源</dt><dd>{sourceLabel(version.script_source)}</dd></div>
                  <div><dt>时长</dt><dd>{seconds(version.duration_seconds)}</dd></div>
                  <div><dt>Calibration</dt><dd>{version.calibration_status}</dd></div>
                </dl>
                {version.audio_available && (
                  <audio controls preload="metadata" src={getVoiceVersionAudioUrl(projectId, version.version)} />
                )}
              </article>
            ))}
          </div>
        </div>
      )}
    </section>
  );
}

