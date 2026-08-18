import { CapabilityItem } from "./CapabilityItem";

interface CapabilityEntry {
  label: string;
  available: boolean | undefined;
}

interface CapabilityGroupProps {
  eyebrow: string;
  title: string;
  entries: CapabilityEntry[];
  loading: boolean;
}

export function CapabilityGroup({
  eyebrow,
  title,
  entries,
  loading,
}: CapabilityGroupProps) {
  return (
    <section className="capability-card" aria-label={`${title} capabilities`}>
      <p className="card-eyebrow">{eyebrow}</p>
      <h3>{title}</h3>
      <div className="capability-list">
        {entries.map((entry) => (
          <CapabilityItem
            key={entry.label}
            label={entry.label}
            available={entry.available}
            loading={loading}
          />
        ))}
      </div>
    </section>
  );
}
