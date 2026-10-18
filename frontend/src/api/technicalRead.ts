/**
 * Fast reading — the policy's TECHNICAL read (docs/v12/11-fast-reading.md).
 *
 * `POST /policies/upload` now runs only the core read and answers
 * `technical_read: {status: "pending"}`; the SPA then fires
 * `POST /policies/{id}/technical-read` as a SECOND request (each well under the
 * 60 s CloudFront origin timeout). The result is a SUGGESTION (rule 6): it sits
 * on an `extraction` row until the broker confirms it on the ficha.
 *
 * The start mutation is built so it SURVIVES the upload dialog closing (the
 * Pólizas list navigates to the new policy the moment the upload commits): its
 * cache work lives in the mutation-level `onSuccess` / `onSettled`, which
 * TanStack runs even after the issuing component unmounted, and the ficha finds
 * the in-flight request through `useTechnicalReadInFlight` (same mutation key,
 * matched by `policyId`).
 */
import {
  useIsMutating,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
  type Mutation,
} from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";

export type TechnicalReadStatus = "none" | "running" | "ready" | "confirmed" | "failed";
export type TechnicalReadErrorCode = "ai_timeout" | "ai_unavailable" | "ai_parse";

/** The six reviewable sections, in the ficha's display order. */
export const TECHNICAL_SECTIONS = [
  "items",
  "coverages",
  "deductibles",
  "warranties",
  "exclusions",
  "particular_clauses",
] as const;
export type TechnicalSection = (typeof TECHNICAL_SECTIONS)[number];

type Num = string | number | null;

/** `tech_risk.items` — ubicación · bien · vehículo · persona. */
export interface TechnicalItem {
  type?: string | null;
  description?: string | null;
  address_or_plate?: string | null;
  insured_amount_uf?: Num;
}

/** `tech_risk.coverages` — sublímites merged into one text. */
export interface TechnicalCoverage {
  code?: string | null;
  name?: string | null;
  limit_uf?: Num;
  sublimit_text?: string | null;
}

export interface TechnicalDeductible {
  peril?: string | null;
  pct?: Num;
  min_uf?: Num;
  max_uf?: Num;
  days?: Num;
  text?: string | null;
}

export interface TechnicalWarranty {
  code?: string | null;
  title?: string | null;
  requirement?: string | null;
  deadline_days?: Num;
  due_date?: string | null;
}

/** Exclusions and particular clauses: number + title ONLY (decision 4). */
export interface TechnicalTitle {
  number?: string | number | null;
  title?: string | null;
}

export interface TechnicalLimits {
  indemnity_limit?: string | null;
  aggregate_limit_uf?: Num;
  valuation_basis?: string | null;
  average_rate_permille?: Num;
  cmf_policy_code?: string | null;
}

export interface TechnicalSections {
  items?: TechnicalItem[];
  coverages?: TechnicalCoverage[];
  deductibles?: TechnicalDeductible[];
  warranties?: TechnicalWarranty[];
  exclusions?: TechnicalTitle[];
  particular_clauses?: TechnicalTitle[];
  limits?: TechnicalLimits | null;
}

/**
 * `GET|POST /policies/{id}/technical-read`.
 *
 * Mirrors `backend/app/schemas/policy_reading.py::TechnicalRead`. A failed
 * technical GROUP (`tech_risk`, `tech_deductibles`, `tech_clauses`) leaves its
 * sections empty and is reported in `section_errors` (`{section: message}`,
 * `limits` included); `technicalSectionErrors` also accepts group-name keys
 * and a `[{section|group, message}]` list, defensively.
 */
export interface TechnicalRead {
  status: TechnicalReadStatus;
  extraction_id?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  error_code?: TechnicalReadErrorCode | string | null;
  /** Spanish, user-facing. */
  message?: string | null;
  sections?: TechnicalSections | null;
  counts?: Partial<Record<TechnicalSection, number>> | null;
  /** `{section: Spanish message}` for the sections whose group failed (the
   *  backend's shape); `errors` is read too, defensively. */
  section_errors?: unknown;
  errors?: unknown;
  /** Sections committed by the confirm (confirmed reads only). */
  confirmed_sections?: string[];
  /** Present on the confirm answer: differences against confirmed core values. */
  warnings?: string[];
}

/** What `POST …/confirm` answers. Read tolerantly: warnings may sit at the top
 *  level or inside a nested `technical_read`. */
export interface TechnicalReadConfirmResult extends Partial<TechnicalRead> {
  technical_read?: TechnicalRead | null;
  warnings?: string[];
}

/** The groups the server splits the technical read into, by section. */
const GROUP_SECTIONS: Record<string, readonly (TechnicalSection | "limits")[]> = {
  tech_risk: ["items", "coverages", "limits"],
  tech_deductibles: ["deductibles", "warranties"],
  tech_clauses: ["exclusions", "particular_clauses"],
};

const SECTION_SET = new Set<string>([...TECHNICAL_SECTIONS, "limits"]);

