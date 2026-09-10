/**
 * `/data` — **Datos**: every consolidated table in the tenant, in one place.
 *
 * This is one half of the old `/analytics`, which fused a KPI dashboard and six
 * data tables into a single scroll. They are now two destinations with one job
 * each: **Datos** answers "show me the rows", **Analítica** answers "show me
 * the shape". Splitting them is also what let the MÁS nav section go away —
 * every table it used to hold is a tab here, and the standalone routes stay
 * alive for deep links from inside a group.
 *
 * Three things every tab inherits from this page:
 *  1. the **scope** (grupo · grupo-cuenta · fechas), applied server-side and
 *     held in the URL, so a scoped table is a shareable link;
 *  2. an **export** (XLSX / PDF) of the whole filtered result, not the page;
 *  3. the tab switch animates — see `components/ui/tabs.tsx`.
 *
 * URL discipline: `?tab=` for the active tab, `?group=` / `?account=` /
 * `?from=` / `?to=` for the scope, `?view=` / `?page=` / per-tab filters for
 * tab state. Switching tab drops `?page=` (a page index means nothing across
 * two different tables) but KEEPS the scope — the broker is still looking at
 * the same account.
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

import AccountsTab from "./accounts";
import QuotesTab from "./quotes";
import ProposalsTab from "./proposals";
import PoliciesTab from "./policies";
import PostsaleTab from "./postsale";
import DocumentsTab from "./documents";
import ClientsPage from "@/pages/clients";
import PlacementsPage from "@/pages/placements";
import InspectionsPage from "@/pages/inspections";
import OfferingsPage from "@/pages/offerings";
import LeadsPage from "@/pages/leads";

type TabId =
  | "accounts"
  | "clients"
  | "quotes"
  | "proposals"
  | "policies"
  | "postsale"
  | "placements"
  | "inspections"
  | "offerings"
  | "leads"
  | "documents";

/** Tab → the module(s) whose `View` grant makes it exist. Post-venta renders
 *  when ANY of its three modules is granted. */
const TAB_GATES: Record<TabId, PermissionModule[]> = {
  accounts: ["CaseFiles"],
  clients: ["Clients"],
  quotes: ["Quotes"],
  proposals: ["Proposals"],
  policies: ["Policies"],
  postsale: ["Endorsements", "Collections", "Claims"],
  placements: ["Placements"],
  inspections: ["Inspections"],
  offerings: ["Offerings"],
  leads: ["Leads"],
  documents: ["Documents"],
};

/** Tab → the export entity. Post-venta spans three, so it has no single one. */
const TAB_EXPORT: Partial<Record<TabId, ExportEntity>> = {
  accounts: "case_files",
  clients: "clients",
  quotes: "quotes",
  proposals: "proposals",
  policies: "policies",
  placements: "placements",
  inspections: "inspections",
  offerings: "offerings",
  leads: "leads",
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
  "accounts",
  "clients",
  "quotes",
  "proposals",
  "policies",
  "postsale",
  "placements",
  "inspections",
  "offerings",
  "leads",
  "documents",
];

export default function DataPage() {
  const { t } = useTranslation("analytics");
  const perms = usePermissions();
  const [tabParam] = useUrlParam("tab");
  const setParams = useSetUrlParams();
  const scope = useScope();

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
   * `status` means one thing to Cuentas and another to Colocaciones. Carrying
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

  const exportEntity = activeTab ? TAB_EXPORT[activeTab] : undefined;

  return (
    <div className="flex flex-col gap-5">
      <PageHeader
        eyebrow={t("data.eyebrow")}
        title={t("data.title")}
        subtitle={t("data.subtitle")}
      />

      <ScopeFilter
        trailing={
          exportEntity ? (
            <ExportMenu
              entity={exportEntity}
              filters={scopeParams(scope)}
              filenameStem={activeTab ?? undefined}
            />
          ) : (
            // Post-venta is three entities in one tab; there is no single
            // export for it. Say why rather than showing a button that can't
            // decide what to export.
            <ExportMenu
              entity="case_files"
              filters={scopeParams(scope)}
              disabledHint={t("export.pickOneEntity")}
            />
          )
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
            {activeTab === "accounts" ? (
              <AccountsTab />
            ) : activeTab === "clients" ? (
              <ClientsPage embedded />
            ) : activeTab === "quotes" ? (
              <QuotesTab />
            ) : activeTab === "proposals" ? (
              <ProposalsTab />
            ) : activeTab === "policies" ? (
              <PoliciesTab />
            ) : activeTab === "placements" ? (
              <PlacementsPage embedded />
            ) : activeTab === "inspections" ? (
              <InspectionsPage embedded />
            ) : activeTab === "offerings" ? (
              <OfferingsPage embedded />
            ) : activeTab === "leads" ? (
              <LeadsPage embedded />
            ) : activeTab === "documents" ? (
              <DocumentsTab />
            ) : (
              <PostsaleTab />
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
