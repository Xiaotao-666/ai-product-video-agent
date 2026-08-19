import { useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  regenerateStoryboard,
  reviseStoryboard,
} from "../../api/client";
import type {
  AvailableAction,
  TaskRecord,
} from "../../api/types";
import {
  toTaskActionError,
  useProjectTaskPolling,
} from "../../hooks/useProjectTaskPolling";
import type { TaskActionError } from "../../hooks/useProjectTaskPolling";


const MAX_FEEDBACK_LENGTH = 4_000;
type RevisionOperation = "STORYBOARD_REVISE" | "STORYBOARD_REGENERATE";
type StoryboardTaskOperation = "STORYBOARD_GENERATE" | RevisionOperation;

interface StoryboardRevisionActionProps {
  projectId: string;
  availableActions: AvailableAction[];
  onTerminalRefresh: () => Promise<void>;
  onActiveTaskChange?: (active: boolean) => void;
}

function isStoryboardTask(
  task: TaskRecord,
): task is TaskRecord & { operation: StoryboardTaskOperation } {
  return (
    task.operation === "STORYBOARD_GENERATE" ||
    task.operation === "STORYBOARD_REVISE" ||
    task.operation === "STORYBOARD_REGENERATE"
  );
}

function isRevisionTask(
  task: TaskRecord,
): task is TaskRecord & { operation: RevisionOperation } {
  return (
    task.operation === "STORYBOARD_REVISE" ||
    task.operation === "STORYBOARD_REGENERATE"
  );
}

function taskCopy(
  operation: RevisionOperation,
  status: TaskRecord["status"] | "SUBMITTING",
): string {
  if (operation === "STORYBOARD_REVISE") {
    if (status === "SUBMITTING") return "正在提交修改任务…";
    if (status === "QUEUED") return "修改任务已提交，排队中…";
    if (status === "RUNNING") return "正在修改分镜…";
    if (status === "SUCCEEDED") return "分镜修改完成。";
    if (status === "FAILED") return "修改分镜失败。";
    if (status === "INTERRUPTED") return "修改任务已中断。";
    return "修改任务已取消。";
  }
  if (status === "SUBMITTING") return "正在提交重新生成任务…";
  if (status === "QUEUED") return "重新生成任务已提交，排队中…";
  if (status === "RUNNING") return "正在重新生成分镜…";
  if (status === "SUCCEEDED") return "分镜重新生成完成。";
  if (status === "FAILED") return "重新生成分镜失败。";
  if (status === "INTERRUPTED") return "重新生成任务已中断。";
  return "重新生成任务已取消。";
}