/** Normalise whatever per-section error shape the server sent. */
export function technicalSectionErrors(
  read: TechnicalRead | null | undefined,
): Partial<Record<TechnicalSection | "limits", string>> {
  const out: Partial<Record<TechnicalSection | "limits", string>> = {};
  const assign = (key: string, message: unknown) => {
    const text = typeof message === "string" ? message : null;
    if (!text) return;
    if (SECTION_SET.has(key)) {
      out[key as TechnicalSection | "limits"] ??= text;
      return;
    }
    for (const section of GROUP_SECTIONS[key] ?? []) out[section] ??= text;
  };
  for (const raw of [read?.section_errors, read?.errors]) {
    if (!raw) continue;
    if (Array.isArray(raw)) {
      for (const row of raw) {
        if (!row || typeof row !== "object") continue;
        const r = row as { section?: unknown; group?: unknown; message?: unknown; error?: unknown };
        const key = typeof r.section === "string" ? r.section : typeof r.group === "string" ? r.group : null;
        if (key) assign(key, r.message ?? r.error);
      }
    } else if (typeof raw === "object") {
      for (const [key, value] of Object.entries(raw as Record<string, unknown>)) {
        const message =
          value && typeof value === "object"
            ? ((value as { message?: unknown }).message ?? (value as { error?: unknown }).error)
            : value;
        assign(key, message);
      }
    }
  }
  return out;
}

/** The count the server sent, else the length of the section it sent. */
export function technicalCount(read: TechnicalRead, section: TechnicalSection): number {
  const fromCounts = read.counts?.[section];
  if (typeof fromCounts === "number") return fromCounts;
  const rows = read.sections?.[section];
  return Array.isArray(rows) ? rows.length : 0;
}

// --- Queries & mutations ------------------------------------------------------

const START_KEY = ["policies", "technical-read", "start"] as const;

export interface StartTechnicalReadVars {
  policyId: number;
  /** `?force=true` — re-read even when an unconfirmed result exists (Reintentar). */
  force?: boolean;
}

/**
 * `GET /policies/{id}/technical-read` → the latest read or `{status: "none"}`.
 * Polls every 4 s while the server reports `running`.
 */
export function useTechnicalRead(policyId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.policies.technicalRead(policyId ?? 0),
    enabled: !!policyId && enabled,
    queryFn: async () => {
      const { data } = await api.get<TechnicalRead>(`/policies/${policyId}/technical-read`);
      return data;
    },
    refetchInterval: (query) => (query.state.data?.status === "running" ? 4_000 : false),
  });
}

/**
 * `POST /policies/{id}/technical-read[?force=true]` — run the technical groups.
 * Fire-and-forget safe: cache writes are mutation-level, so they still land if
 * the caller (the upload dialog) is gone by the time the read finishes.
 */
export function useStartTechnicalRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationKey: START_KEY,
    mutationFn: async ({ policyId, force }: StartTechnicalReadVars) => {
      const { data } = await api.post<TechnicalRead>(
        `/policies/${policyId}/technical-read`,
        {},
        force ? { params: { force: true } } : undefined,
      );
      return data;
    },
    onSuccess: (data, { policyId }) => {
      qc.setQueryData(qk.policies.technicalRead(policyId), data);
    },
    onSettled: (_data, _error, { policyId }) => {
      // On an edge timeout the server may still finish: the GET tells the truth.
      void qc.invalidateQueries({ queryKey: qk.policies.technicalRead(policyId) });
    },
  });
}

function isStartFor(policyId: number) {
  return (mutation: Mutation<unknown, Error, unknown, unknown>) => {
    const key = mutation.options.mutationKey;
    const vars = mutation.state.variables as StartTechnicalReadVars | undefined;
    return (
      Array.isArray(key) &&
      key[0] === START_KEY[0] &&
      key[1] === START_KEY[1] &&
      key[2] === START_KEY[2] &&
      vars?.policyId === policyId
    );
  };
}

/** Is a `POST …/technical-read` for this policy in flight (from ANY component)? */
export function useTechnicalReadInFlight(policyId: number): boolean {
  return useIsMutating({ predicate: isStartFor(policyId) }) > 0;
}

/** The error of the latest start attempt for this policy, if it failed. */
export function useLastTechnicalReadError(policyId: number): unknown {
  const states = useMutationState<{ status: string; error: unknown }>({
    filters: { predicate: isStartFor(policyId) },
    select: (m) => ({ status: m.state.status, error: m.state.error }),
  });
  const last = states[states.length - 1];
  return last?.status === "error" ? last.error : null;
}

export function useConfirmTechnicalRead(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({
      extractionId,
      sections,
    }: {
      extractionId: number;
      sections?: TechnicalSection[];
    }) => {
      const { data } = await api.post<TechnicalReadConfirmResult>(
        `/policies/${policyId}/technical-read/${extractionId}/confirm`,
        sections ? { sections } : {},
      );
      return data;
    },
    onSettled: () => {
      // Confirm writes deductibles, limits, warranties and the payload.
      void qc.invalidateQueries({ queryKey: qk.policies.detail(policyId) });
      void qc.invalidateQueries({ queryKey: qk.policies.lists() });
      void qc.invalidateQueries({ queryKey: qk.warranties.all });
    },
  });
}

export function useDiscardTechnicalRead(policyId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (extractionId: number) => {
      const { data } = await api.post<TechnicalRead>(
        `/policies/${policyId}/technical-read/${extractionId}/discard`,
        {},
      );
      return data;
    },
    onSettled: () => {
      void qc.invalidateQueries({ queryKey: qk.policies.technicalRead(policyId) });
    },
  });
}
