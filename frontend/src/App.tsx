import { Routes, Route, Navigate } from "react-router-dom";
import { AppShell } from "@/components/layout/AppShell";
import { ProtectedRoute } from "@/components/layout/ProtectedRoute";

import Login from "@/pages/auth/Login";
import DashboardPage from "@/pages/dashboard";
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

/**
 * v2 router.
 *
 * A route exists here only once a real endpoint is behind the page it renders.
 * Modules still to land are NOT routed and are shown greyed with a "pronto"
 * chip in the sidebar, rather than as links that resolve to an empty screen.
 */
export default function App() {
  return (
    <Routes>
      {/* Public */}
      <Route path="/login" element={<Login />} />

      {/* Protected app shell */}
      <Route element={<ProtectedRoute />}>
        <Route element={<AppShell />}>
          <Route index element={<DashboardPage />} />
          <Route path="clients" element={<ClientsPage />} />
          <Route path="clients/:id" element={<ClientDetailPage />} />
          <Route path="placements" element={<PlacementsPage />} />
          <Route path="placements/:id" element={<PlacementDetailPage />} />
          <Route path="inspections" element={<InspectionsPage />} />
          <Route path="inspections/:id" element={<InspectionDetailPage />} />
          <Route path="settings" element={<SettingsPage />} />

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
        </Route>
      </Route>

      {/* Fallback */}
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
