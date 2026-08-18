import type { ProjectSummary } from "../api/types";
import {
  formatProjectDate,
  statusPresentation,
  WORKFLOW_PHASE_LABELS,
} from "../projectPresentation";
import { StatusBadge } from "./StatusBadge";

interface ProjectCardProps {
  project: ProjectSummary;
}

function versionLabel(version: number | null): string {
  return version === null ? "" : ` · v${version}`;
}

export function ProjectCard({ project }: ProjectCardProps) {
  const projectStatus = statusPresentation(project.status);
  const assemblyStatus = project.assembly.needs_update
    ? { label: "需要更新", tone: "warning" as const }
    : statusPresentation(project.assembly.status);
  const exportStatus = project.final_export.stale
    ? { label: "需要更新", tone: "warning" as const }
    : statusPresentation(project.final_export.status);

  return (
    <article className="project-card">
      <div className="project-card-topline">
        <StatusBadge label={projectStatus.label} tone={projectStatus.tone} />
        <span className="project-updated">
          更新于 {formatProjectDate(project.updated_at)}
        </span>
      </div>

      <div className="project-identity">
        <h2>{project.name}</h2>
        <p>{WORKFLOW_PHASE_LABELS[project.workflow_phase]}</p>
      </div>

      <dl className="project-metadata">
        <div>
          <dt>Assembly</dt>
          <dd className={`metadata-${assemblyStatus.tone}`}>
            {assemblyStatus.label}
            {versionLabel(project.assembly.version)}
          </dd>
        </div>
        <div>
          <dt>Final Export</dt>
          <dd className={`metadata-${exportStatus.tone}`}>
            {exportStatus.label}
            {versionLabel(project.final_export.version)}
          </dd>
        </div>
      </dl>

      <button
        className="project-open-button"
        type="button"
        disabled
        title="Project Workspace 将在后续阶段开放"
      >
        打开项目
        <span>即将开放</span>
      </button>
    </article>
  );
}
