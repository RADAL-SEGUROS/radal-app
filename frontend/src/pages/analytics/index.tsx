/**
 * `/analytics` — **Analítica**: the shape of the portfolio, nothing else.
 *
 * This page used to be a dashboard AND six data tables in one scroll. The
 * tables moved to **Datos** (`pages/data/index.tsx`); what is left here answers
 * a different question — not "show me the rows" but "show me the shape". Both
 * destinations speak the SAME scope vocabulary (grupo · grupo-cuenta · fechas)
 * so the broker can narrow one, switch, and still be looking at the same slice.
 *
 * Two controls make it interactive rather than a poster:
 *  - the **scope**, which narrows every KPI and every chart to one grupo or one
 *    grupo-cuenta, server-side;
 *  - the **dimensión** (`?by=`), which re-buckets the group-by charts. Adding a
 *    dimension is a server change — the charts are generic (`BucketBarChart`).
 *
 * Comparing several groups side by side is deliberately absent: that is the
 * agent's job. This narrows to ONE and answers well.
 *
 * Exports: every chart card carries a PNG capture (the format a broker actually
 * pastes into WhatsApp), and the header exports the whole scoped dashboard as a
 * branded PDF.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { AlarmClock, FileSearch, FolderOpen, Repeat, Send, ShieldCheck, Sigma } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { Stagger, Swap } from "@/components/common/motion";
import { Skeleton } from "@/components/ui/skeleton";
import { uf } from "@/components/common/kit";
import { ScopeFilter, scopeParams, useScope } from "@/components/common/ScopeFilter";
import { ExportMenu } from "@/components/common/ExportMenu";
import { FilterChip } from "@/pages/data/shared";
import { useCaseFilesSummary } from "@/api/caseFiles";
import { useQuotesSummary } from "@/api/quotes";
import { useProposalsSummary } from "@/api/proposals";
import { usePoliciesSummary } from "@/api/policies";
import { num0, type WithGroupedSummary } from "@/api/types";
import { supports, unionDimensions, useExportCatalog } from "@/api/exports";
import { usePermissions, can } from "@/lib/permissions";
import { useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import {
  BucketBarChart,
  BucketTrendChart,
  InsurerDonutChart,
  StageBarChart,
  useBucketLabel,
} from "./charts";

/**
 * The two entities charted here. Their dimensions do NOT match — `case_files`
 * groups by `stage` and has no insurer, `proposals` groups by `insurer` and has
 * no stage — so the picker offers the union and each chart asks only for a
 * dimension its own entity can answer. Anything else would be a 422 behind a
 * control that looked live (rule 3).
 */
const CHART_ENTITIES = ["case_files", "proposals"] as const;

/** `month` is the only ORDERED dimension, so it is the only one drawn as a trend. */
const TIME_DIMENSION = "month";

