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
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
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

/**
 * Reading / aligning / promoting moves the account's journey server-side
 * (review r2 A5/A6: Cotizaciones recibidas → Comparación, a colocación created
 * on demand) — refresh the views that show the stage.
 */
function invalidateJourney(qc: QueryClient) {
  void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
  void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
  void qc.invalidateQueries({ queryKey: qk.navigator.all });
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

/**
 * Re-read the comparison until `done(comparison)` holds — the recovery path
 * when an entry upload / align lost its answer (network drop, a 504 from the
 * 60 s CloudFront edge while the Lambda kept working for up to 120 s). Each
 * read lands in the detail cache, so the grid updates as it goes. Resolves to
 * the matching comparison, or `null` when the server never got there.
 */
export async function pollComparison(
  qc: QueryClient,
  comparisonId: number,
  done: (comparison: Comparison) => boolean,
  { attempts = 14, intervalMs = 5_000, signal }: {
    attempts?: number;
    intervalMs?: number;
    signal?: { cancelled: boolean };
  } = {},
): Promise<Comparison | null> {
  for (let i = 0; i < attempts; i += 1) {
    if (signal?.cancelled) return null;
    try {
      const { data } = await api.get<Comparison>(`/comparisons/${comparisonId}`);
      qc.setQueryData(qk.comparisons.detail(comparisonId), data);
      if (done(data)) return data;
    } catch {
      /* a transient failure: keep polling */
    }
    if (i < attempts - 1) await new Promise((r) => setTimeout(r, intervalMs));
  }
  return null;
}

/** POST /comparisons — create-or-get the account's live comparison. */
/**
 * Open (or create) the account's comparison. `POST /comparisons` returns the
 * existing one for the account, so it is safe as a query: fetched once per
 * account and kept, and it survives StrictMode remounts.
 */
export function useEnsureComparison(caseFileId: number | undefined) {
  const qc = useQueryClient();
  return useQuery({
    queryKey: ["comparisons", "ensure", caseFileId ?? 0],
    enabled: !!caseFileId,
    staleTime: Infinity,
    gcTime: Infinity,
    retry: false,
    queryFn: async () => {
      const { data } = await api.post<Comparison>("/comparisons", { case_file_id: caseFileId });
      qc.setQueryData(qk.comparisons.detail(data.id), data);
      return data;
    },
  });
}

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
      invalidateJourney(qc);
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

/**
 * POST /comparisons/{id}/entries/{entryId}/retry — *Reintentar* a column whose
 * read failed (review r2 A2): the column's own document is read again INTO
 * THE SAME column. A failure keeps the column failed and answers the same
 * structured AI error as adding one.
 */
export function useRetryEntry(comparisonId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (entryId: number) => {
      const { data } = await api.post<ComparisonEntryResult>(
        `/comparisons/${comparisonId}/entries/${entryId}/retry`,
        {},
      );
      return data;
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.comparisons.detail(comparisonId) });
      invalidateJourney(qc);
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
      invalidateJourney(qc);
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
      void qc.invalidateQueries({ queryKey: qk.placements.all });
      invalidateJourney(qc);
    },
  });
}
