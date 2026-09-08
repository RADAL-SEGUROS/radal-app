import * as React from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { AppShell } from "@/components/layout/AppShell";
import { StandardPage } from "@/components/layout/StandardPage";

const StyleLabPage = React.lazy(() => import("@/pages/style-lab"));
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";
import { UnderConstruction } from "@/components/common/UnderConstruction";
import { Skeleton } from "@/components/ui/skeleton";

import Login from "@/pages/auth/Login";
import DashboardPage from "@/pages/dashboard";
import AnalyticsPage from "@/pages/analytics";
import ClientsPage from "@/pages/clients";
import ClientDetailPage from "@/pages/clients/detail";
import PlacementsPage from "@/pages/placements";
import PlacementDetailPage from "@/pages/placements/detail";
import InspectionsPage from "@/pages/inspections";
import InspectionDetailPage from "@/pages/inspections/detail";
import SettingsPage from "@/pages/settings";
import QuotesPage from "@/pages/quotes";
import QuoteDetailPage from "@/pages/quotes/detail";
import ProposalComparisonPage from "@/pages/proposals/compare";
import ProposalsPage from "@/pages/proposals";
import ProposalUploadPage from "@/pages/proposals/upload";
import ProposalDetailPage from "@/pages/proposals/detail";
import InsurersPage from "@/pages/insurers";
import InsurerDetailPage from "@/pages/insurers/detail";
import OfferingsPage from "@/pages/offerings";
import OfferingDetailPage from "@/pages/offerings/detail";

// Case files, leads and the agent — the v2 expediente pass.
import CasesPage from "@/pages/cases";
import CaseDetailPage from "@/pages/cases/detail";
import CasePacksPage from "@/pages/cases/packs";
import LeadsPage from "@/pages/leads";
import LeadDetailPage from "@/pages/leads/detail";
import AgentPage from "@/pages/agent";

/**
 * Deferred pages live in another pass's own directories, but the ROUTES belong
 * here so the app has exactly one router and two passes never edit the same
 * file.
 *
 * They are resolved through `import.meta.glob` rather than a static `import`
 * on purpose: the route contract (paths, params) is published by this file
 * BEFORE the pages exist, and a static import of a not-yet-written module would
 * break the build for everyone. A missing page renders the "en construcción"
 * placeholder — never a blank screen, never a route that silently 404s.
 *
 * This is the mechanism the post-sale pass introduced; the v3 groups pass
 * (D3/D5/D6) reuses it verbatim.
 */
const deferredPages = import.meta.glob([
  // post-sale (v2)
  "./pages/policies/index.tsx",
  "./pages/policies/detail.tsx",
  "./pages/endorsements/detail.tsx",
  "./pages/collections/detail.tsx",
  "./pages/claims/index.tsx",
  "./pages/claims/detail.tsx",
  // groups & accounts (v3, spec §5.4)
  "./components/groups/GroupShell.tsx",
  "./pages/groups/index.tsx",
  "./pages/groups/new.tsx",
  "./pages/groups/overview.tsx",
  "./pages/groups/period.tsx",
  "./pages/groups/account-new.tsx",
  "./pages/groups/account.tsx",
  "./pages/groups/renew.tsx",
  "./pages/groups/reperiod.tsx",
  "./pages/groups/policy.tsx",
  "./pages/groups/extend.tsx",
  // AI assistants (v3, spec §5.2 / §5.4)
  "./pages/ai/reader.tsx",
  "./pages/ai/comparator.tsx",
]);

type PageModule = { default: React.ComponentType };

function DeferredPlaceholder({
  titleKey,
  fallbackTitle,
}: {
  titleKey: string;
  fallbackTitle: string;
}) {
  const { t } = useTranslation("common");
  return (
    <UnderConstruction
      title={t(titleKey, { defaultValue: fallbackTitle })}
      subtitle={t("nav.comingSoon")}
    />
  );
}

