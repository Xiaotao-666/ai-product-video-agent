import { useCallback, useEffect, useRef, useState } from "react";
import { Link, NavLink, useParams } from "react-router-dom";

import {
  ApiClientError,
  getCreativeContent,
  getProject,
  getProjectWorkflow,
  getStoryboardContent,
  getVideoPrompts,
} from "../api/client";
import type {
  CreativeContentResponse,
  ProjectDetail,
  ProjectWorkflowResponse,
  StoryboardContentResponse,
  VideoPromptsContentResponse,
} from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { PostProductionStageContent } from "../components/PostProductionStageContent";
import { PlanningStageContent } from "../components/planning/PlanningStageContent";
import type {
  CreativeRefreshSnapshot,
  StoryboardRefreshSnapshot,
  VideoPromptsRefreshSnapshot,
} from "../components/planning/PlanningStageContent";
import { CreativeGenerateAction } from "../components/planning/CreativeGenerateAction";
import { CreativeApproveAction } from "../components/planning/CreativeApproveAction";
import { StoryboardGenerateAction } from "../components/planning/StoryboardGenerateAction";
import { StoryboardApproveAction } from "../components/planning/StoryboardApproveAction";
import { StoryboardRevisionAction } from "../components/planning/StoryboardRevisionAction";
import { VideoPromptGenerateAction } from "../components/planning/VideoPromptGenerateAction";
import { VideoPromptApproveAction } from "../components/planning/VideoPromptApproveAction";
import { VideoPromptRevisionAction } from "../components/planning/VideoPromptRevisionAction";
import { ShotsStageContent } from "../components/shots/ShotsStageContent";
import {
  AVAILABLE_ACTION_LABELS,
  formatProjectDate,
  statusPresentation,
  WORKFLOW_PHASE_LABELS,
} from "../projectPresentation";
import {
  actionsForStage,
  getStageDefinition,
  isStageKey,
  projectStagePath,
  projectWorkspacePath,
  stagePresentation,
  STAGE_DEFINITIONS,
} from "../stageDefinitions";

type StagePageState = "loading" | "success" | "error";

interface StagePageData {
  routeKey: string;
  detail: ProjectDetail;
  workflow: ProjectWorkflowResponse;
}

interface StagePageError {
  routeKey: string;
  code: string;
  correlationId: string | null;
}

function errorCopy(code: string): { title: string; message: string } {
  if (code === "PROJECT_NOT_FOUND" || code === "INVALID_PROJECT_ID") {
    return {
      title: "项目不存在或已被删除",
      message: "请返回 Projects 选择其他项目。",
    };
  }
  if (
    code === "PROJECT_DATA_CORRUPT" ||
    code === "PROJECT_DATA_UNSUPPORTED"
  ) {
    return {
      title: "项目数据暂时无法读取",
      message: "项目文件可能损坏或版本暂不受支持，请稍后重试。",
    };
  }
  if (code === "NETWORK_ERROR") {
    return {
      title: "无法连接 Backend",
      message: "请确认本地 FastAPI 服务已启动，然后重试。",
    };
  }
  return {
    title: "暂时无法加载项目阶段",
    message: "项目详情与工作流未能完整读取，请重试。",
  };
}

function stageNavigationClass({ isActive }: { isActive: boolean }): string {
  return `stage-nav-link${isActive ? " stage-nav-link-active" : ""}`;
}

