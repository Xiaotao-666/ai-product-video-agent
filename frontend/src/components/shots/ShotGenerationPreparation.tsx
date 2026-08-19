import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";

import {
  ApiClientError,
  getProjectTasks,
  getReferenceAssets,
  getReferenceImageUrl,
  getShotGenerationOptions,
  getShotGenerationStatus,
  getTask,
  preflightShotGeneration,
  resumeShotGeneration,
  startShotGeneration,
} from "../../api/client";
import type {
  GenerationModelSelection,
  GenerationOptionsResponse,
  GenerationPreflightRequest,
  GenerationPreflightResponse,
  GenerationVisualInputMode,
  ReferenceAsset,
  ShotGenerationStatusResponse,
  TaskRecord,
} from "../../api/types";
import { projectWorkspacePath } from "../../stageDefinitions";

interface Props {
  projectId: string;
  shotId: string;
  onCompleted?: () => void | Promise<void>;
}

type LoadState = "loading" | "success" | "error";
const ACTIVE_TASK_STATUSES = new Set(["QUEUED", "RUNNING"]);
const SHOT_TASK_OPERATIONS = new Set(["SHOT_GENERATE", "SHOT_RESUME"]);
const GENERATION_STATE_COPY: Record<string, string> = {
  QUEUED: "排队中…",
  SUBMITTING: "正在提交视频生成请求…",
  PROVIDER_RUNNING: "正在生成视频…",
  READY_TO_DOWNLOAD: "视频已生成，等待下载…",
  DOWNLOADING: "正在下载视频…",
  LOCAL_FINALIZING: "正在保存生成结果…",
  WAITING_REVIEW: "视频已生成，正在刷新镜头…",
  FAILED: "生成失败",
  INTERRUPTED: "上次 Web 任务已中断",
};

function loadErrorMessage(error: unknown): string {
  return error instanceof ApiClientError && error.code === "NETWORK_ERROR"
    ? "无法连接 Backend，请确认本地服务已启动。"
    : "暂时无法读取生成选项，请稍后重试。";
}

function requestErrorMessage(error: unknown): string {
  if (!(error instanceof ApiClientError)) return "视频生成请求暂时无法处理。";
  if (error.code === "GENERATION_PREFLIGHT_STALE") {
    return "生成配置已发生变化，请重新检查配置。";
  }
  if (error.code === "PROJECT_BUSY") return "项目当前正在执行其他操作，请稍后查看。";
  return `${error.message}${error.correlationId ? `（错误编号：${error.correlationId}）` : ""}`;
}

