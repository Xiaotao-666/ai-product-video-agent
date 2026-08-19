import { useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  generateCreative,
  regenerateCreative,
  reviseCreative,
} from "../../api/client";
import type {
  AvailableAction,
  TaskOperation,
  TaskRecord,
} from "../../api/types";
import {
  toTaskActionError,
  useProjectTaskPolling,
} from "../../hooks/useProjectTaskPolling";
import type { TaskActionError } from "../../hooks/useProjectTaskPolling";


const MAX_FEEDBACK_LENGTH = 4_000;
const CREATIVE_TASK_OPERATIONS: ReadonlySet<TaskOperation> = new Set([
  "CREATIVE_GENERATE",
  "CREATIVE_REVISE",
  "CREATIVE_REGENERATE",
]);

type CreativeTaskOperation =
  | "CREATIVE_GENERATE"
  | "CREATIVE_REVISE"
  | "CREATIVE_REGENERATE";

interface CreativeGenerateActionProps {
  projectId: string;
  availableActions: AvailableAction[];
  hasCreative: boolean | null;
  onTerminalRefresh: () => Promise<void>;
  onActiveTaskChange?: (active: boolean) => void;
}

function isCreativeTask(
  task: TaskRecord,
): task is TaskRecord & { operation: CreativeTaskOperation } {
  return CREATIVE_TASK_OPERATIONS.has(task.operation);
}

function taskStatusCopy(
  task: TaskRecord | null,
  submittingOperation: CreativeTaskOperation | null,
  hasCreative: boolean | null,
): string {
  if (submittingOperation === "CREATIVE_GENERATE") {
    return "正在提交创意生成任务…";
  }
  if (task?.operation === "CREATIVE_GENERATE" && task.status === "QUEUED") {
    return "排队中…";
  }
  if (task?.operation === "CREATIVE_GENERATE" && task.status === "RUNNING") {
    return "正在生成创意…";
  }
  if (hasCreative) return "已生成";
  if (!task || task.operation !== "CREATIVE_GENERATE") {
    return hasCreative === false ? "未开始" : "正在确认状态…";
  }
  if (task.status === "SUCCEEDED") return "生成成功";
  if (task.status === "FAILED") return "生成失败";
  if (task.status === "INTERRUPTED") return "任务中断";
  return "任务已取消";
}

function creativeTaskCopy(
  operation: CreativeTaskOperation,
  status: TaskRecord["status"] | "SUBMITTING",
): string {
  if (operation === "CREATIVE_REVISE") {
    if (status === "SUBMITTING") return "正在提交修改任务…";
    if (status === "QUEUED") return "修改任务已提交，排队中…";
    if (status === "RUNNING") return "正在修改创意…";
    if (status === "SUCCEEDED") return "创意修改完成。";
    if (status === "FAILED") return "修改创意失败。";
    if (status === "INTERRUPTED") return "修改任务已中断。";
    return "修改任务已取消。";
  }
  if (operation === "CREATIVE_REGENERATE") {
    if (status === "SUBMITTING") return "正在提交重新生成任务…";
    if (status === "QUEUED") return "重新生成任务已提交，排队中…";
    if (status === "RUNNING") return "正在重新生成创意…";
    if (status === "SUCCEEDED") return "创意重新生成完成。";
    if (status === "FAILED") return "重新生成创意失败。";
    if (status === "INTERRUPTED") return "重新生成任务已中断。";
    return "重新生成任务已取消。";
  }
  return "正在生成创意…";
}

