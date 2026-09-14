/**
 * Portafolio — **Asegurados**: the broker's book, from either end.
 *
 * The old page split this in two tabs (Clientes and Cuentas) and the broker
 * filing issue #2 read them as one question asked twice: *who do I insure, and
 * what have I placed for them*. So they are one tab with two sub-views behind a
 * segmented toggle, persisted in `?sub=` exactly like the post-sale tab used
 * to:
 *  - `clients`  — the empresas (RUTs) themselves, gated on `Clients`;
 *  - `accounts` — the grupo-cuentas (ramo × vigencia), gated on `CaseFiles`.
 *
 * Each sub-view renders ONLY when its module grants `View`; the parent tab
 * exists when EITHER does, and with a single grant the toggle disappears
 * rather than offering a control that cannot move.
 *
 * The two sub-views export DIFFERENT entities (`clients` / `case_files`), so
 * {@link useInsuredsSub} is exported for the page header to read: the export
 * button must follow the sub-view, or the file and the table on screen would
 * disagree about what was filtered.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { can, usePermissions } from "@/lib/permissions";
import type { ExportEntity } from "@/lib/exports";
import ClientsPage from "@/pages/clients";

import AccountsTab from "./accounts";
import { useSetUrlParams, useUrlParam } from "./shared";

export type InsuredsSub = "clients" | "accounts";

/** Sub-view → the `POST /exports/{entity}` entity it lists. */
export const INSUREDS_EXPORT: Record<InsuredsSub, ExportEntity> = {
  clients: "clients",
  accounts: "case_files",
};

/**
 * The granted sub-views and the active one.
 *
 * Shared with the page header (it owns the export button) so the grant logic
 * and the `?sub=` fallback are written once — a user with only `CaseFiles`
 * lands on `accounts` in both places, never on a `clients` export of a table
 * they are not being shown.
 */
export function useInsuredsSub(): { granted: InsuredsSub[]; sub: InsuredsSub } {
  const perms = usePermissions();
  const [subParam] = useUrlParam("sub");

  const granted = React.useMemo<InsuredsSub[]>(() => {
    const out: InsuredsSub[] = [];
    if (can(perms.data, "Clients", "View")) out.push("clients");
    if (can(perms.data, "CaseFiles", "View")) out.push("accounts");
    return out;
  }, [perms.data]);

  const sub: InsuredsSub =
    subParam && granted.includes(subParam as InsuredsSub)
      ? (subParam as InsuredsSub)
      : (granted[0] ?? "clients");

  return { granted, sub };
}

export default function InsuredsTab() {
  const { t } = useTranslation("analytics");
  const perms = usePermissions();
  const { granted, sub } = useInsuredsSub();
  const setParams = useSetUrlParams();

  // One batched update: the two sub-views share a URL namespace but not a
  // vocabulary (`status` is a client status on one side and a case-file status
  // on the other), and a page index means nothing across two tables.
  const switchSub = (next: string) =>
    setParams({ sub: next, status: null, page: null, view: null, stage: null, line: null });

  if (perms.isLoading) return null;
  if (granted.length === 0) return null; // the parent tab is already hidden

  return (
    <div className="flex flex-col gap-3">
      {granted.length > 1 ? (
        <Tabs value={sub} onValueChange={switchSub}>
          <TabsList>
            {granted.map((view) => (
              <TabsTrigger key={view} value={view}>
                {t(`insureds.${view}`)}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      ) : null}

      {sub === "clients" ? <ClientsPage embedded /> : <AccountsTab />}
    </div>
  );
}
