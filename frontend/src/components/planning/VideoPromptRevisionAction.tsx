import { useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  regenerateVideoPrompts,
  reviseVideoPrompts,
} from "../../api/client";
import type { AvailableAction, TaskRecord } from "../../api/types";
import {
  toTaskActionError,
  useProjectTaskPolling,
} from "../../hooks/useProjectTaskPolling";
import type { TaskActionError } from "../../hooks/useProjectTaskPolling";


const MAX_FEEDBACK_LENGTH = 4_000;
type RevisionOperation =
  | "VIDEO_PROMPT_REVISE"
  | "VIDEO_PROMPT_REGENERATE";
type VideoPromptTaskOperation = "VIDEO_PROMPT_GENERATE" | RevisionOperation;

interface VideoPromptRevisionActionProps {
  projectId: string;
  availableActions: AvailableAction[];
  onTerminalRefresh: () => Promise<void>;
  onActiveTaskChange?: (active: boolean) => void;
}

function isVideoPromptTask(
  task: TaskRecord,
): task is TaskRecord & { operation: VideoPromptTaskOperation } {
  return (
    task.operation === "VIDEO_PROMPT_GENERATE" ||
    task.operation === "VIDEO_PROMPT_REVISE" ||
    task.operation === "VIDEO_PROMPT_REGENERATE"
  );
}

function isRevisionTask(
  task: TaskRecord,
): task is TaskRecord & { operation: RevisionOperation } {
  return (
    task.operation === "VIDEO_PROMPT_REVISE" ||
    task.operation === "VIDEO_PROMPT_REGENERATE"
  );
}

function taskCopy(
  operation: RevisionOperation,
  status: TaskRecord["status"] | "SUBMITTING",
): string {
  const revise = operation === "VIDEO_PROMPT_REVISE";
  if (status === "SUBMITTING") {
    return revise ? "正在提交修改任务…" : "正在提交重新生成任务…";
  }
  if (status === "QUEUED") {
    return revise ? "修改任务已提交，排队中…" : "重新生成任务已提交，排队中…";
  }
  if (status === "RUNNING") {
    return revise ? "正在修改视频提示词…" : "正在重新生成视频提示词…";
  }
  if (status === "SUCCEEDED") {
    return revise ? "视频提示词修改完成。" : "视频提示词重新生成完成。";
  }
  if (status === "FAILED") {
    return revise ? "修改视频提示词失败。" : "重新生成视频提示词失败。";
  }
  if (status === "INTERRUPTED") {
    return revise ? "修改任务已中断。" : "重新生成任务已中断。";
  }
  return "视频提示词任务已取消。";
}

