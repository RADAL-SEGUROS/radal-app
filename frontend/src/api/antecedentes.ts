/**
 * Antecedentes expediente — the v6 flow (spec §B/§E).
 *
 * Doctrine extends CLAUDE.md rule 6: **extract → human validates & completes →
 * register → view + PDF**. Nothing auto-writes.
 *   1. `useProcessAntecedentes()` consolidates every antecedentes document of
 *      one account into the ramo schema. It writes ONE `extraction` row and a
 *      `record_expediente` in status `review` holding the suggested payload; it
 *      commits no registered expediente.
 *   2. The broker validates & completes the suggestion in `AntecedentesReview`.
 *   3. `useRegisterAntecedentes()` commits the human-completed payload.
 *   4. `useExpediente()` reads it back; `useExpedientePdf()` fetches a
 *      short-lived URL for the generated, branded PDF Document (rule 8).
 *
 * The maintainer CRUD (`useRamoSchemas`/`useRamoSchema`/create/update/delete)
 * is broker-scoped; reads also surface the global Radal seed (broker_id NULL).
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import type {
  AntecedentesExpediente,
  AntecedentesRegisterRequest,
  AntecedentesSuggestion,
  DocumentDownload,
  LineAssignmentRequest,
  LineRecordSchema,
  LineRecordSchemaCreate,
  LineRecordSchemaUpdate,
  ListResponse,
} from "@/api/types";

/** True when an axios error carries a 404 status. */
function isNotFound(error: unknown): boolean {
  return (error as { response?: { status?: number } })?.response?.status === 404;
}

// --- Expediente: suggest → validate → register → view/pdf --------------------

/**
 * SUGGEST step. Consolidates the account's antecedentes documents into the
 * ramo schema; writes only an extraction row + a `review` expediente.
 */
export function useProcessAntecedentes() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ case_file_id }: { case_file_id: number }) => {
      const { data } = await api.post<AntecedentesSuggestion>(
        `/case-files/${case_file_id}/antecedentes/process`,
      );
      return data;
    },
    onSuccess: (data) => {
      // Seed the detail cache so a re-open shows the in-progress expediente.
      qc.setQueryData(qk.expedientes.detail(data.case_file_id), data);
      void qc.invalidateQueries({ queryKey: qk.expedientes.all });
    },
  });
}

/** The registered / in-progress expediente (schema + payload + status). */
export function useExpediente(caseId: number | undefined) {
  return useQuery({
    queryKey: qk.expedientes.detail(caseId ?? 0),
    enabled: !!caseId,
    queryFn: async () => {
      try {
        const { data } = await api.get<AntecedentesExpediente>(
          `/case-files/${caseId}/antecedentes`,
        );
        return data;
      } catch (error) {
        // No expediente yet is a legitimate empty state, not an error surface.
        if (isNotFound(error)) return null;
        throw error;
      }
    },
  });
}

/** COMMIT step. The human-completed payload becomes the registered expediente. */
export function useRegisterAntecedentes(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: AntecedentesRegisterRequest) => {
      const { data } = await api.post<AntecedentesExpediente>(
        `/case-files/${caseId}/antecedentes/register`,
        payload,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.expedientes.detail(caseId), data);
      void qc.invalidateQueries({ queryKey: qk.expedientes.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
    },
  });
}

/**
 * The branded PDF: a short-lived URL for the generated Document (two-step
 * download, mirroring `useDocumentDownload`). Only meaningful once registered.
 */
export function useExpedientePdf(caseId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: [...qk.expedientes.detail(caseId ?? 0), "pdf"] as const,
    enabled: !!caseId && enabled,
    staleTime: 30_000,
    queryFn: async () => {
      const { data } = await api.get<DocumentDownload>(
        `/case-files/${caseId}/antecedentes/pdf`,
      );
      return data;
    },
  });
}

// --- Ramo-schema maintainer CRUD (admin) -------------------------------------

/** Every schema visible to the broker (its own rows + the global seed). */
export function useRamoSchemas() {
  return useQuery({
    queryKey: qk.ramoSchemas.lists(),
    queryFn: async () => {
      const { data } = await api.get<ListResponse<LineRecordSchema>>("/line-record-schemas");
      return data;
    },
  });
}

/**
 * Resolve the active schema for one ramo — the broker's own if present, else
 * the global Radal seed. `null` = the ramo has no schema (what disables the
 * "Procesar antecedentes" CTA with a reason).
 */
export function useRamoSchema(insuranceLineId: number | null | undefined) {
  return useQuery({
    queryKey: qk.ramoSchemas.detail(insuranceLineId ?? 0),
    enabled: !!insuranceLineId,
    queryFn: async () => {
      try {
        const { data } = await api.get<LineRecordSchema>(
          `/line-record-schemas/${insuranceLineId}`,
        );
        return data;
      } catch (error) {
        if (isNotFound(error)) return null;
        throw error;
      }
    },
  });
}

/**
 * Create a broker line. Either a full definition (from scratch, or a template
 * prefilled and edited) or a `from_template_id` clone — the server copies the
 * template's ramo + definition in the latter case.
 */
export function useCreateRamoSchema() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: LineRecordSchemaCreate) => {
      const { data } = await api.post<LineRecordSchema>("/line-record-schemas", body);
      return data;
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.ramoSchemas.all }),
  });
}

export function useUpdateRamoSchema(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: LineRecordSchemaUpdate) => {
      const { data } = await api.put<LineRecordSchema>(`/line-record-schemas/${id}`, body);
      return data;
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.ramoSchemas.all }),
  });
}

export function useDeleteRamoSchema() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: number) => {
      await api.delete(`/line-record-schemas/${id}`);
      return id;
    },
    onSuccess: () => void qc.invalidateQueries({ queryKey: qk.ramoSchemas.all }),
  });
}

/**
 * Assign a line to an account (`POST /case-files/{id}/line`). Re-resolves the
 * expediente, so the detail cache is seeded from the response and the group
 * tree / navigator (which surface the assigned line name) are invalidated.
 */
export function useAssignLine(caseId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: LineAssignmentRequest) => {
      const { data } = await api.post<AntecedentesExpediente>(
        `/case-files/${caseId}/line`,
        body,
      );
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.expedientes.detail(caseId), data);
      void qc.invalidateQueries({ queryKey: qk.expedientes.all });
      void qc.invalidateQueries({ queryKey: qk.ramoSchemas.all });
      void qc.invalidateQueries({ queryKey: qk.navigator.all });
      void qc.invalidateQueries({ queryKey: qk.accountGroups.all });
      void qc.invalidateQueries({ queryKey: qk.caseFiles.all });
    },
  });
}
