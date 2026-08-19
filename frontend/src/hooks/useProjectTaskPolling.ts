import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  getProjectTasks,
  getTask,
} from "../api/client";
import type { TaskRecord, TaskStatus } from "../api/types";


const POLL_INTERVAL_MS = 2_000;
const ACTIVE_STATUSES: ReadonlySet<TaskStatus> = new Set([
  "QUEUED",
  "RUNNING",
]);

export interface TaskActionError {
  code: string;
  correlationId: string | null;
}

interface ProjectTaskPollingOptions {
  projectId: string;
  isTask: (task: TaskRecord) => boolean;
  onTerminalRefresh: () => Promise<void>;
}

export function isActiveTaskStatus(status: TaskStatus): boolean {
  return ACTIVE_STATUSES.has(status);
}

export function toTaskActionError(caught: unknown): TaskActionError {
  return {
    code: caught instanceof ApiClientError ? caught.code : "UNKNOWN_ERROR",
    correlationId:
      caught instanceof ApiClientError ? caught.correlationId : null,
  };
}

export function useProjectTaskPolling({
  projectId,
  isTask,
  onTerminalRefresh,
}: ProjectTaskPollingOptions) {
  const [task, setTask] = useState<TaskRecord | null>(null);
  const [error, setError] = useState<TaskActionError | null>(null);
  const [terminalRefreshPending, setTerminalRefreshPending] = useState(false);
  const handledTerminalTasks = useRef(new Set<string>());
  const active = Boolean(task && isActiveTaskStatus(task.status));

  useEffect(() => {
    let cancelled = false;
    setTask(null);
    setError(null);
    setTerminalRefreshPending(false);
    handledTerminalTasks.current.clear();

    void getProjectTasks(projectId)
      .then((result) => {
        if (cancelled) return;
        const recovered = result.data.tasks.find(
          (candidate) => isTask(candidate) && isActiveTaskStatus(candidate.status),
        );
        if (recovered) setTask(recovered);
      })
      .catch((caught) => {
        if (!cancelled) {
          setTerminalRefreshPending(false);
          setError(toTaskActionError(caught));
        }
      });

    return () => {
      cancelled = true;
    };
  }, [isTask, projectId]);

  useEffect(() => {
    if (!task || !isActiveTaskStatus(task.status)) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    const poll = async () => {
      try {
        const result = await getTask(task.task_id);
        if (cancelled) return;
        if (
          result.data.project_id !== projectId ||
          !isTask(result.data) ||
          result.data.operation !== task.operation
        ) {
          throw new ApiClientError({
            message: "Task response did not match the active project task.",
            code: "INVALID_RESPONSE",
          });
        }
        setTask(result.data);
        setError(null);
      } catch (caught) {
        if (!cancelled) setError(toTaskActionError(caught));
      }
    };

    timer = setTimeout(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [isTask, projectId, task]);

  useEffect(() => {
    if (!task || isActiveTaskStatus(task.status)) return;
    if (handledTerminalTasks.current.has(task.task_id)) return;
    handledTerminalTasks.current.add(task.task_id);
    let cancelled = false;
    setTerminalRefreshPending(true);
    void onTerminalRefresh()
      .then(() => {
        if (!cancelled) setTerminalRefreshPending(false);
      })
      .catch((caught) => {
        if (!cancelled) {
          setTerminalRefreshPending(false);
          setError(toTaskActionError(caught));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [onTerminalRefresh, task]);

  const attachToExistingTask = useCallback(async (): Promise<boolean> => {
    const result = await getProjectTasks(projectId);
    const existing = result.data.tasks.find(
      (candidate) => isTask(candidate) && isActiveTaskStatus(candidate.status),
    );
    if (!existing) return false;
    setTask(existing);
    setError(null);
    return true;
  }, [isTask, projectId]);

  return {
    task,
    setTask,
    error,
    setError,
    active,
    terminalRefreshPending,
    attachToExistingTask,
  };
}