function safeErrorCopy(error: TaskActionError): string {
  if (error.code === "PROJECT_BUSY") {
    return "项目当前正在执行其他任务。";
  }
  if (error.code === "CAPABILITY_UNAVAILABLE") {
    return "分镜生成服务尚未配置。";
  }
  if (error.code === "ACTION_NOT_ALLOWED") {
    return "当前项目状态不允许执行此操作，请刷新后确认最新状态。";
  }
  if (error.code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "Storyboard 请求暂时无法处理。";
}

export function StoryboardRevisionAction({
  projectId,
  availableActions,
  onTerminalRefresh,
  onActiveTaskChange,
}: StoryboardRevisionActionProps) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [regenerateConfirming, setRegenerateConfirming] = useState(false);
  const [submittingOperation, setSubmittingOperation] =
    useState<RevisionOperation | null>(null);
  const submissionGuard = useRef(false);
  const canRevise = availableActions.includes("REVISE_STORYBOARD");
  const canRegenerate = availableActions.includes("REGENERATE_STORYBOARD");
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
    isTask: isStoryboardTask,
    onTerminalRefresh,
  });
  const busy = active || submittingOperation !== null || terminalRefreshPending;
  const normalizedFeedback = feedback.trim();
  const storyboardTask = task && isStoryboardTask(task) ? task : null;
  const revisionTask =
    storyboardTask && isRevisionTask(storyboardTask) ? storyboardTask : null;

  useEffect(() => {
    setFeedbackOpen(false);
    setFeedback("");
    setRegenerateConfirming(false);
    setSubmittingOperation(null);
    submissionGuard.current = false;
  }, [projectId]);

  useEffect(() => {
    onActiveTaskChange?.(busy);
    return () => onActiveTaskChange?.(false);
  }, [busy, onActiveTaskChange]);

  const submit = async (operation: RevisionOperation) => {
    const allowed =
      (operation === "STORYBOARD_REVISE" && canRevise) ||
      (operation === "STORYBOARD_REGENERATE" && canRegenerate);
    if (
      submissionGuard.current ||
      !allowed ||
      active ||
      (operation === "STORYBOARD_REVISE" &&
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
        operation === "STORYBOARD_REVISE"
          ? await reviseStoryboard(projectId, normalizedFeedback)
          : await regenerateStoryboard(projectId);
      if (
        result.data.project_id !== projectId ||
        result.data.operation !== operation
      ) {
        throw new ApiClientError({
          message: "Storyboard task response did not match the request.",
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

  if (
    !canRevise &&
    !canRegenerate &&
    !revisionTask &&
    !submittingOperation &&
    !error
  ) {
    return null;
  }

  return (
    <section
      className="stage-section creative-revision-section storyboard-revision-section"
      aria-labelledby="storyboard-revision-title"
    >
      <div className="stage-section-heading creative-action-heading">
        <div>
          <p className="page-kicker">AI REVISION</p>
          <h2 id="storyboard-revision-title">修改或重新生成分镜</h2>
        </div>
        {(revisionTask || submittingOperation) && (
          <span className="creative-task-status" role="status">
            {revisionTask
              ? taskCopy(revisionTask.operation, revisionTask.status)
              : taskCopy(submittingOperation!, "SUBMITTING")}
          </span>
        )}
      </div>

      {(active || submittingOperation) && (
        <div className="creative-action-message" role="status">
          <strong>
            {storyboardTask?.operation === "STORYBOARD_GENERATE"
              ? "正在生成分镜…"
              : revisionTask?.operation === "STORYBOARD_REGENERATE" ||
            submittingOperation === "STORYBOARD_REGENERATE"
              ? "正在重新生成分镜…"
              : "正在修改分镜…"}
          </strong>
          <span>当前 Storyboard 会继续保留并显示，任务成功后才会刷新。</span>
        </div>
      )}

      {revisionTask?.status === "FAILED" && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{taskCopy(revisionTask.operation, "FAILED")}</strong>
          <span>{revisionTask.error?.message ?? "请刷新项目状态后重试。"}</span>
          <small>错误编号：{revisionTask.correlation_id}</small>
        </div>
      )}
      {revisionTask?.status === "INTERRUPTED" && (
        <div className="creative-action-message" role="status">
          <strong>{taskCopy(revisionTask.operation, "INTERRUPTED")}</strong>
          <span>已重新读取 Storyboard 与 Workflow，不会自动再次提交。</span>
        </div>
      )}
      {revisionTask?.status === "SUCCEEDED" && (
        <div className="creative-action-message creative-revision-success" role="status">
          <strong>{taskCopy(revisionTask.operation, "SUCCEEDED")}</strong>
          <span>新 Storyboard 已载入，仍需再次人工审核。</span>
        </div>
      )}
      {error && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{safeErrorCopy(error)}</strong>
          {error.correlationId && <small>错误编号：{error.correlationId}</small>}
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
              修改分镜
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
              重新生成分镜
            </button>
          )}
        </div>
      )}

      {feedbackOpen && canRevise && !active && (
        <div className="creative-feedback-panel">
          <label htmlFor="storyboard-revision-feedback">修改意见</label>
          <textarea
            id="storyboard-revision-feedback"
            maxLength={MAX_FEEDBACK_LENGTH}
            placeholder="请说明希望保留、删除或调整的镜头、旁白、字幕、节奏或画面结构。例如：保留3个镜头；第二镜头减少旁白；第三镜头增加产品近景；前2秒继续不出现旁白和字幕。"
            rows={6}
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
                submittingOperation !== null || normalizedFeedback.length === 0
              }
              onClick={() => void submit("STORYBOARD_REVISE")}
            >
              {submittingOperation === "STORYBOARD_REVISE"
                ? "正在提交…"
                : "提交修改"}
            </button>
          </div>
        </div>
      )}

      {regenerateConfirming && canRegenerate && !active && (
        <div className="creative-approval-confirmation" role="dialog" aria-modal="false">
          <strong>确认重新生成 Storyboard？</strong>
          <p>
            将基于已审核Creative和原项目需求重新生成整套分镜，并重新执行Timeline规划。
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
              onClick={() => void submit("STORYBOARD_REGENERATE")}
            >
              {submittingOperation === "STORYBOARD_REGENERATE"
                ? "正在提交…"
                : "确认重新生成"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
