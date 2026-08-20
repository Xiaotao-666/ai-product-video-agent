import { useCallback, useEffect, useRef, useState } from "react";

import {
  ApiClientError,
  getPromptRevisionDraft,
  submitPromptRevisionDraft,
} from "../../api/client";
import type {
  PromptRevisionDraftResponse,
  TaskRecord,
} from "../../api/types";
import {
  toTaskActionError,
  useProjectTaskPolling,
} from "../../hooks/useProjectTaskPolling";
import type { TaskActionError } from "../../hooks/useProjectTaskPolling";


const MAX_FEEDBACK_LENGTH = 2_000;

interface ShotPromptRevisionDraftActionProps {
  projectId: string;
  shotId: string;
  basePromptVersion: number;
}

function taskStatusCopy(status: TaskRecord["status"] | "SUBMITTING"): string {
  if (status === "SUBMITTING") return "正在提交修改建议…";
  if (status === "QUEUED") return "等待生成";
  if (status === "RUNNING") return "AI正在修改Prompt";
  if (status === "SUCCEEDED") return "修改完成";
  if (status === "FAILED") return "AI修改失败";
  if (status === "INTERRUPTED") return "修改任务已中断";
  return "修改任务已取消";
}

function actionErrorCopy(error: TaskActionError): string {
  if (error.code === "PROJECT_BUSY") return "项目当前正在执行其他任务，请稍后重试。";
  if (error.code === "CAPABILITY_UNAVAILABLE") return "AI Prompt修改服务尚未配置。";
  if (error.code === "ACTION_NOT_ALLOWED") return "当前Prompt状态已变化，请刷新后重试。";
  if (error.code === "NETWORK_ERROR") return "无法连接本地 Backend，请确认服务已启动。";
  return "AI Prompt修改请求暂时无法处理。";
}

function taskFailureCopy(task: TaskRecord): string {
  if (task.error?.code === "PROMPT_REVISION_OUTPUT_INVALID") {
    return "AI返回的Prompt修改建议未通过校验，可以重新尝试。";
  }
  if (task.error?.code === "PROVIDER_FAILED") {
    return "AI Prompt修改服务暂时不可用，请稍后重试。";
  }
  return task.error?.message ?? "AI Prompt修改任务未能完成。";
}

