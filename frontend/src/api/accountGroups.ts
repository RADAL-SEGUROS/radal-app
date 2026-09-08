/**
 * Account groups — the broker-private label above the expediente (spec v3 §4.1).
 *
 * A group carries **no money and no stage** (rule 7). It holds clients (a
 * broker×RUT belongs to at most one group) and, through them, the accounts —
 * `case_file(kind=account|renewal)`. Archiving or deleting a group DETACHES
 * (SET NULL); it never cascades, so nothing is ever lost by tidying the rail.
 *
 * Two shapes differ from the rest of the API on purpose:
 *
 *  - **the tree** (`GET /account-groups/{id}/tree`) is the contextual sidebar's
 *    whole payload — vigencia → ramo → account / policies / renewal, already
 *    narrowed to what the caller may see. Render it as it arrives;
 *  - **the timeline** is CURSOR-paged, not offset-paged: it is merged over
 *    every visible case of the group and sorted DESCENDING before truncation,
 *    which an offset cannot express. Pass the previous page's `next_before`
 *    back as `before`; a `null` `next_before` means you reached the end.
 *
 * Every mutation here invalidates the navigator, because a rename, an archive
 * or a new member all change the main rail.
 */
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  AccountGroup,
  AccountGroupAttachClient,
  AccountGroupCreate,
  AccountGroupDetail,
  AccountGroupStatus,
  AccountGroupUpdate,
  ArchiveCreate,
  ArchiveResponse,
  GroupTimelinePage,
  GroupTree,
  Page,
} from "@/api/types";

export interface AccountGroupListParams {
  q?: string;
  status?: AccountGroupStatus;
  page?: number;
  page_size?: number;
}

export interface GroupTimelineParams {
  limit?: number;
  /** ISO datetime cursor — the previous page's `next_before`. */
  before?: string | null;
}

/**
 * Invalidate everything a group mutation can move, in one place (spec §5.4).
 *
 * `caseFiles` is in the list because `case_file.account_group_id` is
 * DENORMALISED — the tree counts never join `client` — so moving a RUT in or
 * out of a group, or archiving one, changes rows the case lists already hold.
 * `accountGroups.all` is a prefix of `accountGroups.tree(id)`, so the explicit
 * tree line only matters for readers that scope their invalidation narrower.
 */
function invalidateGroupScope(
  qc: ReturnType<typeof useQueryClient>,
  groupId?: number,
): void {
  void qc.invalidateQueries({ queryKey: qk.navigator.all });
  void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
  void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
  if (groupId) void qc.invalidateQueries({ queryKey: qk.accountGroups.tree(groupId) });
}

// --- Queries -----------------------------------------------------------------

export function useAccountGroups(params: AccountGroupListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.accountGroups.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<Page<AccountGroup>>("/account-groups", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useAccountGroup(groupId: number | undefined) {
  return useQuery({
    queryKey: qk.accountGroups.detail(groupId ?? 0),
    enabled: !!groupId,
    queryFn: async () => {
      const { data } = await api.get<AccountGroupDetail>(`/account-groups/${groupId}`);
      return data;
    },
  });
}

/**
 * The contextual rail. Gated by `CaseFiles.View`, not `Groups.View` — the tree
 * IS the case list. Kept across refetches so the sidebar never collapses back
 * to a skeleton while the user is navigating inside it.
 */
export function useGroupTree(groupId: number | undefined) {
  return useQuery({
    queryKey: qk.accountGroups.tree(groupId ?? 0),
    enabled: !!groupId,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const { data } = await api.get<GroupTree>(`/account-groups/${groupId}/tree`);
      return data;
    },
  });
}

/**
 * One cursor page of the merged group timeline, newest first.
 *
 * To load more: keep the entries you have, then re-render this hook with
 * `before: page.next_before`. It is a plain query, not an infinite one, so the
 * accumulation lives in the page's state where it can also be reset by a
 * filter change.
 */
export function useGroupTimeline(
  groupId: number | undefined,
  params: GroupTimelineParams = {},
) {
  return useQuery({
    queryKey: qk.accountGroups.timeline(groupId ?? 0, params),
    enabled: !!groupId,
    queryFn: async () => {
      const { data } = await api.get<GroupTimelinePage>(
        `/account-groups/${groupId}/timeline`,
        { params: clean(params) },
      );
      return data;
    },
  });
}

// --- Mutations ---------------------------------------------------------------

export function useCreateGroup() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AccountGroupCreate) => {
      const { data } = await api.post<AccountGroupDetail>("/account-groups", payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.accountGroups.detail(data.id), data);
      invalidateGroupScope(qc, data.id);
      // `client_ids` moves those clients into the group.
      void qc.invalidateQueries({ queryKey: qk.clients.all });
    },
  });
}

export function useUpdateGroup(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AccountGroupUpdate) => {
      const { data } = await api.patch<AccountGroupDetail>(
        `/account-groups/${groupId}`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.accountGroups.detail(groupId), data);
      invalidateGroupScope(qc, groupId);
      // Archiving DETACHES clients and cases (SET NULL) — it never cascades,
      // so nothing is lost; the client list simply loses its group column.
      void qc.invalidateQueries({ queryKey: qk.clients.all });
    },
  });
}

/** 422 `client_in_other_group` when the client already belongs elsewhere. */
export function useAttachGroupClient(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AccountGroupAttachClient) => {
      const { data } = await api.post<AccountGroupDetail>(
        `/account-groups/${groupId}/clients`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.accountGroups.detail(groupId), data);
      invalidateGroupScope(qc, groupId);
      void qc.invalidateQueries({ queryKey: qk.clients.all });
    },
  });
}

/** 422 when the client still has open folders in this group. */
export function useDetachGroupClient(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: number) => {
      await api.delete(`/account-groups/${groupId}/clients/${clientId}`);
      return clientId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.accountGroups.detail(groupId) });
      invalidateGroupScope(qc, groupId);
      void qc.invalidateQueries({ queryKey: qk.clients.all });
    },
  });
}

/**
 * Generate the group's ZIP — the whole group, or one vigencia when
 * `period_label` is given. It lands as a
 * `document(entity_type=account_group, category=archive_pack)` row and comes
 * back as an ordinary presigned `DocumentDownload`: nothing streams through
 * the buffered Lambda, so open `result.download.url` rather than piping bytes.
 * Only documents of cases visible to the caller are included.
 */
export function useCreateArchive(groupId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ArchiveCreate = {}) => {
      const { data } = await api.post<ArchiveResponse>(
        `/account-groups/${groupId}/archives`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.accountGroups.archives(groupId) });
      void qc.invalidateQueries({ queryKey: qk.documents.all });
    },
  });
}
