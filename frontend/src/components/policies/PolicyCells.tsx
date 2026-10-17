/**
 * The v12 policy row, cell by cell — shared by the desktop table and the
 * mobile card list of `/policies` so the two can never disagree.
 *
 * Column order follows the team spec ("las cinco primeras son Asegurado · Ramo
 * · Vigencia · Compañía · N° póliza", Estado al final, montos en UF).
 */
import { useTranslation } from "react-i18next";
import { AlertTriangle, CalendarClock } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { LevelBadge } from "@/components/collections/LevelBadge";
import { formatDate } from "@/lib/format";
import { uf } from "@/components/common/kit";
import { formatRut } from "@/pages/clients/rut";
import type { PolicyRow } from "@/api/policies";
import {
  COMPUTED_STATUS_LABEL,
  COMPUTED_STATUS_TONE,
  MISSING_LABEL,
  STORED_STATUS_LABEL,
  labelOf,
} from "@/components/policies/policyLabels";

export function money(value: string | null | undefined, currency: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "—";
  if (currency === "USD") {
    const n = Number(value);
    return Number.isNaN(n)
      ? `USD ${value}`
      : `USD ${new Intl.NumberFormat("es-CL", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(n)}`;
  }
  return uf(value);
}

export function InsuredCell({ policy }: { policy: PolicyRow }) {
  const name = policy.insured_name ?? policy.client_legal_name ?? "—";
  return (
    <div className="min-w-0">
      <div className="truncate font-medium text-ink">{name}</div>
      {policy.insured_rut ? (
        <div className="text-caption tabular-nums text-ink-3">{formatRut(policy.insured_rut)}</div>
      ) : null}
    </div>
  );
}

export function RamoCell({ policy }: { policy: PolicyRow }) {
  return (
    <div className="min-w-0">
      <div className="truncate text-ink-2">{policy.line?.name ?? "—"}</div>
      {policy.subtype ? (
        <div className="truncate text-caption text-ink-3">{policy.subtype.name}</div>
      ) : null}
    </div>
  );
}

export function PeriodCell({ policy }: { policy: PolicyRow }) {
  const start = policy.start_date ?? policy.period_start_at;
  const end = policy.end_date ?? policy.period_end_at;
  return (
    <span className="whitespace-nowrap text-caption tabular-nums text-ink-2">
      {formatDate(start)} → {formatDate(end)}
    </span>
  );
}

export function NumberCell({ policy }: { policy: PolicyRow }) {
  const { t } = useTranslation("policies");
  if (!policy.has_policy_number) {
    return <Badge variant="warn">{t("list.noNumber")}</Badge>;
  }
  return <span className="whitespace-nowrap tabular-nums text-ink">{policy.policy_number}</span>;
}

export function PremiumCell({ policy }: { policy: PolicyRow }) {
  return (
    <span className="whitespace-nowrap font-medium tabular-nums text-ink">
      {money(policy.total_premium_uf, policy.currency)}
    </span>
  );
}

export function CollectionCell({ policy }: { policy: PolicyRow }) {
  const { t } = useTranslation("policies");
  const c = policy.collection_summary;
  if (!c || c.state === "no_plan" || !c.plan_id) {
    return <Badge variant="muted">{t("collection.no_plan")}</Badge>;
  }
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <div className="flex flex-wrap items-center gap-1.5">
        {c.overdue_count > 0 ? (
          <Badge variant="danger" className="gap-1">
            <AlertTriangle className="h-3 w-3" />
            {t("list.overdueCount", { count: c.overdue_count })}
          </Badge>
        ) : null}
        <LevelBadge level={c.level} reason={c.level_reason} size="sm" />
      </div>
      {c.next_due_date ? (
        <span className="inline-flex items-center gap-1 whitespace-nowrap text-caption tabular-nums text-ink-3">
          <CalendarClock className="h-3 w-3" />
          {formatDate(c.next_due_date)}
          {c.next_due_amount_uf ? ` · ${money(c.next_due_amount_uf, policy.currency)}` : null}
        </span>
      ) : (
        <span className="text-caption text-ink-3">
          {c.state === "ok" && c.paid_count >= c.installments_count
            ? t("list.allPaid")
            : t("collection.ok")}
        </span>
      )}
    </div>
  );
}

export function MissingChips({ policy, max = 3 }: { policy: PolicyRow; max?: number }) {
  const { t } = useTranslation("policies");
  const missing = policy.missing ?? [];
  if (missing.length === 0) return <span className="text-caption text-ink-3">—</span>;
  const shown = missing.slice(0, max);
  const rest = missing.length - shown.length;
  return (
    <div className="flex flex-wrap gap-1">
      {shown.map((key) => (
        <Badge key={key} variant="warn" className="px-1.5">
          {labelOf(MISSING_LABEL, key, t)}
        </Badge>
      ))}
      {rest > 0 ? (
        <Badge
          variant="muted"
          className="px-1.5"
          title={missing
            .slice(max)
            .map((key) => labelOf(MISSING_LABEL, key, t))
            .join(" · ")}
        >
          +{rest}
        </Badge>
      ) : null}
    </div>
  );
}

export function StatusCell({ policy }: { policy: PolicyRow }) {
  const { t } = useTranslation("policies");
  const computed = policy.computed_status;
  return (
    <div className="flex flex-wrap items-center gap-1">
      {computed ? (
        <Badge variant={COMPUTED_STATUS_TONE[computed] ?? "neutral"} dot>
          {labelOf(COMPUTED_STATUS_LABEL, computed, t)}
        </Badge>
      ) : (
        <Badge variant="neutral" dot>
          {labelOf(STORED_STATUS_LABEL, policy.status, t)}
        </Badge>
      )}
      {!policy.has_source_document ? (
        <Badge variant="outline">{t("marks.noSource")}</Badge>
      ) : null}
      {policy.is_provisional ? <Badge variant="outline">{t("marks.provisional")}</Badge> : null}
    </div>
  );
}
