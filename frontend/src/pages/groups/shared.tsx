/**
 * Shared helpers for the group pages (spec v3 §5.4).
 *
 * Everything here is small, page-level plumbing: the `:groupId` param, the
 * structured-422 translator, the vigencia date helpers and two presentational
 * chips. Group-level widgets (`OriginBadge`, `DownloadArchiveButton`,
 * `Journey`) live in `components/groups/`.
 */
import * as React from "react";
import { Link, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { Badge } from "@/components/ui/badge";
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb";
import { formatDate } from "@/lib/format";
import { apiError } from "@/components/common/kit";
import {
  ACCOUNT_ERROR_CODES,
  type CaseFileStatus,
  type CaseOrigin,
  type CaseStage,
  type IsoDate,
} from "@/api/types";

/** Translate function of the `accounts` namespace, as these helpers need it. */
export type Translate = (key: string, options?: Record<string, unknown>) => string;

// -----------------------------------------------------------------------------
// Params & small hooks
// -----------------------------------------------------------------------------

/** `:groupId` as a number. `NaN` when the route is entered without one. */
export function useGroupId(): number {
  const { groupId } = useParams();
  return Number(groupId);
}

/** `:caseId` as a number. */
export function useCaseId(): number {
  const { caseId } = useParams();
  return Number(caseId);
}

/** `:policyId` as a number. */
export function usePolicyId(): number {
  const { policyId } = useParams();
  return Number(policyId);
}

/** Debounce a value — the same three lines every list page carries. */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = React.useState(value);
  React.useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(id);
  }, [value, delay]);
  return debounced;
}

// -----------------------------------------------------------------------------
// Errors
// -----------------------------------------------------------------------------

/** The `detail.code` of a structured 422, when the server sent one. */
export function errorCode(error: unknown): string | null {
  const detail = (
    error as { response?: { data?: { detail?: unknown } } } | undefined
  )?.response?.data?.detail;
  if (detail && typeof detail === "object" && !Array.isArray(detail)) {
    const code = (detail as { code?: unknown }).code;
    if (typeof code === "string") return code;
  }
  return null;
}

/**
 * A refusal the group flows return as `{"code": "period_locked", …}` becomes
 * the Spanish sentence the broker needs, not the English `detail` string.
 * Anything else falls through to the generic axios message.
 */
export function accountErrorMessage(error: unknown, t: Translate): string {
  const code = errorCode(error);
  if (code && (ACCOUNT_ERROR_CODES as readonly string[]).includes(code)) {
    return t(`errors.${code}`);
  }
  return apiError(error, t("errors.generic"));
}

// -----------------------------------------------------------------------------
// Vigencia helpers
// -----------------------------------------------------------------------------

/** `"2026-2027"` — the grouping LABEL, never a range (spec §1). */
export function derivePeriodLabel(
  start: IsoDate | null | undefined,
  end: IsoDate | null | undefined,
): string {
  if (!start || !end) return "";
  return `${start.slice(0, 4)}-${end.slice(0, 4)}`;
}

/** Same calendar day, `n` years later — the default a renewal proposes. */
export function addYears(iso: IsoDate | null | undefined, years = 1): string {
  if (!iso) return "";
  const [y, m, d] = iso.slice(0, 10).split("-");
  const year = Number(y) + years;
  if (!Number.isFinite(year)) return "";
  return `${String(year).padStart(4, "0")}-${m}-${d}`;
}

/** Today as `yyyy-mm-dd`, for a date input's default. */
export function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

/**
 * `"15 oct 2025 – 15 oct 2026"`. Full dates are shown at ramo and policy level
 * on purpose: the label groups, the dates are the contract (spec §1).
 */
export function periodDates(
  start: IsoDate | null | undefined,
  end: IsoDate | null | undefined,
): string {
  if (!start && !end) return "—";
  return `${start ? formatDate(start) : "—"} – ${end ? formatDate(end) : "—"}`;
}

// -----------------------------------------------------------------------------
// Presentation
// -----------------------------------------------------------------------------

const ORIGIN_TONE: Record<CaseOrigin, "neutral" | "brand" | "warn"> = {
  new: "neutral",
  renewal: "brand",
  period_change: "warn",
};

/**
 * How the folder came to exist. Dynamic key — `accounts:origin.*` covers every
 * `CaseOrigin` member in both locales, so this can never print a raw key.
 */
export function OriginChip({
  origin,
  t,
}: {
  origin: CaseOrigin;
  t: Translate;
}) {
  return <Badge variant={ORIGIN_TONE[origin]}>{t(`origin.${origin}`)}</Badge>;
}

const STATUS_TONE: Record<CaseFileStatus, "neutral" | "brand" | "success" | "warn" | "muted"> = {
  open: "brand",
  on_hold: "warn",
  won: "success",
  lost: "muted",
  cancelled: "muted",
  closed: "neutral",
};

/** The journey stage, labelled from the `cases` namespace (29 members, both locales). */
export function StageBadge({ stage }: { stage: CaseStage }) {
  const { t } = useTranslation("cases");
  return <Badge variant="neutral">{t(`stages.${stage}`, { defaultValue: stage })}</Badge>;
}

/** The folder's own status — open / closed / won / lost. */
export function CaseStatusBadge({ status }: { status: CaseFileStatus }) {
  const { t } = useTranslation("cases");
  return (
    <Badge variant={STATUS_TONE[status]} dot>
      {t(`statuses.${status}`, { defaultValue: status })}
    </Badge>
  );
}

export interface Crumb {
  label: React.ReactNode;
  to?: string;
}

/** *Grupos › Viña Indómita › 2025–2026 › Incendio* */
export function GroupCrumbs({ items }: { items: Crumb[] }) {
  return (
    <Breadcrumb>
      <BreadcrumbList className="gap-1 text-caption sm:gap-1.5">
        {items.map((crumb, index) => (
          <React.Fragment key={index}>
            {index > 0 ? <BreadcrumbSeparator className="[&>svg]:size-3" /> : null}
            <BreadcrumbItem className="min-w-0">
              {crumb.to ? (
                <BreadcrumbLink asChild>
                  <Link to={crumb.to} className="truncate no-underline">
                    {crumb.label}
                  </Link>
                </BreadcrumbLink>
              ) : (
                <BreadcrumbPage className="truncate font-normal text-ink-2">
                  {crumb.label}
                </BreadcrumbPage>
              )}
            </BreadcrumbItem>
          </React.Fragment>
        ))}
      </BreadcrumbList>
    </Breadcrumb>
  );
}

/** The page body inside `GroupShell`'s pane — the rail owns the padding. */
export function GroupPage({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return <div className={className ?? "flex flex-col gap-5"}>{children}</div>;
}