function safeActionErrorCopy(error: TaskActionError): string {
  if (error.code === "PROJECT_BUSY") return "项目当前正在执行其他任务。";
  if (error.code === "CAPABILITY_UNAVAILABLE") {
    return "视频提示词生成服务尚未配置。";
  }
  if (error.code === "ACTION_NOT_ALLOWED") {
    return "当前项目状态不允许执行此操作，请刷新后确认最新状态。";
  }
  if (error.code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "视频提示词请求暂时无法处理。";
}

function taskFailureCopy(task: TaskRecord): string {
  if (task.error?.code === "VIDEO_PROMPT_OUTPUT_INVALID") {
    return "部分镜头的视频提示词未通过校验，可以重新尝试。";
  }
  return task.error?.message ?? "请刷新项目状态后重试。";
}

export function VideoPromptRevisionAction({
  projectId,
  availableActions,
  onTerminalRefresh,
  onActiveTaskChange,
}: VideoPromptRevisionActionProps) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [regenerateConfirming, setRegenerateConfirming] = useState(false);
  const [submittingOperation, setSubmittingOperation] =
    useState<RevisionOperation | null>(null);
  const submissionGuard = useRef(false);
  const canRevise = availableActions.includes("REVISE_VIDEO_PROMPTS");
  const canRegenerate = availableActions.includes("REGENERATE_VIDEO_PROMPTS");
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
    isTask: isVideoPromptTask,
    onTerminalRefresh,
    recoverLatestTerminalTask: true,
  });
  const busy = active || submittingOperation !== null || terminalRefreshPending;
  const normalizedFeedback = feedback.trim();
  const videoPromptTask = task && isVideoPromptTask(task) ? task : null;
  const revisionTask =
    videoPromptTask && isRevisionTask(videoPromptTask) ? videoPromptTask : null;

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
      (operation === "VIDEO_PROMPT_REVISE" && canRevise) ||
      (operation === "VIDEO_PROMPT_REGENERATE" && canRegenerate);
    if (
      submissionGuard.current ||
      !allowed ||
      active ||
      (operation === "VIDEO_PROMPT_REVISE" &&
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
        operation === "VIDEO_PROMPT_REVISE"
          ? await reviseVideoPrompts(projectId, normalizedFeedback)
          : await regenerateVideoPrompts(projectId);
      if (
        result.data.project_id !== projectId ||
        result.data.operation !== operation
      ) {
        throw new ApiClientError({
          message: "Video Prompt task response did not match the request.",
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
    !active &&
    !error
  ) {
    return null;
  }

  return (
    <section
      className="stage-section creative-revision-section video-prompt-revision-section"
      aria-labelledby="video-prompt-revision-title"
    >
      <div className="stage-section-heading creative-action-heading">
        <div>
          <p className="page-kicker">AI REVISION</p>
          <h2 id="video-prompt-revision-title">修改或重新生成视频提示词</h2>
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
            {videoPromptTask?.operation === "VIDEO_PROMPT_GENERATE"
              ? "正在生成视频提示词…"
              : revisionTask?.operation === "VIDEO_PROMPT_REGENERATE" ||
                  submittingOperation === "VIDEO_PROMPT_REGENERATE"
                ? "正在重新生成视频提示词…"
                : "正在修改视频提示词…"}
          </strong>
          <span>当前旧提示词会继续保留并显示，整批成功后才会刷新。</span>
        </div>
      )}

      {revisionTask?.status === "FAILED" && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{taskCopy(revisionTask.operation, "FAILED")}</strong>
          <span>{taskFailureCopy(revisionTask)}</span>
          <small>错误编号：{revisionTask.correlation_id}</small>
        </div>
      )}
      {revisionTask?.status === "INTERRUPTED" && (
        <div className="creative-action-message" role="status">
          <strong>{taskCopy(revisionTask.operation, "INTERRUPTED")}</strong>
          <span>已重新读取正式提示词与 Workflow，不会自动再次提交。</span>
        </div>
      )}
      {revisionTask?.status === "SUCCEEDED" && (
        <div className="creative-action-message creative-revision-success" role="status">
          <strong>{taskCopy(revisionTask.operation, "SUCCEEDED")}</strong>
          <span>新提示词已载入，仍需再次人工审核。</span>
        </div>
      )}
      {error && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{safeActionErrorCopy(error)}</strong>
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
              修改视频提示词
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
              重新生成视频提示词
            </button>
          )}
        </div>
      )}

      {feedbackOpen && canRevise && !active && (
        <div className="creative-feedback-panel">
          <label htmlFor="video-prompt-revision-feedback">修改意见</label>
          <textarea
            id="video-prompt-revision-feedback"
            maxLength={MAX_FEEDBACK_LENGTH}
            placeholder="保留当前镜头结构，减少镜头运动，产品主体更稳定，不要出现人物，加强包装和产品质感特写。"
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
              onClick={() => void submit("VIDEO_PROMPT_REVISE")}
            >
              {submittingOperation === "VIDEO_PROMPT_REVISE"
                ? "正在提交…"
                : "提交修改"}
            </button>
          </div>
        </div>
      )}

      {regenerateConfirming && canRegenerate && !active && (
        <div className="creative-approval-confirmation" role="dialog" aria-modal="false">
          <strong>确认重新生成视频提示词？</strong>
          <p>
            将根据已审核分镜重新生成所有需要的视频提示词。系统会重新执行每个镜头的Prompt生成与校验。
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
              onClick={() => void submit("VIDEO_PROMPT_REGENERATE")}
            >
              {submittingOperation === "VIDEO_PROMPT_REGENERATE"
                ? "正在提交…"
                : "确认重新生成"}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
