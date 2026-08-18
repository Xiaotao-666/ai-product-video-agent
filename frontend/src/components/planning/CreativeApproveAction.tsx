import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiClientError, approveCreative } from "../../api/client";
import type { AvailableAction } from "../../api/types";
import { projectStagePath } from "../../stageDefinitions";


interface CreativeApproveActionProps {
  projectId: string;
  availableActions: AvailableAction[];
  onApprovedRefresh: () => Promise<void>;
  disabled?: boolean;
}

interface ActionError {
  code: string;
  correlationId: string | null;
}

function actionError(caught: unknown): ActionError {
  return {
    code: caught instanceof ApiClientError ? caught.code : "UNKNOWN_ERROR",
    correlationId:
      caught instanceof ApiClientError ? caught.correlationId : null,
  };
}

function safeErrorCopy(error: ActionError): string {
  if (error.code === "ACTION_NOT_ALLOWED") {
    return "当前项目状态不允许审核通过，请刷新后确认最新状态。";
  }
  if (error.code === "PROJECT_BUSY") {
    return "项目当前正在执行其他任务，请稍后重试。";
  }
  if (
    error.code === "PROJECT_NOT_FOUND" ||
    error.code === "INVALID_PROJECT_ID"
  ) {
    return "项目不存在或已被删除。";
  }
  if (error.code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "Creative 审核请求暂时无法处理。";
}

export function CreativeApproveAction({
  projectId,
  availableActions,
  onApprovedRefresh,
  disabled = false,
}: CreativeApproveActionProps) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState<ActionError | null>(null);
  const submissionGuard = useRef(false);
  const canApprove = availableActions.includes("APPROVE_CREATIVE");
  const showApprove = canApprove && !approved;
  const canContinueToStoryboard =
    availableActions.includes("GENERATE_STORYBOARD") || approved;

  useEffect(() => {
    setConfirming(false);
    setSubmitting(false);
    setApproved(false);
    setError(null);
    submissionGuard.current = false;
  }, [projectId]);

  useEffect(() => {
    if (disabled) setConfirming(false);
  }, [disabled]);

  const submit = async () => {
    if (submissionGuard.current || !canApprove || disabled) return;
    submissionGuard.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await approveCreative(projectId);
      if (result.data.project_id !== projectId) {
        throw new ApiClientError({
          message: "Creative approval response did not match project.",
          code: "INVALID_RESPONSE",
          correlationId: result.correlationId,
        });
      }
      await onApprovedRefresh();
      setApproved(true);
      setConfirming(false);
    } catch (caught) {
      setError(actionError(caught));
    } finally {
      submissionGuard.current = false;
      setSubmitting(false);
    }
  };

  if (!showApprove && !canContinueToStoryboard && !error) return null;

  return (
    <section
      className="stage-section creative-approve-section"
      aria-labelledby="creative-approve-title"
    >
      <div className="stage-section-heading">
        <p className="page-kicker">HUMAN REVIEW</p>
        <h2 id="creative-approve-title">Creative 审核</h2>
      </div>

      {error && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{safeErrorCopy(error)}</strong>
          {error.correlationId && <small>错误编号：{error.correlationId}</small>}
        </div>
      )}

      {showApprove && !confirming && (
        <>
          <p className="creative-approve-copy">
            请确认当前 Creative 内容符合要求。审核通过后将解锁 Storyboard，
            但不会自动生成分镜。
          </p>
          <button
            className="primary-button"
            type="button"
            disabled={submitting || disabled}
            onClick={() => {
              setError(null);
              setConfirming(true);
            }}
          >
            审核通过
          </button>
        </>
      )}

      {showApprove && disabled && (
        <p className="stage-empty-copy">
          Creative 更新任务运行中，完成前不能审核通过。
        </p>
      )}

      {showApprove && confirming && (
        <div className="creative-approval-confirmation" role="dialog" aria-modal="false">
          <strong>确认 Creative 审核通过？</strong>
          <p>通过后将进入 Storyboard 下一步，当前操作不会生成 Storyboard。</p>
          <div className="creative-approval-buttons">
            <button
              className="secondary-button"
              type="button"
              disabled={submitting}
              onClick={() => setConfirming(false)}
            >
              取消
            </button>
            <button
              className="primary-button"
              type="button"
              disabled={submitting}
              onClick={submit}
            >
              {submitting ? "审核中…" : "确认通过"}
            </button>
          </div>
        </div>
      )}

      {!showApprove && canContinueToStoryboard && (
        <div className="creative-action-message creative-approval-success" role="status">
          <strong>Creative 已审核通过。</strong>
          <span>下一步可前往 Storyboard；本阶段不会自动生成分镜。</span>
          <Link
            className="secondary-button creative-storyboard-link"
            to={projectStagePath(projectId, "storyboard")}
          >
            前往 Storyboard
          </Link>
        </div>
      )}
    </section>
  );
}
