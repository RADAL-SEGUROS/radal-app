export type InspeccionEstado =
  | "solicitada"
  | "asignada"
  | "en_progreso"
  | "enviada"
  | "observada"
  | "validada"
  | "cerrada";

export type SolicitudEstado = "solicitada" | "asignada";
export type Urgencia = "baja" | "media" | "alta";

export interface Ref {
  id: number;
  nombre: string;
}

/** Row shape returned by GET /inspecciones. */
export interface InspeccionListItem {
  id: number;
  activo: Ref;
  cliente: Ref;
  ramo?: Ref | null;
  inspector?: Ref | null;
  version: number;
  estado: InspeccionEstado;
  solicitud_id?: number | null;
  motivo?: string | null;
  urgencia?: Urgencia | null;
  fecha_objetivo?: string | null;
}

/** KPIs from GET /inspecciones/summary. */
export interface InspeccionesSummary {
  solicitadas: number;
  en_progreso: number;
  validadas: number;
  abiertas_total: number;
}

/** Checklist item — categorized, typed sí/no/N.A./texto. */
export type ChecklistItemTipo = "boolean" | "sino" | "texto" | "na";

export interface ChecklistItem {
  id?: string;
  /** Prompt / question label. */
  label?: string;
  pregunta?: string;
  categoria?: string | null;
  tipo?: ChecklistItemTipo | string | null;
  /** Answer: "si" | "no" | "na" for sino, or free text. */
  valor?: string | boolean | null;
  respuesta?: string | boolean | null;
  nota?: string | null;
}

/** Checklist may arrive grouped by categoria or as a flat item list. */
export interface ChecklistCategoria {
  categoria: string;
  items: ChecklistItem[];
}

export type Checklist =
  | ChecklistItem[]
  | ChecklistCategoria[]
  | { categorias: ChecklistCategoria[] }
  | { items: ChecklistItem[] }
  | null;

export interface SolicitudResumen {
  id?: number;
  motivo?: string | null;
  urgencia?: Urgencia | string | null;
  fecha_objetivo?: string | null;
  estado?: SolicitudEstado | string | null;
  created_by?: Ref | string | null;
  proceso_ramo_id?: number | null;
}

export interface ActivoResumen {
  id: number;
  nombre: string;
  tipo_activo?: string | null;
  direccion?: string | null;
  estado?: string | null;
  cliente?: Ref | null;
}

export interface Documento {
  id: number;
  nombre: string;
  tipo?: string | null;
  url?: string | null;
  version?: number | null;
  autor?: Ref | string | null;
  created_at?: string | null;
}

export interface Observacion {
  id: number;
  texto: string;
  autor?: Ref | string | null;
  created_at?: string | null;
}

export interface InspeccionDetail extends InspeccionListItem {
  checklist?: Checklist;
  solicitud?: SolicitudResumen | null;
  activo_resumen?: ActivoResumen | null;
  documentos?: Documento[];
  observaciones?: Observacion[];
}

/** Body sent to POST /inspecciones/solicitudes (Solicitar inspección). */
export interface SolicitudCreate {
  activo_id: number;
  proceso_ramo_id?: number | null;
  motivo: string;
  urgencia: Urgencia;
  fecha_objetivo: string;
}

export interface RamoRef extends Ref {
  requiere_inspeccion?: boolean;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
