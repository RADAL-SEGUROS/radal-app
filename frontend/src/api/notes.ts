/**
 * Notes and the activity feed.
 *
 * `is_internal` marks broker-private commentary that must never reach an
 * insured or insurer user; `follow_up_on` is the single date the team asked
 * for. Permission is resolved server-side from the TARGET entity's module, so
 * the same hook works for a case file, a lead, a policy or a claim.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  Activity,
  EntityType,
  IsoDate,
  IsoDateTime,
  Note,
  NoteCreate,
  NoteUpdate,
  OffsetPage,
} from "@/api/types";

export interface NoteListParams {
  entity_type: EntityType;
  entity_id: number;
  is_internal?: boolean;
  follow_up_before?: IsoDate;
  limit?: number;
  offset?: number;
}

export function useNotes(params: NoteListParams, enabled = true) {
  return useQuery({
    queryKey: qk.notes.forEntity(params.entity_type, params.entity_id, {
      is_internal: params.is_internal,
      follow_up_before: params.follow_up_before,
    }),
    enabled: enabled && !!params.entity_id,
    queryFn: async () => {
      const { data } = await api.get<OffsetPage<Note>>("/notes", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useCreateNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: NoteCreate) => {
      const { data } = await api.post<Note>("/notes", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.notes.all });
      void qc.invalidateQueries({ queryKey: qk.activities.all });
    },
  });
}

export function useUpdateNote(noteId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: NoteUpdate) => {
      const { data } = await api.patch<Note>(`/notes/${noteId}`, payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.notes.all });
    },
  });
}

export function useDeleteNote() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (noteId: number) => {
      await api.delete(`/notes/${noteId}`);
      return noteId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.notes.all });
    },
  });
}

export interface ActivityListParams {
  entity_type?: EntityType;
  entity_id?: number;
  action?: string;
  since?: IsoDateTime;
  limit?: number;
  offset?: number;
}

/** The real activity feed — replaces the fabricated client-side timeline. */
export function useActivities(params: ActivityListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.activities.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<OffsetPage<Activity>>("/activities", {
        params: clean(params),
      });
      return data;
    },
  });
}
