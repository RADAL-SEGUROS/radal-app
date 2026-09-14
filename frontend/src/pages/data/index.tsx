/**
 * `/data` — **Portafolio**: every consolidated table in the tenant, in the
 * order a broker actually works.
 *
 * This is one half of the old `/analytics`, which fused a KPI dashboard and six
 * data tables into a single scroll. They are now two destinations with one job
 * each: **Portafolio** answers "show me the rows", **Analítica** answers "show
 * me the shape".
 *
 * Nine tabs, and they are the broker's own sequence (issue #2 — the eleven
 * table-shaped tabs before them were the data model's sequence, not his):
 *
 *   Asegurados · Pipeline · Cotizaciones · Propuestas · Pólizas ·
 *   Endosos · Cobranza · Siniestros · Documentos
 *
 * What changed and why:
 *  - **Asegurados** merges Cuentas + Clientes behind one `?sub=` toggle — "who
 *    do I insure" and "what have I placed for them" are one question asked
 *    from two ends.
 *  - **Cotizaciones** no longer lists `quote_request` rows; it lists
 *    **colocaciones** (`case_file(kind=account)`) in three market states.
 *  - **Endosos / Cobranza / Siniestros** were promoted out of the single
 *    "Post-venta" tab: three desks, three tabs, three export entities — which
 *    is also what removed the old "pick one entity" disabled export.
 *  - **Colocaciones, Inspecciones and Ofertas lost their tabs.** They are
 *    absorbed into the account's own journey (a placement is an account's
 *    market submission; an inspection and an offering are steps inside it),
 *    and the broker never opened them as flat cross-group lists. Their
 *    standalone pages and routes (`/placements`, `/inspections`, `/offerings`)
 *    are untouched and still resolve for deep links from inside a group —
 *    nothing was hidden without a path to it.
 *  - A **Renovaciones** tab was asked for and is deliberately NOT here: the
 *    renewal workflow is not settled, and a placeholder would be a dead tab.
 *
 * Three things every tab inherits from this page:
 *  1. the **scope** (grupo · grupo-cuenta · fechas), applied server-side and
 *     held in the URL, so a scoped table is a shareable link;
 *  2. an **export** (XLSX / PDF) of the whole filtered result, not the page —
 *     and it follows the sub-view where the entity differs, so the file and
 *     the table on screen can never disagree about what was filtered;
 *  3. the tab switch animates — see `components/ui/tabs.tsx`.
 *
 * URL discipline: `?tab=` for the active tab, `?group=` / `?account=` /
 * `?from=` / `?to=` for the scope, `?sub=` / `?view=` / `?page=` / per-tab
 * filters for tab state. Switching tab drops everything tab-local (a page
 * index, or a `status` value one table has never heard of, means nothing on
 * another) but KEEPS the scope — the broker is still looking at the same
 * account.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { PageHeader } from "@/components/common/PageHeader";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Swap } from "@/components/common/motion";
import { ScopeFilter, scopeParams, useScope } from "@/components/common/ScopeFilter";
import { ExportMenu } from "@/components/common/ExportMenu";
import { usePermissions, can, type PermissionModule } from "@/lib/permissions";
import { useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import type { ExportEntity } from "@/lib/exports";

import InsuredsTab, { INSUREDS_EXPORT, useInsuredsSub } from "./insureds";
import QuotesTab from "./quotes";
import ProposalsTab from "./proposals";
import PoliciesTab from "./policies";
import { ClaimsTab, CollectionsTab, EndorsementsTab } from "./postsale";
import DocumentsTab from "./documents";
import LeadsPage from "@/pages/leads";

type TabId =
  | "insureds"
  | "pipeline"
  | "quotes"
  | "proposals"
  | "policies"
  | "endorsements"
  | "collections"
  | "claims"
  | "documents";

/** Tab → the module(s) whose `View` grant makes it exist. Asegurados renders
 *  when EITHER of its two modules is granted (the toggle hides the other). */
const TAB_GATES: Record<TabId, PermissionModule[]> = {
  insureds: ["CaseFiles", "Clients"],
  pipeline: ["Leads"],
  quotes: ["CaseFiles"],
  proposals: ["Proposals"],
  policies: ["Policies"],
  endorsements: ["Endorsements"],
  collections: ["Collections"],
  claims: ["Claims"],
  documents: ["Documents"],
};

