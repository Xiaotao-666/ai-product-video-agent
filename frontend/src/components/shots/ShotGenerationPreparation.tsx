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
  generateShotWithPromptVersion,
  preflightShotGeneration,
  resumeShotGeneration,
  regenerateShotGeneration,
  startShotGeneration,
} from "../../api/client";
import type {
  GenerationIntent,
  GenerationModelSelection,
  GenerationOptionsResponse,
  GenerationPreflightRequest,
  GenerationPreflightResponse,
  GenerationVisualInputMode,
  ReferenceAsset,
  ShotGenerationStatusResponse,
  TaskRecord,
} from "../../api/types";
import { isActiveTaskStatus } from "../../hooks/useProjectTaskPolling";
import { projectWorkspacePath } from "../../stageDefinitions";

interface Props {
  projectId: string;
  shotId: string;
  onCompleted?: () => void | Promise<void>;
  intent?: GenerationIntent;
  manualPrompt?: {
    videoVersion: number;
    promptVersion: number;
    editablePrompt: string;
  };
  targetPromptVersion?: number;
}

type LoadState = "loading" | "success" | "error";
type ManualSessionState =
  | "IDLE"
  | "EDITING"
  | "CONFIGURING"
  | "PREFLIGHT_READY"
  | "SUBMITTING"
  | "TASK_ATTACHED"
  | "SUCCEEDED"
  | "FAILED"
  | "STATUS_UNCERTAIN";

interface GenerationVersionPresentation {
  basePromptVersion: number | null;
  generationPromptVersion: number | null;
  nextPromptVersion: number | null;
  nextVideoVersion: number | null;
  isCreatingNewPrompt: boolean;
}

function generationVersionPresentation(
  intent: GenerationIntent,
  shot: GenerationOptionsResponse["shot"],
): GenerationVersionPresentation {
  const basePromptVersion = shot.prompt_version ?? null;
  const isCreatingNewPrompt = intent === "REGENERATE_MANUAL_PROMPT";
  const nextPromptVersion = isCreatingNewPrompt
    ? shot.next_prompt_version ?? null
    : null;
  return {
    basePromptVersion,
    generationPromptVersion: isCreatingNewPrompt
      ? nextPromptVersion
      : basePromptVersion,
    nextPromptVersion,
    nextVideoVersion: shot.next_video_version ?? null,
    isCreatingNewPrompt,
  };
}

function versionLabel(subject: "Prompt" | "Video", version: number | null): string {
  return version === null ? "待计算" : `${subject} v${version}`;
}
const MANUAL_STATUS_SESSIONS = new Set<ManualSessionState>([
  "TASK_ATTACHED",
  "SUCCEEDED",
  "FAILED",
  "STATUS_UNCERTAIN",
]);
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

function taskOperationMatchesIntent(task: TaskRecord, intent: GenerationIntent): boolean {
  if (task.operation === "SHOT_RESUME") return true;
  if (intent === "GENERATE_WITH_PROMPT_VERSION") {
    return task.operation === "SHOT_PROMPT_VERSION_GENERATE";
  }
  return intent === "INITIAL"
    ? task.operation === "SHOT_GENERATE"
    : task.operation === "SHOT_REGENERATE";
}

function findRecoverableTask(
  tasks: TaskRecord[],
  status: ShotGenerationStatusResponse,
  shotId: string,
  intent: GenerationIntent,
  targetPromptVersion: number | null,
): TaskRecord | null {
  if (intent !== "INITIAL" && status.generation_intent !== intent) return null;
  if (
    intent === "GENERATE_WITH_PROMPT_VERSION"
    && status.prompt_version !== targetPromptVersion
  ) return null;
  if (
    intent === "INITIAL"
    && status.generation_intent !== null
    && status.generation_intent !== undefined
    && status.generation_intent !== "INITIAL"
  ) return null;
  return tasks.find(
    (task) => isActiveTaskStatus(task.status)
      && task.target_id === shotId
      && taskOperationMatchesIntent(task, intent),
  ) ?? null;
}

