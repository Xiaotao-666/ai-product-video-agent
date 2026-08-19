import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";

import { ApiClientError, approveVideoPrompts } from "../../api/client";
import type { AvailableAction } from "../../api/types";
import { projectStagePath } from "../../stageDefinitions";


interface VideoPromptApproveActionProps {
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
  return "视频提示词审核请求暂时无法处理。";
}

export function VideoPromptApproveAction({
  projectId,
  availableActions,
  onApprovedRefresh,
  disabled = false,
}: VideoPromptApproveActionProps) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState<ActionError | null>(null);
  const submissionGuard = useRef(false);
  const canApprove = availableActions.includes("APPROVE_VIDEO_PROMPTS");
  const showApprove = canApprove && !approved;
  const canContinueToShots =
    availableActions.includes("GENERATE_SHOTS") || approved;

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
      const result = await approveVideoPrompts(projectId);
      if (result.data.project_id !== projectId) {
        throw new ApiClientError({
          message: "Video Prompt approval response did not match project.",
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

  if (!showApprove && !canContinueToShots && !error) return null;

  return (
    <section
      className="stage-section creative-approve-section video-prompt-approve-section"
      aria-labelledby="video-prompt-approve-title"
    >
      <div className="stage-section-heading">
        <p className="page-kicker">HUMAN REVIEW</p>
        <h2 id="video-prompt-approve-title">视频提示词审核</h2>
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
            确认当前所有镜头的视频提示词，并进入镜头生成阶段。审核通过不会自动生成视频。
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
          视频提示词生成任务运行中，完成前不能审核通过。
        </p>
      )}

      {showApprove && confirming && (
        <div className="creative-approval-confirmation" role="dialog" aria-modal="false">
          <strong>确认通过当前视频提示词？</strong>
          <p>通过后将进入镜头生成阶段，但不会自动生成视频。</p>
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
              onClick={() => void submit()}
            >
              {submitting ? "审核中…" : "确认通过"}
            </button>
          </div>
        </div>
      )}

      {!showApprove && canContinueToShots && (
        <div className="creative-action-message creative-approval-success" role="status">
          <strong>视频提示词已审核通过。</strong>
          <span>下一步可前往镜头；当前操作没有生成视频。</span>
          <Link
            className="secondary-button creative-storyboard-link"
            to={projectStagePath(projectId, "shots")}
          >
            前往镜头
          </Link>
        </div>
      )}
    </section>
  );
}
