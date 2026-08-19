import { useEffect, useRef, useState } from "react";

import { ApiClientError, generateStoryboard } from "../../api/client";
import type { AvailableAction, TaskRecord } from "../../api/types";
import {
  toTaskActionError,
  useProjectTaskPolling,
} from "../../hooks/useProjectTaskPolling";
import type { TaskActionError } from "../../hooks/useProjectTaskPolling";


interface StoryboardGenerateActionProps {
  projectId: string;
  availableActions: AvailableAction[];
  hasStoryboard: boolean | null;
  onTerminalRefresh: () => Promise<void>;
}

function isStoryboardGenerateTask(task: TaskRecord): boolean {
  return task.operation === "STORYBOARD_GENERATE";
}

function statusCopy(
  task: TaskRecord | null,
  submitting: boolean,
  hasStoryboard: boolean | null,
): string {
  if (submitting) return "正在提交分镜生成任务…";
  if (task?.status === "QUEUED") return "排队中…";
  if (task?.status === "RUNNING") return "正在生成分镜…";
  if (hasStoryboard) return "已生成";
  if (!task) return hasStoryboard === false ? "未开始" : "正在确认状态…";
  if (task.status === "SUCCEEDED") return "生成成功";
  if (task.status === "FAILED") return "生成失败";
  if (task.status === "INTERRUPTED") return "任务中断";
  return "任务已取消";
}

function safeErrorCopy(error: TaskActionError): string {
  if (error.code === "PROJECT_BUSY") {
    return "项目当前正在执行其他任务。";
  }
  if (error.code === "CAPABILITY_UNAVAILABLE") {
    return "分镜生成服务尚未配置。";
  }
  if (error.code === "ACTION_NOT_ALLOWED") {
    return "当前项目状态不允许生成分镜，请刷新后确认最新状态。";
  }
  if (error.code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "Storyboard 请求暂时无法处理。";
}

export function StoryboardGenerateAction({
  projectId,
  availableActions,
  hasStoryboard,
  onTerminalRefresh,
}: StoryboardGenerateActionProps) {
  const [submitting, setSubmitting] = useState(false);
  const submissionGuard = useRef(false);
  const canGenerate = availableActions.includes("GENERATE_STORYBOARD");
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
    isTask: isStoryboardGenerateTask,
    onTerminalRefresh,
  });
  const busy = submitting || active || terminalRefreshPending;

  useEffect(() => {
    setSubmitting(false);
  }, [projectId]);

  const submit = async () => {
    if (submissionGuard.current || !canGenerate || active) return;
    submissionGuard.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await generateStoryboard(projectId);
      if (
        result.data.project_id !== projectId ||
        result.data.operation !== "STORYBOARD_GENERATE"
      ) {
        throw new ApiClientError({
          message: "Storyboard task response did not match the request.",
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
    canGenerate &&
    !(task?.status === "SUCCEEDED") &&
    !active;

  return (
    <section
      className="stage-section creative-action-section storyboard-action-section"
      aria-labelledby="storyboard-action-title"
    >
      <div className="stage-section-heading creative-action-heading">
        <div>
          <p className="page-kicker">STORYBOARD GENERATION</p>
          <h2 id="storyboard-action-title">生成分镜</h2>
        </div>
        <span className="creative-task-status" role="status">
          {statusCopy(task, submitting, hasStoryboard)}
        </span>
      </div>

      <p className="stage-content-note">
        将根据已审核通过的 Creative 生成并保存 Storyboard。生成完成后仍需人工审核，不会自动进入视频提示词阶段。
      </p>

      {active && (
        <div className="creative-action-message" role="status">
          <strong>分镜生成任务正在执行。</strong>
          <span>页面会自动检查任务状态，已保存内容会继续保留。</span>
        </div>
      )}

      {task?.status === "FAILED" && !hasStoryboard && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>分镜生成失败。</strong>
          <span>{task.error?.message ?? "请刷新项目状态后重试。"}</span>
          <small>错误编号：{task.correlation_id}</small>
        </div>
      )}
      {task?.status === "INTERRUPTED" && !hasStoryboard && (
        <div className="creative-action-message" role="status">
          <strong>上次生成任务被中断。</strong>
          <span>已重新检查 Storyboard 与 Workflow，不会自动再次提交。</span>
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
          onClick={() => void submit()}
        >
          {submitting ? "正在提交…" : "生成分镜"}
        </button>
      )}
      {!canGenerate && !active && hasStoryboard === false && (
        <p className="stage-empty-copy">当前项目状态不允许生成分镜。</p>
      )}
    </section>
  );
}
