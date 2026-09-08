/**
 * Shared post-sale primitives — policy, endorsement, collection, claim.
 *
 * It lives under `pages/policies/` for the same reason `pages/proposals/shared`
 * exists: the four post-sale page directories are one pass, and the status
 * vocabulary, the signed-delta rendering and the noon-convention datetime are
 * used by all of them. One copy, not four.
 *
 * Copy comes from the `postsale` namespace (`es` authoritative, `en` mirrors).
 * The generic shells — Section, KeyValue, DisabledHint, EmptyState — are reused
 * from `@/pages/proposals/shared`; nothing is reimplemented here.
 */
import { useTranslation } from "react-i18next";
import { Clock } from "lucide-react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { formatDate, formatDateTime, formatNumber } from "@/lib/format";
import { num, type DecimalString } from "@/api/types";
import { uf } from "@/pages/proposals/shared";

type Variant = NonNullable<BadgeProps["variant"]>;

/**
 * Enum token -> badge tone, for every post-sale vocabulary.
 *
 * Tokens are unique enough across the four modules that one table is honest;
 * where two modules share a token (`pending`, `cancelled`, `settled`) they also
 * share the meaning, so they share the colour.
 */
const POSTSALE_TONE: Record<string, Variant> = {
  // policy
  draft: "neutral",
  active: "success",
  expired: "muted",
  cancelled: "muted",
  renewed: "brand",
  // endorsement
  proposed: "action",
  issued: "brand",
  applied: "success",
  rejected: "danger",
  // warranty
  pending: "neutral",
  in_progress: "action",
  met_on_time: "success",
  met_late: "warn",
  met_after_claim: "warn",
  breached: "danger",
  waived: "muted",
  // collection plan
  current: "success",
  overdue: "danger",
  settled: "success",
  suspended: "warn",
  terminated: "danger",
  rehabilitated: "brand",
  // instalment
  due: "warn",
  paid: "success",
  paid_late: "warn",
  credited: "action",
  // claim
  reported: "action",
  under_review: "warn",
  closed: "muted",
  // claim ruling
  covered: "success",
  partially_covered: "warn",
  // severity — mirror-diff rows and collection alerts both emit high/medium/low
  high: "danger",
  medium: "warn",
  low: "neutral",
  // case-file kinds on the sub-funnel
  account: "brand",
  endorsement: "action",
  collection: "warn",
  claim: "danger",
  renewal: "brand",
};

export function postsaleTone(value: string | null | undefined): Variant {
  return (value && POSTSALE_TONE[value]) || "neutral";
}

/** Badge coloured from the backend enum token, labelled from `postsale`. */
export function PostsaleBadge({
  value,
  label,
  className,
}: {
  value: string | null | undefined;
  label?: string;
  className?: string;
}) {
  return (
    <Badge variant={postsaleTone(value)} className={className}>
      {label ?? value ?? "—"}
    </Badge>
  );
}

// =============================================================================
// Money with a sign
// =============================================================================

/**
 * A delta rendered WITH its sign. An exclusion or a sum-insured decrease is
 * negative and an administrative endorsement is exactly zero — never take an
 * absolute value, and never hide the zero.
 */
export function signedUf(
  value: DecimalString | number | null | undefined,
  decimals = 2,
): string {
  const parsed = num(value);
  if (parsed === null) return "—";
  const body = uf(Math.abs(parsed), decimals);
  if (parsed > 0) return `+${body}`;
  if (parsed < 0) return `−${body}`;
  return body;
}

export function DeltaAmount({
  value,
  decimals = 2,
  className,
}: {
  value: DecimalString | number | null | undefined;
  decimals?: number;
  className?: string;
}) {
  const parsed = num(value);
  const tone =
    parsed === null || parsed === 0
      ? "text-text-tertiary"
      : parsed > 0
        ? "text-warn-text"
        : "text-pos-text";
  return (
    <span className={cn("tabular-nums font-medium", tone, className)}>
      {signedUf(value, decimals)}
    </span>
  );
}

