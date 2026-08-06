/**
 * Clients — the broker <-> insured relationship plus the broker-private CRM row.
 *
 * A client is created from a **RUT**, not an `insured_id`: the server validates
 * mod-11, finds-or-creates the canonical insured and links the broker's own row.
 * The broker is never blocked on the relationship.
 *
 * Endpoints: `GET|POST /clients`, `GET /clients/summary`,
 * `GET|PATCH|DELETE /clients/{id}`.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import api from "@/lib/api";
import { clean, qk } from "@/api/keys";
import type {
  Client,
  ClientCreate,
  ClientListItem,
  ClientStatus,
  ClientSummary,
  ClientUpdate,
  Page,
} from "@/api/types";

export interface ClientListParams {
  q?: string;
  /** Repeatable — sent as `?status=a&status=b`. */
  status?: ClientStatus[];
  account_manager_id?: number;
  sector?: string;
  source?: string;
  page?: number;
  page_size?: number;
}

export function useClients(params: ClientListParams = {}, enabled = true) {
  return useQuery({
    queryKey: qk.clients.list(params),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<Page<ClientListItem>>("/clients", {
        params: clean(params),
      });
      return data;
    },
  });
}

export function useClientsSummary(enabled = true) {
  return useQuery({
    queryKey: qk.clients.summary(),
    enabled,
    queryFn: async () => {
      const { data } = await api.get<ClientSummary>("/clients/summary");
      return data;
    },
  });
}

export function useClient(clientId: number | undefined) {
  return useQuery({
    queryKey: qk.clients.detail(clientId ?? 0),
    enabled: !!clientId,
    queryFn: async () => {
      const { data } = await api.get<Client>(`/clients/${clientId}`);
      return data;
    },
  });
}

export function useCreateClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ClientCreate) => {
      const { data } = await api.post<Client>("/clients", payload);
      return data;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.clients.all });
    },
  });
}

export function useUpdateClient(clientId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (payload: ClientUpdate) => {
      const { data } = await api.patch<Client>(`/clients/${clientId}`, payload);
      return data;
    },
    onSuccess: (data) => {
      qc.setQueryData(qk.clients.detail(clientId), data);
      void qc.invalidateQueries({ queryKey: qk.clients.lists() });
    },
  });
}

export function useDeleteClient() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (clientId: number) => {
      await api.delete(`/clients/${clientId}`);
      return clientId;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.clients.all });
    },
  });
}
