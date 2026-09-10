/**
 * Broker propuesta (v8) — the OUTBOUND artifact.
 *
 * TERMINOLOGY TRAP: this is NOT a `Proposal` (the insurer's inbound offer). The
 * broker mints a `BrokerProposal` SOLELY from an ALIGNED comparison plus a
 * promoted winning column, ratifies it, and sends it. Minting snapshots the
 * aligned terms into `payload` with a stable `content_hash`, sets status=draft,
 * and best-effort advances the account stage.
 *
 * The API surface: list-by-case, mint, read-by-id, ratify. A propuesta is
 * discovered through `GET /broker-proposals?case_file_id={id}` (newest-first),
 * so the account panel no longer needs to remember the id itself.
 *
 * RBAC: mint and ratify both require `Proposals.Approve`; list and read require
 * `Proposals.View`. Query keys/types come from `@/api/keys` and `@/api/types`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk, clean } from "@/api/keys";
import type {
  BrokerProposal,
  BrokerProposalCreate,
  BrokerProposalRatify,
} from "@/api/types";

/**
 * `GET /broker-proposals?case_file_id={id}` — the account's propuestas,
 * newest-first. The panel takes the newest; an empty array means "none yet".
 */
export function useBrokerProposalsByCase(
  caseId: number | undefined,
  enabled = true,
) {
  return useQuery({
    queryKey: qk.brokerProposals.list({ case_file_id: caseId ?? 0 }),
    enabled: !!caseId && enabled,
    queryFn: async () => {
      const { data } = await api.get<BrokerProposal[]>("/broker-proposals", {
        params: clean({ case_file_id: caseId }),
      });
      return data;
    },
  });
}

/** `GET /broker-proposals/{id}`. A foreign-tenant / missing row is a 404. */
export function useBrokerProposal(bpId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.brokerProposals.detail(bpId ?? 0),
    enabled: !!bpId && enabled,
    // A 404 is a legitimate "no propuesta yet" — don't hammer the endpoint.
    retry: false,
    queryFn: async () => {
      const { data } = await api.get<BrokerProposal>(`/broker-proposals/${bpId}`);
      return data;
    },
  });
}

/**
 * `POST /broker-proposals` — mint from an aligned comparison + a promoted winner.
 * Answers 422 (`comparison_not_aligned` / `winner_not_in_comparison`) when the
 * preconditions are not met; the caller surfaces the server message.
 */
export function useMintBrokerProposal() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: BrokerProposalCreate) => {
      const { data } = await api.post<BrokerProposal>("/broker-proposals", payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.brokerProposals.detail(data.id), data);
      // The panel discovers the propuesta through the list — refresh it.
      void qc.invalidateQueries({ queryKey: qk.brokerProposals.all });
      // The mint best-effort advances the account stage — refresh the tree/case.
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
    },
  });
}

/** `POST /broker-proposals/{id}/ratify` — freeze the hash, status=ratified. */
export function useRatifyBrokerProposal(bpId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: BrokerProposalRatify = {}) => {
      const { data } = await api.post<BrokerProposal>(
        `/broker-proposals/${bpId}/ratify`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.brokerProposals.detail(bpId), data);
      // The panel reads the propuesta off the list — refresh its status there.
      void qc.invalidateQueries({ queryKey: qk.brokerProposals.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
    },
  });
}
