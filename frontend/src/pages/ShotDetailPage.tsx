import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiClientError,
  getProject,
  getShot,
  getShotGenerationStatus,
  getShotVideoUrl,
} from "../api/client";
import type {
  ProjectDetail,
  ShotDetail,
  ShotGenerationStatusResponse,
  ShotVersion,
  ShotVisualInputMode,
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { ShotGenerationPreparation } from "../components/shots/ShotGenerationPreparation";
import { ShotApproveAction } from "../components/shots/ShotApproveAction";
import { ShotSetOfficialAction } from "../components/shots/ShotSetOfficialAction";
import { formatProjectDate, statusPresentation } from "../projectPresentation";
import { projectStagePath, projectWorkspacePath } from "../stageDefinitions";


type PageState = "loading" | "success" | "error";

interface PageData {
  project: ProjectDetail;
  shot: ShotDetail;
  generationStatus: ShotGenerationStatusResponse;
}

interface PageError {
  code: string;
  correlationId: string | null;
}

const REVIEW_LABELS: Record<string, string> = {
  APPROVED: "已审核通过",
  REJECTED: "此前未成为正式版本",
  WAITING_REVIEW: "等待审核",
  GENERATING: "生成中",
  FAILED: "失败",
  NOT_STARTED: "未开始",
  COMPLETED: "已完成",
  HISTORY: "历史记录",
  UNKNOWN: "未记录",
};

function reviewLabel(version: ShotVersion): string {
  if (version.role !== "HISTORY") {
    return REVIEW_LABELS[version.review_status] ?? "未记录";
  }
  if (version.history_reason === "PREVIOUSLY_APPROVED") {
    return "曾审核通过";
  }
  if (version.history_reason === "SUPERSEDED") {
    return "已被后续版本替代";
  }
  if (version.history_reason === "EXPLICITLY_REJECTED") {
    return "曾由用户明确不采用";
  }
  if (version.review_status === "APPROVED") {
    return "曾审核通过";
  }
  return "此前未成为正式版本";
}

const PROMPT_SOURCE_LABELS: Record<string, string> = {
  ai_generated: "AI 生成",
  ai_revision: "AI 修订",
  manual_edit: "手动编辑",
  same_prompt: "沿用 Prompt",
};

const VISUAL_INPUT_LABELS: Record<ShotVisualInputMode, string> = {
  NONE: "无",
  FIRST_FRAME: "首帧",
  REFERENCE_ASSET: "Reference Asset",
  UNKNOWN: "未记录",
};

function errorCopy(code: string): { title: string; message: string } {
  if (code === "SHOT_NOT_FOUND" || code === "INVALID_SHOT_ID") {
    return {
      title: "镜头不存在或已被删除",
      message: "请返回镜头列表选择其他镜头。",
    };
  }
  if (code === "PROJECT_NOT_FOUND" || code === "INVALID_PROJECT_ID") {
    return {
      title: "项目不存在或已被删除",
      message: "请返回 Projects 选择其他项目。",
    };
  }
  if (code === "SHOT_DATA_CORRUPT" || code === "PROJECT_DATA_CORRUPT") {
    return {
      title: "镜头数据暂时无法读取",
      message: "镜头元数据可能损坏，请检查本地项目文件。",
    };
  }
  if (code === "NETWORK_ERROR") {
    return {
      title: "无法连接 Backend",
      message: "请确认本地 FastAPI 服务已启动，然后重试。",
    };
  }
  return {
    title: "暂时无法加载镜头",
    message: "镜头详情未能完整读取，请重试。",
  };
}

function modelLabel(value: string | null): string {
  if (!value) {
    return "未记录";
  }
  return value
    .replace("MiniMax-H3", "MiniMax H3")
    .replace("MiniMax-Hailuo", "MiniMax Hailuo");
}

function editablePromptCore(version: ShotVersion): string {
  if (version.prompt.visual_prompt_core?.trim()) {
    return version.prompt.visual_prompt_core.trim();
  }
  const finalPrompt = version.prompt.final_prompt?.trim() ?? "";
  const markers = [
    "[Composition Constraint]",
    "[Global Hard Constraints]",
    "[Text Overlay Constraint]",
    "[Audio Constraint]",
  ];
  const positions = markers
    .map((marker) => finalPrompt.indexOf(marker))
    .filter((position) => position >= 0);
  return positions.length > 0
    ? finalPrompt.slice(0, Math.min(...positions)).trim()
    : finalPrompt;
}

function VersionCard({
  projectId,
  shotId,
  version,
}: {
  projectId: string;
  shotId: string;
  version: ShotVersion;
}) {
  const roleLabel =
    version.role === "OFFICIAL"
      ? "当前正式版本"
      : version.role === "PENDING_REVIEW"
        ? "待审核新版本"
        : "历史版本";
  return (
    <article
      className={`shot-version-card shot-version-card-${version.role.toLowerCase()}`}
    >
      <div className="shot-version-heading">
        <div>
          <p className={`shot-version-role-badge shot-version-role-${version.role.toLowerCase()}`}>
            {roleLabel}
          </p>
          <h3>
            Video v{version.version} / Prompt{" "}
            {version.prompt.version ? `v${version.prompt.version}` : "未记录"}
          </h3>
        </div>
        <span className="shot-review-label">
          {reviewLabel(version)}
        </span>
      </div>

      <dl className="shot-version-facts">
        <div><dt>模型</dt><dd>{modelLabel(version.generation.model)}</dd></div>
        <div><dt>Visual Input</dt><dd>{VISUAL_INPUT_LABELS[version.generation.visual_input_mode]}</dd></div>
        <div><dt>生成时间</dt><dd>{version.created_at ? formatProjectDate(version.created_at) : "未记录"}</dd></div>
        <div><dt>Prompt 来源</dt><dd>{version.prompt.source ? (PROMPT_SOURCE_LABELS[version.prompt.source] ?? "历史记录") : "未记录"}</dd></div>
      </dl>

      <div className="shot-video-panel">
        <h4>视频预览</h4>
        {version.video_available ? (
          <video
            aria-label={`Video v${version.version} 预览`}
            controls
            preload="metadata"
            src={getShotVideoUrl(projectId, shotId, version.version)}
          >
            当前浏览器不支持视频播放。
          </video>
        ) : (
          <p className="shot-video-unavailable">视频文件不可用</p>
        )}
      </div>

      <div className="shot-prompt-grid">
        <div className="prompt-text-block">
          <h4>视觉 Prompt 核心</h4>
          <p>
            {version.prompt.visual_prompt_core ??
              "该版本未单独保存视觉 Prompt 核心。"}
          </p>
        </div>
        <div className="prompt-text-block prompt-text-block-final">
          <h4>最终视频 Prompt</h4>
          <p>
            {version.prompt.final_prompt ??
              "该版本未保存可展示的最终 Prompt。"}
          </p>
        </div>
      </div>
    </article>
  );
}

export function ShotDetailPage() {
  const { projectId, shotId } = useParams<{
    projectId: string;
    shotId: string;
  }>();
  const [state, setState] = useState<PageState>("loading");
  const [data, setData] = useState<PageData | null>(null);
  const [loadError, setLoadError] = useState<PageError | null>(null);

  const loadShot = useCallback(async () => {
    if (!projectId || !shotId) {
      setLoadError({ code: "SHOT_NOT_FOUND", correlationId: null });
      setState("error");
      return;
    }
    setState("loading");
    setData(null);
    setLoadError(null);
    try {
      const [projectResult, shotResult, generationStatusResult] = await Promise.all([
        getProject(projectId),
        getShot(projectId, shotId),
        getShotGenerationStatus(projectId, shotId),
      ]);
      if (
        projectResult.data.project_id !== shotResult.data.project_id
        || generationStatusResult.data.project_id !== shotResult.data.project_id
        || generationStatusResult.data.shot_id !== shotResult.data.shot_id
      ) {
        throw new ApiClientError({
          message: "Project responses did not match.",
          code: "INVALID_RESPONSE",
        });
      }
      setData({
        project: projectResult.data,
        shot: shotResult.data,
        generationStatus: generationStatusResult.data,
      });
      setState("success");
    } catch (error) {
      setLoadError({
        code: error instanceof ApiClientError ? error.code : "UNKNOWN_ERROR",
        correlationId:
          error instanceof ApiClientError ? error.correlationId : null,
      });
      setState("error");
    }
  }, [projectId, shotId]);

  useEffect(() => {
    void loadShot();
  }, [loadShot]);

  const shotsPath = projectId
    ? projectStagePath(projectId, "shots")
    : "/projects";

  if (state === "loading") {
    return (
      <main className="main-content shot-detail-page" aria-busy="true">
        <section className="workspace-loading" aria-label="正在加载镜头详情">
          <p className="page-kicker">SHOT DETAIL</p>
          <h1>正在加载镜头详情…</h1>
          <div className="workspace-loading-lines" aria-hidden="true">
            <span /><span /><span />
          </div>
        </section>
      </main>
    );
  }

  if (state === "error" || !data) {
    const copy = errorCopy(loadError?.code ?? "UNKNOWN_ERROR");
    return (
      <main className="main-content shot-detail-page">
        <section className="empty-panel error-panel workspace-error" role="alert">
          <p className="empty-kicker">SHOT UNAVAILABLE</p>
          <h1>{copy.title}</h1>
          <p>{copy.message}</p>
          {loadError?.correlationId && (
            <small>错误编号：{loadError.correlationId}</small>
          )}
          <div className="workspace-error-actions">
            <Link className="secondary-button" to={shotsPath}>返回镜头列表</Link>
            <button className="primary-button" type="button" onClick={loadShot}>重试</button>
          </div>
        </section>
      </main>
    );
  }

  const { project, shot, generationStatus } = data;
  const status = statusPresentation(shot.status);
  const official = shot.versions.find((version) => version.role === "OFFICIAL");
  const pending = shot.versions.find((version) => version.role === "PENDING_REVIEW");
  const history = shot.versions
    .filter((version) => version.role === "HISTORY")
    .sort((left, right) => right.version - left.version);
  const generationIsActive = [
    "QUEUED",
    "SUBMITTING",
    "PROVIDER_RUNNING",
    "READY_TO_DOWNLOAD",
    "DOWNLOADING",
    "LOCAL_FINALIZING",
  ].includes(generationStatus.state);
  const workspacePath = projectWorkspacePath(project.project_id);
  const canonicalShotsPath = projectStagePath(project.project_id, "shots");
  const showInitialGenerationPreparation =
    !official &&
    !pending &&
    ["NOT_STARTED", "GENERATING", "FAILED"].includes(shot.status);
  const showCurrentPromptRegeneration = Boolean(official || pending);
  const manualPromptBase = pending ?? official;

  return (
    <main className="main-content shot-detail-page">
      <nav className="stage-breadcrumb" aria-label="Breadcrumb">
        <Link to="/projects">Projects</Link><span aria-hidden="true">/</span>
        <Link to={workspacePath}>{project.name}</Link><span aria-hidden="true">/</span>
        <Link to={canonicalShotsPath}>镜头</Link><span aria-hidden="true">/</span>
        <span aria-current="page">{shot.shot_id.replace("shot_", "Shot ")}</span>
      </nav>

      <Link className="back-link stage-back-link" to={canonicalShotsPath}>
        ← 返回镜头列表
      </Link>

      <header className="stage-header shot-detail-header">
        <div className="stage-title-row">
          <div>
            <p className="stage-project-name">{project.name}</p>
            <p className="page-kicker">SHOT DETAIL</p>
            <h1>{shot.shot_id.replace("shot_", "Shot ")}</h1>
          </div>
          <StatusBadge label={status.label} tone={status.tone} />
        </div>
        <dl className="shot-detail-summary">
          <div><dt>当前正式版本</dt><dd>{official ? `Video v${official.version} / Prompt ${official.prompt.version ? `v${official.prompt.version}` : "未记录"}` : "尚无"}</dd></div>
          <div><dt>待审核新版本</dt><dd>{pending ? `Video v${pending.version} / Prompt ${pending.prompt.version ? `v${pending.prompt.version}` : "未记录"}` : "无"}</dd></div>
          <div><dt>版本数量</dt><dd>{shot.version_count}</dd></div>
          <div><dt>累计生成</dt><dd>{shot.generation_count}</dd></div>
        </dl>
      </header>

      {showInitialGenerationPreparation && (
        <ShotGenerationPreparation
          projectId={project.project_id}
          shotId={shot.shot_id}
          onCompleted={loadShot}
        />
      )}

      {showCurrentPromptRegeneration && (
        <ShotGenerationPreparation
          projectId={project.project_id}
          shotId={shot.shot_id}
          intent="REGENERATE_CURRENT_PROMPT"
          onCompleted={loadShot}
        />
      )}

      {manualPromptBase?.prompt.version && editablePromptCore(manualPromptBase) && (
        <ShotGenerationPreparation
          projectId={project.project_id}
          shotId={shot.shot_id}
          intent="REGENERATE_MANUAL_PROMPT"
          manualPrompt={{
            videoVersion: manualPromptBase.version,
            promptVersion: manualPromptBase.prompt.version,
            editablePrompt: editablePromptCore(manualPromptBase),
          }}
          onCompleted={loadShot}
        />
      )}

      <section
        className="shot-version-section shot-version-section-official"
        aria-labelledby="official-version-title"
      >
        <div className="stage-section-heading">
          <p className="page-kicker">OFFICIAL</p>
          <h2 id="official-version-title">当前正式版本</h2>
        </div>
        {official ? (
          <VersionCard projectId={project.project_id} shotId={shot.shot_id} version={official} />
        ) : (
          <p className="stage-empty-copy">当前尚无正式版本。</p>
        )}
      </section>

      {pending && (
        <section
          className="shot-version-section shot-version-section-pending"
          aria-labelledby="pending-version-title"
        >
          <div className="stage-section-heading">
            <p className="page-kicker">PENDING REVIEW</p>
            <h2 id="pending-version-title">待审核新版本</h2>
          </div>
          <VersionCard projectId={project.project_id} shotId={shot.shot_id} version={pending} />
          <ShotApproveAction
            projectId={project.project_id}
            shotId={shot.shot_id}
            version={pending.version}
            previousOfficialVersion={official?.version ?? null}
            onApprovedRefresh={loadShot}
          />
        </section>
      )}

      <section className="shot-version-section" aria-labelledby="history-version-title">
        <div className="stage-section-heading">
          <p className="page-kicker">VERSION HISTORY</p>
          <h2 id="history-version-title">历史版本</h2>
        </div>
        {history.length > 0 ? (
          <div className="shot-history-list">
            {history.map((version) => (
              <div className="shot-history-item" key={version.version}>
                <VersionCard
                  projectId={project.project_id}
                  shotId={shot.shot_id}
                  version={version}
                />
                {official && (
                  <ShotSetOfficialAction
                    projectId={project.project_id}
                    shotId={shot.shot_id}
                    version={version.version}
                    promptVersion={version.prompt.version}
                    currentOfficialVersion={official.version}
                    blockedReason={
                      pending
                        ? "PENDING_REVIEW"
                        : generationIsActive
                          ? "ACTIVE_GENERATION"
                          : !version.video_available
                            ? "INCOMPLETE_VERSION"
                            : null
                    }
                    onSelectedRefresh={loadShot}
                  />
                )}
              </div>
            ))}
          </div>
        ) : (
          <p className="stage-empty-copy">当前没有历史版本。</p>
        )}
      </section>
    </main>
  );
}
