export type CotizacionEstado = "pendiente" | "respondida";
export type Prioridad = "baja" | "media" | "alta";
export type DiasColor = "rojo" | "ambar" | "gris";

export interface Ref {
  id: number;
  nombre: string;
}

/** Row shape returned by GET /cotizaciones. */
export interface CotizacionListItem {
  id: number;
  cliente: Ref;
  activo?: Ref | null;
  ramo: Ref;
  bien_asegurar: string;
  valor_declarado_uf: number;
  fecha_envio: string | null;
  fecha_vence: string | null;
  dias_restantes: number | null;
  dias_color: DiasColor;
  prioridad: Prioridad;
  estado: CotizacionEstado;
}

/** KPIs from GET /cotizaciones/summary. */
export interface CotizacionesSummary {
  en_curso: number;
  valor_declarado_total_uf: number;
  alta_prioridad: number;
  por_vencer_l7d: number;
}

/** Body sent to POST /cotizaciones. */
export interface CotizacionCreate {
  cliente_id: number;
  activo_id?: number | null;
  ramo_id: number;
  bien_asegurar: string;
  valor_declarado: number;
  fecha_envio: string;
  fecha_vence: string;
  prioridad: Prioridad;
  estado: CotizacionEstado;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