export default function AnalyticsPage() {
  const { t } = useTranslation("analytics");
  const { t: tCases } = useTranslation("cases");
  const perms = usePermissions();
  const scope = useScope();
  const [byParam] = useUrlParam("by");
  const setParams = useSetUrlParams();

  const catalog = useExportCatalog();
  const dimensions = React.useMemo(
    () => unionDimensions(catalog.data, [...CHART_ENTITIES]),
    [catalog.data],
  );

  // Fall back to the first offered dimension rather than a hardcoded "stage":
  // a deployment whose catalog does not offer it would otherwise 422 on load.
  const dimension: string =
    byParam && dimensions.includes(byParam) ? byParam : (dimensions[0] ?? "");

  const casesGrouped = supports(catalog.data, "case_files", dimension);
  const proposalsGrouped = supports(catalog.data, "proposals", dimension);

  // --- Data, each summary behind its module's View grant --------------------
  const canSeeCases = can(perms.data, "CaseFiles", "View");
  const canSeeQuotes = can(perms.data, "Quotes", "View");
  const canSeeProposals = can(perms.data, "Proposals", "View");
  const canSeePolicies = can(perms.data, "Policies", "View");

  const filters = React.useMemo(() => scopeParams(scope), [scope]);

  const cases = useCaseFilesSummary(
    canSeeCases,
    React.useMemo(
      () => (casesGrouped ? { ...filters, group_by: dimension } : filters),
      [filters, dimension, casesGrouped],
    ),
  );
  const quotes = useQuotesSummary(canSeeQuotes, filters);
  const proposals = useProposalsSummary(
    canSeeProposals,
    React.useMemo(
      () => (proposalsGrouped ? { ...filters, group_by: dimension } : filters),
      [filters, dimension, proposalsGrouped],
    ),
  );
  const policies = usePoliciesSummary(canSeePolicies, filters);

  const caseKpis = React.useMemo(() => {
    const data = cases.data;
    if (!data) return null;
    const byStage = data.by_stage ?? {};
    return {
      open: data.open,
      total: data.total,
      inMarket: (byStage["market_submission"] ?? 0) + (byStage["quotes_received"] ?? 0),
      renewals: byStage["renewal_review"] ?? 0,
      overdueCollections: byStage["collection_overdue"] ?? 0,
    };
  }, [cases.data]);

  const quotesInProgress = quotes.data
    ? (quotes.data.by_status.draft ?? 0) +
      (quotes.data.by_status.sent ?? 0) +
      (quotes.data.by_status.receiving ?? 0)
    : 0;

  const anyKpiVisible = canSeeCases || canSeeQuotes || canSeeProposals || canSeePolicies;
  const kpisLoading =
    (canSeeCases && cases.isLoading) ||
    (canSeeQuotes && quotes.isLoading) ||
    (canSeeProposals && proposals.isLoading) ||
    (canSeePolicies && policies.isLoading);

  // Enum buckets arrive as raw values; only the frontend has the Spanish.
  const bucketLabel = useBucketLabel(dimension);

  const stageLabel = React.useCallback(
    (stage: string) => tCases(`stages.${stage}`, { defaultValue: stage }),
    [tCases],
  );

  /** The server's bucket envelope, present only when `group_by` was asked. */
  const caseBuckets = (cases.data as WithGroupedSummary | undefined)?.grouped?.buckets;
  const proposalBuckets = (proposals.data as WithGroupedSummary | undefined)?.grouped
    ?.buckets;

  // `defaultValue` matters: the catalog can name a dimension this build has no
  // copy for yet, and a missing key must render as the key, not blank.
  const dimensionOptions = dimensions.map((value) => ({
    value,
    label: t(`dimensions.${value}`, { defaultValue: value }),
  }));

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={t("eyebrow")}
        title={t("title")}
        subtitle={t("subtitle")}
      />

      <ScopeFilter
        trailing={
          // `group_by` is a sibling of `filters`, never a member of it:
          // `ScopeFilterPayload` is extra="forbid", so a stray key 422s rather
          // than being silently dropped. Only send it when case_files can
          // actually bucket by it.
          <ExportMenu
            entity="case_files"
            filters={filters}
            groupBy={casesGrouped ? dimension : null}
            filenameStem="analitica"
          />
        }
      />

      {/* ---- KPI row ------------------------------------------------------ */}
      {anyKpiVisible ? (
        kpisLoading ? (
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-[104px] w-full rounded-card" />
            ))}
          </div>
        ) : (
          <Stagger className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {caseKpis ? (
              <>
                <KpiCard
                  label={t("kpis.openAccounts")}
                  countTo={caseKpis.open}
                  icon={<FolderOpen />}
                  hint={t("kpis.openAccountsHint", { count: caseKpis.total })}
                />
                <KpiCard
                  label={t("kpis.inMarket")}
                  countTo={caseKpis.inMarket}
                  icon={<Send />}
                  hint={t("kpis.inMarketHint")}
                />
                <KpiCard
                  label={t("kpis.renewals")}
                  countTo={caseKpis.renewals}
                  icon={<Repeat />}
                  hint={t("kpis.renewalsHint")}
                />
                <KpiCard
                  label={t("kpis.overdueCollections")}
                  countTo={caseKpis.overdueCollections}
                  icon={<AlarmClock />}
                  tone={caseKpis.overdueCollections > 0 ? "danger" : "default"}
                  hint={t("kpis.overdueCollectionsHint")}
                />
              </>
            ) : null}
            {quotes.data ? (
              <KpiCard
                label={t("kpis.quotesInProgress")}
                countTo={quotesInProgress}
                icon={<FileSearch />}
                tone={quotes.data.overdue > 0 ? "danger" : "default"}
                hint={
                  quotes.data.overdue > 0
                    ? t("kpis.quotesOverdueHint", { count: quotes.data.overdue })
                    : t("kpis.quotesSentHint", { count: quotes.data.sent_last_30d })
                }
              />
            ) : null}
            {proposals.data ? (
              <KpiCard
                label={t("kpis.proposedPremium")}
                countTo={num0(proposals.data.total_premium_uf)}
                format={(n) => uf(n, 0)}
                icon={<Sigma />}
                hint={t("kpis.proposedPremiumHint", {
                  count: proposals.data.confirmed_count,
                })}
              />
            ) : null}
            {policies.data ? (
              <KpiCard
                label={t("kpis.activePolicies")}
                countTo={policies.data.active_count}
                icon={<ShieldCheck />}
                tone={policies.data.expiring_within_60_days > 0 ? "warn" : "default"}
                hint={
                  policies.data.expiring_within_60_days > 0
                    ? t("kpis.policiesExpiringHint", {
                        count: policies.data.expiring_within_60_days,
                      })
                    : t("kpis.policiesTotalHint", { count: policies.data.total })
                }
              />
            ) : null}
          </Stagger>
        )
      ) : null}

      {/* ---- The dimension picker ------------------------------------------ */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-caption text-ink-3">{t("dimensions.label")}</span>
        <FilterChip
          label={t("dimensions.label")}
          value={dimension}
          options={dimensionOptions}
          onChange={(value) => setParams({ by: value })}
        />
      </div>

      {/* ---- Group-by charts ----------------------------------------------- */}
      {/* Keyed on the dimension so re-bucketing animates instead of snapping. */}
      <Swap swapKey={dimension}>
        <div className="grid gap-3 lg:grid-cols-2">
          {canSeeCases && casesGrouped ? (
            dimension === TIME_DIMENSION ? (
              <BucketTrendChart
                title={t("charts.casesBy.title", {
                  dimension: t(`dimensions.${dimension}`),
                })}
                subtitle={t("charts.casesBy.subtitle")}
                buckets={caseBuckets}
                isLoading={cases.isLoading}
                unitLabel={t("charts.byStage.unit")}
                exportAs={`expedientes-por-${dimension}`}
                labelFor={bucketLabel}
              />
            ) : (
              <BucketBarChart
                title={t("charts.casesBy.title", {
                  dimension: t(`dimensions.${dimension}`),
                })}
                subtitle={t("charts.casesBy.subtitle")}
                buckets={caseBuckets}
                isLoading={cases.isLoading}
                unitLabel={t("charts.byStage.unit")}
                exportAs={`expedientes-por-${dimension}`}
                labelFor={bucketLabel}
              />
            )
          ) : null}

          {canSeeProposals && proposalsGrouped ? (
            <BucketBarChart
              title={t("charts.premiumBy.title", {
                dimension: t(`dimensions.${dimension}`),
              })}
              subtitle={t("charts.premiumBy.subtitle")}
              buckets={proposalBuckets}
              isLoading={proposals.isLoading}
              metric="uf"
              unitLabel="UF"
              exportAs={`prima-por-${dimension}`}
              labelFor={bucketLabel}
            />
          ) : null}
        </div>
      </Swap>

      {/* ---- The two fixed charts ------------------------------------------ */}
      {canSeeCases || canSeeProposals ? (
        <div className="grid gap-3 lg:grid-cols-2">
          {canSeeCases ? (
            <StageBarChart
              byStage={cases.data?.by_stage}
              labelFor={stageLabel}
              isLoading={cases.isLoading}
            />
          ) : null}
          {canSeeProposals ? (
            <InsurerDonutChart
              byInsurer={proposals.data?.by_insurer}
              total={proposals.data?.total}
              isLoading={proposals.isLoading}
            />
          ) : null}
        </div>
      ) : null}

      {!anyKpiVisible ? (
        // A role with no analytics module at all: say so instead of a blank
        // page — nothing is hidden without a reason.
        <p className="text-body text-ink-3">{t("noModules")}</p>
      ) : null}
    </div>
  );
}
