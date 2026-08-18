interface CapabilityItemProps {
  label: string;
  available: boolean | undefined;
  loading: boolean;
}

export function CapabilityItem({
  label,
  available,
  loading,
}: CapabilityItemProps) {
  const status = loading
    ? "Checking"
    : available
      ? "Available"
      : "Unavailable";
  const statusClass = loading
    ? "status-neutral"
    : available
      ? "status-positive"
      : "status-muted";

  return (
    <div className="capability-item">
      <span className="capability-label">{label}</span>
      <span className={`capability-state ${statusClass}`}>
        <span className="mini-dot" aria-hidden="true" />
        {status}
      </span>
    </div>
  );
}