export function ShotGenerationPreparation({ projectId, shotId, onCompleted }: Props) {
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [options, setOptions] = useState<GenerationOptionsResponse | null>(null);
  const [assets, setAssets] = useState<ReferenceAsset[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selection, setSelection] = useState<GenerationModelSelection>("AUTO");
  const [requestedModel, setRequestedModel] = useState<string | null>(null);
  const [visualMode, setVisualMode] = useState<GenerationVisualInputMode>("none");
  const [assetId, setAssetId] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);
  const [result, setResult] = useState<GenerationPreflightResponse | null>(null);
  const [generationStatus, setGenerationStatus] = useState<ShotGenerationStatusResponse | null>(null);
  const [activeTask, setActiveTask] = useState<TaskRecord | null>(null);
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const submitGuard = useRef(false);

  const loadGenerationState = useCallback(async () => {
    const [statusResult, tasksResult] = await Promise.all([
      getShotGenerationStatus(projectId, shotId),
      getProjectTasks(projectId),
    ]);
    if (statusResult.data.project_id !== projectId || statusResult.data.shot_id !== shotId || tasksResult.data.project_id !== projectId) {
      throw new ApiClientError({ code: "INVALID_RESPONSE", message: "镜头生成状态不一致。" });
    }
    setGenerationStatus(statusResult.data);
    const active = tasksResult.data.tasks.find(
      (task) => SHOT_TASK_OPERATIONS.has(task.operation)
        && ACTIVE_TASK_STATUSES.has(task.status)
        && task.target_id === shotId,
    );
    if (active) setActiveTask(active);
  }, [projectId, shotId]);

  useEffect(() => {
    let mounted = true;
    setLoadState("loading");
    setLoadError(null);
    Promise.all([
      getShotGenerationOptions(projectId, shotId),
      getReferenceAssets(projectId),
      getShotGenerationStatus(projectId, shotId),
      getProjectTasks(projectId),
    ]).then(([optionsResult, referencesResult, statusResult, tasksResult]) => {
      if (!mounted) return;
      if (optionsResult.data.project_id !== projectId || referencesResult.data.project_id !== projectId || statusResult.data.project_id !== projectId || statusResult.data.shot_id !== shotId || tasksResult.data.project_id !== projectId) {
        throw new ApiClientError({ code: "INVALID_RESPONSE", message: "生成准备数据不一致。" });
      }
      setOptions(optionsResult.data);
      setAssets(referencesResult.data.assets);
      setGenerationStatus(statusResult.data);
      setActiveTask(tasksResult.data.tasks.find(
        (task) => SHOT_TASK_OPERATIONS.has(task.operation)
          && ACTIVE_TASK_STATUSES.has(task.status)
          && task.target_id === shotId,
      ) ?? null);
      setLoadState("success");
    }).catch((error: unknown) => {
      if (!mounted) return;
      setLoadError(loadErrorMessage(error));
      setLoadState("error");
    });
    return () => { mounted = false; };
  }, [projectId, shotId]);

  useEffect(() => {
    if (!activeTask || !ACTIVE_TASK_STATUSES.has(activeTask.status)) return;
    let mounted = true;
    const poll = async () => {
      try {
        const [taskResult, statusResult] = await Promise.all([
          getTask(activeTask.task_id),
          getShotGenerationStatus(projectId, shotId),
        ]);
        if (!mounted) return;
        setActiveTask(taskResult.data);
        setGenerationStatus(statusResult.data);
        if (taskResult.data.status === "SUCCEEDED") await onCompleted?.();
        else if (["FAILED", "INTERRUPTED"].includes(taskResult.data.status)) {
          setSubmitError(taskResult.data.error?.message ?? "视频生成任务未能完成。");
        }
      } catch (error) {
        if (mounted) setSubmitError(requestErrorMessage(error));
      }
    };
    const timer = window.setInterval(() => void poll(), 1000);
    void poll();
    return () => { mounted = false; window.clearInterval(timer); };
  }, [activeTask?.task_id, activeTask?.status, onCompleted, projectId, shotId]);

  const selectedAsset = useMemo(() => assets.find((asset) => asset.asset_id === assetId) ?? null, [assetId, assets]);
  const selectedModel = useMemo(() => options?.models.find((model) => model.model_id === requestedModel) ?? null, [options, requestedModel]);
  const manualCompatible = selectedModel?.supported_visual_input_modes.includes(visualMode) ?? true;
  const taskRunning = activeTask ? ACTIVE_TASK_STATUSES.has(activeTask.status) : false;
  const statusCopy = generationStatus ? GENERATION_STATE_COPY[generationStatus.state] : null;

  function clearResult() {
    setResult(null);
    setSubmitError(null);
    setConfirmOpen(false);
  }

  function generationPayload(): GenerationPreflightRequest {
    return {
      model_selection: selection,
      requested_model: selection === "MANUAL" ? requestedModel : null,
      visual_input: { mode: visualMode, asset_ids: visualMode === "none" || !assetId ? [] : [assetId] },
    };
  }

  function changeSelection(value: GenerationModelSelection) {
    setSelection(value);
    if (value === "AUTO") setRequestedModel(null);
    else if (!requestedModel && options) {
      setRequestedModel(options.models.find((model) => model.supported_visual_input_modes.includes(visualMode))?.model_id ?? options.models[0]?.model_id ?? null);
    }
    clearResult();
  }

  function changeVisualMode(value: GenerationVisualInputMode) {
    setVisualMode(value);
    setAssetId(null);
    clearResult();
  }

  async function checkConfiguration() {
    if (!options || checking || submitGuard.current || !options.eligible || taskRunning) return;
    submitGuard.current = true;
    setChecking(true);
    setSubmitError(null);
    setResult(null);
    try {
      setResult((await preflightShotGeneration(projectId, shotId, generationPayload())).data);
    } catch (error) {
      setSubmitError(requestErrorMessage(error));
    } finally {
      submitGuard.current = false;
      setChecking(false);
    }
  }

  async function attachBusyTask() {
    const tasks = await getProjectTasks(projectId);
    const active = tasks.data.tasks.find((task) => ACTIVE_TASK_STATUSES.has(task.status));
    if (
      active
      && SHOT_TASK_OPERATIONS.has(active.operation)
      && active.target_id === shotId
    ) setActiveTask(active);
  }

  async function confirmGeneration() {
    if (submitGuard.current || taskRunning || !result?.ready || !result.preflight_fingerprint) return;
    submitGuard.current = true;
    setSubmitError(null);
    try {
      const response = await startShotGeneration(projectId, shotId, {
        ...generationPayload(),
        preflight_fingerprint: result.preflight_fingerprint,
        confirm_paid_call: true,
      });
      setActiveTask(response.data);
      setGenerationStatus((current) => current ? { ...current, state: "QUEUED" } : current);
      setConfirmOpen(false);
    } catch (error) {
      if (error instanceof ApiClientError && error.code === "PROJECT_BUSY") await attachBusyTask();
      if (error instanceof ApiClientError && error.code === "GENERATION_PREFLIGHT_STALE") {
        setResult(null);
        setConfirmOpen(false);
      }
      setSubmitError(requestErrorMessage(error));
    } finally {
      submitGuard.current = false;
    }
  }

  async function resumeGeneration() {
    if (submitGuard.current || taskRunning || !generationStatus?.resume_available) return;
    submitGuard.current = true;
    setSubmitError(null);
    try {
      setActiveTask((await resumeShotGeneration(projectId, shotId)).data);
    } catch (error) {
      setSubmitError(requestErrorMessage(error));
      await loadGenerationState().catch(() => undefined);
    } finally {
      submitGuard.current = false;
    }
  }

  return (
    <section className="shot-generation-preparation" aria-labelledby="generation-preparation-title">
      <div className="stage-section-heading">
        <p className="page-kicker">GENERATION PREPARATION</p>
        <h2 id="generation-preparation-title">生成设置</h2>
        <p>先检查模型、Visual Input 和素材兼容性，再明确确认付费生成。</p>
      </div>
      {loadState === "loading" && <p aria-live="polite">正在读取生成选项…</p>}
      {loadState === "error" && <p role="alert">{loadError}</p>}
      {loadState === "success" && options && (
        <>
          {(taskRunning || (statusCopy && generationStatus?.state !== "NOT_STARTED")) && (
            <section className="generation-task-status" aria-live="polite">
              <h3>{activeTask?.status === "QUEUED"
                ? "排队中…"
                : statusCopy && generationStatus?.state !== "NOT_STARTED"
                  ? statusCopy
                  : "正在生成视频…"}</h3>
              <p>页面切换不会取消任务；重新打开页面后会继续读取持久化状态。</p>
            </section>
          )}
          {generationStatus?.state === "SUBMISSION_UNKNOWN" && (
            <div className="generation-submit-error" role="alert">
              <strong>无法确认视频生成请求是否已提交，请不要立即重复生成。</strong>
              <p>当前不会提供普通重试或继续提交按钮，以避免重复付费。</p>
            </div>
          )}
          {generationStatus?.resume_available && !taskRunning && (
            <div className="generation-resume-panel">
              <p>上次 Web 任务中断，已有远端或本地进度可以安全继续。</p>
              <button className="primary-button" type="button" onClick={() => void resumeGeneration()}>继续生成</button>
            </div>
          )}
          <dl className="generation-context-facts">
            <div><dt>镜头</dt><dd>{options.shot.shot_id.replace("shot_", "Shot ")}</dd></div>
            <div><dt>将使用 Prompt</dt><dd>{options.shot.prompt_version ? `v${options.shot.prompt_version}` : "未就绪"}</dd></div>
            <div><dt>时长</dt><dd>{options.shot.duration_seconds} 秒</dd></div>
            <div><dt>分辨率</dt><dd>{options.shot.resolution}</dd></div>
          </dl>
          {!options.eligible && !taskRunning && !generationStatus?.resume_available && (
            <div className="generation-issues" role="status"><h3>当前尚不能检查初次生成配置</h3><ul>{options.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}</ul></div>
          )}
          <fieldset className="generation-fieldset" disabled={!options.eligible || checking || taskRunning}>
            <legend>模型选择</legend>
            <label><input type="radio" name="model-selection" checked={selection === "AUTO"} onChange={() => changeSelection("AUTO")} />自动</label>
            <label><input type="radio" name="model-selection" checked={selection === "MANUAL"} onChange={() => changeSelection("MANUAL")} />手动</label>
            {selection === "MANUAL" && <label className="generation-select-label">视频模型<select aria-label="视频模型" value={requestedModel ?? ""} onChange={(event) => { setRequestedModel(event.target.value || null); clearResult(); }}>{options.models.map((model) => { const compatible = model.supported_visual_input_modes.includes(visualMode); return <option key={model.model_id} value={model.model_id}>{model.display_name}{!model.available ? " · 未配置" : ""}{!compatible ? " · 不兼容当前模式" : ""}</option>; })}</select></label>}
            {selection === "MANUAL" && selectedModel && !manualCompatible && <p className="generation-inline-warning" role="status">所选模型不支持当前 Visual Input；配置检查不会自动更换模型。</p>}
          </fieldset>
          <fieldset className="generation-fieldset" disabled={!options.eligible || checking || taskRunning}>
            <legend>Visual Input</legend>
            <div className="visual-mode-options">{options.visual_input_modes.map((option) => <label key={option.mode} className="visual-mode-option"><span><input type="radio" name="visual-mode" checked={visualMode === option.mode} onChange={() => changeVisualMode(option.mode)} />{option.display_name}</span><small>{option.description}</small></label>)}</div>
          </fieldset>
          {visualMode !== "none" && <div className="reference-selector"><h3>选择项目已有素材</h3>{assets.length === 0 ? <p className="stage-empty-copy">当前项目暂无参考素材。可先<Link to={projectWorkspacePath(projectId)}>前往项目素材库添加</Link>。</p> : <div className="reference-grid">{assets.map((asset) => <label key={asset.asset_id} className="reference-card"><input type="radio" name="reference-asset" checked={assetId === asset.asset_id} disabled={!options.eligible || checking || taskRunning} onChange={() => { setAssetId(asset.asset_id); clearResult(); }} /><img src={getReferenceImageUrl(projectId, asset.asset_id)} alt={`${asset.filename} 参考图预览`} /><span>{asset.filename}</span><small>{asset.width} × {asset.height}</small></label>)}</div>}</div>}
          <div className="generation-preflight-actions"><button className="primary-button" type="button" disabled={!options.eligible || checking || taskRunning} onClick={() => void checkConfiguration()}>{checking ? "正在检查生成配置…" : "检查生成配置"}</button></div>
          <p className="paid-call-warning">真正生成视频会调用付费视频模型；只有最终确认后才会提交请求。</p>
          {submitError && <p className="generation-submit-error" role="alert">{submitError}</p>}
          {result && (
            <section className={`preflight-summary ${result.ready ? "preflight-summary-ready" : "preflight-summary-not-ready"}`} aria-label="生成前确认摘要">
              <p className="page-kicker">PREFLIGHT</p><h3>{result.ready ? "配置检查通过" : "配置尚未就绪"}</h3>
              <dl>
                <div><dt>镜头</dt><dd>{result.shot.shot_id.replace("shot_", "Shot ")}</dd></div><div><dt>Prompt</dt><dd>{result.shot.prompt_version ? `v${result.shot.prompt_version}` : "未就绪"}</dd></div><div><dt>时长</dt><dd>{result.shot.duration_seconds} 秒</dd></div><div><dt>分辨率</dt><dd>{result.shot.resolution}</dd></div><div><dt>Visual Input</dt><dd>{options.visual_input_modes.find((item) => item.mode === result.resolved?.visual_input_mode)?.display_name ?? visualMode}{selectedAsset ? ` · ${selectedAsset.asset_id}` : ""}</dd></div><div><dt>模型</dt><dd>{result.resolved?.model_display_name ?? "未解析"}</dd></div><div><dt>Provider</dt><dd>{result.resolved?.provider_display_name ?? "未解析"}</dd></div><div><dt>API Version</dt><dd>{result.resolved?.api_version ?? "未解析"}</dd></div><div><dt>生成模式</dt><dd>{result.resolved?.generation_mode_display_name ?? "未解析"}</dd></div>
              </dl>
              {result.issues.length > 0 && <ul className="generation-issues-list">{result.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}</ul>}
              {result.ready && result.preflight_fingerprint && <button className="primary-button" type="button" onClick={() => setConfirmOpen(true)}>生成视频</button>}
              {!result.ready && <p>配置未通过，不会创建任务或调用视频模型。</p>}
            </section>
          )}
          {confirmOpen && result?.ready && result.resolved && (
            <div className="generation-confirm-backdrop" role="presentation"><section className="generation-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="paid-generation-title">
              <p className="page-kicker">FINAL CONFIRMATION</p><h3 id="paid-generation-title">确认生成视频</h3>
              <dl><div><dt>Shot</dt><dd>{result.shot.shot_id.replace("shot_", "Shot ")}</dd></div><div><dt>Prompt Version</dt><dd>v{result.shot.prompt_version}</dd></div><div><dt>Duration</dt><dd>{result.shot.duration_seconds} 秒</dd></div><div><dt>Resolution</dt><dd>{result.shot.resolution}</dd></div><div><dt>Visual Input</dt><dd>{result.resolved.visual_input_mode}{selectedAsset ? ` · ${selectedAsset.asset_id}` : ""}</dd></div><div><dt>Provider</dt><dd>{result.resolved.provider_display_name}</dd></div><div><dt>Model</dt><dd>{result.resolved.model_display_name}</dd></div><div><dt>API Version</dt><dd>{result.resolved.api_version}</dd></div><div><dt>Generation Mode</dt><dd>{result.resolved.generation_mode_display_name}</dd></div></dl>
              <p className="paid-call-warning"><strong>确认后将向视频生成模型提交付费请求。</strong></p>
              <div className="generation-confirm-actions"><button className="secondary-button" type="button" onClick={() => setConfirmOpen(false)}>取消</button><button className="primary-button" type="button" disabled={submitGuard.current} onClick={() => void confirmGeneration()}>确认并生成视频</button></div>
            </section></div>
          )}
        </>
      )}
    </section>
  );
}