export function ShotGenerationPreparation({
  projectId,
  shotId,
  onCompleted,
  intent = "INITIAL",
  manualPrompt,
  targetPromptVersion,
}: Props) {
  const regenerating = intent !== "INITIAL";
  const manualRegeneration = intent === "REGENERATE_MANUAL_PROMPT";
  const selectedPromptGeneration = intent === "GENERATE_WITH_PROMPT_VERSION";
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
  const [submissionPending, setSubmissionPending] = useState(false);
  const [acceptedStatusUncertain, setAcceptedStatusUncertain] = useState(false);
  const [acceptedCorrelationId, setAcceptedCorrelationId] = useState<string | null>(null);
  const [reconciliationPending, setReconciliationPending] = useState(false);
  const [editorOpen, setEditorOpen] = useState(false);
  const [reviewOpen, setReviewOpen] = useState(false);
  const [editedPrompt, setEditedPrompt] = useState(manualPrompt?.editablePrompt ?? "");
  const [editorError, setEditorError] = useState<string | null>(null);
  const [manualSession, setManualSession] = useState<ManualSessionState>("IDLE");
  const [selectedPromptOpen, setSelectedPromptOpen] = useState(false);
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
    const active = findRecoverableTask(
      tasksResult.data.tasks,
      statusResult.data,
      shotId,
      intent,
      targetPromptVersion ?? null,
    );
    setActiveTask(active);
    if (manualRegeneration) {
      if (active || (
        statusResult.data.generation_intent === intent
        && statusResult.data.resume_available
      )) setManualSession("TASK_ATTACHED");
      else if (
        statusResult.data.generation_intent === intent
        && statusResult.data.state === "SUBMISSION_UNKNOWN"
      ) setManualSession("STATUS_UNCERTAIN");
    }
  }, [intent, manualRegeneration, projectId, shotId, targetPromptVersion]);

  useEffect(() => {
    if (manualRegeneration && !editorOpen) {
      setLoadState("success");
      setLoadError(null);
      let mounted = true;
      Promise.all([
        getShotGenerationStatus(projectId, shotId),
        getProjectTasks(projectId),
      ]).then(([statusResult, tasksResult]) => {
        if (!mounted) return;
        if (statusResult.data.project_id !== projectId || statusResult.data.shot_id !== shotId || tasksResult.data.project_id !== projectId) {
          throw new ApiClientError({ code: "INVALID_RESPONSE", message: "镜头生成状态不一致。" });
        }
        setGenerationStatus(statusResult.data);
        const recovered = findRecoverableTask(
          tasksResult.data.tasks,
          statusResult.data,
          shotId,
          intent,
          targetPromptVersion ?? null,
        );
        const ownsDurableProgress = statusResult.data.generation_intent === intent
          && (recovered !== null
            || statusResult.data.resume_available
            || statusResult.data.state === "SUBMISSION_UNKNOWN");
        if (!ownsDurableProgress) return;
        setActiveTask(recovered);
        setManualSession(
          statusResult.data.state === "SUBMISSION_UNKNOWN"
            ? "STATUS_UNCERTAIN"
            : "TASK_ATTACHED",
        );
        setReviewOpen(true);
        setEditorOpen(true);
      }).catch(() => {
        // The collapsed editor remains safe and idle when recovery reads fail.
      });
      return () => { mounted = false; };
    }
    let mounted = true;
    setLoadState("loading");
    setLoadError(null);
    Promise.all([
      regenerating
        ? selectedPromptGeneration
          ? getShotGenerationOptions(
              projectId,
              shotId,
              intent,
              targetPromptVersion ?? null,
            )
          : getShotGenerationOptions(projectId, shotId, intent)
        : getShotGenerationOptions(projectId, shotId),
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
      const recovered = findRecoverableTask(
        tasksResult.data.tasks,
        statusResult.data,
        shotId,
        intent,
        targetPromptVersion ?? null,
      );
      setActiveTask(recovered);
      if (
        selectedPromptGeneration
        && (
          recovered
          || (
            statusResult.data.generation_intent === intent
            && statusResult.data.prompt_version === targetPromptVersion
          )
        )
      ) setSelectedPromptOpen(true);
      if (manualRegeneration && recovered) setManualSession("TASK_ATTACHED");
      else if (
        manualRegeneration
        && statusResult.data.generation_intent === intent
        && statusResult.data.state === "SUBMISSION_UNKNOWN"
      ) setManualSession("STATUS_UNCERTAIN");
      setLoadState("success");
    }).catch((error: unknown) => {
      if (!mounted) return;
      setLoadError(loadErrorMessage(error));
      setLoadState("error");
    });
    return () => { mounted = false; };
  }, [editorOpen, intent, manualRegeneration, projectId, selectedPromptGeneration, shotId, targetPromptVersion]);

  useEffect(() => {
    if (!activeTask || !isActiveTaskStatus(activeTask.status)) return;
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
        if (taskResult.data.status === "SUCCEEDED") {
          if (manualRegeneration) setManualSession("SUCCEEDED");
          await onCompleted?.();
        }
        else if (["FAILED", "INTERRUPTED"].includes(taskResult.data.status)) {
          if (manualRegeneration) setManualSession("FAILED");
          setSubmitError(taskResult.data.error?.message ?? "视频生成任务未能完成。");
        }
      } catch (error) {
        if (mounted) setSubmitError(requestErrorMessage(error));
      }
    };
    const timer = window.setInterval(() => void poll(), 1000);
    void poll();
    return () => { mounted = false; window.clearInterval(timer); };
  }, [activeTask?.task_id, activeTask?.status, manualRegeneration, onCompleted, projectId, shotId]);

  const selectedAsset = useMemo(() => assets.find((asset) => asset.asset_id === assetId) ?? null, [assetId, assets]);
  const selectedModel = useMemo(() => options?.models.find((model) => model.model_id === requestedModel) ?? null, [options, requestedModel]);
  const manualCompatible = selectedModel?.supported_visual_input_modes.includes(visualMode) ?? true;
  const taskRunning = activeTask ? isActiveTaskStatus(activeTask.status) : false;
  const statusIntentMatchesAction = generationStatus?.generation_intent === intent
    || (intent === "INITIAL" && generationStatus?.generation_intent == null);
  const durableRecoveryNeedsAttention = generationStatus?.resume_available
    || generationStatus?.state === "SUBMISSION_UNKNOWN"
    || generationStatus?.state === "FAILED"
    || generationStatus?.state === "INTERRUPTED";
  const actionOwnsPersistentStatus = manualRegeneration
    ? MANUAL_STATUS_SESSIONS.has(manualSession)
    : Boolean(
        activeTask
        || submissionPending
        || acceptedStatusUncertain
        || (statusIntentMatchesAction && durableRecoveryNeedsAttention),
      );
  const actionGenerationStatus = actionOwnsPersistentStatus ? generationStatus : null;
  const paidActionBlocked = taskRunning
    || submissionPending
    || acceptedStatusUncertain
    || actionGenerationStatus?.state === "SUBMISSION_UNKNOWN";
  const statusCopy = actionGenerationStatus ? GENERATION_STATE_COPY[actionGenerationStatus.state] : null;
  const optionVersions = options
    ? generationVersionPresentation(intent, options.shot)
    : null;
  const resultVersions = result
    ? generationVersionPresentation(intent, result.shot)
    : null;

  function clearResult() {
    setResult(null);
    setSubmitError(null);
    setConfirmOpen(false);
  }

  function generationPayload(): GenerationPreflightRequest {
    return {
      ...(regenerating ? { intent } : {}),
      ...(manualRegeneration
        ? {
            base_prompt_version: manualPrompt?.promptVersion ?? null,
            edited_prompt: editedPrompt,
          }
        : {}),
      ...(selectedPromptGeneration
        ? { target_prompt_version: targetPromptVersion ?? null }
        : {}),
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
    if (manualRegeneration) setManualSession("CONFIGURING");
    clearResult();
  }

  function changeVisualMode(value: GenerationVisualInputMode) {
    setVisualMode(value);
    setAssetId(null);
    if (manualRegeneration) setManualSession("CONFIGURING");
    clearResult();
  }

  async function checkConfiguration() {
    if (!options || checking || submitGuard.current || !options.eligible || paidActionBlocked) return;
    if (manualRegeneration && !editedPrompt.trim()) {
      setEditorError("修改后的视觉 Prompt 核心不能为空。");
      return;
    }
    submitGuard.current = true;
    setChecking(true);
    setSubmitError(null);
    setResult(null);
    try {
      const response = await preflightShotGeneration(projectId, shotId, generationPayload());
      setResult(response.data);
      if (manualRegeneration) {
        setManualSession(response.data.ready ? "PREFLIGHT_READY" : "CONFIGURING");
      }
    } catch (error) {
      if (manualRegeneration) setManualSession("CONFIGURING");
      setSubmitError(requestErrorMessage(error));
    } finally {
      submitGuard.current = false;
      setChecking(false);
    }
  }

  async function attachBusyTask(): Promise<boolean> {
    const [tasks, status] = await Promise.all([
      getProjectTasks(projectId),
      getShotGenerationStatus(projectId, shotId),
    ]);
    const active = findRecoverableTask(
      tasks.data.tasks,
      status.data,
      shotId,
      intent,
      targetPromptVersion ?? null,
    );
    if (active) {
      setActiveTask(active);
      setGenerationStatus(status.data);
      if (manualRegeneration) setManualSession("TASK_ATTACHED");
      return true;
    }
    return false;
  }

  async function confirmGeneration() {
    if (submitGuard.current || paidActionBlocked || !result?.ready || !result.preflight_fingerprint) return;
    submitGuard.current = true;
    setSubmissionPending(true);
    if (manualRegeneration) setManualSession("SUBMITTING");
    setSubmitError(null);
    try {
      const submit = selectedPromptGeneration
        ? generateShotWithPromptVersion
        : regenerating
          ? regenerateShotGeneration
          : startShotGeneration;
      const response = await submit(projectId, shotId, {
        ...generationPayload(),
        preflight_fingerprint: result.preflight_fingerprint,
        confirm_paid_call: true,
      });
      const expectedOperation = selectedPromptGeneration
        ? "SHOT_PROMPT_VERSION_GENERATE"
        : regenerating ? "SHOT_REGENERATE" : "SHOT_GENERATE";
      if (
        response.data.operation !== expectedOperation
        || response.data.target_id !== shotId
      ) {
        setSubmissionPending(false);
        setAcceptedStatusUncertain(true);
        setAcceptedCorrelationId(response.correlationId);
        if (manualRegeneration) setManualSession("STATUS_UNCERTAIN");
        setConfirmOpen(false);
        return;
      }
      setActiveTask(response.data);
      setGenerationStatus((current) => current ? { ...current, state: "QUEUED" } : current);
      if (manualRegeneration) setManualSession("TASK_ATTACHED");
      setConfirmOpen(false);
      setSubmissionPending(false);
    } catch (error) {
      if (
        error instanceof ApiClientError
        && (error.requestAccepted || error.code === "ACCEPTED_TASK_STATUS_UNREADABLE")
      ) {
        setSubmissionPending(false);
        setAcceptedStatusUncertain(true);
        setAcceptedCorrelationId(error.correlationId);
        if (manualRegeneration) setManualSession("STATUS_UNCERTAIN");
        setConfirmOpen(false);
        return;
      }
      let attached = false;
      if (error instanceof ApiClientError && error.code === "PROJECT_BUSY") {
        attached = await attachBusyTask().catch(() => false);
      }
      if (error instanceof ApiClientError && error.code === "GENERATION_PREFLIGHT_STALE") {
        setResult(null);
        setConfirmOpen(false);
        if (manualRegeneration) setManualSession("CONFIGURING");
      }
      setSubmissionPending(false);
      if (!attached) {
        if (manualRegeneration && !(error instanceof ApiClientError && error.code === "GENERATION_PREFLIGHT_STALE")) {
          setManualSession("PREFLIGHT_READY");
        }
        setSubmitError(requestErrorMessage(error));
      }
    } finally {
      submitGuard.current = false;
    }
  }

  async function recheckAcceptedTask() {
    if (reconciliationPending || !acceptedStatusUncertain) return;
    setReconciliationPending(true);
    try {
      const [tasksResult, statusResult] = await Promise.all([
        getProjectTasks(projectId),
        getShotGenerationStatus(projectId, shotId),
      ]);
      const expectedOperation = selectedPromptGeneration
        ? "SHOT_PROMPT_VERSION_GENERATE"
        : regenerating ? "SHOT_REGENERATE" : "SHOT_GENERATE";
      const matching = tasksResult.data.tasks.filter((task) =>
        task.operation === expectedOperation
        && task.target_id === shotId
        && (
          acceptedCorrelationId === null
          || task.correlation_id === acceptedCorrelationId
        )
      );
      if (matching.length !== 1) return;
      if (
        manualRegeneration
        && acceptedCorrelationId === null
        && statusResult.data.generation_intent !== intent
      ) return;
      const task = matching[0];
      setActiveTask(task);
      setGenerationStatus(statusResult.data);
      setAcceptedStatusUncertain(false);
      setAcceptedCorrelationId(null);
      if (manualRegeneration) {
        setManualSession(
          task.status === "SUCCEEDED"
            ? "SUCCEEDED"
            : ["FAILED", "INTERRUPTED"].includes(task.status)
              ? "FAILED"
              : "TASK_ATTACHED",
        );
      }
      setSubmitError(
        ["FAILED", "INTERRUPTED"].includes(task.status)
          ? task.error?.message ?? "视频生成任务未能完成。"
          : null,
      );
      if (task.status === "SUCCEEDED") await onCompleted?.();
    } catch {
      // Keep the paid action locked. The user may retry this GET-only check.
    } finally {
      setReconciliationPending(false);
    }
  }

  async function resumeGeneration() {
    if (submitGuard.current || taskRunning || !actionGenerationStatus?.resume_available) return;
    submitGuard.current = true;
    setSubmitError(null);
    try {
      setActiveTask((await resumeShotGeneration(projectId, shotId)).data);
      if (manualRegeneration) setManualSession("TASK_ATTACHED");
    } catch (error) {
      setSubmitError(requestErrorMessage(error));
      await loadGenerationState().catch(() => undefined);
    } finally {
      submitGuard.current = false;
    }
  }

  return (
    <section className="shot-generation-preparation" aria-labelledby={`generation-preparation-title-${intent}`}>
      <div className="stage-section-heading">
        <p className="page-kicker">{selectedPromptGeneration ? "ADOPTED AI PROMPT + VIDEO" : manualRegeneration ? "NEW PROMPT + VIDEO VERSION" : regenerating ? "NEW VIDEO VERSION" : "GENERATION PREPARATION"}</p>
        <h2 id={`generation-preparation-title-${intent}`}>{selectedPromptGeneration ? "使用 AI 修改后的 Prompt 生成" : manualRegeneration ? "手动编辑 Prompt 并生成" : regenerating ? "用当前 Prompt 重新生成" : "生成设置"}</h2>
        <p>{regenerating
          ? selectedPromptGeneration
            ? "使用已经采用的 AI Revision Prompt 创建新 Video；不会修改 Prompt Version 或当前正式组合。"
            : manualRegeneration
            ? "将创建新的 Prompt Version 与 Video Version；当前正式组合会保留到新视频审核通过。"
            : "可重新选择模型与 Visual Input。当前正式版本会保留，只有新版本审核通过后才会替换。"
          : "先检查模型、Visual Input 和素材兼容性，再明确确认付费生成。"}</p>
      </div>
      {selectedPromptGeneration && !selectedPromptOpen && (
        <div className="manual-prompt-entry">
          <dl className="generation-context-facts">
            <div><dt>Prompt Version</dt><dd>v{targetPromptVersion ?? "-"}</dd></div>
            <div><dt>Source</dt><dd>AI Revision</dd></div>
          </dl>
          <button
            className="primary-button"
            type="button"
            disabled={paidActionBlocked || loadState !== "success" || !options?.eligible}
            onClick={() => {
              clearResult();
              setSelectedPromptOpen(true);
            }}
          >使用此 Prompt 生成视频</button>
          <p>当前正式 Video 与 Prompt 会保留，只有新视频审核通过后才会替换。</p>
        </div>
      )}
      {manualRegeneration && !editorOpen && (
        <div className="manual-prompt-entry">
          <button className="primary-button" type="button" disabled={paidActionBlocked || !manualPrompt} onClick={() => {
            setActiveTask(null);
            setAcceptedStatusUncertain(false);
            setAcceptedCorrelationId(null);
            setManualSession("EDITING");
            setEditorOpen(true);
          }}>编辑 Prompt 并生成新版本</button>
          <p>编辑内容只会先保留在当前页面；取消不会创建 Prompt Version 或生成任务。</p>
        </div>
      )}
      {loadState === "loading" && (!manualRegeneration || editorOpen) && !selectedPromptGeneration && <p aria-live="polite">正在读取生成选项…</p>}
      {loadState === "error" && (!manualRegeneration || editorOpen) && <p role="alert">{loadError}</p>}
      {loadState === "success" && options && (!selectedPromptGeneration || selectedPromptOpen) && (
        <>
          {manualRegeneration && editorOpen && (
            <section className="manual-prompt-editor" aria-labelledby="manual-prompt-editor-title">
              <div className="stage-section-heading">
                <p className="page-kicker">MANUAL PROMPT EDIT</p>
                <h3 id="manual-prompt-editor-title">编辑视觉 Prompt 核心</h3>
                <p>系统确定性约束由 Core 重新验证和编译，无需在此手动复制。</p>
              </div>
              <dl className="generation-context-facts">
                <div><dt>当前 Video</dt><dd>v{manualPrompt?.videoVersion ?? options.shot.base_video_version ?? "-"}</dd></div>
                <div><dt>当前 Prompt</dt><dd>v{manualPrompt?.promptVersion ?? options.shot.prompt_version ?? "-"}</dd></div>
              </dl>
              <label className="manual-prompt-label">视觉 Prompt 核心
                <textarea
                  aria-label="视觉 Prompt 核心"
                  rows={12}
                  value={editedPrompt}
                  disabled={paidActionBlocked}
                   onChange={(event) => {
                     setEditedPrompt(event.target.value);
                     setEditorError(null);
                     setManualSession("EDITING");
                     setReviewOpen(false);
                     clearResult();
                  }}
                />
              </label>
              <div className="manual-prompt-comparison" aria-label="Prompt 修改对照">
                <div><h4>原 Prompt</h4><p>{manualPrompt?.editablePrompt ?? "无法读取"}</p></div>
                <div><h4>修改后 Prompt</h4><p>{editedPrompt || "尚未输入"}</p></div>
              </div>
              <p className="manual-prompt-version-note">确认生成时将创建新的 Prompt Version；当前 Prompt 不会被覆盖。</p>
              {editorError && <p className="generation-submit-error" role="alert">{editorError}</p>}
              <div className="generation-confirm-actions">
                <button className="secondary-button" type="button" disabled={paidActionBlocked} onClick={() => {
                   setEditedPrompt(manualPrompt?.editablePrompt ?? "");
                   setEditorError(null);
                   setManualSession("IDLE");
                   setActiveTask(null);
                   setGenerationStatus(null);
                   setAcceptedStatusUncertain(false);
                   setAcceptedCorrelationId(null);
                   setReviewOpen(false);
                   setEditorOpen(false);
                  clearResult();
                }}>取消</button>
                <button className="primary-button" type="button" disabled={paidActionBlocked} onClick={() => {
                  const value = editedPrompt.trim();
                  if (!value) {
                    setEditorError("修改后的视觉 Prompt 核心不能为空。");
                    return;
                  }
                  if (value === manualPrompt?.editablePrompt.trim()) {
                    setEditorError("Prompt 没有发生变化，无需创建新版本。");
                    return;
                  }
                   setEditedPrompt(value);
                   setEditorError(null);
                   setActiveTask(null);
                   setGenerationStatus(null);
                   setAcceptedStatusUncertain(false);
                   setAcceptedCorrelationId(null);
                   setManualSession("CONFIGURING");
                   setReviewOpen(true);
                 }}>继续检查生成配置</button>
              </div>
            </section>
          )}
          {(!manualRegeneration || reviewOpen) && <>
          {(submissionPending || taskRunning || (statusCopy && actionGenerationStatus?.state !== "NOT_STARTED")) && (
            <section className="generation-task-status" aria-live="polite">
              <h3>{submissionPending
                ? "正在确认后端是否已接受生成任务…"
                : activeTask?.status === "QUEUED"
                ? "排队中…"
                : statusCopy && actionGenerationStatus?.state !== "NOT_STARTED"
                  ? statusCopy
                  : "正在生成视频…"}</h3>
              <p>页面切换不会取消任务；重新打开页面后会继续读取持久化状态。</p>
            </section>
          )}
          {actionGenerationStatus?.state === "SUBMISSION_UNKNOWN" && (
            <div className="generation-submit-error" role="alert">
              <strong>无法确认视频生成请求是否已提交，请不要立即重复生成。</strong>
              <p>当前不会提供普通重试或继续提交按钮，以避免重复付费。</p>
            </div>
          )}
          {acceptedStatusUncertain && (
            <div className="generation-submit-error" role="alert">
              <strong>生成请求已被后端接受，但当前无法读取任务状态。请勿重复提交生成请求。</strong>
              <p>重新检查只会读取现有任务状态，不会再次调用视频模型。</p>
              <button
                className="secondary-button"
                type="button"
                disabled={reconciliationPending}
                onClick={() => void recheckAcceptedTask()}
              >{reconciliationPending ? "正在重新检查状态…" : "重新检查状态"}</button>
            </div>
          )}
          {actionGenerationStatus?.resume_available && !paidActionBlocked && (
            <div className="generation-resume-panel">
              <p>上次 Web 任务中断，已有远端或本地进度可以安全继续。</p>
              <button className="primary-button" type="button" onClick={() => void resumeGeneration()}>继续生成</button>
            </div>
          )}
          <dl className="generation-context-facts">
            <div><dt>镜头</dt><dd>{options.shot.shot_id.replace("shot_", "Shot ")}</dd></div>
            {selectedPromptGeneration ? <>
              <div><dt>当前正式 Video</dt><dd>{options.shot.official_video_version ? `v${options.shot.official_video_version}` : "尚无"}</dd></div>
              <div><dt>当前正式 Prompt</dt><dd>{options.shot.official_prompt_version ? `v${options.shot.official_prompt_version}` : "尚无"}</dd></div>
              <div><dt>生成使用 Prompt</dt><dd>{optionVersions?.generationPromptVersion ? `v${optionVersions.generationPromptVersion}` : "未就绪"}</dd></div>
              <div><dt>Prompt Source</dt><dd>AI Revision</dd></div>
              <div><dt>此次将生成</dt><dd>{versionLabel("Video", optionVersions?.nextVideoVersion ?? null)}</dd></div>
            </> : manualRegeneration ? <>
              <div><dt>基础 Prompt</dt><dd>{optionVersions?.basePromptVersion ? `v${optionVersions.basePromptVersion}` : "未就绪"}</dd></div>
              <div><dt>此次将创建</dt><dd>{versionLabel("Prompt", optionVersions?.nextPromptVersion ?? null)}</dd></div>
              <div><dt>生成将使用</dt><dd>{versionLabel("Prompt", optionVersions?.generationPromptVersion ?? null)}</dd></div>
              <div><dt>当前正式 Video</dt><dd>{options.shot.official_video_version ? `v${options.shot.official_video_version}` : "尚无"}</dd></div>
              <div><dt>此次将生成</dt><dd>{versionLabel("Video", optionVersions?.nextVideoVersion ?? null)}</dd></div>
            </> : <>
              <div><dt>将使用 Prompt</dt><dd>{optionVersions?.generationPromptVersion ? `v${optionVersions.generationPromptVersion}` : "未就绪"}</dd></div>
              {regenerating && <div><dt>当前正式版本</dt><dd>{options.shot.official_video_version ? `v${options.shot.official_video_version}` : "尚无"}</dd></div>}
              {regenerating && <div><dt>此次将生成</dt><dd>{options.shot.next_video_version ? `v${options.shot.next_video_version}` : "待计算"}</dd></div>}
            </>}
            <div><dt>时长</dt><dd>{options.shot.duration_seconds} 秒</dd></div>
            <div><dt>分辨率</dt><dd>{options.shot.resolution}</dd></div>
          </dl>
          {!options.eligible && !paidActionBlocked && !actionGenerationStatus?.resume_available && (
            <div className="generation-issues" role="status"><h3>{regenerating ? "当前尚不能生成新的待审核版本" : "当前尚不能检查初次生成配置"}</h3><ul>{options.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}</ul></div>
          )}
          <fieldset className="generation-fieldset" disabled={!options.eligible || checking || paidActionBlocked}>
            <legend>模型选择</legend>
            <label><input type="radio" name={`model-selection-${intent}`} checked={selection === "AUTO"} onChange={() => changeSelection("AUTO")} />自动</label>
            <label><input type="radio" name={`model-selection-${intent}`} checked={selection === "MANUAL"} onChange={() => changeSelection("MANUAL")} />手动</label>
            {selection === "MANUAL" && <label className="generation-select-label">视频模型<select aria-label="视频模型" value={requestedModel ?? ""} onChange={(event) => { setRequestedModel(event.target.value || null); clearResult(); }}>{options.models.map((model) => { const compatible = model.supported_visual_input_modes.includes(visualMode); return <option key={model.model_id} value={model.model_id}>{model.display_name}{!model.available ? " · 未配置" : ""}{!compatible ? " · 不兼容当前模式" : ""}</option>; })}</select></label>}
            {selection === "MANUAL" && selectedModel && !manualCompatible && <p className="generation-inline-warning" role="status">所选模型不支持当前 Visual Input；配置检查不会自动更换模型。</p>}
          </fieldset>
          <fieldset className="generation-fieldset" disabled={!options.eligible || checking || paidActionBlocked}>
            <legend>Visual Input</legend>
            <div className="visual-mode-options">{options.visual_input_modes.map((option) => <label key={option.mode} className="visual-mode-option"><span><input type="radio" name={`visual-mode-${intent}`} checked={visualMode === option.mode} onChange={() => changeVisualMode(option.mode)} />{option.display_name}</span><small>{option.description}</small></label>)}</div>
          </fieldset>
          {visualMode !== "none" && <div className="reference-selector"><h3>选择项目已有素材</h3>{assets.length === 0 ? <p className="stage-empty-copy">当前项目暂无参考素材。可先<Link to={projectWorkspacePath(projectId)}>前往项目素材库添加</Link>。</p> : <div className="reference-grid">{assets.map((asset) => <label key={asset.asset_id} className="reference-card"><input type="radio" name={`reference-asset-${intent}`} checked={assetId === asset.asset_id} disabled={!options.eligible || checking || paidActionBlocked} onChange={() => { setAssetId(asset.asset_id); if (manualRegeneration) setManualSession("CONFIGURING"); clearResult(); }} /><img src={getReferenceImageUrl(projectId, asset.asset_id)} alt={`${asset.filename} 参考图预览`} /><span>{asset.filename}</span><small>{asset.width} × {asset.height}</small></label>)}</div>}</div>}
          <div className="generation-preflight-actions"><button className="primary-button" type="button" disabled={!options.eligible || checking || paidActionBlocked} onClick={() => void checkConfiguration()}>{checking ? "正在检查生成配置…" : "检查生成配置"}</button></div>
          <p className="paid-call-warning">真正生成视频会调用付费视频模型；只有最终确认后才会提交请求。</p>
          {submitError && <p className="generation-submit-error" role="alert">{submitError}</p>}
          {result && (
            <section className={`preflight-summary ${result.ready ? "preflight-summary-ready" : "preflight-summary-not-ready"}`} aria-label="生成前确认摘要">
              <p className="page-kicker">PREFLIGHT</p><h3>{result.ready ? "配置检查通过" : "配置尚未就绪"}</h3>
              <dl>
                <div><dt>镜头</dt><dd>{result.shot.shot_id.replace("shot_", "Shot ")}</dd></div>
                {selectedPromptGeneration ? <>
                  <div><dt>当前正式 Video</dt><dd>{result.shot.official_video_version ? `v${result.shot.official_video_version}` : "尚无"}</dd></div>
                  <div><dt>当前正式 Prompt</dt><dd>{result.shot.official_prompt_version ? `v${result.shot.official_prompt_version}` : "尚无"}</dd></div>
                  <div><dt>生成使用 Prompt</dt><dd>{versionLabel("Prompt", resultVersions?.generationPromptVersion ?? null)}</dd></div>
                  <div><dt>此次将生成</dt><dd>{versionLabel("Video", resultVersions?.nextVideoVersion ?? null)}</dd></div>
                </> : manualRegeneration ? <>
                  <div><dt>基础 Prompt</dt><dd>{resultVersions?.basePromptVersion ? `v${resultVersions.basePromptVersion}` : "未就绪"}</dd></div>
                  <div><dt>此次将创建</dt><dd>{versionLabel("Prompt", resultVersions?.nextPromptVersion ?? null)}</dd></div>
                  <div><dt>生成将使用</dt><dd>{versionLabel("Prompt", resultVersions?.generationPromptVersion ?? null)}</dd></div>
                  <div><dt>此次将生成</dt><dd>{versionLabel("Video", resultVersions?.nextVideoVersion ?? null)}</dd></div>
                </> : <div><dt>将使用 Prompt</dt><dd>{resultVersions?.generationPromptVersion ? `v${resultVersions.generationPromptVersion}` : "未就绪"}</dd></div>}
                <div><dt>时长</dt><dd>{result.shot.duration_seconds} 秒</dd></div><div><dt>分辨率</dt><dd>{result.shot.resolution}</dd></div><div><dt>Visual Input</dt><dd>{options.visual_input_modes.find((item) => item.mode === result.resolved?.visual_input_mode)?.display_name ?? visualMode}{selectedAsset ? ` · ${selectedAsset.asset_id}` : ""}</dd></div><div><dt>模型</dt><dd>{result.resolved?.model_display_name ?? "未解析"}</dd></div><div><dt>Provider</dt><dd>{result.resolved?.provider_display_name ?? "未解析"}</dd></div><div><dt>API Version</dt><dd>{result.resolved?.api_version ?? "未解析"}</dd></div><div><dt>生成模式</dt><dd>{result.resolved?.generation_mode_display_name ?? "未解析"}</dd></div>
              </dl>
              {result.issues.length > 0 && <ul className="generation-issues-list">{result.issues.map((issue) => <li key={issue.code}>{issue.message}</li>)}</ul>}
              {result.ready && result.preflight_fingerprint && <button className="primary-button" type="button" disabled={paidActionBlocked} onClick={() => setConfirmOpen(true)}>{selectedPromptGeneration ? "使用此 Prompt 生成视频" : manualRegeneration ? "确认 Prompt 修改与生成配置" : regenerating ? "生成新的待审核版本" : "生成视频"}</button>}
              {!result.ready && <p>配置未通过，不会创建任务或调用视频模型。</p>}
            </section>
          )}
          {confirmOpen && result?.ready && result.resolved && (
            <div className="generation-confirm-backdrop" role="presentation"><section className="generation-confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="paid-generation-title">
              <p className="page-kicker">FINAL CONFIRMATION</p><h3 id="paid-generation-title">{selectedPromptGeneration ? "确认使用 AI Revision Prompt 生成" : manualRegeneration ? "确认修改并生成" : regenerating ? "确认生成新的待审核版本" : "确认生成视频"}</h3>
              <dl>
                <div><dt>Shot</dt><dd>{result.shot.shot_id.replace("shot_", "Shot ")}</dd></div>
                {selectedPromptGeneration ? <>
                  <div><dt>当前正式</dt><dd>{versionLabel("Video", result.shot.official_video_version ?? null)} / {versionLabel("Prompt", result.shot.official_prompt_version ?? null)}</dd></div>
                  <div><dt>本次视频生成将使用</dt><dd>{versionLabel("Prompt", resultVersions?.generationPromptVersion ?? null)}</dd></div>
                  <div><dt>将创建</dt><dd>{versionLabel("Video", resultVersions?.nextVideoVersion ?? null)}</dd></div>
                </> : manualRegeneration ? <>
                  <div><dt>当前基础 Prompt</dt><dd>{resultVersions?.basePromptVersion ? `v${resultVersions.basePromptVersion}` : "未就绪"}</dd></div>
                  <div><dt>将创建新 Prompt</dt><dd>{resultVersions?.nextPromptVersion ? `v${resultVersions.nextPromptVersion}` : "待计算"}</dd></div>
                  <div><dt>本次视频生成将使用</dt><dd>{versionLabel("Prompt", resultVersions?.generationPromptVersion ?? null)}</dd></div>
                  <div><dt>将创建 Video</dt><dd>{resultVersions?.nextVideoVersion ? `v${resultVersions.nextVideoVersion}` : "待计算"}</dd></div>
                </> : <div><dt>Prompt Version</dt><dd>v{result.shot.prompt_version}</dd></div>}
                <div><dt>Duration</dt><dd>{result.shot.duration_seconds} 秒</dd></div><div><dt>Resolution</dt><dd>{result.shot.resolution}</dd></div><div><dt>Visual Input</dt><dd>{result.resolved.visual_input_mode}{selectedAsset ? ` · ${selectedAsset.asset_id}` : ""}</dd></div><div><dt>Provider</dt><dd>{result.resolved.provider_display_name}</dd></div><div><dt>Model</dt><dd>{result.resolved.model_display_name}</dd></div><div><dt>API Version</dt><dd>{result.resolved.api_version}</dd></div><div><dt>Generation Mode</dt><dd>{result.resolved.generation_mode_display_name}</dd></div>
              </dl>
              {manualRegeneration && <p>本次将创建 {versionLabel("Prompt", resultVersions?.nextPromptVersion ?? null)}，并使用 {versionLabel("Prompt", resultVersions?.generationPromptVersion ?? null)} 生成 {versionLabel("Video", resultVersions?.nextVideoVersion ?? null)}。</p>}
              {selectedPromptGeneration && <p>本次将使用 {versionLabel("Prompt", resultVersions?.generationPromptVersion ?? null)} 生成 {versionLabel("Video", resultVersions?.nextVideoVersion ?? null)}；不会创建或修改 Prompt Version。</p>}
              <p className="paid-call-warning"><strong>{selectedPromptGeneration || manualRegeneration ? "确认后将调用付费视频模型。" : regenerating ? "确认后将调用付费视频模型并创建新的视频版本。" : "确认后将向视频生成模型提交付费请求。"}</strong></p>
              <div className="generation-confirm-actions"><button className="secondary-button" type="button" disabled={submissionPending} onClick={() => setConfirmOpen(false)}>取消</button><button className="primary-button" type="button" disabled={submissionPending || acceptedStatusUncertain} onClick={() => void confirmGeneration()}>{selectedPromptGeneration ? "确认使用此 Prompt 生成" : manualRegeneration ? "确认修改并生成" : "确认并生成视频"}</button></div>
            </section></div>
          )}
          </>}
        </>
      )}
    </section>
  );
}
