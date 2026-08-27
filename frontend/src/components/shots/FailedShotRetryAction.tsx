import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiClientError, getFailedRetryOptions, getProjectTasks, getReferenceAssets, getTask,
  preflightFailedRetry, resumeShotGeneration, retryFailedShotGeneration,
} from "../../api/client";
import type {
  FailedRetryOptions, FailedRetryPreflight, FailedRetryPreflightRequest,
  FailureRecovery, ReferenceAsset, TaskRecord,
} from "../../api/types";
import { isActiveTaskStatus } from "../../hooks/useProjectTaskPolling";

interface Props {
  projectId: string;
  shotId: string;
  recovery: FailureRecovery;
  onCompleted: () => void | Promise<void>;
}

interface AcceptedBarrier {
  taskId: string | null;
  submittedAt: number;
  previousIds: string[];
  correlationId: string | null;
}

const version = (value: number | null | undefined) =>
  value == null ? "未记录" : `v${String(value).padStart(3, "0")}`;
const unknownMessage = "外部请求状态未知，为避免重复收费，暂不能直接重新提交。";

function message(error: unknown): string {
  if (error instanceof ApiClientError && error.code === "FAILED_RETRY_STALE") {
    return "失败恢复状态或配置已变化，请刷新状态并重新预检。";
  }
  return error instanceof ApiClientError ? error.message : "暂时无法读取生成状态，请稍后检查。";
}

