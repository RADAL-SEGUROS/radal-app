import type { CoberturaEstado } from "@/lib/format";

export interface Ref {
  id: number;
  nombre: string;
}

export interface PolizaListItem {
  id: number;
  numero_poliza: string;
  cliente: Ref;
  ramo: Ref;
  aseguradora: Ref;
  tiene_coaseguro: boolean;
  prima_uf: number;
  comision_pct: number;
  suma_asegurada_uf: number;
  tipo_cobertura: CoberturaEstado;
  cobertura_pct: number;
  cobertura_estado: CoberturaEstado;
  vigencia_inicio: string;
  vigencia_fin: string;
  estado: "vigente" | "no_vigente";
}

export interface PolizasList {
  items: PolizaListItem[];
  total: number;
  page: number;
  page_size: number;
}

export interface PolizasSummary {
  polizas_vigentes: number;
  prima_total_uf: number;
  suma_asegurada_total_uf: number;
  con_coaseguro: number;
}

export interface CoaseguroParticipacion {
  aseguradora: Ref;
  es_lider: boolean;
  porcentaje: number;
}

export interface UbicacionPoliza {
  id?: number;
  nombre: string;
  direccion: string;
  suma_asegurada_uf: number;
  porcentaje: number;
}

export interface PlanPago {
  cuotas: number | null;
  metodo: string | null;
}

export interface PolizaDetail extends PolizaListItem {
  coaseguro_participaciones: CoaseguroParticipacion[];
  ubicaciones: UbicacionPoliza[];
  coberturas: string[];
  exclusiones: string[];
  deducible_texto: string | null;
  limite_indemnizacion_uf: number | null;
  plan_pago: PlanPago;
  pago_url: string | null;
  documentos?: unknown[];
  observaciones?: unknown[];
}