export function ShotPromptRevisionDraftAction({
  projectId,
  shotId,
  basePromptVersion,
}: ShotPromptRevisionDraftActionProps) {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedback, setFeedback] = useState("");
  const [draft, setDraft] = useState<PromptRevisionDraftResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const submissionGuard = useRef(false);
  const normalizedFeedback = feedback.trim();

  const isDraftTask = useCallback(
    (task: TaskRecord) =>
      task.operation === "SHOT_PROMPT_REVISION_DRAFT" &&
      task.target_id === shotId,
    [shotId],
  );

  const loadDraft = useCallback(async () => {
    try {
      const result = await getPromptRevisionDraft(projectId, shotId);
      setDraft(result.data);
    } catch (caught) {
      if (
        caught instanceof ApiClientError &&
        caught.code === "PROMPT_REVISION_DRAFT_NOT_FOUND"
      ) {
        setDraft(null);
        return;
      }
      throw caught;
    }
  }, [projectId, shotId]);

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
    isTask: isDraftTask,
    onTerminalRefresh: loadDraft,
    recoverLatestTerminalTask: true,
  });

  useEffect(() => {
    setFeedbackOpen(false);
    setFeedback("");
    setDraft(null);
    setSubmitting(false);
    submissionGuard.current = false;
    void loadDraft().catch((caught) => setError(toTaskActionError(caught)));
  }, [loadDraft, setError]);

  const submit = async (value: string) => {
    const normalized = value.trim();
    if (
      submissionGuard.current ||
      active ||
      normalized.length === 0 ||
      normalized.length > MAX_FEEDBACK_LENGTH
    ) {
      return;
    }
    submissionGuard.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await submitPromptRevisionDraft(projectId, shotId, {
        feedback: normalized,
      });
      if (
        result.data.project_id !== projectId ||
        result.data.operation !== "SHOT_PROMPT_REVISION_DRAFT" ||
        result.data.target_id !== shotId
      ) {
        throw new ApiClientError({
          message: "Prompt revision task did not match the request.",
          code: "INVALID_RESPONSE",
          correlationId: result.correlationId,
        });
      }
      setTask(result.data);
      setFeedback(normalized);
      setFeedbackOpen(false);
    } catch (caught) {
      const mapped = toTaskActionError(caught);
      if (mapped.code === "PROJECT_BUSY") {
        try {
          if (await attachToExistingTask()) {
            setFeedbackOpen(false);
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
      setSubmitting(false);
    }
  };

  const draftTask = task && isDraftTask(task) ? task : null;
  const busy = submitting || active || terminalRefreshPending;

  return (
    <section
      className="shot-prompt-revision-draft"
      aria-labelledby="shot-prompt-revision-title"
    >
      <div className="stage-section-heading creative-action-heading">
        <div>
          <p className="page-kicker">AI PROMPT REVISION</p>
          <h2 id="shot-prompt-revision-title">AI修改Prompt</h2>
          <p className="shot-prompt-revision-intro">
            基于当前 Prompt v{basePromptVersion} 生成一份修改建议。本阶段不会创建新Prompt版本，也不会生成视频。
          </p>
        </div>
        {(draftTask || submitting) && (
          <span className="creative-task-status" role="status">
            {taskStatusCopy(draftTask?.status ?? "SUBMITTING")}
          </span>
        )}
      </div>

      {(submitting || active) && (
        <div className="creative-action-message" role="status">
          <strong>{taskStatusCopy(draftTask?.status ?? "SUBMITTING")}</strong>
          <span>当前正式Prompt保持不变，完成后只展示AI Draft。</span>
        </div>
      )}

      {draftTask?.status === "FAILED" && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{taskStatusCopy("FAILED")}</strong>
          <span>{taskFailureCopy(draftTask)}</span>
          <small>错误编号：{draftTask.correlation_id}</small>
        </div>
      )}

      {draftTask?.status === "INTERRUPTED" && (
        <div className="creative-action-message" role="status">
          <strong>{taskStatusCopy("INTERRUPTED")}</strong>
          <span>刷新不会重新提交AI请求；可在确认当前Prompt后重新操作。</span>
        </div>
      )}

      {error && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{actionErrorCopy(error)}</strong>
          {error.correlationId && <small>错误编号：{error.correlationId}</small>}
        </div>
      )}

      {!feedbackOpen && !draft && !busy && (
        <button
          className="secondary-button"
          type="button"
          onClick={() => {
            setError(null);
            setFeedbackOpen(true);
          }}
        >
          AI修改Prompt
        </button>
      )}

      {feedbackOpen && !active && (
        <div className="creative-feedback-panel">
          <label htmlFor="shot-prompt-revision-feedback">修改意见</label>
          <textarea
            id="shot-prompt-revision-feedback"
            maxLength={MAX_FEEDBACK_LENGTH}
            placeholder="例如：增强电影感，提高产品质感，镜头更加高级。"
            rows={6}
            value={feedback}
            disabled={submitting}
            onChange={(event) => setFeedback(event.target.value)}
          />
          <small>{feedback.length}/{MAX_FEEDBACK_LENGTH}</small>
          <div className="creative-approval-buttons">
            <button
              className="secondary-button"
              type="button"
              disabled={submitting}
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
                submitting ||
                normalizedFeedback.length === 0 ||
                normalizedFeedback.length > MAX_FEEDBACK_LENGTH
              }
              onClick={() => void submit(normalizedFeedback)}
            >
              {submitting ? "正在提交…" : "生成修改建议"}
            </button>
          </div>
        </div>
      )}

      {draft && (
        <div className="shot-prompt-revision-result">
          <div className="shot-prompt-revision-meta">
            <span>基础 Prompt v{draft.base_prompt_version}</span>
            <span>AI Draft · 尚未采用</span>
          </div>
          <div className="shot-prompt-revision-compare">
            <article>
              <h3>原 Prompt</h3>
              <p>{draft.original_prompt}</p>
            </article>
            <article>
              <h3>AI 修改后的 Prompt Draft</h3>
              <p>{draft.draft_prompt}</p>
            </article>
          </div>
          <div className="creative-review-buttons">
            <button className="primary-button" type="button" disabled>
              采用修改（下一阶段）
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={busy}
              onClick={() => void submit(draft.feedback)}
            >
              重新生成修改建议
            </button>
            <button
              className="secondary-button"
              type="button"
              disabled={busy}
              onClick={() => {
                setDraft(null);
                setTask(null);
                setFeedback("");
                setError(null);
              }}
            >
              取消
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
