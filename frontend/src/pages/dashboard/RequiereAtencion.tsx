import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  RefreshCw,
  FileText,
  ShieldAlert,
  ClipboardCheck,
} from "lucide-react";
import { SectionCard, EmptyState } from "./SectionCard";
import type { AtencionTipo, RequiereAtencionItem } from "./types";

const TIPO_ICON: Record<AtencionTipo, React.ReactNode> = {
  renovacion: <RefreshCw className="h-4 w-4" />,
  cotizacion: <FileText className="h-4 w-4" />,
  siniestro: <ShieldAlert className="h-4 w-4" />,
  inspeccion: <ClipboardCheck className="h-4 w-4" />,
};

interface RequiereAtencionProps {
  items?: RequiereAtencionItem[];
}

export function RequiereAtencion({ items }: RequiereAtencionProps) {
  const { t } = useTranslation("dashboard");
  const navigate = useNavigate();
  const list = (items ?? []).slice(0, 3);

  return (
    <SectionCard
      title={t("requiereAtencion.title")}
      icon={<AlertTriangle className="h-4 w-4" />}
    >
      {list.length === 0 ? (
        <EmptyState>{t("requiereAtencion.empty")}</EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {list.map((item) => (
            <li key={`${item.tipo}-${item.id}`}>
              <button
                type="button"
                onClick={() => navigate(item.url)}
                className="flex w-full items-start gap-3 px-4 py-2.5 text-left transition-colors hover:bg-bg-recessed"
              >
                <span className="mt-0.5 shrink-0 text-signal-warn">
                  {TIPO_ICON[item.tipo]}
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-body text-text-primary">
                    {item.titulo}
                  </span>
                  <span className="block truncate text-caption text-text-muted">
                    {item.detalle}
                  </span>
                </span>
                <span className="shrink-0 font-mono text-mono-sm uppercase tracking-wide text-text-muted">
                  {t(`requiereAtencion.tipos.${item.tipo}`)}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  );
}