/**
 * Tab → the export entity. Every tab maps to exactly one, except Asegurados,
 * whose entity follows its `?sub=` (see {@link INSUREDS_EXPORT}) — so there is
 * no longer an "export can't decide what to export" case.
 */
const TAB_EXPORT: Record<Exclude<TabId, "insureds">, ExportEntity> = {
  pipeline: "leads",
  quotes: "case_files",
  proposals: "proposals",
  policies: "policies",
  endorsements: "endorsements",
  collections: "collections",
  claims: "claims",
  documents: "documents",
};

/**
 * URL keys owned by an individual tab, cleared whenever the tab changes.
 * The scope keys (`group`, `account`, `from`, `to`) are deliberately absent.
 */
const TAB_LOCAL_PARAMS = [
  "page",
  "view",
  "sub",
  "stage",
  "status",
  "line",
  "insurer",
  "category",
  "entity",
  "client_id",
  "asset_id",
] as const;

const TAB_ORDER: TabId[] = [
  "insureds",
  "pipeline",
  "quotes",
  "proposals",
  "policies",
  "endorsements",
  "collections",
  "claims",
  "documents",
];

export default function DataPage() {
  const { t } = useTranslation("analytics");
  const perms = usePermissions();
  const [tabParam] = useUrlParam("tab");
  const setParams = useSetUrlParams();
  const scope = useScope();
  // The Asegurados export follows its sub-view; the grant logic and the
  // `?sub=` fallback live in the tab itself so both read the same answer.
  const insureds = useInsuredsSub();

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

  /**
   * Tab switch keeps the SCOPE and drops everything tab-local.
   *
   * The per-tab filters share a URL namespace, and several share a NAME —
   * `status` means one thing to Endosos and another to Siniestros. Carrying
   * `status=open` from one into the other silently filters a table by a value
   * it has never heard of, which reads as "no results" rather than as the leak
   * it is. So the tab-local keys are cleared on every switch; only the scope
   * (`group`/`account`/`from`/`to`) survives, because the broker is still
   * looking at the same account.
   */
  const setTab = (tab: string) =>
    setParams({
      tab,
      ...Object.fromEntries(TAB_LOCAL_PARAMS.map((key) => [key, null])),
    });

  const exportEntity: ExportEntity | null =
    activeTab === null
      ? null
      : activeTab === "insureds"
        ? INSUREDS_EXPORT[insureds.sub]
        : TAB_EXPORT[activeTab];

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={t("data.eyebrow")}
        title={t("data.title")}
        subtitle={t("data.subtitle")}
      />

      <ScopeFilter
        trailing={
          // No granted tab means no table on screen, so there is nothing to
          // export — an export button here would be a dead button.
          exportEntity ? (
            <ExportMenu
              entity={exportEntity}
              filters={scopeParams(scope)}
              filenameStem={activeTab ?? undefined}
            />
          ) : undefined
        }
      />

      {perms.isLoading ? (
        <Skeleton className="h-64 w-full rounded-card" />
      ) : activeTab ? (
        <div className="flex flex-col gap-4">
          <Tabs value={activeTab} onValueChange={setTab}>
            <TabsList variant="underline" className="flex-wrap">
              {visibleTabs.map((tab) => (
                <TabsTrigger key={tab} value={tab}>
                  {t(`tabs.${tab}`, { defaultValue: tab })}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          {/* The panels are rendered outside <TabsContent> (each tab owns its
              own URL state), so the swap is animated explicitly here. */}
          <Swap swapKey={activeTab}>
            {activeTab === "insureds" ? (
              <InsuredsTab />
            ) : activeTab === "pipeline" ? (
              <LeadsPage embedded />
            ) : activeTab === "quotes" ? (
              <QuotesTab />
            ) : activeTab === "proposals" ? (
              <ProposalsTab />
            ) : activeTab === "policies" ? (
              <PoliciesTab />
            ) : activeTab === "endorsements" ? (
              <EndorsementsTab />
            ) : activeTab === "collections" ? (
              <CollectionsTab />
            ) : activeTab === "claims" ? (
              <ClaimsTab />
            ) : (
              <DocumentsTab />
            )}
          </Swap>
        </div>
      ) : (
        // A role with no list module at all (matrix says no to everything):
        // say so instead of a blank pane — nothing is hidden without a reason.
        <p className="text-body text-ink-3">{t("noModules")}</p>
      )}
    </div>
  );
}
