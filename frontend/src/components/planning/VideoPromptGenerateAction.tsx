import { useEffect, useRef, useState } from "react";

import { ApiClientError, generateVideoPrompts } from "../../api/client";
import type { AvailableAction, TaskRecord } from "../../api/types";
import {
  toTaskActionError,
  useProjectTaskPolling,
} from "../../hooks/useProjectTaskPolling";
import type { TaskActionError } from "../../hooks/useProjectTaskPolling";


interface VideoPromptGenerateActionProps {
  projectId: string;
  availableActions: AvailableAction[];
  videoPromptStatus: string;
  hasVideoPrompts: boolean | null;
  onTerminalRefresh: () => Promise<void>;
  onActiveTaskChange?: (active: boolean) => void;
}

function isVideoPromptGenerateTask(task: TaskRecord): boolean {
  return task.operation === "VIDEO_PROMPT_GENERATE";
}

function isRecoveryState(status: string, task: TaskRecord | null): boolean {
  return (
    status === "FAILED" ||
    status === "RUNNING" ||
    task?.status === "FAILED" ||
    task?.status === "INTERRUPTED"
  );
}

function statusCopy(
  task: TaskRecord | null,
  submitting: boolean,
  hasVideoPrompts: boolean | null,
): string {
  if (submitting) return "正在提交视频提示词生成任务…";
  if (task?.status === "QUEUED") return "排队中…";
  if (task?.status === "RUNNING") return "正在生成视频提示词…";
  if (hasVideoPrompts) return "已生成";
  if (!task) return hasVideoPrompts === false ? "未开始" : "正在确认状态…";
  if (task.status === "SUCCEEDED") return "生成成功";
  if (task.status === "FAILED") return "生成失败";
  if (task.status === "INTERRUPTED") return "任务中断";
  return "任务已取消";
}

function safeTaskFailureCopy(code: string | undefined): string {
  if (code === "VIDEO_PROMPT_OUTPUT_INVALID") {
    return "部分镜头的视频提示词未通过校验，可以重新尝试。";
  }
  if (code === "PROVIDER_REQUEST_FAILED") {
    return "视频提示词生成服务暂时不可用，请稍后重试。";
  }
  if (code === "PROJECT_WRITE_FAILED") {
    return "视频提示词结果暂时无法保存，可以重新尝试。";
  }
  return "视频提示词生成未完成，可以重新检查状态后再试。";
}

function safeActionErrorCopy(error: TaskActionError): string {
  if (error.code === "PROJECT_BUSY") return "项目当前正在执行其他任务。";
  if (error.code === "CAPABILITY_UNAVAILABLE") {
    return "视频提示词生成服务尚未配置。";
  }
  if (error.code === "ACTION_NOT_ALLOWED") {
    return "当前项目状态不允许生成视频提示词，请刷新后确认最新状态。";
  }
  if (error.code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "视频提示词请求暂时无法处理。";
}

export function VideoPromptGenerateAction({
  projectId,
  availableActions,
  videoPromptStatus,
  hasVideoPrompts,
  onTerminalRefresh,
  onActiveTaskChange,
}: VideoPromptGenerateActionProps) {
  const [submitting, setSubmitting] = useState(false);
  const submissionGuard = useRef(false);
  const canGenerate = availableActions.includes("GENERATE_VIDEO_PROMPTS");
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
    isTask: isVideoPromptGenerateTask,
    onTerminalRefresh,
    enabled:
      canGenerate ||
      hasVideoPrompts === false ||
      videoPromptStatus === "FAILED" ||
      videoPromptStatus === "RUNNING",
    recoverLatestTerminalTask: true,
  });
  const busy = submitting || active || terminalRefreshPending;
  const recovery = isRecoveryState(videoPromptStatus, task);

  useEffect(() => {
    setSubmitting(false);
  }, [projectId]);

  useEffect(() => {
    onActiveTaskChange?.(busy);
    return () => onActiveTaskChange?.(false);
  }, [busy, onActiveTaskChange]);

  const submit = async () => {
    if (submissionGuard.current || !canGenerate || active) return;
    submissionGuard.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await generateVideoPrompts(projectId);
      if (
        result.data.project_id !== projectId ||
        result.data.operation !== "VIDEO_PROMPT_GENERATE"
      ) {
        throw new ApiClientError({
          message: "Video Prompt task response did not match the request.",
          code: "INVALID_RESPONSE",
          correlationId: result.correlationId,
        });
      }
      setTask(result.data);
    } catch (caught) {
      const mapped = toTaskActionError(caught);
      if (mapped.code === "PROJECT_BUSY") {
        try {
          if (await attachToExistingTask()) return;
        } catch (recoveryError) {
          setError(toTaskActionError(recoveryError));
          return;
        }
      }
      setError(mapped);
    } finally {
      submissionGuard.current = false;
      setSubmitting(false);
    }
  };

  const showGenerateButton =
    canGenerate && task?.status !== "SUCCEEDED" && !active;

  return (
    <section
      className="stage-section creative-action-section storyboard-action-section"
      aria-labelledby="video-prompt-action-title"
    >
      <div className="stage-section-heading creative-action-heading">
        <div>
          <p className="page-kicker">VIDEO PROMPT GENERATION</p>
          <h2 id="video-prompt-action-title">生成视频提示词</h2>
        </div>
        <span className="creative-task-status" role="status">
          {statusCopy(task, submitting, hasVideoPrompts)}
        </span>
      </div>

      <p className="stage-content-note">
        根据已审核分镜，为每个镜头生成视频模型提示词。生成完成后仍需人工审核，不会自动生成镜头视频。
      </p>

      {active && (
        <div className="creative-action-message" role="status">
          <strong>视频提示词生成任务正在执行。</strong>
          <span>页面会自动检查任务状态，已完成镜头的生成进度会安全保留。</span>
        </div>
      )}

      {task?.status === "FAILED" && !hasVideoPrompts && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>视频提示词生成失败。</strong>
          <span>{safeTaskFailureCopy(task.error?.code)}</span>
          <small>错误编号：{task.correlation_id}</small>
        </div>
      )}
      {task?.status === "INTERRUPTED" && !hasVideoPrompts && (
        <div className="creative-action-message" role="status">
          <strong>上次生成任务被中断。</strong>
          <span>不会自动重新提交；你可以在确认状态后人工继续生成。</span>
        </div>
      )}
      {error && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{safeActionErrorCopy(error)}</strong>
          {error.correlationId && <small>错误编号：{error.correlationId}</small>}
        </div>
      )}

      {showGenerateButton && (
        <button
          className="primary-button"
          type="button"
          disabled={busy}
          onClick={() => void submit()}
        >
          {submitting
            ? "正在提交…"
            : recovery
              ? "重新尝试生成"
              : "生成视频提示词"}
        </button>
      )}
      {!canGenerate && !active && hasVideoPrompts === false && (
        <p className="stage-empty-copy">当前项目状态不允许生成视频提示词。</p>
      )}
    </section>
  );
}
