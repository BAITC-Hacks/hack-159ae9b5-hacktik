import { Urgency, URGENCY_LABEL, URGENCY_COLOR } from "@/lib/api";

export default function UrgencyBadge({ urgency }: { urgency: Urgency }) {
  const color = URGENCY_COLOR[urgency];
  return (
    <span className="inline-flex items-center gap-1.5 text-[13px]">
      <span className="urgency-dot" style={{ background: color }} />
      <span style={{ color }}>{URGENCY_LABEL[urgency]}</span>
    </span>
  );
}
