/**
 * Cotizar en portales — the portal-search RPA (`/portal-searches/*`, v12 06-rpa).
 *
 * Three tabs, all state in the URL so every view is a link (the bell's
 * `portal_search_*` notifications point at `?run=ID`, Configuración →
 * Integraciones points at `?tab=credentials`):
 *
 *  - **Cotizar** (`?tab=search`, default): one card per portal from the server
 *    (HDI available / in maintenance; the rest "pronto" with the server's
 *    reason), and the search form of the selected portal (`?portal=`). A
 *    `?case=ID` links the search to a colocación.
 *  - **Búsquedas** (`?tab=runs`): the runs list, polling while any is in flight.
 *  - **Credenciales** (`?tab=credentials`): write-only logins (Settings.Manage).
 *
 * `?run=ID` opens the run's detail sheet over any tab: the steps timeline, the
 * plans found, and on failure the explanation by `error_kind` with
 * Reintentar · Subir cotización PDF · Otros portales.
 *
 * Permissions come from `GET /auth/permissions` only: the page needs
 * Integrations.View, searching needs Integrations.Submit, credentials need
 * Settings.Manage — each control renders disabled with its reason otherwise.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { FileUp, Lock } from "lucide-react";

import { usePortals, type PortalRun } from "@/api/rpa";
import { PageHeader } from "@/components/common/PageHeader";
import { EmptyState, ErrorBanner, LoadingRows } from "@/components/common/kit";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { can, usePermissions } from "@/lib/permissions";
import { useNumericUrlParam, useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import { CredentialsPanel } from "@/pages/portal-search/CredentialsPanel";
import { PortalCards, portalUsable } from "@/pages/portal-search/PortalCards";
import { RunDetailSheet, uploadQuotePath } from "@/pages/portal-search/RunDetail";
import { RunsList } from "@/pages/portal-search/RunsList";
import { SearchForm, type SearchPrefill } from "@/pages/portal-search/SearchForm";

type TabId = "search" | "runs" | "credentials";
const TABS: readonly TabId[] = ["search", "runs", "credentials"];
const TAB_LABEL_KEY: Record<TabId, string> = {
  search: "tabs.search",
  runs: "tabs.runs",
  credentials: "tabs.credentials",
};

function asTab(value: string | null): TabId {
  return (TABS as readonly string[]).includes(value ?? "") ? (value as TabId) : "search";
}

export default function PortalSearchPage() {
  const { t } = useTranslation("rpa");
  const { t: tc } = useTranslation("common");

  // --- Permissions (server matrix) -----------------------------------------------
  const permissions = usePermissions();
  const perms = permissions.data;
  const permsLoading = permissions.isLoading;
  const canView = can(perms, "Integrations", "View");
  const canSubmit = can(perms, "Integrations", "Submit");
  const canManageCredentials = can(perms, "Settings", "Manage");

  // --- URL state --------------------------------------------------------------------
  const [rawTab] = useUrlParam("tab");
  const tab = asTab(rawTab);
  const [portalParam] = useUrlParam("portal");
  const runId = useNumericUrlParam("run") ?? null;
  const caseFileId = useNumericUrlParam("case") ?? null;
  const setParams = useSetUrlParams();

  /** "Otros portales" carries the failed run's data into a new search. */
  const [prefill, setPrefill] = React.useState<SearchPrefill | null>(null);

  const portals = usePortals(canView);
  const list = portals.data ?? [];
  const usable = list.filter(portalUsable);
  const selected =
    usable.find((p) => p.key === portalParam) ?? (portalParam ? null : usable[0] ?? null);

  const goTab = (next: TabId) => setParams({ tab: next === "search" ? null : next });
  const openRun = (id: number) => setParams({ run: String(id) });
  const closeRun = () => setParams({ run: null });
  const configure = () => setParams({ tab: "credentials", run: null });
  const selectPortal = (key: string) => {
    setPrefill(null);
    setParams({ portal: key });
  };
  const onQueued = (run: PortalRun) => {
    setPrefill(null);
    setParams({ tab: "runs", run: String(run.id) });
  };
  const onOtherPortal = (portalKey: string, run: PortalRun) => {
    setPrefill({ runId: run.id, input: run.input ?? {}, subtypeId: run.insurance_subtype_id });
    setParams({
      tab: null,
      run: null,
      portal: portalKey,
      case: run.case_file_id ? String(run.case_file_id) : null,
    });
  };

  if (!permsLoading && !canView) {
    return (
      <div className="flex flex-col gap-5">
        <PageHeader title={t("title")} subtitle={t("description")} />
        <Card className="p-2">
          <EmptyState icon={<Lock />} title={tc("state.noAccess")} hint={tc("state.noAccessHint")} />
        </Card>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <PageHeader title={t("title")} subtitle={t("description")} />

      <Tabs value={tab} onValueChange={(value) => goTab(asTab(value))}>
        <div className="-mx-1 overflow-x-auto px-1">
          <TabsList variant="underline">
            {TABS.map((id) => (
              <TabsTrigger key={id} value={id} className="min-h-10 whitespace-nowrap">
                {t(TAB_LABEL_KEY[id])}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
      </Tabs>

      {permsLoading || portals.isLoading ? (
        <LoadingRows rows={4} />
      ) : portals.isError ? (
        <ErrorBanner error={portals.error ?? t("loadError")} />
      ) : tab === "runs" ? (
        <RunsList onOpen={openRun} onNewSearch={() => goTab("search")} />
      ) : tab === "credentials" ? (
        <CredentialsPanel
          portals={list}
          canManage={canManageCredentials}
          permsLoading={permsLoading}
        />
      ) : list.length === 0 ? (
        <Card className="p-2">
          <EmptyState title={t("portal.empty")} />
        </Card>
      ) : (
        <div className="flex flex-col gap-4">
          <PortalCards
            portals={list}
            selectedKey={selected?.key ?? null}
            onSelect={selectPortal}
            onConfigure={configure}
          />

          {selected ? (
            <SearchForm
              key={`${selected.key}-${prefill?.runId ?? "new"}`}
              portal={selected}
              canSubmit={canSubmit}
              permsLoading={permsLoading}
              canManageCredentials={canManageCredentials}
              caseFileId={caseFileId}
              onUnlinkCase={() => setParams({ case: null })}
              prefill={prefill}
              onQueued={onQueued}
              onConfigure={configure}
            />
          ) : (
            <Card className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
              <p className="text-caption text-ink-2 [text-wrap:pretty]">
                {usable.length === 0 ? t("form.noneAvailable") : t("form.pickPortal")}
              </p>
              {usable.length === 0 ? (
                <Button asChild size="sm" variant="secondary" className="min-h-10 shrink-0 sm:min-h-8">
                  <Link to={uploadQuotePath(caseFileId)}>
                    <FileUp className="h-3.5 w-3.5" strokeWidth={1.5} aria-hidden />
                    {t("detail.uploadPdf")}
                  </Link>
                </Button>
              ) : null}
            </Card>
          )}
        </div>
      )}

      <RunDetailSheet
        runId={canView ? runId : null}
        onClose={closeRun}
        onOpenRun={openRun}
        onOtherPortal={onOtherPortal}
        onConfigure={configure}
        canSubmit={canSubmit}
        canManageCredentials={canManageCredentials}
      />
    </div>
  );
}
