import { useQuery } from "@tanstack/react-query";
import api from "@/lib/api";
import { GreetingHeader } from "./GreetingHeader";
import { KpiGrid } from "./KpiGrid";
import { QuickActions } from "./QuickActions";
import { RequiereAtencion } from "./RequiereAtencion";
import { PrincipalesClientes } from "./PrincipalesClientes";
import { ActividadReciente } from "./ActividadReciente";
import { ProximasRenovaciones } from "./ProximasRenovaciones";
import type { DashboardPayload } from "./types";

export default function DashboardPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: async () => {
      const res = await api.get<DashboardPayload>("/dashboard");
      return res.data;
    },
  });

  return (
    // No-scroll command center: fills the viewport, panels scroll internally.
    <div className="flex h-full min-h-0 flex-col gap-4">
      <header className="flex shrink-0 flex-col gap-1">
        <GreetingHeader saludo={data?.saludo} loading={isLoading} />
      </header>

      <div className="shrink-0">
        <KpiGrid kpis={data?.kpis} loading={isLoading} />
      </div>

      <div className="shrink-0">
        <QuickActions />
      </div>

      {/* Two rows of panels fill remaining height; each panel scrolls internally. */}
      <div className="grid min-h-0 flex-1 grid-cols-1 gap-4 lg:grid-cols-3 lg:grid-rows-2">
        <div className="min-h-0 lg:col-span-2">
          <RequiereAtencion items={data?.requiere_atencion} />
        </div>
        <div className="min-h-0 lg:row-span-2">
          <ProximasRenovaciones items={data?.proximas_renovaciones} />
        </div>
        <div className="min-h-0">
          <PrincipalesClientes items={data?.principales_clientes} />
        </div>
        <div className="min-h-0">
          <ActividadReciente initial={data?.actividad_reciente} />
        </div>
      </div>
    </div>
  );
}