export function FailedShotRetryAction({ projectId, shotId, recovery, onCompleted }: Props) {
  const storageKey = `shot-failed-retry:${projectId}:${shotId}`;
  const [barrier, setBarrier] = useState<AcceptedBarrier | null>(() => {
    try {
      const saved = sessionStorage.getItem(storageKey);
      if (!saved) return null;
      const value = JSON.parse(saved) as AcceptedBarrier;
      if (!Array.isArray(value.previousIds) || typeof value.submittedAt !== "number") throw Error();
      return value;
    } catch {
      // Unreadable recovery data must not reopen a potentially paid submission.
      return { taskId: null, submittedAt: Number.MAX_SAFE_INTEGER, previousIds: [], correlationId: null };
    }
  });
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [options, setOptions] = useState<FailedRetryOptions | null>(null);
  const [assets, setAssets] = useState<ReferenceAsset[]>([]);
  const [config, setConfig] = useState<FailedRetryPreflightRequest | null>(null);
  const [prepared, setPrepared] = useState<{
    result: FailedRetryPreflight; config: FailedRetryPreflightRequest;
  } | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const guard = useRef(false);
  const previousIds = useRef<string[]>([]);
  const finishedTask = useRef<string | null>(null);
  const barrierRef = useRef(barrier);

  const saveBarrier = useCallback((value: AcceptedBarrier | null) => {
    // Persist before POST; inability to persist aborts the paid action.
    if (value) sessionStorage.setItem(storageKey, JSON.stringify(value));
    else sessionStorage.removeItem(storageKey);
    barrierRef.current = value;
    setBarrier(value);
  }, [storageKey]);

  const acceptTask = useCallback(async (current: TaskRecord, paidMatch = false) => {
    if (current.project_id !== projectId) throw new Error("Task identity mismatch");
    if (paidMatch && (current.target_id !== shotId || current.operation !== "SHOT_GENERATE")) {
      throw new Error("Task identity mismatch");
    }
    setTask(current);
    if (!isActiveTaskStatus(current.status) && finishedTask.current !== current.task_id) {
      finishedTask.current = current.task_id;
      if (paidMatch) saveBarrier(null);
      setPrepared(null);
      setOptions(null);
      setConfig(null);
      if (current.status !== "SUCCEEDED") setError(current.error?.message ?? "任务已结束，请重新检查恢复状态。");
      await onCompleted();
    }
  }, [onCompleted, projectId, saveBarrier, shotId]);

  const reconcile = useCallback(async () => {
    try {
      if (barrier?.taskId || recovery.active_task_id) {
        const taskId = barrier?.taskId ?? recovery.active_task_id!;
        const result = await getTask(taskId);
        if (barrierRef.current !== barrier) return;
        if (result.data.task_id !== taskId) throw new Error("Task identity mismatch");
        await acceptTask(result.data, Boolean(barrier?.taskId));
        return;
      }
      if (!barrier) return;
      const result = await getProjectTasks(projectId);
      if (barrierRef.current !== barrier) return;
      if (result.data.project_id !== projectId) throw new Error("Project identity mismatch");
      const candidates = result.data.tasks.filter((item) =>
        item.project_id === projectId && item.target_id === shotId
        && item.operation === "SHOT_GENERATE" && !barrier.previousIds.includes(item.task_id)
        && (barrier.correlationId ? item.correlation_id === barrier.correlationId
          : Date.parse(item.created_at) >= barrier.submittedAt - 60_000));
      if (candidates.length === 1) {
        saveBarrier({ ...barrier, taskId: candidates[0].task_id });
        await acceptTask(candidates[0], true);
      } else {
        setError("请求可能已被接受，任务状态暂不可读。请仅检查状态，不要再次提交。");
      }
    } catch (caught) {
      if (barrierRef.current === barrier) setError(message(caught));
    }
  }, [acceptTask, barrier, projectId, recovery.active_task_id, saveBarrier, shotId]);

  useEffect(() => { void reconcile(); }, [reconcile]);
  useEffect(() => {
    if (!task || !isActiveTaskStatus(task.status)) return;
    const timer = window.setInterval(() => {
      void getTask(task.task_id).then(async (result) => {
        if (result.data.task_id !== task.task_id) throw new Error("Task identity mismatch");
        await acceptTask(result.data, Boolean(barrier && task.operation === "SHOT_GENERATE"));
      }).catch((caught: unknown) => setError(message(caught)));
    }, 1000);
    return () => window.clearInterval(timer);
  }, [acceptTask, barrier, task]);

  const blocked = busy || Boolean(barrier) || Boolean(task && isActiveTaskStatus(task.status));
  const begin = async () => {
    if (blocked || !recovery.can_retry) return;
    setBusy(true); setError(null);
    try {
      const [found, references, tasks] = await Promise.all([
        getFailedRetryOptions(projectId, shotId), getReferenceAssets(projectId), getProjectTasks(projectId),
      ]);
      if (found.data.project_id !== projectId || found.data.shot.shot_id !== shotId
        || references.data.project_id !== projectId || tasks.data.project_id !== projectId) throw Error();
      previousIds.current = tasks.data.tasks.map((item) => item.task_id);
      setOptions(found.data);
      setAssets(references.data.assets);
      setConfig({ intent: "FAILED_RETRY", model_selection: "MANUAL", requested_model: null,
        duration: found.data.shot.duration_seconds, resolution: found.data.shot.resolution,
        visual_input: { mode: "none", asset_ids: [] } });
      setPrepared(null);
    } catch (caught) { setError(message(caught)); }
    finally { setBusy(false); }
  };
  const change = (patch: Partial<FailedRetryPreflightRequest>) => {
    setConfig((current) => current ? { ...current, ...patch } : null);
    setPrepared(null); setConfirmOpen(false);
  };
  const preflight = async () => {
    if (!config || blocked) return;
    setBusy(true); setError(null); setPrepared(null);
    try {
      const result = await preflightFailedRetry(projectId, shotId, config);
      if (result.data.shot.shot_id !== shotId) throw Error();
      setPrepared({ result: result.data, config });
    } catch (caught) { setError(message(caught)); }
    finally { setBusy(false); }
  };
  const submit = async () => {
    if (guard.current || blocked || !prepared?.result.ready
      || !prepared.result.failure_recovery.can_retry || !prepared.result.preflight_fingerprint) return;
    guard.current = true; setBusy(true); setError(null); setConfirmOpen(false);
    const pending: AcceptedBarrier = {
      taskId: null, submittedAt: Date.now(), previousIds: previousIds.current, correlationId: null,
    };
    try {
      saveBarrier(pending);
      const accepted = await retryFailedShotGeneration(projectId, shotId, {
        ...prepared.config, preflight_fingerprint: prepared.result.preflight_fingerprint,
        confirm_external_video_call: true,
      });
      saveBarrier({ ...pending, taskId: accepted.data.task_id, correlationId: accepted.data.correlation_id });
      await acceptTask(accepted.data, true);
    } catch (caught) {
      // A confirmed non-accepted 4xx is safe to re-preflight. Network/5xx/202 stays locked.
      if (caught instanceof ApiClientError && !caught.requestAccepted
        && caught.status !== null && caught.status >= 400 && caught.status < 500) {
        saveBarrier(null); setPrepared(null);
      } else if (caught instanceof ApiClientError && caught.requestAccepted) {
        saveBarrier({ ...pending, correlationId: caught.correlationId });
      }
      setError(message(caught));
    } finally { guard.current = false; setBusy(false); }
  };
  const resume = async () => {
    if (guard.current || blocked) return;
    guard.current = true; setBusy(true); setError(null);
    try {
      const accepted = await resumeShotGeneration(projectId, shotId);
      if (accepted.data.target_id !== shotId || accepted.data.operation !== "SHOT_RESUME") throw Error();
      await acceptTask(accepted.data);
    } catch (caught) { setError(message(caught)); }
    finally { guard.current = false; setBusy(false); }
  };
  const summary = prepared && <dl className="generation-context-facts">
    <div><dt>Prompt Version</dt><dd>{version(prepared.result.shot.prompt_version)}</dd></div>
    <div><dt>Model</dt><dd>{prepared.result.resolved?.model_display_name ?? "未解析"}</dd></div>
    <div><dt>Duration</dt><dd>{prepared.result.shot.duration_seconds} 秒</dd></div>
    <div><dt>Resolution</dt><dd>{prepared.result.shot.resolution}</dd></div>
    <div><dt>Visual Input</dt><dd>{prepared.result.resolved?.visual_input_mode ?? prepared.config.visual_input.mode}
      {prepared.result.selected_asset_ids.map((id) => ` · ${assets.find((asset) => asset.asset_id === id)?.filename ?? id}`)}</dd></div>
    <div><dt>下一 Generation Version</dt><dd>{version(prepared.result.shot.next_video_version)}</dd></div>
  </dl>;

  return <section className="shot-generation-preparation" aria-label="镜头失败恢复">
    <p className="page-kicker">SHOT FAILURE RECOVERY</p>
    <h2>{task && isActiveTaskStatus(task.status) ? "镜头生成任务进行中" : "镜头生成失败恢复"}</h2>
    {recovery.state !== "BUSINESS_ALREADY_COMPLETE" && <p>未生成可用视频</p>}
    <p>{recovery.state === "RETRY_BLOCKED_SUBMISSION_UNKNOWN" ? unknownMessage : recovery.safe_message}</p>
    <p>上一次尝试：{version(recovery.last_attempt_version)}</p>
    {error && <p role="alert">{error}</p>}
    {task && <p role="status">Task · {task.operation} · {task.status}{task.status === "SUCCEEDED" ? " · 等待审核" : ""}</p>}
    {barrier && <div role="status"><p>已进入请求恢复保护；只检查已有任务，不会再次提交。</p>
      <button type="button" className="secondary-button" disabled={busy} onClick={() => void reconcile()}>检查已接受任务状态</button></div>}
    {recovery.can_retry && <button type="button" className="secondary-button" disabled={blocked} onClick={() => void begin()}>调整配置并重新尝试</button>}
    {(recovery.state === "RESUME_AVAILABLE" || recovery.state === "BUSINESS_ALREADY_COMPLETE") &&
      <button type="button" className="secondary-button" disabled={blocked} onClick={() => void resume()}>
        {recovery.state === "BUSINESS_ALREADY_COMPLETE" ? "恢复已有视频" : "继续检查生成结果 / Resume"}
      </button>}
    {options && config && <section aria-label="Failed Retry Preparation">
      <h3>Failed Retry Preparation</h3>
      <p>沿用 Prompt {version(options.shot.prompt_version)}；不会重新生成 Prompt。下一版本 {version(options.shot.next_video_version)}。</p>
      <p>本地预检不会查询套餐或调用 MiniMax；请自行确认套餐支持的组合。不会自动更换配置。</p>
      {!options.eligible && <p role="alert">{options.failure_recovery.safe_message}</p>}
      <fieldset className="generation-fieldset" disabled={blocked || !options.eligible}>
        <legend>本次生成配置</legend>
        <label>模型选择<select aria-label="重试模型选择" value={config.model_selection}
          onChange={(event) => change({ model_selection: event.target.value as "AUTO" | "MANUAL", requested_model: null })}>
          <option value="AUTO">自动路由（最终模型在预检中确认）</option><option value="MANUAL">手动选择</option>
        </select></label>
        {config.model_selection === "MANUAL" && <label>Model<select aria-label="重试模型" value={config.requested_model ?? ""}
          onChange={(event) => change({ requested_model: event.target.value || null })}>
          <option value="">请选择模型</option>{options.models.map((model) => <option key={model.model_id} value={model.model_id}>{model.display_name}</option>)}
        </select></label>}
        <label>Duration<input aria-label="重试时长" type="number" min="1" max="600" step="1" value={config.duration}
          onChange={(event) => change({ duration: Number(event.target.value) })} /></label>
        <label>Resolution<select aria-label="重试分辨率" value={config.resolution}
          onChange={(event) => change({ resolution: event.target.value })}>
          {[...new Set([options.shot.resolution, ...options.models.flatMap((model) => model.supported_resolutions)])]
            .map((value) => <option key={value} value={value}>{value}</option>)}
        </select></label>
        <label>Visual Input<select aria-label="重试 Visual Input" value={config.visual_input.mode}
          onChange={(event) => change({ visual_input: { mode: event.target.value as FailedRetryPreflightRequest["visual_input"]["mode"], asset_ids: [] } })}>
          {options.visual_input_modes.map((mode) => <option key={mode.mode} value={mode.mode}>{mode.display_name}</option>)}
        </select></label>
        {config.visual_input.mode !== "none" && <label>参考素材<select aria-label="重试参考素材" value={config.visual_input.asset_ids[0] ?? ""}
          onChange={(event) => change({ visual_input: { ...config.visual_input, asset_ids: event.target.value ? [event.target.value] : [] } })}>
          <option value="">请选择已有素材</option>{assets.map((asset) => <option key={asset.asset_id} value={asset.asset_id}>{asset.filename}</option>)}
        </select></label>}
        <button className="primary-button" type="button" onClick={() => void preflight()}>检查重试配置</button>
      </fieldset>
      {prepared && <section className="preflight-summary" aria-label="失败重试预检结果">
        <h3>{prepared.result.ready ? "配置检查通过" : "配置尚未就绪"}</h3>{summary}
        {[...prepared.result.issues, ...prepared.result.warnings].map((issue) => <p key={issue.code}>{issue.message}</p>)}
        <p className="paid-call-warning">确认后将发起新的 MiniMax 视频生成请求，可能产生费用。</p>
        <button className="primary-button" type="button" disabled={blocked || !prepared.result.ready || !prepared.result.failure_recovery.can_retry}
          onClick={() => setConfirmOpen(true)}>查看重试确认</button>
      </section>}
      {confirmOpen && prepared && <div className="generation-confirm-backdrop">
        <section className="generation-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="failed-retry-confirm">
          <h3 id="failed-retry-confirm">重新尝试生成镜头</h3><p>上一次失败：{recovery.safe_message}</p>{summary}
          <p className="paid-call-warning">确认后将发起新的 MiniMax 视频生成请求，可能产生费用。</p>
          <div className="generation-confirm-actions">
            <button type="button" className="secondary-button" disabled={busy} onClick={() => setConfirmOpen(false)}>取消</button>
            <button type="button" className="primary-button" disabled={blocked} onClick={() => void submit()}>确认并重新生成</button>
          </div>
        </section>
      </div>}
    </section>}
  </section>;
}
