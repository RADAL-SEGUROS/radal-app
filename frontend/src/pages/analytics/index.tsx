/**
 * /analytics — dashboard first, then the consolidated tables (spec v4 §4,
 * standing decision 4).
 *
 * Top half: the portfolio dashboard — a KPI row fed by the four module
 * summary endpoints (`/case-files/summary`, `/quotes/summary`,
 * `/proposals/summary`, `/policies/summary`, each gated on its module's `View`
 * grant) and two recharts charts (case files by stage, proposals by insurer).
 *
 * Bottom half: the entity tabs (underline variant) — Cuentas, Cotizaciones,
 * Propuestas, Pólizas, Post-venta, Documentos — each backed by its existing
 * list endpoint with server-side pagination (50 per page, `?page=`). A tab
 * whose module lacks `View` in the server matrix does not render; the page
 * itself is reachable by any role and per-tab gating handles narrow roles.
 *
 * URL discipline: `?tab=` for the active tab, `?sub=` / `?view=` / filter
 * params / `?page=` for tab state — a filtered view is a shareable link.
 * Switching tab drops `?page=` (a page index is meaningless across tables).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { AlarmClock, FileSearch, FolderOpen, Repeat, Send, ShieldCheck, Sigma } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { Stagger } from "@/components/common/motion";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { uf } from "@/components/common/kit";
import { useCaseFilesSummary } from "@/api/caseFiles";
import { useQuotesSummary } from "@/api/quotes";
import { useProposalsSummary } from "@/api/proposals";
import { usePoliciesSummary } from "@/api/policies";
import { num0 } from "@/api/types";
import { usePermissions, can, type PermissionModule } from "@/lib/permissions";
import { useSetUrlParams, useUrlParam } from "./shared";
import { InsurerDonutChart, StageBarChart } from "./charts";

import AccountsTab from "./accounts";
import QuotesTab from "./quotes";
import ProposalsTab from "./proposals";
import PoliciesTab from "./policies";
import PostsaleTab from "./postsale";
import DocumentsTab from "./documents";

type TabId = "accounts" | "quotes" | "proposals" | "policies" | "postsale" | "documents";

/** Tab → the module(s) whose `View` grant makes it exist. Post-venta renders
 *  when ANY of its three modules is granted (§4.2). */
const TAB_GATES: Record<TabId, PermissionModule[]> = {
  accounts: ["CaseFiles"],
  quotes: ["Quotes"],
  proposals: ["Proposals"],
  policies: ["Policies"],
  postsale: ["Endorsements", "Collections", "Claims"],
  documents: ["Documents"],
};

const TAB_ORDER: TabId[] = [
  "accounts",
  "quotes",
  "proposals",
  "policies",
  "postsale",
  "documents",
];

export default function AnalyticsPage() {
  const { t } = useTranslation("analytics");
  const { t: tCases } = useTranslation("cases");
  const perms = usePermissions();
  const [tabParam] = useUrlParam("tab");
  const setParams = useSetUrlParams();

  const visibleTabs = React.useMemo(
    () =>
      TAB_ORDER.filter((tab) =>
        TAB_GATES[tab].some((module) => can(perms.data, module, "View")),
      ),
    [perms.data],
  );

  const activeTab: TabId | null =
    tabParam && visibleTabs.includes(tabParam as TabId)
      ? (tabParam as TabId)
      : (visibleTabs[0] ?? null);

  /** Tab switch drops the page (and the post-sale sub-view's status leak). */
  const setTab = (tab: string) => setParams({ tab, page: null });

  // --- Dashboard data, each summary behind its module's View grant ----------
  const canSeeCases = can(perms.data, "CaseFiles", "View");
  const canSeeQuotes = can(perms.data, "Quotes", "View");
  const canSeeProposals = can(perms.data, "Proposals", "View");
  const canSeePolicies = can(perms.data, "Policies", "View");

  const cases = useCaseFilesSummary(canSeeCases);
  const quotes = useQuotesSummary(canSeeQuotes);
  const proposals = useProposalsSummary(canSeeProposals);
  const policies = usePoliciesSummary(canSeePolicies);

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

  const stageLabel = React.useCallback(
    (stage: string) => tCases(`stages.${stage}`, { defaultValue: stage }),
    [tCases],
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={t("eyebrow")}
        title={t("title")}
        subtitle={t("subtitle")}
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

      {/* ---- Charts row --------------------------------------------------- */}
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

      {/* ---- Entity tabs -------------------------------------------------- */}
      {perms.isLoading ? (
        <Skeleton className="h-64 w-full rounded-card" />
      ) : activeTab ? (
        <div className="flex flex-col gap-4">
          <Tabs value={activeTab} onValueChange={setTab}>
            <TabsList variant="underline">
              {visibleTabs.map((tab) => (
                <TabsTrigger key={tab} value={tab}>
                  {t(`tabs.${tab}`, { defaultValue: tab })}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          {activeTab === "accounts" ? (
            <AccountsTab />
          ) : activeTab === "quotes" ? (
            <QuotesTab />
          ) : activeTab === "proposals" ? (
            <ProposalsTab />
          ) : activeTab === "policies" ? (
            <PoliciesTab />
          ) : activeTab === "documents" ? (
            <DocumentsTab />
          ) : (
            <PostsaleTab />
          )}
        </div>
      ) : (
        // A role with no list module at all (matrix says no to everything):
        // say so instead of a blank pane — nothing is hidden without a reason.
        <p className="text-body text-ink-3">{t("noModules")}</p>
      )}
    </div>
  );
}
