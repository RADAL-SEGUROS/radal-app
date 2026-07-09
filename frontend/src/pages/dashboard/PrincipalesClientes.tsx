import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Building2 } from "lucide-react";
import { EstadoBadge } from "@/components/common/EstadoBadge";
import { formatUF } from "@/lib/format";
import { SectionCard, EmptyState } from "./SectionCard";
import type { PrincipalCliente } from "./types";

interface PrincipalesClientesProps {
  items?: PrincipalCliente[];
}

export function PrincipalesClientes({ items }: PrincipalesClientesProps) {
  const { t } = useTranslation("dashboard");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const list = items ?? [];

  return (
    <SectionCard
      title={t("principalesClientes.title")}
      icon={<Building2 className="h-4 w-4" />}
      linkTo="/clientes"
      linkLabel={tc("actions.viewAll")}
    >
      {list.length === 0 ? (
        <EmptyState>{t("principalesClientes.empty")}</EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {list.map((cliente) => (
            <li key={cliente.id}>
              <button
                type="button"
                onClick={() => navigate(`/clientes/${cliente.id}`)}
                className="flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-bg-recessed"
              >
                <span className="min-w-0 flex-1">
                  <span className="flex items-center gap-2">
                    <span className="truncate text-body text-text-primary">
                      {cliente.nombre}
                    </span>
                    <EstadoBadge
                      estado={cliente.estado}
                      label={tc(`estados.${cliente.estado}`, {
                        defaultValue: cliente.estado,
                      })}
                    />
                  </span>
                  <span className="block truncate text-caption text-text-muted">
                    {cliente.sector} ·{" "}
                    {t("principalesClientes.polizas", {
                      count: cliente.polizas_vigentes,
                    })}
                  </span>
                </span>
                <span className="shrink-0 text-right font-mono text-mono text-text-primary tabular-nums">
                  {formatUF(cliente.prima_total_uf, { decimals: 0 })}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
