/**
 * AccountSummary — the WHOLE-account landing surface (v8 capstone).
 *
 * A bare account URL lands here, not on a single category tab: the broker
 * arrives at "where does this account stand and what must I still do", not at
 * one desk's detail. Three bands:
 *   1. CRITICAL PENDING ACTIONS — the typed gaps from
 *      `GET /case-files/{id}/pending-actions`, each row deep-linking to the desk
 *      that owns the fix and carrying the SERVER's own reason verbatim (rule 1);
 *   2. a KPI strip — records completeness, quotes, proposals, policies, read off
 *      the tree's account node (already tenant-narrowed);
 *   3. QUICK LINKS — one chip per essential desk, the agile shortcut into a tab.
 *
 * It never re-implements a guard: the Journey hero above owns stage moves, this
 * only reads and routes. `onSelectTab` switches the account page's `?tab=` in
 * place, so a pending row and a quick chip both land the broker on the right
 * desk without a full navigation.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  FileText,
  GitCompareArrows,
  Info,
  Layers,
  ScrollText,
  ShieldAlert,
  Sparkles,
} from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { KpiCard } from "@/components/common/KpiCard";
import { EmptyState, ErrorBanner } from "@/components/common/kit";
import { usePendingActions, pendingActionTab } from "@/api/pendingActions";
import { cn } from "@/lib/utils";
import type { CaseFileDetail, PendingAction, TreeLineNode } from "@/api/types";

/** Blocker → danger, warning → warn, everything else → neutral/info. */
function severityTone(severity: string): "danger" | "warn" | "neutral" {
  if (severity === "blocker") return "danger";
  if (severity === "warning") return "warn";
  return "neutral";
}

function SeverityIcon({ severity }: { severity: string }) {
  if (severity === "blocker")
    return <ShieldAlert className="h-4 w-4 text-neg-text" aria-hidden />;
  if (severity === "warning")
    return <AlertTriangle className="h-4 w-4 text-warn-text" aria-hidden />;
  return <Info className="h-4 w-4 text-ink-3" aria-hidden />;
}

function PendingRow({
  action,
  onSelectTab,
}: {
  action: PendingAction;
  onSelectTab: (tab: string) => void;
}) {
  const { t } = useTranslation("accounts");
  const tone = severityTone(action.severity);
  return (
    <li className="flex flex-wrap items-start gap-3 px-5 py-3.5">
      <span className="mt-0.5 shrink-0">
        <SeverityIcon severity={action.severity} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-body font-medium text-ink">
            {t(`summary.codes.${action.code}`, { defaultValue: action.code })}
          </span>
          <Badge variant={tone} className="px-1.5 py-0">
            {t(`summary.severity.${action.severity}`, { defaultValue: action.severity })}
          </Badge>
          {action.count > 1 ? (
            <span className="text-caption tabular-nums text-ink-3">
              {t("summary.pending.itemsChip", { count: action.count })}
            </span>
          ) : null}
        </div>
        {/* Spanish copy keyed by the action code; the server's own sentence
            (English for `stage_blocked`) is the fallback when a code is new. */}
        <p className="mt-0.5 text-pretty text-caption text-ink-2">
          {t(`summary.reasons.${action.code}`, { defaultValue: action.reason })}
        </p>
      </div>
      <Button
        size="sm"
        variant="secondary"
        className="shrink-0"
        onClick={() => onSelectTab(pendingActionTab(action.tab))}
      >
        {t("summary.pending.goTo")}
        <ArrowRight className="h-3.5 w-3.5" />
      </Button>
    </li>
  );
}

/** One quick-jump chip into an essential desk (the agile shortcut). */
function QuickChip({
  icon,
  label,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-md border border-line bg-bone px-2.5 py-1.5",
        "text-caption font-medium text-ink-2 no-underline",
        "transition-[border-color,background-color,color] duration-150",
        "hover:border-line-strong hover:bg-paper-2/60 hover:text-ink",
      )}
    >
      <span className="[&_svg]:h-3.5 [&_svg]:w-3.5 [&_svg]:text-ink-3">{icon}</span>
      {label}
    </button>
  );
}

