/**
 * Documents — the ONLY table that holds an S3 key. Domain entities reference a
 * file by `document.id`, never by a raw key string.
 *
 * Upload is `multipart/form-data`, whose exact bytes the browser assembles with
 * a boundary we cannot see. `lib/api.ts` detects that and sends the SigV4
 * `UNSIGNED-PAYLOAD` sentinel instead of a body hash, so the CloudFront OAC path
 * keeps working for uploads.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  DocumentCategory,
  DocumentDownload,
  DocumentUpdate,
  EntityType,
  ListResponse,
  RadalDocument,
} from "@/api/types";

export interface DocumentListParams {
  entity_type?: EntityType;
  entity_id?: number;
  category?: DocumentCategory;
  phase?: string;
  limit?: number;
  offset?: number;
}

export function useDocuments(params: DocumentListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.documents.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<ListResponse<RadalDocument>>("/documents", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useDocument(documentId: number | undefined) {
  return useQuery({
    queryKey: qk.documents.detail(documentId ?? 0),
    enabled: !!documentId,
    queryFn: async () => {
      const { data } = await api.get<RadalDocument>(`/documents/${documentId}`);
      return data;
    },
  });
}

/** A short-lived URL for the stored bytes. Fetch on demand, do not cache long. */
export function useDocumentDownload(documentId: number | undefined, enabled = true) {
  return useQuery({
    queryKey: qk.documents.download(documentId ?? 0),
    enabled: !!documentId && enabled,
    staleTime: 30_000,
    queryFn: async () => {
      const { data } = await api.get<DocumentDownload>(`/documents/${documentId}/download`);
      return data;
    },
  });
}

export interface UploadDocumentVars {
  file: File;
  entity_type: EntityType;
  entity_id: number;
  category?: DocumentCategory;
  phase?: string | null;
}

export function useUploadDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async ({ file, entity_type, entity_id, category, phase }: UploadDocumentVars) => {
      const form = new FormData();
      form.append("file", file);
      form.append("entity_type", entity_type);
      form.append("entity_id", String(entity_id));
      if (category) form.append("category", category);
      if (phase) form.append("phase", phase);
      const { data } = await api.post<RadalDocument>("/documents", form, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.documents.all });
    },
  });
}

/** Metadata-only patch — the stored bytes and their key are immutable. */
export function useUpdateDocument(documentId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: DocumentUpdate) => {
      const { data } = await api.patch<RadalDocument>(`/documents/${documentId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.documents.detail(documentId), data);
      void qc.invalidateQueries({ queryKey: qk.documents.lists() });
    },
  });
}

export function useDeleteDocument() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (documentId: number) => {
      await api.delete(`/documents/${documentId}`);
      return documentId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.documents.all });
    },
  });
}
