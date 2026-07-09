import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/* Types (mirror docs/api-contract.md — Siniestros)                    */
/* ------------------------------------------------------------------ */

export const SINIESTRO_ESTADOS = [
  "reportado",
  "en_documentacion",
  "en_evaluacion",
  "pre_liquidado",
  "liquidado",
  "cerrado",
] as const;

export type SiniestroEstado = (typeof SINIESTRO_ESTADOS)[number];

/** Estados that count as "abierto" (not liquidado / cerrado). */
export function esAbierto(estado: SiniestroEstado): boolean {
  return estado !== "liquidado" && estado !== "cerrado";
}

export interface Ref {
  id: number;
  nombre: string;
}

export interface PolizaRef {
  id: number;
  numero_poliza: string;
}

export interface SiniestroListItem {
  id: number;
  cliente: Ref | null;
  poliza: PolizaRef | null;
  activo?: Ref | null;
  tipo: string | null;
  fecha_evento: string | null;
  estado: SiniestroEstado;
  monto_estimado_uf: number | null;
  monto_liquidado_uf: number | null;
  fecha_liquidacion: string | null;
}

export interface SiniestrosListResponse {
  items: SiniestroListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface SiniestrosSummary {
  abiertos: number;
  en_evaluacion: number;
  monto_estimado_total_uf: number;
  monto_liquidado_total_uf: number;
}

export interface DocumentoResumen {
  id: number;
  nombre: string;
  tipo: string | null;
  version?: number | null;
  url?: string | null;
  created_at: string;
}

export interface ObservacionItem {
  id: number;
  texto: string;
  autor?: string | null;
  created_at: string;
}

export interface ActividadItem {
  id: number;
  accion: string;
  descripcion: string | null;
  usuario?: string | null;
  rol?: string | null;
  created_at: string;
}

export interface ActivoResumen {
  id: number;
  tipo_activo?: string | null;
  nombre: string;
  direccion?: string | null;
  estado?: string | null;
}

export interface SiniestroDetail extends SiniestroListItem {
  descripcion: string | null;
  activo?: ActivoResumen | Ref | null;
  documentos?: DocumentoResumen[];
  observaciones?: ObservacionItem[];
  actividad?: ActividadItem[];
}

/* ------------------------------------------------------------------ */
/* Query keys + hooks                                                  */
/* ------------------------------------------------------------------ */

export interface SiniestroFilters {
  q?: string;
  estado?: string;
  cliente_id?: number;
  poliza_id?: number;
}

export const siniestrosKeys = {
  all: ["siniestros"] as const,
  summary: () => [...siniestrosKeys.all, "summary"] as const,
  list: (filters: SiniestroFilters) =>
    [...siniestrosKeys.all, "list", filters] as const,
  detail: (id: string | number) =>
    [...siniestrosKeys.all, "detail", String(id)] as const,
};

export function useSiniestros(filters: SiniestroFilters) {
  return useQuery({
    queryKey: siniestrosKeys.list(filters),
    queryFn: async () => {
      const params: Record<string, string | number> = { page_size: 100 };
      if (filters.q) params.q = filters.q;
      if (filters.estado) params.estado = filters.estado;
      if (filters.cliente_id) params.cliente_id = filters.cliente_id;
      if (filters.poliza_id) params.poliza_id = filters.poliza_id;
      const { data } = await api.get<
        SiniestrosListResponse | SiniestroListItem[]
      >("/siniestros", { params });
      // Tolerate either the paginated envelope or a bare array.
      return Array.isArray(data) ? data : data.items;
    },
  });
}

export function useSiniestrosSummary() {
  return useQuery({
    queryKey: siniestrosKeys.summary(),
    queryFn: async () => {
      const { data } = await api.get<SiniestrosSummary>("/siniestros/summary");
      return data;
    },
  });
}

export function useSiniestro(id: string | undefined) {
  return useQuery({
    queryKey: siniestrosKeys.detail(id ?? ""),
    enabled: !!id,
    queryFn: async () => {
      const { data } = await api.get<SiniestroDetail>(`/siniestros/${id}`);
      return data;
    },
  });
}
