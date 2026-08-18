import { useCallback, useEffect, useState } from "react";

import { ApiClientError, getProjects } from "../api/client";
import type { ProjectSummary } from "../api/types";
import { ProjectCard } from "../components/ProjectCard";

type ProjectsState = "loading" | "success" | "error";

export function ProjectsPage() {
  const [state, setState] = useState<ProjectsState>("loading");
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [correlationId, setCorrelationId] = useState<string | null>(null);

  const loadProjects = useCallback(async () => {
    setState("loading");
    setCorrelationId(null);
    try {
      const result = await getProjects();
      setProjects(result.data.projects);
      setState("success");
    } catch (error) {
      setProjects([]);
      setCorrelationId(
        error instanceof ApiClientError ? error.correlationId : null,
      );
      setState("error");
    }
  }, []);

  useEffect(() => {
    void loadProjects();
  }, [loadProjects]);

  const loading = state === "loading";

  return (
    <main className="main-content projects-page" aria-busy={loading}>
      <header className="page-header projects-header">
        <div>
          <p className="page-kicker">LOCAL WORKSPACE</p>
          <h1>Projects</h1>
          <p className="projects-subtitle">管理你的 AI 产品视频项目</p>
        </div>
        <div className="projects-header-actions">
          <span className={`backend-state backend-state-${state}`}>
            <span aria-hidden="true" />
            {loading
              ? "Backend Checking"
              : state === "success"
                ? "Backend Connected"
                : "Backend Offline"}
          </span>
          <button className="secondary-button" type="button" onClick={loadProjects} disabled={loading}>
            {loading ? "刷新中…" : "刷新"}
          </button>
          <button className="primary-button" type="button" disabled>
            新建项目
            <small>Coming soon</small>
          </button>
        </div>
      </header>

      {state === "loading" && (
        <section className="projects-state" aria-label="正在加载项目">
          <p className="state-message">正在加载项目…</p>
          <div className="projects-grid" aria-hidden="true">
            {[0, 1, 2].map((index) => (
              <div className="project-card project-card-skeleton" key={index}>
                <span />
                <strong />
                <p />
              </div>
            ))}
          </div>
        </section>
      )}

      {state === "error" && (
        <section className="empty-panel error-panel" role="alert">
          <p className="empty-kicker">CONNECTION ERROR</p>
          <h2>无法连接 Backend</h2>
          <p>请确认本地 FastAPI 服务已启动，然后重试。</p>
          {correlationId && <small>错误编号：{correlationId}</small>}
          <button className="secondary-button" type="button" onClick={loadProjects}>
            重试
          </button>
        </section>
      )}

      {state === "success" && projects.length === 0 && (
        <section className="empty-panel">
          <p className="empty-kicker">NO PROJECTS YET</p>
          <h2>还没有项目</h2>
          <p>创建你的第一个 AI 产品视频项目。</p>
          <button className="primary-button" type="button" disabled>
            新建项目
            <small>即将在下一阶段开放</small>
          </button>
        </section>
      )}

      {state === "success" && projects.length > 0 && (
        <section className="projects-grid" aria-label="项目列表">
          {projects.map((project) => (
            <ProjectCard key={project.project_id} project={project} />
          ))}
        </section>
      )}
    </main>
  );
}
