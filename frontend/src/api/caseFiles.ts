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
} from "@/api/types";

export interface CaseFileListParams {
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
  account_group_id?: number;
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

export function useCaseFilesSummary(enabled = true) {
  return useQuery({
    queryKey: qk.caseFiles.summary(),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<CaseFileSummary>("/case-files/summary");
      return data;
    },
  });
}

export function useCaseFile(caseId: number | undefined) {
  return useQuery({
    queryKey: qk.caseFiles.detail(caseId ?? 0),
    enabled: !!caseId,
    queryFn: async () => {
      const { data } = await api.get<CaseFileDetail>(`/case-files/${caseId}`);
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
    mutationFn: async (payload: CaseFileCreate) => {
      const { data } = await api.post<CaseFileDetail>("/case-files", payload);
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
