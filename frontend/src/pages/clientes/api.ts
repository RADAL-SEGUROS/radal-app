import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";

/* ------------------------------------------------------------------ */
/* Types (mirror docs/api-contract.md — Clientes)                      */
/* ------------------------------------------------------------------ */

export type ClienteEstado =
  | "activo"
  | "onboarding"
  | "prospecto"
  | "suspendido"
  | "archivado";

export interface UsuarioRef {
  id: number;
  nombre: string;
}

export interface EntidadRef {
  id: number;
  nombre: string;
}

export interface ClienteListItem {
  id: number;
  nombre: string;
  rut: string;
  sector: string | null;
  estado: ClienteEstado;
  contacto_principal: string | null;
  telefono: string | null;
  email: string | null;
  ejecutivo: UsuarioRef | null;
  polizas_vigentes: number;
  asegurados_adicionales?: number | null;
  prima_total_uf: number;
  fecha_alta: string;
}

export interface ClienteListResponse {
  items: ClienteListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface ClienteKpis {
  polizas_vigentes: number;
  prima_total_uf: number;
  suma_asegurada_uf: number;
  activos_count: number;
  siniestros_abiertos: number;
}

export type CoberturaEstado = "infravalorada" | "optima" | "sobrevalorada";

export interface PolizaResumen {
  id: number;
  numero_poliza: string;
  ramo?: EntidadRef | null;
  aseguradora?: EntidadRef | null;
  prima_uf: number;
  tipo_cobertura?: CoberturaEstado | null;
  cobertura_pct?: number | null;
  cobertura_estado?: CoberturaEstado | null;
  vigencia_inicio: string;
  vigencia_fin: string;
  estado: "vigente" | "no_vigente";
}

export interface AseguradoAdicional {
  id: number;
  rut: string;
  entidad: string | null;
  tipo_seguro: string | null;
  relacion_bien: string | null;
}

export interface CotizacionResumen {
  id: number;
  bien_asegurar: string | null;
  ramo?: EntidadRef | null;
  valor_declarado_uf: number;
  fecha_vence: string | null;
  prioridad: "baja" | "media" | "alta";
  estado: "pendiente" | "respondida";
}

export interface DocumentoResumen {
  id: number;
  nombre: string;
  tipo: string | null;
  version?: number | null;
  url?: string | null;
  created_at: string;
}

export interface SiniestroResumen {
  id: number;
  poliza?: { id: number; numero_poliza: string } | null;
  tipo: string | null;
  fecha_evento: string | null;
  estado: string;
  monto_estimado_uf: number | null;
}

export interface ActividadItem {
  id: number;
  accion: string;
  descripcion: string | null;
  usuario?: string | null;
  rol?: string | null;
  created_at: string;
}

export interface ProcesoRamoResumen {
  id: number;
  periodo?: string | null;
  estado: string;
}

export interface ActivoResumen {
  id: number;
  tipo_activo: string | null;
  nombre: string;
  direccion: string | null;
  estado: string;
  proceso_ramos?: ProcesoRamoResumen[];
}

export interface ClienteDetail extends ClienteListItem {
  activos: ActivoResumen[];
  polizas: PolizaResumen[];
  asegurados_adicionales_items?: AseguradoAdicional[];
  cotizaciones?: CotizacionResumen[];
  documentos?: DocumentoResumen[];
  siniestros: SiniestroResumen[];
  actividad?: ActividadItem[];
  kpis: ClienteKpis;
}

/* ------------------------------------------------------------------ */
/* Query keys + hooks                                                  */
/* ------------------------------------------------------------------ */

export interface ClienteFilters {
  q?: string;
  estado?: string;
  sector?: string;
  ejecutivo_id?: number;
}

export const clientesKeys = {
  all: ["clientes"] as const,
  list: (filters: ClienteFilters) =>
    [...clientesKeys.all, "list", filters] as const,
  detail: (id: string | number) =>
    [...clientesKeys.all, "detail", String(id)] as const,
};

export function useClientes(filters: ClienteFilters) {
  return useQuery({
    queryKey: clientesKeys.list(filters),
    queryFn: async () => {
      const params: Record<string, string | number> = { page_size: 100 };
      if (filters.q) params.q = filters.q;
      if (filters.estado) params.estado = filters.estado;
      if (filters.sector) params.sector = filters.sector;
      if (filters.ejecutivo_id) params.ejecutivo_id = filters.ejecutivo_id;
      const { data } = await api.get<ClienteListResponse | ClienteListItem[]>(
        "/clientes",
        { params },
      );
      // Tolerate either the paginated envelope or a bare array.
      return Array.isArray(data) ? data : data.items;
    },
  });
}

export function useCliente(id: string | undefined) {
  return useQuery({
    queryKey: clientesKeys.detail(id ?? ""),
    enabled: !!id,
    queryFn: async () => {
      const { data } = await api.get<ClienteDetail>(`/clientes/${id}`);
      return data;
    },
  });
}
