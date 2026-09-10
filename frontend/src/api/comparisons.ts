/**
 * Comparison expedient (v8) — the account's COMPARISON-stage worktable.
 *
 * Offers arrive ONE AT A TIME: the broker uploads a cotización PDF (through the
 * normal documents flow), then `POST /comparisons/{id}/entries` reads it
 * dynamically and adds a column. `align` re-runs the alignment over a monotonic
 * canonical dictionary; `promote` mints a real inbound `proposal` from a column
 * so accept / offering keep working (suggest → confirm → commit).
 *
 * RBAC reuses the `Proposals` module (View / Create / Edit; promote =
 * `Proposals.Approve`).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type {
  Comparison,
  ComparisonAlignmentResult,
  ComparisonCreate,
  ComparisonEntryCreate,
  ComparisonEntryResult,
  ComparisonEntryUpdate,
  ComparisonPromotePayload,
  ComparisonPromoteResult,
} from "@/api/types";

/**
 * True when a request failed because the AI provider is DOWN (502/503/504).
 *
 * The align endpoint BLOCKS on a provider outage — it returns a clean gateway
 * error (never a half-baked matrix) tagged with the `X-Radal-AI-Error` header —
 * so the UI shows a retryable "provider unavailable" state instead of an empty
 * grid, and does NOT mark the comparison aligned.
 */
export function isProviderDown(error: unknown): boolean {
  const status = (
    error as { response?: { status?: number } } | undefined
  )?.response?.status;
  return status === 502 || status === 503 || status === 504;
}

/** GET /comparisons/{id} — header + columns + aligned matrix + dictionary. */
export function useComparison(comparisonId: number | undefined) {
  return useQuery({
    queryKey: qk.comparisons.detail(comparisonId ?? 0),
    enabled: !!comparisonId,
    queryFn: async () => {
      const { data } = await api.get<Comparison>(`/comparisons/${comparisonId}`);
      return data;
    },
  });
}

/** POST /comparisons — create-or-get the account's live comparison. */
export function useCreateComparison() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ComparisonCreate) => {
      const { data } = await api.post<Comparison>("/comparisons", payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.comparisons.detail(data.id), data);
    },
  });
}

/**
 * POST /comparisons/{id}/entries — add one already-uploaded cotización as a
 * column. The document must be created through the documents flow first; this
 * only passes its id. A `not_a_proposal` result is a VISIBLE rejection on the
 * returned entry, never an error.
 */
export function useAddEntry(comparisonId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ComparisonEntryCreate) => {
      const { data } = await api.post<ComparisonEntryResult>(
        `/comparisons/${comparisonId}/entries`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.comparisons.detail(comparisonId) });
    },
  });
}

/** PATCH /comparisons/{id}/entries/{entryId} — recommend / reorder / override. */
export function usePatchEntry(comparisonId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      entryId,
      ...payload
    }: ComparisonEntryUpdate & { entryId: number }) => {
      const { data } = await api.patch(
        `/comparisons/${comparisonId}/entries/${entryId}`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.comparisons.detail(comparisonId) });
    },
  });
}

/** DELETE /comparisons/{id}/entries/{entryId} — remove a column. */
export function useDeleteEntry(comparisonId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (entryId: number) => {
      await api.delete(`/comparisons/${comparisonId}/entries/${entryId}`);
      return entryId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.comparisons.detail(comparisonId) });
    },
  });
}

/** POST /comparisons/{id}/align — re-run the alignment (a paid AI call). */
export function useAlign(comparisonId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await api.post<ComparisonAlignmentResult>(
        `/comparisons/${comparisonId}/align`,
        {},
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.comparisons.detail(comparisonId), data.comparison);
    },
  });
}

/**
 * POST /comparisons/{id}/entries/{entryId}/promote — mint a real proposal.
 *
 * The reviewer may supply the insurer's identity (`insurer_rut` /
 * `insurer_cmf_code`) because a cotización PDF carries only the insured's RUT,
 * leaving the extractor unable to resolve the insurer. A valid supplied value
 * wins over the parsed one; otherwise the server 422s asking for it.
 */
export function usePromoteEntry(comparisonId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      entryId,
      ...payload
    }: ComparisonPromotePayload & { entryId: number }) => {
      const { data } = await api.post<ComparisonPromoteResult>(
        `/comparisons/${comparisonId}/entries/${entryId}/promote`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.comparisons.detail(comparisonId) });
      void qc.invalidateQueries({ queryKey: qk.proposals.all });
    },
  });
}
