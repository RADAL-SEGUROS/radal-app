export type RenovacionEstado = "por_iniciar" | "cotizando" | "negociando";
export type DiasColor = "rojo" | "ambar" | "gris";

export interface Ref {
  id: number;
  nombre: string;
}

export interface PolizaRef {
  id: number;
  numero_poliza: string;
}

/** Row shape returned by GET /renovaciones. */
export interface RenovacionListItem {
  id: number;
  codigo: string;
  cliente: Ref;
  poliza: PolizaRef | null;
  ramo: Ref;
  aseguradora: Ref;
  aplica_coaseguro: boolean;
  prima_defender_uf: number;
  comision_pct: number;
  fecha_vencimiento: string;
  dias_restantes: number | null;
  dias_color: DiasColor;
  estado: RenovacionEstado;
  ejecutivo?: Ref | null;
  estado_negociacion_texto?: string | null;
}

export interface RenovacionesSummary {
  renovaciones_activas: number;
  en_negociacion: number;
  prima_en_juego_uf: number;
  por_vencer_30d: number;
}

export interface CoaseguroParticipacion {
  id?: number;
  aseguradora: Ref;
  es_lider: boolean;
  porcentaje: number;
}

export interface UbicacionPoliza {
  id?: number;
  nombre: string;
  direccion?: string | null;
  suma_asegurada_uf: number;
  porcentaje: number;
}

/** Póliza summary embedded in the renovación detail. */
export interface PolizaResumen {
  id: number;
  numero_poliza: string;
  bien_asegurar?: string | null;
  suma_asegurada_uf?: number | null;
  limite_indemnizacion_uf?: number | null;
  deducible_texto?: string | null;
  tipo_cobertura?: string | null;
  cobertura_pct?: number | null;
  cobertura_estado?: string | null;
  vigencia_inicio?: string | null;
  vigencia_fin?: string | null;
  plan_pago?: { cuotas?: number | null; metodo?: string | null } | null;
  coaseguro_participaciones?: CoaseguroParticipacion[];
  ubicaciones?: UbicacionPoliza[];
  coberturas?: string[];
  exclusiones?: string[];
}

export interface RenovacionDetail extends RenovacionListItem {
  poliza_resumen?: PolizaResumen | null;
}

export interface Paginated<T> {
  items: T[];
  total: number;
  page: number;
  page_size: number;
}
