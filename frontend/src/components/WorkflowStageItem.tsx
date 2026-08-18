import { statusPresentation } from "../projectPresentation";
import { StatusBadge } from "./StatusBadge";

interface WorkflowStageItemProps {
  name: string;
  status: string;
  summary: string;
}

export function WorkflowStageItem({
  name,
  status,
  summary,
}: WorkflowStageItemProps) {
  const presentation = statusPresentation(status);

  return (
    <article className="workflow-stage-item">
      <div className="workflow-stage-heading">
        <h3>{name}</h3>
        <StatusBadge
          label={presentation.label}
          tone={presentation.tone}
        />
      </div>
      <p>{summary}</p>
    </article>
  );
}