export function AccountSummary({
  caseId,
  line,
  detail,
  onSelectTab,
}: {
  caseId: number;
  line: TreeLineNode | undefined;
  detail: CaseFileDetail | undefined;
  onSelectTab: (tab: string) => void;
}) {
  const { t } = useTranslation("accounts");
  const pending = usePendingActions(caseId);

  const account = line?.account;
  const documentsCount = account?.documents_count ?? detail?.documents_count ?? 0;
  const quotesCount = account?.quotes_count ?? detail?.quote_requests_count ?? 0;
  const proposalsCount = account?.proposals_count ?? detail?.proposals_count ?? 0;
  const policiesCount = line?.policies.length ?? 0;

  // Records completeness: how many of the server-declared record folders hold
  // at least one document (`record_counts`, every key present, 0 when empty).
  const recordCounts = account?.record_counts;
  const completeness = React.useMemo(() => {
    if (!recordCounts) return null;
    const keys = Object.keys(recordCounts);
    if (keys.length === 0) return null;
    const filled = keys.filter((k) => (recordCounts[k] ?? 0) > 0).length;
    return { filled, total: keys.length };
  }, [recordCounts]);

  const actions = pending.data?.actions ?? [];

  const quickLinks: { key: string; tab: string; icon: React.ReactNode }[] = [
    { key: "records", tab: "antecedentes", icon: <FileText /> },
    { key: "technical", tab: "bases-tecnicas", icon: <ScrollText /> },
    { key: "comparison", tab: "comparison", icon: <GitCompareArrows /> },
    { key: "proposal", tab: "propuesta", icon: <Sparkles /> },
    { key: "policies", tab: "policies", icon: <Layers /> },
  ];

  return (
    <div className="flex flex-col gap-4">
      {/* 1. Critical pending actions — the "what must I do" band. */}
      <Card className="overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <h3 className="text-h3 tracking-tight text-ink">{t("summary.pending.title")}</h3>
          {actions.length > 0 ? (
            <Badge variant="warn" dot>
              {t("summary.pending.count", { count: actions.length })}
            </Badge>
          ) : null}
        </div>

        {pending.isError ? (
          <div className="p-5">
            <ErrorBanner error={pending.error} />
          </div>
        ) : pending.isLoading ? (
          <div className="flex flex-col gap-2 p-5">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : actions.length === 0 ? (
          <EmptyState
            icon={<CheckCircle2 className="h-6 w-6 text-pos-text" />}
            title={t("summary.pending.empty")}
            hint={t("summary.pending.emptyHint")}
          />
        ) : (
          <ul className="divide-y divide-line">
            {actions.map((action, index) => (
              <PendingRow
                key={`${action.code}-${index}`}
                action={action}
                onSelectTab={onSelectTab}
              />
            ))}
          </ul>
        )}
      </Card>

      {/* 2. KPI strip — the whole-account numbers, read off the tree node. */}
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <KpiCard
          label={t("summary.kpi.records")}
          value={documentsCount}
          icon={<FileText />}
          hint={
            completeness
              ? t("summary.kpi.completeness", {
                  filled: completeness.filled,
                  total: completeness.total,
                })
              : undefined
          }
          tone={completeness && completeness.filled < completeness.total ? "warn" : "brand"}
        />
        <KpiCard label={t("summary.kpi.quotes")} value={quotesCount} icon={<GitCompareArrows />} />
        <KpiCard label={t("summary.kpi.proposals")} value={proposalsCount} icon={<Sparkles />} />
        <KpiCard label={t("summary.kpi.policies")} value={policiesCount} icon={<Layers />} />
      </div>

      {/* 3. Quick links — the agile shortcut into each essential desk. */}
      <Card className="flex flex-col gap-2.5 p-4">
        <span className="text-label font-medium text-ink">{t("summary.quickLinks.title")}</span>
        <div className="flex flex-wrap gap-2">
          {quickLinks.map((link) => (
            <QuickChip
              key={link.key}
              icon={link.icon}
              label={t(`summary.quickLinks.${link.key}`)}
              onClick={() => onSelectTab(link.tab)}
            />
          ))}
        </div>
      </Card>
    </div>
  );
}
