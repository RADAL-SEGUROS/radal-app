/**
 * Pending actions — the typed list of actionable gaps that feed the overview
 * Journey and the account SUMMARY (`GET /case-files/{id}/pending-actions`,
 * gated by `CaseFiles.View`).
 *
 * Every item is `{code, severity, tab, reason, count}` with an English `code`
 * token and the SERVER's own human `reason` sentence — the UI never invents a
 * guard reason, it surfaces this one verbatim (rule 1 / rule 3). The list is
 * derived read-only; it is invalidated by `useTransitionCaseFile` because a
 * stage move can close or open a gap.
 *
 * The `tab` value is the server's desk vocabulary (`record | comparison |
 * proposal | journey`); `pendingActionTab()` maps it onto the account page's
 * `?tab=` key so a row can deep-link to the desk that owns the fix.
 */
import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type { PendingActionsResponse } from "@/api/types";

export function usePendingActions(caseId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.caseFiles.pendingActions(caseId ?? 0),
    enabled: !!caseId && enabled,
    queryFn: async () => {
      const { data } = await api.get<PendingActionsResponse>(
        `/case-files/${caseId}/pending-actions`,
      );
      return data;
    },
  });
}

/**
 * Map a pending action's server `tab` onto the account page's `?tab=` key.
 * `journey` has no tab of its own — the Journey hero lives above the tabs — so
 * it resolves to the SUMMARY, where the whole picture (and the hero) is shown.
 */
export function pendingActionTab(tab: string): string {
  switch (tab) {
    case "record":
      return "antecedentes";
    case "proposal":
      return "propuesta";
    case "comparison":
      return "comparison";
    case "journey":
      return "summary";
    default:
      return tab;
  }
}
