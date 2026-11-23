import { badgeTone } from "@/lib/status";

export function StatusBadge({
  label,
  status,
  tone,
}: {
  label: string;
  status?: string;
  tone?: string;
}) {
  return (
    <span className={`badge ${tone ?? badgeTone(status ?? "")}`}>{label}</span>
  );
}