/**
 * Resolve one deferred module into a route element.
 *
 * `fallbackTitle` is only ever read while the owning pass has not landed its
 * i18n keys yet, so the placeholder shows real Spanish instead of a raw key
 * (the dynamic-key trap — `tsc` cannot see these).
 */
function deferredRoute(
  path: string,
  titleKey: string,
  fallbackTitle: string,
): React.ReactElement {
  const loader = deferredPages[path] as (() => Promise<PageModule>) | undefined;
  if (!loader) {
    return <DeferredPlaceholder titleKey={titleKey} fallbackTitle={fallbackTitle} />;
  }
  const Lazy = React.lazy(loader);
  return (
    <React.Suspense fallback={<Skeleton className="h-64 w-full" />}>
      <Lazy />
    </React.Suspense>
  );
}

/**
 * v3 router.
 *
 * Two layout levels inside `ProtectedRoute`:
 *   1. `AppShell`     — main sidebar + topbar, no width constraint;
 *   2. `StandardPage` — the `max-w-[1380px]` reading column plus the
 *      `key={pathname}` motion remount, applied to every page EXCEPT the group
 *      family, which renders its own contextual rail through `GroupShell` and
 *      must not remount on navigation.
 *
 * The app STARTS AT GROUPS: `/` redirects to `/groups` and the dashboard lives
 * at `/dashboard`. A route exists here only once a real endpoint is behind the
 * page it renders; modules still to land are shown greyed with a "pronto" chip
 * in the sidebar rather than as links that resolve to an empty screen.
 */
