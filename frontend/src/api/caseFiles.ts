/**
 * Case files (expedientes) — the narrative container the broker thinks in.
 *
 * `stage` NEVER moves through PATCH. It moves only through
 * `POST /case-files/{id}/transition`, and `GET /case-files/{id}/transitions`
 * returns `{to_stage, allowed, reason}` per option — the Journey component enables
 * its buttons from exactly that payload, so a stage the server would refuse is
 * never clickable.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  CaseClientAttachRequest,
  CaseDocumentGroups,
  CaseFile,
  CaseFileCreate,
  CaseFileDetail,
  CaseFileKind,
  CaseFileRef,
  CaseFileStatus,
  CaseFileSummary,
  CaseFileTimeline,
  CaseFileTransitions,
  CaseFileUpdate,
  CaseHistory,
  CaseOrigin,
  CaseRenewRequest,
  CaseReperiodRequest,
  CaseSection,
  CaseStage,
  Page,
  SummaryParams,
  ScopeParams,
} from "@/api/types";
import type { PlacementRoute } from "@/api/catalog";

// =============================================================================
// v12 — closed catalogue & partial journeys (types live here, not in types.ts)
// =============================================================================

/** How the colocación entered Radal (team `origen`; no screen of its own). */
export type CaseEntryOrigin =
  | "full_journey"
  | "quote_comparison"
  | "policy_upload"
  | "manual_entry"
  | "portal_rpa"
  | "migration";

/** One tick of the journey rail (`GET /case-files/{id}` → `stages[]`). */
export type StageState = "done" | "current" | "skipped" | "pending";

export interface CaseStageState {
  stage: CaseStage;
  state: StageState;
  entered_at: string | null;
  skipped: boolean;
}

/** The v12 fields every case-file read carries on top of the v11 shape. */
export interface CaseFileV12Fields {
  insurance_subtype_id?: number | null;
  insurance_subtype_code?: string | null;
  insurance_subtype_name?: string | null;
  entry_origin?: CaseEntryOrigin | null;
  route?: PlacementRoute | null;
}

/** `GET /case-files/{id}` as v12 serves it: the rail and the skip doors. */
export interface CaseFileDetailV12 extends CaseFileDetail, CaseFileV12Fields {
  stages: CaseStageState[];
  has_policy: boolean;
  /** Where "Saltar a…" may land (already ahead of the current stage). */
  skip_targets: CaseStage[];
}

/** `POST /case-files` with the v12 catalogue fields. */
export interface CaseFileCreateV12 extends CaseFileCreate {
  /** An ACTIVE catalogue subtipo; the server derives the ramo from it (422 otherwise). */
  insurance_subtype_id?: number | null;
  /** The vía; omitted → the subtipo's `suggested_route`. */
  route?: PlacementRoute | null;
}

/** Stages that only make sense once the folder holds a policy (server rule). */
export const POLICY_SKIP_TARGETS: readonly CaseStage[] = ["policy_issued", "active"];

export interface CaseFileListParams extends ScopeParams {
  kind?: CaseFileKind[];
  stage?: CaseStage[];
  status?: CaseFileStatus[];
  client_id?: number;
  policy_id?: number;
  parent_id?: number;
  insurance_line_id?: number;
  owner_user_id?: number;
  q?: string;
  page?: number;
  page_size?: number;

  // --- Groups & accounts (spec v3 §4.3) -----------------------------------
  /** The vigencia LABEL (`"2026-2027"`), not a date range. */
  period_label?: string;
  origin?: CaseOrigin;
}

