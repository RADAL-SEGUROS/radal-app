/**
 * Quote requests — the ask issued from a placement, plus its line items and the
 * side-by-side proposal comparison.
 *
 * Hard invariant enforced server-side:
 * `declared_value_uf == SUM(line_items.value_uf)` (±0.01 UF). The line-item
 * mutations pass `sync_declared_value=true` by default so the declared value is
 * kept in step instead of 422-ing the caller.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  OffsetPage,
  ProposalComparison,
  QuoteLineItem,
  QuoteLineItemCreate,
  QuoteRequest,
  QuoteRequestCreate,
  QuoteRequestSend,
  QuoteRequestStatus,
  QuoteRequestUpdate,
} from "@/api/types";

export interface QuoteListParams {
  placement_id?: number;
  client_id?: number;
  status?: QuoteRequestStatus;
  search?: string;
  limit?: number;
  offset?: number;
}

export function useQuotes(params: QuoteListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.quotes.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<OffsetPage<QuoteRequest>>("/quotes", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useQuote(quoteId: number | undefined) {
  return useQuery({
    queryKey: qk.quotes.detail(quoteId ?? 0),
    enabled: !!quoteId,
    queryFn: async () => {
      const { data } = await api.get<QuoteRequest>(`/quotes/${quoteId}`);
      return data;
    },
  });
}

export function useQuoteLineItems(quoteId: number | undefined) {
  return useQuery({
    queryKey: qk.quotes.lineItems(quoteId ?? 0),
    enabled: !!quoteId,
    queryFn: async () => {
      const { data } = await api.get<QuoteLineItem[]>(`/quotes/${quoteId}/line-items`);
      return data;
    },
  });
}

/** The comparator: premiums, rates, deductibles per peril, coverage matrix. */
export function useQuoteComparison(
  quoteId: number | undefined,
  opts: { include_rejected?: boolean } = {},
) {
  return useQuery({
    queryKey: qk.quotes.comparison(quoteId ?? 0, opts),
    enabled: !!quoteId,
    queryFn: async () => {
      const { data } = await api.get<ProposalComparison>(`/quotes/${quoteId}/comparison`, {
        params: clean(opts),
      });
      return data;
    },
  });
}

export function useCreateQuote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: QuoteRequestCreate) => {
      const { data } = await api.post<QuoteRequest>("/quotes", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.quotes.all });
      void qc.invalidateQueries({ queryKey: qk.placements.all });
    },
  });
}

export function useUpdateQuote(quoteId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: QuoteRequestUpdate) => {
      const { data } = await api.patch<QuoteRequest>(`/quotes/${quoteId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.quotes.detail(quoteId), data);
      void qc.invalidateQueries({ queryKey: qk.quotes.lists() });
    },
  });
}

/** Mark a draft as sent to a set of insurers (delivery itself is manual). */
export function useSendQuote(quoteId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: QuoteRequestSend) => {
      const { data } = await api.post<QuoteRequest>(`/quotes/${quoteId}/send`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.quotes.detail(quoteId), data);
      void qc.invalidateQueries({ queryKey: qk.quotes.lists() });
      void qc.invalidateQueries({ queryKey: qk.placements.all });
    },
  });
}

export function useDeleteQuote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (quoteId: number) => {
      await api.delete(`/quotes/${quoteId}`);
      return quoteId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.quotes.all });
    },
  });
}

// --- Line items --------------------------------------------------------------

function useLineItemMutation<TVars>(
  quoteId: number,
  fn: (vars: TVars) => Promise<QuoteRequest>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      qc.setQueryData(qk.quotes.detail(quoteId), data);
      void qc.invalidateQueries({ queryKey: qk.quotes.lineItems(quoteId) });
      void qc.invalidateQueries({ queryKey: qk.quotes.lists() });
    },
  });
}

export function useAddQuoteLineItem(quoteId: number, syncDeclaredValue = true) {
  return useLineItemMutation(quoteId, async (payload: QuoteLineItemCreate) => {
    const { data } = await api.post<QuoteRequest>(`/quotes/${quoteId}/line-items`, payload, {
      params: { sync_declared_value: syncDeclaredValue },
    });
    return data;
  });
}

/** Replaces the WHOLE set of line items. */
export function useReplaceQuoteLineItems(quoteId: number, syncDeclaredValue = true) {
  return useLineItemMutation(quoteId, async (payload: QuoteLineItemCreate[]) => {
    const { data } = await api.put<QuoteRequest>(`/quotes/${quoteId}/line-items`, payload, {
      params: { sync_declared_value: syncDeclaredValue },
    });
    return data;
  });
}

export function useUpdateQuoteLineItem(quoteId: number, syncDeclaredValue = true) {
  return useLineItemMutation(
    quoteId,
    async ({ itemId, ...payload }: Partial<QuoteLineItemCreate> & { itemId: number }) => {
      const { data } = await api.patch<QuoteRequest>(
        `/quotes/${quoteId}/line-items/${itemId}`,
        payload,
        { params: { sync_declared_value: syncDeclaredValue } },
      );
      return data;
    },
  );
}

export function useDeleteQuoteLineItem(quoteId: number, syncDeclaredValue = true) {
  return useLineItemMutation(quoteId, async (itemId: number) => {
    const { data } = await api.delete<QuoteRequest>(
      `/quotes/${quoteId}/line-items/${itemId}`,
      { params: { sync_declared_value: syncDeclaredValue } },
    );
    return data;
  });
}
