import * as React from "react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Activity, ChevronDown } from "lucide-react";
import api from "@/lib/api";
import { cn } from "@/lib/utils";
import { formatDateTime } from "@/lib/format";
import { SectionCard, EmptyState } from "./SectionCard";
import type { ActividadItem } from "./types";

/** Role -> dot color per design-system §6.4. */
function rolDotClass(rol: string): string {
  if (rol.includes("aseguradora")) return "bg-blue";
  if (rol.includes("asegurado")) return "bg-lime";
  // corredora roles (admin/ejecutivo/inspector) + fallback
  return "bg-teal";
}

interface ActividadRecienteProps {
  /** Last 2 activities from the main dashboard payload. */
  initial?: ActividadItem[];
}

function ActivityRow({ item }: { item: ActividadItem }) {
  return (
    <li className="flex items-start gap-3 px-4 py-2.5">
      <span
        className={cn(
          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
          rolDotClass(item.rol),
        )}
        aria-hidden
      />
      <span className="min-w-0 flex-1">
        <span className="block text-body text-text-primary">
          <span className="font-medium">{item.accion}</span>
          {item.descripcion ? (
            <span className="text-text-secondary"> — {item.descripcion}</span>
          ) : null}
        </span>
        <span className="block truncate text-caption text-text-muted">
          {item.usuario} · {formatDateTime(item.created_at)}
        </span>
      </span>
    </li>
  );
}

export function ActividadReciente({ initial }: ActividadRecienteProps) {
  const { t } = useTranslation("dashboard");
  const [expanded, setExpanded] = React.useState(false);

  // Fetch last 10 only once expanded.
  const { data: full } = useQuery({
    queryKey: ["dashboard", "actividad", 10],
    enabled: expanded,
    queryFn: async () => {
      const res = await api.get("/dashboard", { params: { limit: 10 } });
      return (res.data?.actividad_reciente ?? []) as ActividadItem[];
    },
  });

  const base = initial ?? [];
  const items = expanded ? (full ?? base) : base.slice(0, 2);
  const canExpand = base.length >= 2;

  return (
    <SectionCard
      title={t("actividadReciente.title")}
      icon={<Activity className="h-4 w-4" />}
    >
      {items.length === 0 ? (
        <EmptyState>{t("actividadReciente.empty")}</EmptyState>
      ) : (
        <div className="flex h-full flex-col">
          <ul className="min-h-0 flex-1 divide-y divide-line overflow-y-auto">
            {items.map((item) => (
              <ActivityRow key={item.id} item={item} />
            ))}
          </ul>
          {canExpand ? (
            <button
              type="button"
              onClick={() => setExpanded((v) => !v)}
              className="flex shrink-0 items-center justify-center gap-1 border-t border-line py-2 text-label text-blue transition-colors hover:bg-bg-recessed hover:text-blue-deep"
            >
              {expanded
                ? t("actividadReciente.verMenos")
                : t("actividadReciente.verMas")}
              <ChevronDown
                className={cn(
                  "h-4 w-4 transition-transform",
                  expanded && "rotate-180",
                )}
              />
            </button>
          ) : null}
        </div>
      )}
    </SectionCard>
  );
}
