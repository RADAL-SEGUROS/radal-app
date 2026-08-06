import { useTranslation } from "react-i18next";
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { PLACEMENT_STATUSES, type PlacementStatus } from "@/api/types";

/**
 * The placement status pipeline, in the order the backend's state machine walks
 * it (`backend/app/api/routers/placements.py::ALLOWED_TRANSITIONS`).
 *
 * The UI NEVER decides which move is legal — `GET /placements/{id}/transitions`
 * does. This array only drives the visual order of the stepper.
 */
export const PLACEMENT_PIPELINE: PlacementStatus[] = [...PLACEMENT_STATUSES];

type BadgeVariant = NonNullable<BadgeProps["variant"]>;

const STATUS_VARIANT: Record<PlacementStatus, BadgeVariant> = {
  draft: "neutral",
  inspection: "warn",
  pre_underwriting: "action",
  quoting: "action",
  negotiating: "warn",
  awarded: "brand",
  active: "success",
  closed: "muted",
};

/** Dot color used by the pipeline stepper and the compact list rows. */
export const STATUS_DOT: Record<PlacementStatus, string> = {
  draft: "bg-[var(--text-muted)]",
  inspection: "bg-amber",
  pre_underwriting: "bg-blue",
  quoting: "bg-blue",
  negotiating: "bg-amber",
  awarded: "bg-teal",
  active: "bg-lime",
  closed: "bg-[var(--text-muted)]",
};

export function PlacementStatusBadge({
  status,
  className,
}: {
  status: PlacementStatus;
  className?: string;
}) {
  const { t } = useTranslation("placements");
  return (
    <Badge variant={STATUS_VARIANT[status]} className={className}>
      {t(`status.${status}`)}
    </Badge>
  );
}

/** Renovation-style urgency ramp for `days_to_period_end`. */
export type ExpiryTone = "red" | "amber" | "grey";

export function expiryTone(days: number | null | undefined): ExpiryTone {
  if (days === null || days === undefined) return "grey";
  if (days <= 30) return "red";
  if (days <= 60) return "amber";
  return "grey";
}

const TONE_CLASS: Record<ExpiryTone, string> = {
  red: "text-red-deep bg-[color-mix(in_srgb,var(--red)_13%,transparent)] border-[color-mix(in_srgb,var(--red)_28%,transparent)]",
  amber:
    "text-amber-deep bg-[color-mix(in_srgb,var(--amber)_13%,transparent)] border-[color-mix(in_srgb,var(--amber)_28%,transparent)]",
  grey: "text-text-muted bg-bg-recessed border-line",
};

/** "42 días" chip, colored by urgency. Renders an em dash when unknown. */
export function DaysChip({ days }: { days: number | null | undefined }) {
  const { t } = useTranslation("placements");
  if (days === null || days === undefined) {
    return <span className="text-text-muted">—</span>;
  }
  return (
    <span
      className={cn(
        "inline-flex items-center whitespace-nowrap rounded-full border px-2 py-0.5 font-mono text-mono-sm tabular-nums",
        TONE_CLASS[expiryTone(days)],
      )}
    >
      {days < 0 ? t("expiry.overdue", { count: Math.abs(days) }) : t("expiry.days", { count: days })}
    </span>
  );
}