export function useCaseFiles(params: CaseFileListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.caseFiles.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<Page<CaseFile>>("/case-files", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useCaseFilesSummary(
  enabled = true,
  /**
   * Scope: `account_group_id` / `case_file_id` / `date_from` / `date_to`, and
   * `group_by` for the bucket shape the charts consume. The params are part of
   * the cache key, so two scopes never share a cached answer.
   */
  params: SummaryParams = {},
) {
  return useQuery({
    queryKey: qk.caseFiles.summary(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<CaseFileSummary>("/case-files/summary", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useCaseFile(caseId: number | undefined) {
  return useQuery({
    queryKey: qk.caseFiles.detail(caseId ?? 0),
    enabled: !!caseId,
    queryFn: async () => {
      const { data } = await api.get<CaseFileDetailV12>(`/case-files/${caseId}`);
      return data;
    },
  });
}

/** The authoritative list of stage moves the UI may offer right now. */
export function useCaseFileTransitions(caseId: number | undefined) {
  return useQuery({
    queryKey: qk.caseFiles.transitions(caseId ?? 0),
    enabled: !!caseId,
    queryFn: async () => {
      const { data } = await api.get<CaseFileTransitions>(
        `/case-files/${caseId}/transitions`,
      );
      return data;
    },
  });
}

export function useCaseFileTimeline(caseId: number | undefined, limit = 200) {
  return useQuery({
    queryKey: qk.caseFiles.timeline(caseId ?? 0),
    enabled: !!caseId,
    queryFn: async () => {
      const { data } = await api.get<CaseFileTimeline>(
        `/case-files/${caseId}/timeline`,
        { params: clean({ limit }) },
      );
      return data;
    },
  });
}

/** Documents grouped by sub-expediente, each with a ready-to-use download URL. */
export function useCaseFileDocuments(
  caseId: number | undefined,
  section?: CaseSection,
) {
  return useQuery({
    queryKey: qk.caseFiles.documents(caseId ?? 0, { section }),
    enabled: !!caseId,
    queryFn: async () => {
      const { data } = await api.get<CaseDocumentGroups>(
        `/case-files/${caseId}/documents`,
        { params: clean({ section }) },
      );
      return data;
    },
  });
}

export function useCreateCaseFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CaseFileCreateV12) => {
      const { data } = await api.post<CaseFileDetailV12>("/case-files", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      // A new account folder changes the main rail's counts and the group tree.
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
    },
  });
}

export function useUpdateCaseFile(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CaseFileUpdate) => {
      const { data } = await api.patch<CaseFileDetail>(`/case-files/${caseId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.caseFiles.detail(caseId), data);
      void qc.invalidateQueries({ queryKey: qk.caseFiles.lists() });
    },
  });
}

export function useTransitionCaseFile(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { to_stage: CaseStage; note?: string | null }) => {
      const { data } = await api.post<CaseFileDetail>(
        `/case-files/${caseId}/transition`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.caseFiles.detail(caseId), data);
      void qc.invalidateQueries({ queryKey: qk.caseFiles.transitions(caseId) });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.pendingActions(caseId) });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.timeline(caseId) });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.lists() });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.summary() });
      // The tree draws the stage badge and the `period_locked` padlock, and
      // the rail's `recent` is ordered by last update.
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
    },
  });
}

/** Everything a stage move touches — the same fan-out as a transition. */
function invalidateAfterStageMove(
  qc: ReturnType<typeof useQueryClient>,
  caseId: number,
  data: CaseFileDetailV12,
) {
  qc.setQueryData(qk.caseFiles.detail(caseId), data);
  void qc.invalidateQueries({ queryKey: qk.caseFiles.transitions(caseId) });
  void qc.invalidateQueries({ queryKey: qk.caseFiles.pendingActions(caseId) });
  void qc.invalidateQueries({ queryKey: qk.caseFiles.timeline(caseId) });
  void qc.invalidateQueries({ queryKey: qk.caseFiles.lists() });
  void qc.invalidateQueries({ queryKey: qk.caseFiles.summary() });
  void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
  void qc.invalidateQueries({ queryKey: qk.navigator.all });
}

/**
 * "Saltar a…" — `POST /case-files/{id}/skip-to`. Jumps an open account forward
 * to one of `skip_targets`; every stage in between it never entered is written
 * as *omitida* by the server. 409 when the target is not ahead, 422 for a
 * policy target without a policy (Spanish `detail`, surfaced verbatim).
 */
export function useSkipCaseFileTo(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { target_stage: CaseStage; note?: string | null }) => {
      const { data } = await api.post<CaseFileDetailV12>(
        `/case-files/${caseId}/skip-to`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => invalidateAfterStageMove(qc, caseId, data),
  });
}

/**
 * "Activar etapa" — `POST /case-files/{id}/activate-stage`. Re-enters a SKIPPED
 * stage behind the current one; 409 once the folder has a policy (changes go
 * through an endorsement or a correction).
 */
export function useActivateCaseFileStage(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { stage: CaseStage; note?: string | null }) => {
      const { data } = await api.post<CaseFileDetailV12>(
        `/case-files/${caseId}/activate-stage`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => invalidateAfterStageMove(qc, caseId, data),
  });
}

/** Clone the case at `version + 1`, setting `supersedes_case_file_id`. */
export function useCreateCaseFileVersion(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { note?: string | null; title?: string | null }) => {
      const { data } = await api.post<CaseFileDetail>(
        `/case-files/${caseId}/versions`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
    },
  });
}

export function useDeleteCaseFile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (caseId: number) => {
      await api.delete(`/case-files/${caseId}`);
      return caseId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
    },
  });
}

/** The post-sale sub-funnel of a policy: ordered by kind, sequence and version. */
export function usePolicyCaseFiles(policyId: number | undefined) {
  return useQuery({
    queryKey: qk.policies.caseFiles(policyId ?? 0),
    enabled: !!policyId,
    queryFn: async () => {
      const { data } = await api.get<CaseFileRef[]>(`/policies/${policyId}/case-files`);
      return data;
    },
  });
}

// =============================================================================
// Groups & accounts — renew / reperiod / history / members (spec v3 §4.3)
// =============================================================================

/**
 * A vigencia date change is a NEW FOLDER, never an edit (rule 1).
 *
 * `PATCH /case-files/{id}` does not expose the period fields at all, and
 * `PATCH /placements/{id}` accepts them only while the wrapping account has no
 * stage event beyond `intake`. Afterwards the server answers 422
 * `{"code":"period_locked"}` and `useReperiodCase` is the only door. Read
 * `caseFile.period_locked` to decide which affordance to render — never infer
 * it from the stage in the UI.
 */

/**
 * Open the next vigencia: a `kind=renewal, origin=renewal` SIBLING of this
 * folder under the same group, with every source placement cloned as `draft`.
 * Invoked on the ACCOUNT folder, never on a policy (rule 4).
 *
 * The source folder is left untouched, so the whole chain stays readable; the
 * documents named by `copy_sections` are copied as NEW rows on NEW storage
 * keys, never two rows on one key.
 */
export function useRenewCase(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CaseRenewRequest) => {
      const { data } = await api.post<CaseFileDetail>(
        `/case-files/${caseId}/renew`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.caseFiles.detail(data.id), data);
      // The source gains a `renewed_by_case_file_id` in the tree, the group
      // gains a period, and the rail's counts move.
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.placements.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
    },
  });
}

/**
 * Change the validity period once the folder is locked: opens a sibling
 * `kind=account, origin=period_change` folder with the antecedentes copied,
 * and CLOSES the source with `meta.closed_reason="period_change"`.
 *
 * Both folders move, so the source detail is invalidated as well as the new one.
 */
export function useReperiodCase(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CaseReperiodRequest) => {
      const { data } = await api.post<CaseFileDetail>(
        `/case-files/${caseId}/reperiod`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.caseFiles.detail(data.id), data);
      void qc.invalidateQueries({ queryKey: qk.caseFiles.detail(caseId) });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.timeline(caseId) });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
      void qc.invalidateQueries({ queryKey: qk.placements.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
    },
  });
}

/**
 * The origin chain plus the immediate prior folder's records, policies and
 * claims — read-only context for the renewal screen.
 *
 * `prior` is `null` for a folder with `origin=new`; render "Sin vigencia
 * anterior" rather than an empty table. The renewal is never CONSTRAINED by
 * this payload: it informs the broker, it does not pre-fill a decision.
 */
export function useCaseHistory(caseId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.caseFiles.history(caseId ?? 0),
    enabled: !!caseId && enabled,
    queryFn: async () => {
      const { data } = await api.get<CaseHistory>(`/case-files/${caseId}/history`);
      return data;
    },
  });
}

/**
 * Add a RUT to the account (an account has N RUTs — rule 6).
 *
 * 422 `client_not_in_group` when the client belongs to another group: the
 * account's members must all sit under the folder's group.
 */
export function useAddCaseClient(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: CaseClientAttachRequest) => {
      const { data } = await api.post<CaseFileDetail>(
        `/case-files/${caseId}/clients`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.caseFiles.detail(caseId), data);
      void qc.invalidateQueries({ queryKey: qk.caseFiles.lists() });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
    },
  });
}

/** Remove a member RUT. The contratante (`case_file.client_id`) cannot go. */
export function useRemoveCaseClient(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: number) => {
      await api.delete(`/case-files/${caseId}/clients/${clientId}`);
      return clientId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.caseFiles.detail(caseId) });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.lists() });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
    },
  });
}
