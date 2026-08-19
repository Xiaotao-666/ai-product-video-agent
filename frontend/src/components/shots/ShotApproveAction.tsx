import { useEffect, useRef, useState } from "react";

import { ApiClientError, approveShot } from "../../api/client";


interface Props {
  projectId: string;
  shotId: string;
  version: number;
  previousOfficialVersion?: number | null;
  onApprovedRefresh: () => Promise<void>;
}

interface ActionError {
  code: string;
  correlationId: string | null;
}

function safeErrorCopy(code: string): string {
  if (code === "ACTION_NOT_ALLOWED") {
    return "当前镜头状态不允许审核通过，请刷新后确认最新状态。";
  }
  if (code === "PROJECT_BUSY") {
    return "项目当前正在执行其他任务，请稍后重试。";
  }
  if (code === "SHOT_NOT_FOUND" || code === "INVALID_SHOT_ID") {
    return "镜头不存在或已被删除。";
  }
  if (code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "镜头审核请求暂时无法处理。";
}

export function ShotApproveAction({
  projectId,
  shotId,
  version,
  previousOfficialVersion = null,
  onApprovedRefresh,
}: Props) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [approved, setApproved] = useState(false);
  const [error, setError] = useState<ActionError | null>(null);
  const submissionGuard = useRef(false);

  useEffect(() => {
    setConfirming(false);
    setSubmitting(false);
    setApproved(false);
    setError(null);
    submissionGuard.current = false;
  }, [projectId, shotId, version]);

  async function submit() {
    if (submissionGuard.current) return;
    submissionGuard.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await approveShot(projectId, shotId);
      if (
        result.data.project_id !== projectId
        || result.data.shot_id !== shotId
        || result.data.status !== "APPROVED"
        || result.data.official_version !== version
        || result.data.pending_review_version !== null
      ) {
        throw new ApiClientError({
          code: "INVALID_RESPONSE",
          message: "Shot approval response did not match the reviewed version.",
          correlationId: result.correlationId,
        });
      }
      await onApprovedRefresh();
      setApproved(true);
      setConfirming(false);
    } catch (caught) {
      setError({
        code: caught instanceof ApiClientError ? caught.code : "UNKNOWN_ERROR",
        correlationId: caught instanceof ApiClientError
          ? caught.correlationId
          : null,
      });
    } finally {
      submissionGuard.current = false;
      setSubmitting(false);
    }
  }

  return (
    <section className="shot-approve-action" aria-labelledby="shot-approve-title">
      <div className="stage-section-heading">
        <p className="page-kicker">HUMAN REVIEW</p>
        <h3 id="shot-approve-title">镜头审核</h3>
      </div>

      {error && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{safeErrorCopy(error.code)}</strong>
          {error.correlationId && <small>错误编号：{error.correlationId}</small>}
        </div>
      )}

      {!approved && !confirming && (
        <button
          className="primary-button"
          type="button"
          disabled={submitting}
          onClick={() => {
            setError(null);
            setConfirming(true);
          }}
        >
          审核通过
        </button>
      )}

      {!approved && confirming && (
        <div className="creative-approval-confirmation" role="dialog" aria-modal="false">
          <strong>{previousOfficialVersion
            ? `确认将 v${version} 设为新的正式版本？`
            : "确认通过当前视频版本？"}</strong>
          <p>{previousOfficialVersion
            ? `当前正式 v${previousOfficialVersion} 将保留为历史版本。`
            : "通过后该版本将成为正式版本。"}</p>
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

      {approved && (
        <div className="creative-action-message creative-approval-success" role="status">
          <strong>Video v{version} 已成为正式版本。</strong>
        </div>
      )}
    </section>
  );
}