function safeErrorCopy(error: TaskActionError): string {
  if (error.code === "PROJECT_BUSY") {
    return "项目当前正在执行其他任务。";
  }
  if (error.code === "CAPABILITY_UNAVAILABLE") {
    return "创意生成服务尚未配置。";
  }
  if (error.code === "ACTION_NOT_ALLOWED") {
    return "当前项目状态不允许执行此操作，请刷新后确认最新状态。";
  }
  if (error.code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "Creative 请求暂时无法处理。";
}

export function CreativeGenerateAction({
  projectId,
  availableActions,
  hasCreative,
  onTerminalRefresh,
  onActiveTaskChange,
}: CreativeGenerateActionProps) {
  const [submittingOperation, setSubmittingOperation] =
    useState<CreativeTaskOperation | null>(null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [regenerateConfirming, setRegenerateConfirming] = useState(false);
  const submissionGuard = useRef(false);
  const canGenerate = availableActions.includes("GENERATE_CREATIVE");
  const canRevise = availableActions.includes("REVISE_CREATIVE");
  const canRegenerate = availableActions.includes("REGENERATE_CREATIVE");
  const {
    task,
    setTask,
    error,
    setError,
    active,
    terminalRefreshPending,
    attachToExistingTask,
  } = useProjectTaskPolling({
    projectId,
    isTask: isCreativeTask,
    onTerminalRefresh,
  });
  const busy = active || submittingOperation !== null || terminalRefreshPending;
  const normalizedFeedback = feedback.trim();

  useEffect(() => {
    setFeedbackOpen(false);
    setFeedback("");
    setRegenerateConfirming(false);
    setSubmittingOperation(null);
  }, [projectId]);

  useEffect(() => {
    onActiveTaskChange?.(busy);
    return () => onActiveTaskChange?.(false);
  }, [busy, onActiveTaskChange]);

  const submit = async (operation: CreativeTaskOperation) => {
    const allowed =
      (operation === "CREATIVE_GENERATE" && canGenerate) ||
      (operation === "CREATIVE_REVISE" && canRevise) ||
      (operation === "CREATIVE_REGENERATE" && canRegenerate);
    if (
      submissionGuard.current ||
      !allowed ||
      active ||
      (operation === "CREATIVE_REVISE" &&
        (normalizedFeedback.length === 0 ||
          normalizedFeedback.length > MAX_FEEDBACK_LENGTH))
    ) {
      return;
    }
    submissionGuard.current = true;
    setSubmittingOperation(operation);
    setError(null);
    try {
      const result =
        operation === "CREATIVE_GENERATE"
          ? await generateCreative(projectId)
          : operation === "CREATIVE_REVISE"
            ? await reviseCreative(projectId, normalizedFeedback)
            : await regenerateCreative(projectId);
      if (
        result.data.project_id !== projectId ||
        result.data.operation !== operation
      ) {
        throw new ApiClientError({
          message: "Creative task response did not match the request.",
          code: "INVALID_RESPONSE",
          correlationId: result.correlationId,
        });
      }
      setTask(result.data);
      setFeedbackOpen(false);
      setRegenerateConfirming(false);
    } catch (caught) {
      const mapped = toTaskActionError(caught);
      if (mapped.code === "PROJECT_BUSY") {
        try {
          if (await attachToExistingTask()) {
            setFeedbackOpen(false);
            setRegenerateConfirming(false);
            return;
          }
        } catch (recoveryError) {
          setError(toTaskActionError(recoveryError));
          return;
        }
      }
      setError(mapped);
    } finally {
      submissionGuard.current = false;
      setSubmittingOperation(null);
    }
  };

  const showGenerateButton =
    canGenerate &&
    !(task?.operation === "CREATIVE_GENERATE" && task.status === "SUCCEEDED") &&
    !active;
  const statusCopy = taskStatusCopy(task, submittingOperation, hasCreative);
  const revisionTask =
    task &&
    isCreativeTask(task) &&
    (task.operation === "CREATIVE_REVISE" ||
      task.operation === "CREATIVE_REGENERATE")
      ? task
      : null;
  const pendingRevisionOperation =
    submittingOperation === "CREATIVE_REVISE" ||
    submittingOperation === "CREATIVE_REGENERATE"
      ? submittingOperation
      : null;
  const showReviewActions =
    canRevise ||
    canRegenerate ||
    revisionTask !== null ||
    pendingRevisionOperation !== null;

  return (
    <>
    <section
      className="stage-section creative-action-section"
      aria-labelledby="creative-action-title"
    >
      <div className="stage-section-heading creative-action-heading">
        <div>
          <p className="page-kicker">CREATIVE GENERATION</p>
          <h2 id="creative-action-title">生成创意</h2>
        </div>
        <span className="creative-task-status" role="status">
          {statusCopy}
        </span>
      </div>

      {task?.operation === "CREATIVE_GENERATE" &&
        task.status === "FAILED" &&
        !hasCreative && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>创意生成失败。</strong>
          <span>{task.error?.message ?? "请刷新项目状态后重试。"}</span>
          <small>错误编号：{task.correlation_id}</small>
        </div>
      )}
      {task?.operation === "CREATIVE_GENERATE" &&
        task.status === "INTERRUPTED" &&
        !hasCreative && (
        <div className="creative-action-message" role="status">
          <strong>上次生成任务被中断。</strong>
          <span>已重新检查 Creative 与 Workflow，请根据当前项目状态继续。</span>
        </div>
      )}
      {error && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{safeErrorCopy(error)}</strong>
          {error.correlationId && <small>错误编号：{error.correlationId}</small>}
        </div>
      )}

      {showGenerateButton && (
        <button
          className="primary-button"
          type="button"
          disabled={busy}
          onClick={() => void submit("CREATIVE_GENERATE")}
        >
          {submittingOperation === "CREATIVE_GENERATE"
            ? "正在提交…"
            : "生成创意"}
        </button>
      )}
      {!canGenerate &&
        !active &&
        hasCreative === false &&
        task?.status !== "SUCCEEDED" && (
        <p className="stage-empty-copy">当前项目状态不允许生成创意。</p>
      )}
    </section>

    {showReviewActions && (
      <section
        className="stage-section creative-revision-section"
        aria-labelledby="creative-revision-title"
      >
        <div className="stage-section-heading creative-action-heading">
          <div>
            <p className="page-kicker">AI REVISION</p>
            <h2 id="creative-revision-title">修改或重新生成</h2>
          </div>
          {(revisionTask || pendingRevisionOperation) && (
            <span className="creative-task-status" role="status">
              {revisionTask
                ? creativeTaskCopy(revisionTask.operation, revisionTask.status)
                : creativeTaskCopy(pendingRevisionOperation!, "SUBMITTING")}
            </span>
          )}
        </div>

        {(active || pendingRevisionOperation) && (
          <div className="creative-action-message" role="status">
            <strong>正在生成新的创意方案…</strong>
            <span>当前 Creative 会继续保留并显示，任务成功后才会刷新。</span>
          </div>
        )}

        {revisionTask?.status === "FAILED" && (
          <div className="creative-action-message creative-action-error" role="alert">
            <strong>{creativeTaskCopy(revisionTask.operation, "FAILED")}</strong>
            <span>{revisionTask.error?.message ?? "请刷新项目状态后重试。"}</span>
            <small>错误编号：{revisionTask.correlation_id}</small>
          </div>
        )}
        {revisionTask?.status === "INTERRUPTED" && (
          <div className="creative-action-message" role="status">
            <strong>{creativeTaskCopy(revisionTask.operation, "INTERRUPTED")}</strong>
            <span>已重新读取 Creative 与 Workflow，不会自动再次提交。</span>
          </div>
        )}
        {revisionTask?.status === "SUCCEEDED" && (
          <div className="creative-action-message creative-revision-success" role="status">
            <strong>{creativeTaskCopy(revisionTask.operation, "SUCCEEDED")}</strong>
            <span>新 Creative 已载入，仍需再次人工审核。</span>
          </div>
        )}

        {!feedbackOpen && !regenerateConfirming && !active && (
          <div className="creative-review-buttons">
            {canRevise && (
              <button
                className="secondary-button"
                type="button"
                disabled={busy}
                onClick={() => {
                  setError(null);
                  setFeedbackOpen(true);
                }}
              >
                修改创意
              </button>
            )}
            {canRegenerate && (
              <button
                className="secondary-button"
                type="button"
                disabled={busy}
                onClick={() => {
                  setError(null);
                  setRegenerateConfirming(true);
                }}
              >
                重新生成创意
              </button>
            )}
          </div>
        )}

        {feedbackOpen && canRevise && !active && (
          <div className="creative-feedback-panel">
            <label htmlFor="creative-revision-feedback">修改意见</label>
            <textarea
              id="creative-revision-feedback"
              maxLength={MAX_FEEDBACK_LENGTH}
              placeholder="请说明希望保留、删除或调整的内容，例如“保留核心概念，但不要出现人物，增加产品微距和清爽感。”"
              rows={5}
              value={feedback}
              disabled={submittingOperation !== null}
              onChange={(event) => setFeedback(event.target.value)}
            />
            <small>{feedback.length}/{MAX_FEEDBACK_LENGTH}</small>
            <div className="creative-approval-buttons">
              <button
                className="secondary-button"
                type="button"
                disabled={submittingOperation !== null}
                onClick={() => {
                  setFeedbackOpen(false);
                  setFeedback("");
                }}
              >
                取消
              </button>
              <button
                className="primary-button"
                type="button"
                disabled={
                  submittingOperation !== null ||
                  normalizedFeedback.length === 0 ||
                  normalizedFeedback.length > MAX_FEEDBACK_LENGTH
                }
                onClick={() => void submit("CREATIVE_REVISE")}
              >
                {submittingOperation === "CREATIVE_REVISE"
                  ? "正在提交…"
                  : "提交修改"}
              </button>
            </div>
          </div>
        )}

        {regenerateConfirming && canRegenerate && !active && (
          <div className="creative-approval-confirmation" role="dialog" aria-modal="false">
            <strong>确认重新生成 Creative？</strong>
            <p>
              将根据原项目需求生成一套新方案。只有新方案完整生成并验证成功后，
              才会替换当前 Creative；失败时当前内容保持不变。
            </p>
            <div className="creative-approval-buttons">
              <button
                className="secondary-button"
                type="button"
                disabled={submittingOperation !== null}
                onClick={() => setRegenerateConfirming(false)}
              >
                取消
              </button>
              <button
                className="primary-button"
                type="button"
                disabled={submittingOperation !== null}
                onClick={() => void submit("CREATIVE_REGENERATE")}
              >
                {submittingOperation === "CREATIVE_REGENERATE"
                  ? "正在提交…"
                  : "确认重新生成"}
              </button>
            </div>
          </div>
        )}
      </section>
    )}
    </>
  );
}
