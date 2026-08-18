import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import {
  ApiClientError,
  getProject,
  getProjectWorkflow,
} from "../api/client";
import type { ProjectDetail, ProjectWorkflowResponse } from "../api/types";
import { StatusBadge } from "../components/StatusBadge";
import { WorkflowStageItem } from "../components/WorkflowStageItem";
import {
  AVAILABLE_ACTION_LABELS,
  formatProjectDate,
  statusPresentation,
  WORKFLOW_PHASE_LABELS,
} from "../projectPresentation";
import {
  projectStagePath,
  stagePresentation,
  STAGE_DEFINITIONS,
} from "../stageDefinitions";

type WorkspaceState = "loading" | "success" | "error";

interface WorkspaceData {
  detail: ProjectDetail;
  workflow: ProjectWorkflowResponse;
}

interface WorkspaceError {
  code: string;
  correlationId: string | null;
}

function displayText(value: string | null): string {
  return value && value.trim() ? value : "未填写";
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
    title: "暂时无法加载项目",
    message: "项目详情与工作流未能完整读取，请重试。",
  };
}

function headerStatus(workflow: ProjectWorkflowResponse): {
  label: string;
  tone: "neutral" | "progress" | "review" | "success" | "warning" | "danger";
} {
  if (workflow.stages.assembly.needs_update) {
    return { label: "需要重新合片", tone: "warning" };
  }
  if (workflow.stages.export.stale) {
    return { label: "需要重新导出", tone: "warning" };
  }
  if (workflow.workflow_phase === "COMPLETED") {
    return { label: "项目已完成", tone: "success" };
  }
  return statusPresentation(workflow.status);
}

export function ProjectWorkspacePage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [state, setState] = useState<WorkspaceState>("loading");
  const [data, setData] = useState<WorkspaceData | null>(null);
  const [loadError, setLoadError] = useState<WorkspaceError | null>(null);

  const loadWorkspace = useCallback(async () => {
    setState("loading");
    setData(null);
    setLoadError(null);

    if (!projectId) {
      setLoadError({ code: "PROJECT_NOT_FOUND", correlationId: null });
      setState("error");
      return;
    }

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
      setData({
        detail: detailResult.data,
        workflow: workflowResult.data,
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
  }, [projectId]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  if (state === "loading") {
    return (
      <main className="main-content workspace-page" aria-busy="true">
        <Link className="back-link" to="/projects">
          ← Projects
        </Link>
        <section className="workspace-loading" aria-label="正在加载项目">
          <p className="page-kicker">PROJECT WORKSPACE</p>
          <h1>正在加载项目…</h1>
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
    return (
      <main className="main-content workspace-page">
        <Link className="back-link" to="/projects">
          ← Projects
        </Link>
        <section className="empty-panel error-panel workspace-error" role="alert">
          <p className="empty-kicker">WORKSPACE UNAVAILABLE</p>
          <h1>{copy.title}</h1>
          <p>{copy.message}</p>
          {loadError?.correlationId && (
            <small>错误编号：{loadError.correlationId}</small>
          )}
          <div className="workspace-error-actions">
            <Link className="secondary-button" to="/projects">
              返回 Projects
            </Link>
            <button
              className="primary-button"
              type="button"
              onClick={loadWorkspace}
            >
              重试
            </button>
          </div>
        </section>
      </main>
    );
  }

  const { detail, workflow } = data;
  const request = detail.request;
  const projectStatus = headerStatus(workflow);
  const requestItems = [
    { label: "产品名称", value: displayText(request.product_name) },
    {
      label: "视频时长",
      value:
        request.duration_seconds === null
          ? "未填写"
          : `${request.duration_seconds} 秒`,
    },
    {
      label: "产品描述",
      value: displayText(request.product_description),
      wide: true,
    },
    { label: "视觉风格", value: displayText(request.video_style) },
    { label: "视频目的", value: displayText(request.video_purpose) },
    {
      label: "补充要求",
      value: displayText(request.user_notes),
      wide: true,
    },
  ];
  const stageItems = STAGE_DEFINITIONS.map((definition) => {
    const presentation = stagePresentation(workflow, definition.key);
    return {
      name: definition.label,
      status: presentation.status,
      summary: presentation.summary,
      to: projectStagePath(detail.project_id, definition.key),
    };
  });

  return (
    <main className="main-content workspace-page">
      <Link className="back-link" to="/projects">
        ← Projects
      </Link>

      <header className="workspace-header">
        <div className="workspace-title-row">
          <div>
            <p className="page-kicker">PROJECT WORKSPACE</p>
            <h1>{detail.name}</h1>
          </div>
          <StatusBadge label={projectStatus.label} tone={projectStatus.tone} />
        </div>
        <dl className="workspace-header-meta">
          <div>
            <dt>当前阶段</dt>
            <dd>{WORKFLOW_PHASE_LABELS[workflow.workflow_phase]}</dd>
          </div>
          <div>
            <dt>最后更新</dt>
            <dd>{formatProjectDate(workflow.updated_at)}</dd>
          </div>
        </dl>
      </header>

      <section className="workspace-section" aria-labelledby="request-title">
        <div className="workspace-section-heading">
          <div>
            <p className="page-kicker">ORIGINAL BRIEF</p>
            <h2 id="request-title">项目需求</h2>
          </div>
          <p>创建项目时提交的原始信息，仅供查看。</p>
        </div>
        <dl className="request-summary-grid">
          {requestItems.map((item) => (
            <div
              className={item.wide ? "request-summary-wide" : undefined}
              key={item.label}
            >
              <dt>{item.label}</dt>
              <dd>{item.value}</dd>
            </div>
          ))}
        </dl>
      </section>

      <section className="workspace-section" aria-labelledby="workflow-title">
        <div className="workspace-section-heading">
          <div>
            <p className="page-kicker">READ-ONLY OVERVIEW</p>
            <h2 id="workflow-title">Workflow</h2>
          </div>
          <p>所有状态均来自当前 Backend Workflow。</p>
        </div>

        <div className="workflow-stage-list">
          {stageItems.map((stage) => (
            <WorkflowStageItem key={stage.name} {...stage} />
          ))}
        </div>

        <aside className="available-actions" aria-label="当前可进行操作">
          <p className="page-kicker">AVAILABLE ACTIONS</p>
          {workflow.available_actions.length > 0 ? (
            <div>
              <strong>下一步可进行：</strong>
              <ul>
                {workflow.available_actions.map((action) => (
                  <li key={action}>{AVAILABLE_ACTION_LABELS[action]}</li>
                ))}
              </ul>
            </div>
          ) : (
            <p>当前没有待处理操作。</p>
          )}
          <small>本页面仅展示状态，不会执行任何操作。</small>
        </aside>
      </section>
    </main>
  );
}
