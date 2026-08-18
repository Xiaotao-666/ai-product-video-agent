import { Link } from "react-router-dom";

import { statusPresentation } from "../projectPresentation";
import { StatusBadge } from "./StatusBadge";

interface WorkflowStageItemProps {
  name: string;
  status: string;
  summary: string;
  to: string;
}

export function WorkflowStageItem({
  name,
  status,
  summary,
  to,
}: WorkflowStageItemProps) {
  const presentation = statusPresentation(status);

  return (
    <Link className="workflow-stage-link" to={to}>
      <article className="workflow-stage-item">
        <div className="workflow-stage-heading">
          <h3>{name}</h3>
          <StatusBadge
            label={presentation.label}
            tone={presentation.tone}
          />
        </div>
        <p>{summary}</p>
        <span className="workflow-stage-open">查看阶段 →</span>
      </article>
    </Link>
  );
}
