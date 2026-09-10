/**
 * Inspections — the request (`/inspection-requests`) and the report itself
 * (`/inspections`), plus its boundary (colindancia) child rows.
 *
 * Scores are flat 0-100 columns so they are filterable; the checklist is
 * versioned JSON; a re-inspection is a new *version* forked from the previous
 * report rather than an edit in place.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  Inspection,
  InspectionBoundary,
  InspectionBoundaryCreate,
  InspectionBoundaryUpdate,
  InspectionCreate,
  InspectionRequest,
  InspectionRequestCreate,
  InspectionRequestStatus,
  InspectionRequestUpdate,
  InspectionStatus,
  InspectionUpdate,
  InspectionVersionCreate,
  ListResponse,
  ScopeParams,
} from "@/api/types";

// --- Inspection requests -----------------------------------------------------

export interface InspectionRequestListParams {
  asset_id?: number;
  placement_id?: number;
  status?: InspectionRequestStatus;
  limit?: number;
  offset?: number;
}

export function useInspectionRequests(
  params: InspectionRequestListParams = {},
  enabled = true,
) {
  return useQuery({
    queryKey: qk.inspectionRequests.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<ListResponse<InspectionRequest>>("/inspection-requests", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useInspectionRequest(requestId: number | undefined) {
  return useQuery({
    queryKey: qk.inspectionRequests.detail(requestId ?? 0),
    enabled: !!requestId,
    queryFn: async () => {
      const { data } = await api.get<InspectionRequest>(`/inspection-requests/${requestId}`);
      return data;
    },
  });
}

export function useCreateInspectionRequest() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: InspectionRequestCreate) => {
      const { data } = await api.post<InspectionRequest>("/inspection-requests", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.inspectionRequests.all });
      void qc.invalidateQueries({ queryKey: qk.placements.all });
    },
  });
}

export function useUpdateInspectionRequest(requestId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: InspectionRequestUpdate) => {
      const { data } = await api.patch<InspectionRequest>(
        `/inspection-requests/${requestId}`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.inspectionRequests.detail(requestId), data);
      void qc.invalidateQueries({ queryKey: qk.inspectionRequests.lists() });
    },
  });
}

export function useCancelInspectionRequest(requestId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const { data } = await api.post<InspectionRequest>(
        `/inspection-requests/${requestId}/cancel`,
        {},
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.inspectionRequests.detail(requestId), data);
      void qc.invalidateQueries({ queryKey: qk.inspectionRequests.lists() });
    },
  });
}

// --- Inspections -------------------------------------------------------------

export interface InspectionListParams extends ScopeParams {
  asset_id?: number;
  inspection_request_id?: number;
  inspector_id?: number;
  status?: InspectionStatus;
  min_overall_score?: number;
  max_overall_score?: number;
  /** Keep only the newest version per asset. */
  latest_only?: boolean;
  limit?: number;
  offset?: number;
}

export function useInspections(params: InspectionListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.inspections.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<ListResponse<Inspection>>("/inspections", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useInspection(inspectionId: number | undefined) {
  return useQuery({
    queryKey: qk.inspections.detail(inspectionId ?? 0),
    enabled: !!inspectionId,
    queryFn: async () => {
      const { data } = await api.get<Inspection>(`/inspections/${inspectionId}`);
      return data;
    },
  });
}

export function useCreateInspection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: InspectionCreate) => {
      const { data } = await api.post<Inspection>("/inspections", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.inspections.all });
      void qc.invalidateQueries({ queryKey: qk.inspectionRequests.all });
    },
  });
}

function useInspectionWrite<TVars>(
  inspectionId: number,
  fn: (vars: TVars) => Promise<Inspection>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: (data) => {
      qc.setQueryData(qk.inspections.detail(inspectionId), data);
      void qc.invalidateQueries({ queryKey: qk.inspections.lists() });
    },
  });
}

export function useUpdateInspection(inspectionId: number) {
  return useInspectionWrite(inspectionId, async (payload: InspectionUpdate) => {
    const { data } = await api.patch<Inspection>(`/inspections/${inspectionId}`, payload);
    return data;
  });
}

export function useAssignInspection(inspectionId: number) {
  return useInspectionWrite(inspectionId, async (payload: { inspector_id: number | null }) => {
    const { data } = await api.post<Inspection>(`/inspections/${inspectionId}/assign`, payload);
    return data;
  });
}

export function useSetInspectionStatus(inspectionId: number) {
  return useInspectionWrite(inspectionId, async (payload: { status: InspectionStatus }) => {
    const { data } = await api.post<Inspection>(`/inspections/${inspectionId}/status`, payload);
    return data;
  });
}

/** Replaces the checklist wholesale; the version tracks its template. */
export function useReplaceInspectionChecklist(inspectionId: number) {
  return useInspectionWrite(
    inspectionId,
    async (payload: { checklist: Record<string, unknown>; checklist_version?: number | null }) => {
      const { data } = await api.put<Inspection>(
        `/inspections/${inspectionId}/checklist`,
        payload,
      );
      return data;
    },
  );
}

/** Fork this report into the next version for the same asset (re-inspection). */
export function useCreateInspectionVersion(inspectionId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: InspectionVersionCreate = {}) => {
      const { data } = await api.post<Inspection>(
        `/inspections/${inspectionId}/versions`,
        payload,
      );
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.inspections.all });
    },
  });
}

export function useDeleteInspection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (inspectionId: number) => {
      await api.delete(`/inspections/${inspectionId}`);
      return inspectionId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.inspections.all });
    },
  });
}

// --- Boundaries (colindancias) ----------------------------------------------

export function useInspectionBoundaries(inspectionId: number | undefined) {
  return useQuery({
    queryKey: qk.inspections.boundaries(inspectionId ?? 0),
    enabled: !!inspectionId,
    queryFn: async () => {
      const { data } = await api.get<InspectionBoundary[]>(
        `/inspections/${inspectionId}/boundaries`,
      );
      return data;
    },
  });
}

function useBoundaryWrite<TVars, TResult>(
  inspectionId: number,
  fn: (vars: TVars) => Promise<TResult>,
) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.inspections.boundaries(inspectionId) });
      void qc.invalidateQueries({ queryKey: qk.inspections.detail(inspectionId) });
    },
  });
}

export function useAddInspectionBoundary(inspectionId: number) {
  return useBoundaryWrite(inspectionId, async (payload: InspectionBoundaryCreate) => {
    const { data } = await api.post<InspectionBoundary>(
      `/inspections/${inspectionId}/boundaries`,
      payload,
    );
    return data;
  });
}

export function useUpdateInspectionBoundary(inspectionId: number) {
  return useBoundaryWrite(
    inspectionId,
    async ({ boundaryId, ...payload }: InspectionBoundaryUpdate & { boundaryId: number }) => {
      const { data } = await api.patch<InspectionBoundary>(
        `/inspections/${inspectionId}/boundaries/${boundaryId}`,
        payload,
      );
      return data;
    },
  );
}

export function useDeleteInspectionBoundary(inspectionId: number) {
  return useBoundaryWrite(inspectionId, async (boundaryId: number) => {
    await api.delete(`/inspections/${inspectionId}/boundaries/${boundaryId}`);
    return boundaryId;
  });
}
