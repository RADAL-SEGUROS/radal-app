import * as React from "react";
import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  Building2,
  ClipboardCheck,
  FileText,
  LayoutDashboard,
  Receipt,
  RefreshCw,
  ScrollText,
  ShieldAlert,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useTheme } from "@/providers/ThemeProvider";
import { useAuth } from "@/providers/AuthProvider";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  labelKey: string;
  icon: LucideIcon;
  end?: boolean;
}

const gestionItems: NavItem[] = [
  { to: "/", labelKey: "nav.dashboard", icon: LayoutDashboard, end: true },
  { to: "/clientes", labelKey: "nav.clientes", icon: Users },
  { to: "/polizas", labelKey: "nav.polizas", icon: ScrollText },
  { to: "/renovaciones", labelKey: "nav.renovaciones", icon: RefreshCw },
  { to: "/cotizaciones", labelKey: "nav.cotizaciones", icon: FileText },
  { to: "/siniestros", labelKey: "nav.siniestros", icon: ShieldAlert },
  { to: "/inspecciones", labelKey: "nav.inspecciones", icon: ClipboardCheck },
];

const disabledItems: { labelKey: string; icon: LucideIcon }[] = [
  { labelKey: "nav.pipeline", icon: Workflow },
  { labelKey: "nav.facturacion", icon: Receipt },
  { labelKey: "nav.reportes", icon: BarChart3 },
];

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-3 pb-1 pt-4 font-mono text-mono-sm uppercase tracking-wider text-text-muted">
      {children}
    </div>
  );
}

export function Sidebar() {
  const { t } = useTranslation("common");
  const { theme } = useTheme();
  const { user, corredora } = useAuth();

  const logo = theme === "dark" ? "radal-mark-white" : "radal-mark-ink";

  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-line bg-bg-surface">
      {/* Brand */}
      <div className="flex h-16 items-center gap-2 px-4">
        <img
          src={`/brand/${logo}.svg`}
          alt="Radal"
          className="h-7 w-7"
        />
        <div className="flex flex-col leading-tight">
          <span className="wordmark font-display text-h3 text-text-primary">
            Radal.
          </span>
          {corredora?.nombre ? (
            <span className="text-caption text-text-muted">
              {corredora.nombre}
            </span>
          ) : null}
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 overflow-y-auto px-2 pb-4">
        <SectionLabel>{t("nav.sections.gestion")}</SectionLabel>
        <ul className="space-y-0.5">
          {gestionItems.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cn(
                      "relative flex items-center gap-3 rounded-md px-3 py-2 text-label transition-colors",
                      isActive
                        ? "bg-teal-soft font-medium text-teal-deep"
                        : "text-text-secondary hover:bg-bg-recessed",
                    )
                  }
                >
                  {({ isActive }) => (
                    <>
                      {isActive ? (
                        <span className="absolute left-0 top-1/2 h-5 w-[3px] -translate-y-1/2 rounded-full bg-teal" />
                      ) : null}
                      <Icon className="h-4 w-4 shrink-0" />
                      <span className="truncate">{t(item.labelKey)}</span>
                    </>
                  )}
                </NavLink>
              </li>
            );
          })}
        </ul>

        <SectionLabel>{t("nav.sections.comercialOperaciones")}</SectionLabel>
        <TooltipProvider delayDuration={200}>
          <ul className="space-y-0.5">
            {disabledItems.map((item) => {
              const Icon = item.icon;
              return (
                <li key={item.labelKey}>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <div
                        aria-disabled
                        className="flex cursor-not-allowed items-center gap-3 rounded-md px-3 py-2 text-label text-text-muted opacity-45"
                      >
                        <Icon className="h-4 w-4 shrink-0" />
                        <span className="truncate">{t(item.labelKey)}</span>
                      </div>
                    </TooltipTrigger>
                    <TooltipContent side="right">
                      {t("nav.proximamente")}
                    </TooltipContent>
                  </Tooltip>
                </li>
              );
            })}
          </ul>
        </TooltipProvider>
      </nav>

      {/* Bottom: collaborator */}
      {user ? (
        <div className="flex items-center gap-3 border-t border-line px-4 py-3">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-teal-soft">
            <Building2 className="h-4 w-4 text-teal-deep" />
          </div>
          <div className="min-w-0 leading-tight">
            <p className="truncate text-label text-text-primary">
              {user.nombre}
            </p>
            <p className="truncate text-caption text-text-muted">
              {user.cargo}
            </p>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
