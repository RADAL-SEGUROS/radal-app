/**
 * Packs — the generated expediente downloadables (master PDF + ZIP).
 *
 * Generation is synchronous and bounded server-side: a failure is a 502 with
 * `case_pack.status=failed`, and an oversized ZIP is a 413. Re-generating
 * overwrites the fixed S3 keys instead of accumulating, so the download links
 * on screen stay valid.
 *
 * Sending the folder by email is OUT OF SCOPE this pass — the UI renders that
 * control disabled with a "pronto" chip rather than wiring a no-op.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type {
  CasePack,
  DocumentDownload,
  PackKind,
  PackPart,
  RecipientList,
} from "@/api/types";

interface CasePackList {
  case_file_id: number;
  items: CasePack[];
}

export function useCasePacks(caseId: number | undefined) {
  return useQuery({
    queryKey: qk.caseFiles.packs(caseId ?? 0),
    enabled: !!caseId,
    queryFn: async () => {
      const { data } = await api.get<CasePackList>(`/case-files/${caseId}/packs`);
      return data;
    },
  });
}

/** Resolved native insurers + contact (broker+line -> broker -> line -> global). */
export function useCaseRecipients(caseId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.caseFiles.recipients(caseId ?? 0),
    enabled: !!caseId && enabled,
    queryFn: async () => {
      const { data } = await api.get<RecipientList>(`/case-files/${caseId}/recipients`);
      return data;
    },
  });
}

/** Gated by `CaseFiles.Manage`; the caller must check before rendering enabled. */
export function useGeneratePack(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (kind: PackKind) => {
      const { data } = await api.post<CasePack>(`/case-files/${caseId}/packs/${kind}`);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.caseFiles.packs(caseId) });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.detail(caseId) });
      void qc.invalidateQueries({ queryKey: qk.packs.all });
    },
  });
}

export function usePack(packId: number | undefined) {
  return useQuery({
    queryKey: qk.packs.detail(packId ?? 0),
    enabled: !!packId,
    queryFn: async () => {
      const { data } = await api.get<CasePack>(`/packs/${packId}`);
      return data;
    },
  });
}

/** suggest -> edit -> confirmar, applied to the pack's AI prose. */
export function useConfirmPackSummary(packId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: { summary?: string | null; is_summary_confirmed?: boolean }) => {
      const { data } = await api.post<CasePack>(`/packs/${packId}/summary/confirm`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.packs.detail(packId), data);
      void qc.invalidateQueries({ queryKey: qk.caseFiles.packs(data.case_file_id) });
    },
  });
}

/** Fetch a short-lived download URL on demand — never cached long. */
export async function fetchPackDownload(
  packId: number,
  part: PackPart,
): Promise<DocumentDownload> {
  const { data } = await api.get<DocumentDownload>(`/packs/${packId}/download`, {
    params: { part },
  });
  return data;
}