export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login" element={<Login />} />
      {/* Style lab: the three candidate visual directions for the restart.
          Public and outside the shell on purpose — it must render with ZERO
          inheritance from the deprecated theme, and the team opens it without
          logging in. */}
      <Route
        path="/style-lab"
        element={
          <React.Suspense fallback={null}>
            <StyleLabPage />
          </React.Suspense>
        }
      />

      {/* Protected app shell */}
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          {/* ---- Standard pages: reading column + motion remount ---- */}
          <Route element={<StandardPage />}>
            {/* The front door is the group list, not the dashboard. */}
            <Route index element={<Navigate to="/groups" replace />} />
            <Route path="dashboard" element={<DashboardPage />} />
            {/* The portfolio view: dashboard KPIs + the consolidated tables.
                Without this route the sidebar link fell through to the
                catch-all and bounced to /groups. */}
            <Route path="analytics" element={<AnalyticsPage />} />

            <Route path="clients" element={<ClientsPage />} />
            <Route path="clients/:id" element={<ClientDetailPage />} />
            <Route path="placements" element={<PlacementsPage />} />
            <Route path="placements/:id" element={<PlacementDetailPage />} />
            <Route path="inspections" element={<InspectionsPage />} />
            <Route path="inspections/:id" element={<InspectionDetailPage />} />
            <Route path="settings" element={<SettingsPage />} />

            {/* Groups: the list and the create form are ordinary pages; the
                group family itself lives outside StandardPage, below. */}
            <Route
              path="groups"
              element={deferredRoute("./pages/groups/index.tsx", "nav.groups", "Grupos")}
            />
            <Route
              path="groups/new"
              element={deferredRoute("./pages/groups/new.tsx", "nav.groups", "Grupos")}
            />

            {/* Commercial front door: lead -> expediente */}
            <Route path="leads" element={<LeadsPage />} />
            <Route path="leads/:leadId" element={<LeadDetailPage />} />
            <Route path="cases" element={<CasesPage />} />
            <Route path="cases/:caseId" element={<CaseDetailPage />} />
            <Route path="cases/:caseId/packs" element={<CasePacksPage />} />

            {/* Deal flow: quote request -> proposals -> comparison -> offering */}
            <Route path="quotes" element={<QuotesPage />} />
            <Route path="quotes/:quoteId" element={<QuoteDetailPage />} />
            <Route path="quotes/:quoteId/comparison" element={<ProposalComparisonPage />} />
            <Route path="proposals" element={<ProposalsPage />} />
            <Route path="proposals/upload" element={<ProposalUploadPage />} />
            <Route path="proposals/:proposalId" element={<ProposalDetailPage />} />
            <Route path="insurers" element={<InsurersPage />} />
            <Route path="insurers/:insurerId" element={<InsurerDetailPage />} />
            <Route path="offerings" element={<OfferingsPage />} />
            <Route path="offerings/:offeringId" element={<OfferingDetailPage />} />

            {/* Post-sale. Endorsements and collections have no top-level entry:
                they are always reached from the policy they amend or bill. */}
            <Route
              path="policies"
              element={deferredRoute("./pages/policies/index.tsx", "nav.policies", "Pólizas")}
            />
            <Route
              path="policies/:policyId"
              element={deferredRoute("./pages/policies/detail.tsx", "nav.policies", "Pólizas")}
            />
            <Route
              path="endorsements/:endorsementId"
              element={deferredRoute(
                "./pages/endorsements/detail.tsx",
                "nav.endorsements",
                "Endosos",
              )}
            />
            <Route
              path="collections/:planId"
              element={deferredRoute(
                "./pages/collections/detail.tsx",
                "nav.collections",
                "Cobranzas",
              )}
            />
            <Route
              path="claims"
              element={deferredRoute("./pages/claims/index.tsx", "nav.claims", "Siniestros")}
            />
            <Route
              path="claims/:claimId"
              element={deferredRoute("./pages/claims/detail.tsx", "nav.claims", "Siniestros")}
            />

            {/* AI assistants — real pages, not the chat agent. */}
            <Route
              path="ai/reader"
              element={deferredRoute("./pages/ai/reader.tsx", "nav.aiReader", "Lector documental")}
            />
            <Route
              path="ai/comparator"
              element={deferredRoute(
                "./pages/ai/comparator.tsx",
                "nav.aiComparator",
                "Comparador",
              )}
            />

            {/* The AI agent, with SSE streaming and a non-streaming fallback. */}
            <Route path="agent" element={<AgentPage />} />
          </Route>

          {/* ---- The group family: GroupShell owns the contextual rail and
                  the scroll pane, so it is deliberately NOT wrapped in
                  StandardPage — the tree must never remount (spec §5.1). ---- */}
          <Route
            path="groups/:groupId"
            element={deferredRoute(
              "./components/groups/GroupShell.tsx",
              "nav.groups",
              "Grupos",
            )}
          >
            <Route
              index
              element={deferredRoute("./pages/groups/overview.tsx", "nav.groups", "Grupos")}
            />
            <Route
              path="periods/:periodLabel"
              element={deferredRoute("./pages/groups/period.tsx", "nav.groups", "Grupos")}
            />
            <Route
              path="accounts/new"
              element={deferredRoute("./pages/groups/account-new.tsx", "nav.groups", "Grupos")}
            />
            <Route
              path="accounts/:caseId"
              element={deferredRoute("./pages/groups/account.tsx", "nav.groups", "Grupos")}
            />
            <Route
              path="accounts/:caseId/renew"
              element={deferredRoute("./pages/groups/renew.tsx", "nav.groups", "Grupos")}
            />
            <Route
              path="accounts/:caseId/reperiod"
              element={deferredRoute("./pages/groups/reperiod.tsx", "nav.groups", "Grupos")}
            />
            {/* static before dynamic: /policies/extend must not match :policyId */}
            <Route
              path="policies/extend"
              element={deferredRoute("./pages/groups/extend.tsx", "nav.groups", "Grupos")}
            />
            <Route
              path="policies/:policyId"
              element={deferredRoute("./pages/groups/policy.tsx", "nav.groups", "Grupos")}
            />
          </Route>
        </Route>
      </Route>

      {/* Fallback — the app starts at groups, never at /leads. */}
      <Route path="*" element={<Navigate to="/groups" replace />} />
    </Routes>
  );
}
