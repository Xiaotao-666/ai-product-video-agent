import type { StatusTone } from "../projectPresentation";

interface StatusBadgeProps {
  label: string;
  tone: StatusTone;
}

export function StatusBadge({ label, tone }: StatusBadgeProps) {
  return (
    <span className={`status-badge status-badge-${tone}`}>
      <span className="status-badge-dot" aria-hidden="true" />
      {label}
    </span>
  );
}
