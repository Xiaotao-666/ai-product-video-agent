import { useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  generateCreative,
  getProjectTasks,
  getTask,
} from "../../api/client";
import type { AvailableAction, TaskRecord } from "../../api/types";


const POLL_INTERVAL_MS = 2_000;
const ACTIVE_STATUSES = new Set(["QUEUED", "RUNNING"]);

interface CreativeGenerateActionProps {
  projectId: string;
  availableActions: AvailableAction[];
  onTerminalRefresh: () => Promise<void>;
}

interface ActionError {
  code: string;
  correlationId: string | null;
}

function isActiveCreativeTask(task: TaskRecord): boolean {
  return (
    task.operation === "CREATIVE_GENERATE" && ACTIVE_STATUSES.has(task.status)
  );
}

function actionError(caught: unknown): ActionError {
  return {
    code: caught instanceof ApiClientError ? caught.code : "UNKNOWN_ERROR",
    correlationId:
      caught instanceof ApiClientError ? caught.correlationId : null,
  };
}

function taskStatusCopy(task: TaskRecord | null, submitting: boolean): string {
  if (submitting) return "正在提交创意生成任务…";
  if (!task) return "未开始";
  if (task.status === "QUEUED") return "排队中…";
  if (task.status === "RUNNING") return "正在生成创意…";
  if (task.status === "SUCCEEDED") return "生成成功";
  if (task.status === "FAILED") return "生成失败";
  if (task.status === "INTERRUPTED") return "任务中断";
  return "任务已取消";
}

function safeErrorCopy(error: ActionError): string {
  if (error.code === "PROJECT_BUSY") {
    return "项目当前正在执行其他任务。";
  }
  if (error.code === "CAPABILITY_UNAVAILABLE") {
    return "创意生成服务尚未配置。";
  }
  if (error.code === "ACTION_NOT_ALLOWED") {
    return "当前项目状态不允许生成创意。";
  }
  if (error.code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "创意生成请求暂时无法处理。";
}

export function CreativeGenerateAction({
  projectId,
  availableActions,
  onTerminalRefresh,
}: CreativeGenerateActionProps) {
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ActionError | null>(null);
  const submissionGuard = useRef(false);
  const handledTerminalTasks = useRef(new Set<string>());
  const canGenerate = availableActions.includes("GENERATE_CREATIVE");
  const active = Boolean(task && ACTIVE_STATUSES.has(task.status));

  useEffect(() => {
    let cancelled = false;
    setTask(null);
    setError(null);
    handledTerminalTasks.current.clear();

    void getProjectTasks(projectId)
      .then((result) => {
        if (cancelled) return;
        const recovered = result.data.tasks.find(isActiveCreativeTask);
        if (recovered) setTask(recovered);
      })
      .catch((caught) => {
        if (!cancelled) setError(actionError(caught));
      });

    return () => {
      cancelled = true;
    };
  }, [projectId]);

  useEffect(() => {
    if (!task || !ACTIVE_STATUSES.has(task.status)) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const result = await getTask(task.task_id);
        if (cancelled) return;
        if (
          result.data.project_id !== projectId ||
          result.data.operation !== "CREATIVE_GENERATE"
        ) {
          throw new ApiClientError({
            message: "Task response did not match Creative generation.",
            code: "INVALID_RESPONSE",
          });
        }
        setTask(result.data);
        setError(null);
      } catch (caught) {
        if (!cancelled) setError(actionError(caught));
      }
    };

    timer = setTimeout(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [projectId, task]);

  useEffect(() => {
    if (!task || ACTIVE_STATUSES.has(task.status)) return;
    if (handledTerminalTasks.current.has(task.task_id)) return;
    handledTerminalTasks.current.add(task.task_id);
    void onTerminalRefresh().catch((caught) => {
      setError(actionError(caught));
    });
  }, [onTerminalRefresh, task]);

  const attachToExistingTask = async (): Promise<boolean> => {
    const result = await getProjectTasks(projectId);
    const creativeTask = result.data.tasks.find(isActiveCreativeTask);
    if (creativeTask) {
      setTask(creativeTask);
      setError(null);
      return true;
    }
    return false;
  };

  const submit = async () => {
    if (submissionGuard.current || !canGenerate || active) return;
    submissionGuard.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await generateCreative(projectId);
      if (
        result.data.project_id !== projectId ||
        result.data.operation !== "CREATIVE_GENERATE"
      ) {
        throw new ApiClientError({
          message: "Creative task response did not match project.",
          code: "INVALID_RESPONSE",
        });
      }
      setTask(result.data);
    } catch (caught) {
      const mapped = actionError(caught);
      if (mapped.code === "PROJECT_BUSY") {
        try {
          if (await attachToExistingTask()) return;
        } catch (recoveryError) {
          setError(actionError(recoveryError));
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
  const statusCopy = taskStatusCopy(task, submitting);

  return (
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

      {task?.status === "FAILED" && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>创意生成失败。</strong>
          <span>{task.error?.message ?? "请刷新项目状态后重试。"}</span>
          <small>错误编号：{task.correlation_id}</small>
        </div>
      )}
      {task?.status === "INTERRUPTED" && (
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
          disabled={submitting}
          onClick={submit}
        >
          {submitting ? "正在提交…" : "生成创意"}
        </button>
      )}
      {!canGenerate && !active && task?.status !== "SUCCEEDED" && (
        <p className="stage-empty-copy">当前项目状态不允许生成创意。</p>
      )}
    </section>
  );
}