export function ProjectStagePage() {
  const { projectId, stageKey } = useParams<{
    projectId: string;
    stageKey: string;
  }>();
  const validStageKey = isStageKey(stageKey) ? stageKey : null;
  const routeKey =
    projectId && validStageKey ? `${projectId}:${validStageKey}` : null;
  const definition = getStageDefinition(stageKey);
  const [state, setState] = useState<StagePageState>("loading");
  const [data, setData] = useState<StagePageData | null>(null);
  const [loadError, setLoadError] = useState<StagePageError | null>(null);
  const [creativeRefresh, setCreativeRefresh] =
    useState<CreativeRefreshSnapshot | null>(null);
  const [hasCreative, setHasCreative] = useState<boolean | null>(null);
  const [creativeTaskActive, setCreativeTaskActive] = useState(false);
  const [storyboardRefresh, setStoryboardRefresh] =
    useState<StoryboardRefreshSnapshot | null>(null);
  const [hasStoryboard, setHasStoryboard] = useState<boolean | null>(null);
  const [storyboardGenerateTaskActive, setStoryboardGenerateTaskActive] =
    useState(false);
  const [storyboardRevisionTaskActive, setStoryboardRevisionTaskActive] =
    useState(false);
  const [videoPromptsRefresh, setVideoPromptsRefresh] =
    useState<VideoPromptsRefreshSnapshot | null>(null);
  const [hasVideoPrompts, setHasVideoPrompts] = useState<boolean | null>(null);
  const [videoPromptGenerateTaskActive, setVideoPromptGenerateTaskActive] =
    useState(false);
  const [videoPromptRevisionTaskActive, setVideoPromptRevisionTaskActive] =
    useState(false);
  const loadRequest = useRef(0);

  const loadStage = useCallback(async () => {
    if (!projectId || !validStageKey) {
      return;
    }
    const requestedRouteKey = `${projectId}:${validStageKey}`;
    const requestId = ++loadRequest.current;
    setState("loading");
    setData(null);
    setLoadError(null);
    setCreativeRefresh(null);
    setHasCreative(null);
    setCreativeTaskActive(false);
    setStoryboardRefresh(null);
    setHasStoryboard(null);
    setStoryboardGenerateTaskActive(false);
    setStoryboardRevisionTaskActive(false);
    setVideoPromptsRefresh(null);
    setHasVideoPrompts(null);
    setVideoPromptGenerateTaskActive(false);
    setVideoPromptRevisionTaskActive(false);

    try {
      const [detailResult, workflowResult] = await Promise.all([
        getProject(projectId),
        getProjectWorkflow(projectId),
      ]);
      if (detailResult.data.project_id !== workflowResult.data.project_id) {
        throw new ApiClientError({
          message: "Project responses did not match.",
          code: "INVALID_RESPONSE",
        });
      }
      if (requestId !== loadRequest.current) return;
      setData({
        routeKey: requestedRouteKey,
        detail: detailResult.data,
        workflow: workflowResult.data,
      });
      setState("success");
    } catch (error) {
      if (requestId !== loadRequest.current) return;
      setLoadError({
        routeKey: requestedRouteKey,
        code: error instanceof ApiClientError ? error.code : "UNKNOWN_ERROR",
        correlationId:
          error instanceof ApiClientError ? error.correlationId : null,
      });
      setState("error");
    }
  }, [projectId, validStageKey]);

  useEffect(() => {
    if (validStageKey) {
      void loadStage();
    }
    return () => {
      loadRequest.current += 1;
    };
  }, [loadStage, validStageKey]);

  const handleCreativeLoaded = useCallback(
    (response: CreativeContentResponse) => {
      if (response.project_id === projectId) {
        setHasCreative(response.content !== null);
      }
    },
    [projectId],
  );

  const refreshCreativeState = useCallback(async () => {
    if (!projectId || validStageKey !== "creative") return;
    const [detailResult, workflowResult, creativeResult] = await Promise.all([
      getProject(projectId),
      getProjectWorkflow(projectId),
      getCreativeContent(projectId),
    ]);
    const canonicalProjectId = detailResult.data.project_id;
    if (
      workflowResult.data.project_id !== canonicalProjectId ||
      creativeResult.data.project_id !== canonicalProjectId
    ) {
      throw new ApiClientError({
        message: "Creative refresh responses did not match.",
        code: "INVALID_RESPONSE",
      });
    }
    setData({
      routeKey: `${projectId}:creative`,
      detail: detailResult.data,
      workflow: workflowResult.data,
    });
    setHasCreative(creativeResult.data.content !== null);
    setCreativeRefresh((current) => ({
      revision: (current?.revision ?? 0) + 1,
      response: creativeResult.data as CreativeContentResponse,
    }));
  }, [projectId, validStageKey]);

  const handleStoryboardLoaded = useCallback(
    (response: StoryboardContentResponse) => {
      if (response.project_id === projectId) {
        setHasStoryboard(response.content !== null);
      }
    },
    [projectId],
  );

  const refreshStoryboardState = useCallback(async () => {
    if (!projectId || validStageKey !== "storyboard") return;
    const [detailResult, workflowResult, storyboardResult] = await Promise.all([
      getProject(projectId),
      getProjectWorkflow(projectId),
      getStoryboardContent(projectId),
    ]);
    const canonicalProjectId = detailResult.data.project_id;
    if (
      workflowResult.data.project_id !== canonicalProjectId ||
      storyboardResult.data.project_id !== canonicalProjectId
    ) {
      throw new ApiClientError({
        message: "Storyboard refresh responses did not match.",
        code: "INVALID_RESPONSE",
      });
    }
    setData({
      routeKey: `${projectId}:storyboard`,
      detail: detailResult.data,
      workflow: workflowResult.data,
    });
    setHasStoryboard(storyboardResult.data.content !== null);
    setStoryboardRefresh((current) => ({
      revision: (current?.revision ?? 0) + 1,
      response: storyboardResult.data as StoryboardContentResponse,
    }));
  }, [projectId, validStageKey]);

  const handleVideoPromptsLoaded = useCallback(
    (response: VideoPromptsContentResponse) => {
      if (response.project_id === projectId) {
        setHasVideoPrompts(response.content !== null);
      }
    },
    [projectId],
  );

  const refreshVideoPromptState = useCallback(async () => {
    if (!projectId || validStageKey !== "video-prompt") return;
    const [detailResult, workflowResult, videoPromptsResult] = await Promise.all([
      getProject(projectId),
      getProjectWorkflow(projectId),
      getVideoPrompts(projectId),
    ]);
    const canonicalProjectId = detailResult.data.project_id;
    if (
      workflowResult.data.project_id !== canonicalProjectId ||
      videoPromptsResult.data.project_id !== canonicalProjectId
    ) {
      throw new ApiClientError({
        message: "Video Prompt refresh responses did not match.",
        code: "INVALID_RESPONSE",
      });
    }
    setData({
      routeKey: `${projectId}:video-prompt`,
      detail: detailResult.data,
      workflow: workflowResult.data,
    });
    setHasVideoPrompts(videoPromptsResult.data.content !== null);
    setVideoPromptsRefresh((current) => ({
      revision: (current?.revision ?? 0) + 1,
      response: videoPromptsResult.data,
    }));
  }, [projectId, validStageKey]);

  if (!definition || !validStageKey) {
    const overviewPath = projectId ? projectWorkspacePath(projectId) : "/projects";
    return (
      <main className="main-content stage-page">
        <section className="empty-panel error-panel workspace-error" role="alert">
          <p className="empty-kicker">UNKNOWN WORKFLOW STAGE</p>
          <h1>阶段不存在</h1>
          <p>该阶段不属于当前 Workflow。</p>
          <Link className="primary-button" to={overviewPath}>
            返回项目总览
          </Link>
        </section>
      </main>
    );
  }

  if (
    state === "loading" ||
    (state === "success" && data?.routeKey !== routeKey) ||
    (state === "error" && loadError?.routeKey !== routeKey)
  ) {
    return (
      <main className="main-content stage-page" aria-busy="true">
        <section className="workspace-loading" aria-label="正在加载项目阶段">
          <p className="page-kicker">WORKFLOW STAGE</p>
          <h1>正在加载项目阶段…</h1>
          <div className="workspace-loading-lines" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
        </section>
      </main>
    );
  }

  if (state === "error" || !data) {
    const copy = errorCopy(loadError?.code ?? "UNKNOWN_ERROR");
    const overviewPath = projectId ? projectWorkspacePath(projectId) : "/projects";
    return (
      <main className="main-content stage-page">
        <section className="empty-panel error-panel workspace-error" role="alert">
          <p className="empty-kicker">STAGE UNAVAILABLE</p>
          <h1>{copy.title}</h1>
          <p>{copy.message}</p>
          {loadError?.correlationId && (
            <small>错误编号：{loadError.correlationId}</small>
          )}
          <div className="workspace-error-actions">
            <Link className="secondary-button" to={overviewPath}>
              返回项目总览
            </Link>
            <button className="primary-button" type="button" onClick={loadStage}>
              重试
            </button>
          </div>
        </section>
      </main>
    );
  }

  const { detail, workflow } = data;
  const presentation = stagePresentation(workflow, validStageKey);
  const status = statusPresentation(presentation.status);
  const stageActions = actionsForStage(workflow, validStageKey);
  const readOnlyStageActions =
    validStageKey === "creative"
      ? stageActions.filter(
          (action) =>
            ![
              "APPROVE_CREATIVE",
              "RETRY_GENERATE_CREATIVE",
              "REVISE_CREATIVE",
              "REGENERATE_CREATIVE",
            ].includes(action),
        )
      : validStageKey === "storyboard"
        ? stageActions.filter(
            (action) =>
              ![
                "GENERATE_STORYBOARD",
                "APPROVE_STORYBOARD",
                "REVISE_STORYBOARD",
                "REGENERATE_STORYBOARD",
              ].includes(action),
          )
        : validStageKey === "video-prompt"
          ? stageActions.filter(
              (action) =>
                action !== "GENERATE_VIDEO_PROMPTS" &&
                action !== "APPROVE_VIDEO_PROMPTS" &&
                action !== "REVISE_VIDEO_PROMPTS" &&
                action !== "REGENERATE_VIDEO_PROMPTS",
            )
          : stageActions;
  const overviewPath = projectWorkspacePath(detail.project_id);

  return (
    <main className="main-content stage-page">
      <nav className="stage-breadcrumb" aria-label="Breadcrumb">
        <Link to="/projects">Projects</Link>
        <span aria-hidden="true">/</span>
        <Link to={overviewPath}>{detail.name}</Link>
        <span aria-hidden="true">/</span>
        <span aria-current="page">{definition.label}</span>
      </nav>

      <Link className="back-link stage-back-link" to={overviewPath}>
        ← 返回项目总览
      </Link>

      <header className="stage-header">
        <div className="stage-title-row">
          <div>
            <p className="stage-project-name">{detail.name}</p>
            <p className="page-kicker">WORKFLOW STAGE {definition.order}/9</p>
            <h1>{definition.label}</h1>
          </div>
          <StatusBadge label={status.label} tone={status.tone} />
        </div>
        <p className="stage-description">{definition.description}</p>
        <dl className="workspace-header-meta">
          <div>
            <dt>当前项目阶段</dt>
            <dd>{WORKFLOW_PHASE_LABELS[workflow.workflow_phase]}</dd>
          </div>
          <div>
            <dt>最后更新</dt>
            <dd>{formatProjectDate(workflow.updated_at)}</dd>
          </div>
        </dl>
      </header>

      <section className="stage-section" aria-labelledby="stage-summary-title">
        <div className="stage-section-heading">
          <p className="page-kicker">CURRENT STATE</p>
          <h2 id="stage-summary-title">阶段摘要</h2>
        </div>
        <p className="stage-summary-lead">{presentation.summary}</p>
        <dl className="stage-facts">
          {presentation.facts.map((fact) => (
            <div key={fact.label}>
              <dt>{fact.label}</dt>
              <dd>{fact.value}</dd>
            </div>
          ))}
        </dl>
        <p className="stage-content-note">{definition.contentNote}</p>
      </section>

      <PlanningStageContent
        key={`planning:${detail.project_id}:${validStageKey}`}
        projectId={detail.project_id}
        stageKey={validStageKey}
        creativeRefresh={creativeRefresh}
        storyboardRefresh={storyboardRefresh}
        videoPromptsRefresh={videoPromptsRefresh}
        onCreativeLoaded={handleCreativeLoaded}
        onStoryboardLoaded={handleStoryboardLoaded}
        onVideoPromptsLoaded={handleVideoPromptsLoaded}
      />

      {validStageKey === "creative" && (
        <>
          <CreativeGenerateAction
            projectId={detail.project_id}
            availableActions={workflow.available_actions}
            hasCreative={hasCreative}
            onTerminalRefresh={refreshCreativeState}
            onActiveTaskChange={setCreativeTaskActive}
          />
          <CreativeApproveAction
            projectId={detail.project_id}
            availableActions={workflow.available_actions}
            onApprovedRefresh={refreshCreativeState}
            disabled={creativeTaskActive}
          />
        </>
      )}

      {validStageKey === "storyboard" && (
        <>
          <StoryboardGenerateAction
            projectId={detail.project_id}
            availableActions={workflow.available_actions}
            hasStoryboard={hasStoryboard}
            onTerminalRefresh={refreshStoryboardState}
            onActiveTaskChange={setStoryboardGenerateTaskActive}
          />
          {(hasStoryboard === true ||
            workflow.available_actions.includes("REVISE_STORYBOARD") ||
            workflow.available_actions.includes("REGENERATE_STORYBOARD")) && (
            <StoryboardRevisionAction
              projectId={detail.project_id}
              availableActions={workflow.available_actions}
              onTerminalRefresh={refreshStoryboardState}
              onActiveTaskChange={setStoryboardRevisionTaskActive}
            />
          )}
          <StoryboardApproveAction
            projectId={detail.project_id}
            availableActions={workflow.available_actions}
            onApprovedRefresh={refreshStoryboardState}
            disabled={
              storyboardGenerateTaskActive || storyboardRevisionTaskActive
            }
          />
        </>
      )}

      {validStageKey === "video-prompt" && (
        <>
          <VideoPromptGenerateAction
            projectId={detail.project_id}
            availableActions={workflow.available_actions}
            videoPromptStatus={workflow.stages.video_prompt.status}
            hasVideoPrompts={hasVideoPrompts}
            onTerminalRefresh={refreshVideoPromptState}
            onActiveTaskChange={setVideoPromptGenerateTaskActive}
          />
          {(hasVideoPrompts === true ||
            workflow.available_actions.includes("REVISE_VIDEO_PROMPTS") ||
            workflow.available_actions.includes("REGENERATE_VIDEO_PROMPTS")) && (
            <VideoPromptRevisionAction
              projectId={detail.project_id}
              availableActions={workflow.available_actions}
              onTerminalRefresh={refreshVideoPromptState}
              onActiveTaskChange={setVideoPromptRevisionTaskActive}
            />
          )}
          <VideoPromptApproveAction
            projectId={detail.project_id}
            availableActions={workflow.available_actions}
            onApprovedRefresh={refreshVideoPromptState}
            disabled={
              videoPromptGenerateTaskActive || videoPromptRevisionTaskActive
            }
          />
        </>
      )}

      <ShotsStageContent
        key={`shots:${detail.project_id}:${validStageKey}`}
        projectId={detail.project_id}
        stageKey={validStageKey}
      />

      <PostProductionStageContent
        key={`post-production:${detail.project_id}:${validStageKey}`}
        projectId={detail.project_id}
        stageKey={validStageKey}
      />

      <section className="stage-section" aria-labelledby="stage-actions-title">
        <div className="stage-section-heading">
          <p className="page-kicker">READ-ONLY ACTIONS</p>
          <h2 id="stage-actions-title">当前可进行操作</h2>
        </div>
        {readOnlyStageActions.length > 0 ? (
          <ul className="stage-action-list">
            {readOnlyStageActions.map((action) => (
              <li key={action}>{AVAILABLE_ACTION_LABELS[action]}</li>
            ))}
          </ul>
        ) : (
          <p className="stage-empty-copy">当前阶段没有可进行操作。</p>
        )}
        <p className="stage-readonly-note">
          {validStageKey === "creative"
            ? "Creative 生成、修改、重新生成与审核操作均以 Backend 当前状态为准。"
            : validStageKey === "storyboard"
              ? "Storyboard 生成、修改、重新生成与审核操作均以 Backend 当前状态为准。"
              : validStageKey === "video-prompt"
                ? videoPromptGenerateTaskActive || videoPromptRevisionTaskActive
                  ? "视频提示词任务正在执行；完成前不能审核通过。"
                  : "视频提示词生成、修改、重新生成与审核操作均以 Backend 当前状态为准。"
                : "仅展示操作提示，本页面不会执行任何操作。"}
        </p>
      </section>

      <section className="stage-section" aria-labelledby="stage-navigation-title">
        <div className="stage-section-heading">
          <p className="page-kicker">WORKFLOW NAVIGATION</p>
          <h2 id="stage-navigation-title">其他 Workflow Stage</h2>
        </div>
        <nav className="stage-navigation" aria-label="Workflow stages">
          {STAGE_DEFINITIONS.map((item) => (
            <NavLink
              className={stageNavigationClass}
              end
              key={item.key}
              to={projectStagePath(detail.project_id, item.key)}
            >
              <span>{String(item.order).padStart(2, "0")}</span>
              {item.label}
            </NavLink>
          ))}
        </nav>
      </section>
    </main>
  );
}
