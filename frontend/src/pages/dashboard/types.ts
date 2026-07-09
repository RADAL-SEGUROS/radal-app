import type { Rol } from "@/providers/AuthProvider";

export type GreetingPeriodo = "manana" | "tarde" | "noche";

export type AtencionTipo =
  | "renovacion"
  | "cotizacion"
  | "siniestro"
  | "inspeccion";

export interface DashboardSaludo {
  periodo: GreetingPeriodo;
  dia_semana: string;
  nombre: string;
}

export interface DashboardKpis {
  polizas_vigentes: number;
  clientes_activos: number;
  renovaciones_activas: number;
  pipeline_ponderado_uf: number;
}

export interface RequiereAtencionItem {
  tipo: AtencionTipo;
  id: number;
  titulo: string;
  detalle: string;
  url: string;
}

export interface PrincipalCliente {
  id: number;
  nombre: string;
  sector: string;
  prima_total_uf: number;
  polizas_vigentes: number;
  estado: string;
}

export interface ActividadItem {
  id: number;
  accion: string;
  descripcion: string;
  usuario: string;
  rol: Rol | string;
  created_at: string;
}

export interface ProximaRenovacion {
  id: number;
  codigo: string;
  cliente: string;
  fecha_vencimiento: string;
  dias_restantes: number;
  prima_defender_uf: number;
  estado: string;
}

export interface DashboardPayload {
  saludo: DashboardSaludo;
  kpis: DashboardKpis;
  requiere_atencion: RequiereAtencionItem[];
  principales_clientes: PrincipalCliente[];
  actividad_reciente: ActividadItem[];
  proximas_renovaciones: ProximaRenovacion[];
}