/** True when the two amounts differ by more than the server's cent tolerance. */
export function offBy(a: number | null, b: number | null, tolerance = 0.01): boolean {
  if (a === null || b === null) return false;
  return Math.abs(a - b) > tolerance;
}

// =============================================================================
// Dates — the contractual noon convention
// =============================================================================

/**
 * A contractual datetime. The hour is not decoration: policy periods run to
 * 12:00 and claim franchises are counted in hours, so the time is always shown
 * and the fallback to a bare date is explicit.
 */
export function DateTimeValue({
  value,
  fallbackDate,
}: {
  value: string | null | undefined;
  /** The legacy `start_date` / `end_date`, used only when the datetime is absent. */
  fallbackDate?: string | null;
}) {
  if (value) return <>{formatDateTime(value)}</>;
  if (fallbackDate) return <>{formatDate(fallbackDate)}</>;
  return <>—</>;
}

/** Whole days between two instants, or `null` when either is missing. */
export function daysBetween(
  from: string | null | undefined,
  to: string | null | undefined,
): number | null {
  if (!from || !to) return null;
  const a = new Date(from).getTime();
  const b = new Date(to).getTime();
  if (Number.isNaN(a) || Number.isNaN(b)) return null;
  return Math.round((b - a) / 86_400_000);
}

/** "12 días" / "hace 3 días" style counter chip. */
export function DaysChip({
  days,
  tone,
}: {
  days: number | null;
  tone?: Variant;
}) {
  const { t } = useTranslation("postsale");
  if (days === null) return <span className="text-text-muted">—</span>;
  return (
    <Badge variant={tone ?? (days < 0 ? "danger" : "neutral")} className="gap-1">
      <Clock className="h-3 w-3" />
      {t("shared.days", { count: Math.abs(days) })}
    </Badge>
  );
}

/** Percentage 0-100 from a wire decimal, "—" when absent. */
export function pctValue(value: DecimalString | number | null | undefined): string {
  const parsed = num(value);
  if (parsed === null) return "—";
  return `${formatNumber(parsed, 1)} %`;
}

// =============================================================================
// Free-form payload rendering
// =============================================================================

/**
 * Render whatever an extraction / `effect` JSON put in a cell.
 *
 * The shapes vary per endorsement kind and per adjuster, so the renderer stays
 * deliberately dumb: scalars verbatim, objects and arrays flattened to a
 * readable line. Prose survives — it is frequently the coverage itself.
 */
export function renderValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "✓" : "✗";
  if (typeof value === "number") return formatNumber(value, Number.isInteger(value) ? 0 : 2);
  if (typeof value === "string") return value;
  if (Array.isArray(value)) return value.map(renderValue).join(" · ");
  if (typeof value === "object") {
    return Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== null && v !== undefined && v !== "")
      .map(([k, v]) => `${humanizePath(k)}: ${renderValue(v)}`)
      .join(" · ");
  }
  return String(value);
}

/** `premium.net_premium_uf` -> "premium · net premium uf" for a diff row label. */
export function humanizePath(path: string): string {
  return path
    .split(".")
    .map((part) => part.replace(/_/g, " ").replace(/\[(\d+)\]/g, " $1"))
    .join(" · ");
}

/** First non-empty value among several possible keys of a loose payload. */
export function pick<T = unknown>(
  source: Record<string, unknown> | null | undefined,
  ...keys: string[]
): T | undefined {
  if (!source) return undefined;
  for (const key of keys) {
    const value = source[key];
    if (value !== null && value !== undefined && value !== "") return value as T;
  }
  return undefined;
}

/** Coerce a loose payload entry into an array of records — adjuster tables. */
export function asRows(value: unknown): Record<string, unknown>[] {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (row): row is Record<string, unknown> =>
      !!row && typeof row === "object" && !Array.isArray(row),
  );
}
