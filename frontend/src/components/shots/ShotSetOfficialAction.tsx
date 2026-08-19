import { useEffect, useRef, useState } from "react";

import { ApiClientError, setOfficialShotVersion } from "../../api/client";


type BlockedReason =
  | "PENDING_REVIEW"
  | "ACTIVE_GENERATION"
  | "INCOMPLETE_VERSION"
  | null;

interface Props {
  projectId: string;
  shotId: string;
  version: number;
  promptVersion: number | null;
  currentOfficialVersion: number;
  blockedReason: BlockedReason;
  onSelectedRefresh: () => Promise<void>;
}

interface ActionError {
  code: string;
  correlationId: string | null;
}

function safeErrorCopy(code: string): string {
  if (code === "PENDING_VERSION_REQUIRES_REVIEW") {
    return "请先处理当前待审核新版本，再切换正式历史版本。";
  }
  if (code === "PROJECT_BUSY") {
    return "项目当前正在执行镜头任务，请稍后重试。";
  }
  if (code === "ACTION_NOT_ALLOWED" || code === "INVALID_SHOT_VERSION") {
    return "该历史版本当前不能设为正式版本，请刷新后确认最新状态。";
  }
  if (code === "SHOT_NOT_FOUND" || code === "INVALID_SHOT_ID") {
    return "镜头不存在或已被删除。";
  }
  if (code === "NETWORK_ERROR") {
    return "无法连接本地 Backend，请确认服务已启动。";
  }
  return "正式版本切换请求暂时无法处理。";
}

export function ShotSetOfficialAction({
  projectId,
  shotId,
  version,
  promptVersion,
  currentOfficialVersion,
  blockedReason,
  onSelectedRefresh,
}: Props) {
  const [confirming, setConfirming] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<ActionError | null>(null);
  const submissionGuard = useRef(false);

  useEffect(() => {
    setConfirming(false);
    setSubmitting(false);
    setError(null);
    submissionGuard.current = false;
  }, [projectId, shotId, version]);

  async function submit() {
    if (submissionGuard.current || blockedReason) return;
    submissionGuard.current = true;
    setSubmitting(true);
    setError(null);
    try {
      const result = await setOfficialShotVersion(projectId, shotId, version);
      const selected = result.data.versions.find((item) => item.version === version);
      if (
        result.data.project_id !== projectId
        || result.data.shot_id !== shotId
        || result.data.status !== "APPROVED"
        || result.data.official_version !== version
        || result.data.pending_review_version !== null
        || selected?.role !== "OFFICIAL"
      ) {
        throw new ApiClientError({
          code: "INVALID_RESPONSE",
          message: "Shot version selection response did not match the target version.",
          correlationId: result.correlationId,
        });
      }
      await onSelectedRefresh();
      setConfirming(false);
    } catch (caught) {
      setError({
        code: caught instanceof ApiClientError ? caught.code : "UNKNOWN_ERROR",
        correlationId:
          caught instanceof ApiClientError ? caught.correlationId : null,
      });
    } finally {
      submissionGuard.current = false;
      setSubmitting(false);
    }
  }

  const blockedCopy = blockedReason === "PENDING_REVIEW"
    ? "请先处理当前待审核新版本，再切换正式历史版本。"
    : blockedReason === "ACTIVE_GENERATION"
      ? "镜头生成任务进行中，完成后才能切换正式版本。"
      : blockedReason === "INCOMPLETE_VERSION"
        ? "该历史版本文件不完整，不能设为正式版本。"
      : null;

  return (
    <div className="shot-set-official-action">
      {error && (
        <div className="creative-action-message creative-action-error" role="alert">
          <strong>{safeErrorCopy(error.code)}</strong>
          {error.correlationId && <small>错误编号：{error.correlationId}</small>}
        </div>
      )}

      {!confirming && (
        <>
          <button
            className="secondary-button"
            type="button"
            disabled={Boolean(blockedReason) || submitting}
            onClick={() => {
              setError(null);
              setConfirming(true);
            }}
          >
            设为正式版本
          </button>
          {blockedCopy && <small className="shot-set-official-blocked">{blockedCopy}</small>}
        </>
      )}

      {confirming && (
        <div className="creative-approval-confirmation" role="dialog" aria-modal="false">
          <strong>确认将 v{version} 设为当前正式版本？</strong>
          <dl className="shot-version-selection-summary">
            <div><dt>当前正式版本</dt><dd>v{currentOfficialVersion}</dd></div>
            <div><dt>目标版本</dt><dd>v{version}</dd></div>
            <div><dt>目标 Prompt</dt><dd>{promptVersion ? `Prompt v${promptVersion}` : "未记录"}</dd></div>
          </dl>
          <ul>
            <li>不会重新生成视频，也不会产生 MiniMax 费用</li>
            <li>当前正式版本会完整保留在历史中</li>
            <li>如已有合片结果，将标记为需要重新合片</li>
          </ul>
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
              {submitting ? "切换中…" : "确认设为正式版本"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
