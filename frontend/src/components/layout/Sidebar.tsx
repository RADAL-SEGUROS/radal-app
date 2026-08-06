import * as React from "react";
import { NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  Briefcase,
  Building2,
  ClipboardCheck,
  FileText,
  LayoutGrid,
  Receipt,
  RefreshCw,
  Send,
  Settings,
  Shield,
  ShieldAlert,
  Users,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { useTheme } from "@/providers/ThemeProvider";
import { useAuth } from "@/providers/AuthProvider";
import {
  can,
  useIsBrokerAdmin,
  usePermissions,
  type PermissionModule,
} from "@/lib/permissions";
import { cn } from "@/lib/utils";

interface NavItem {
  to: string;
  labelKey: string;
  icon: LucideIcon;
  end?: boolean;
  /** RBAC module gating this entry; hidden unless the user has `View` on it. */
  module: PermissionModule;
}

/**
 * Only routes that exist are listed here. A nav entry whose page has not been
 * built yet lives in `disabledItems` and renders greyed with a "pronto" chip —
 * never a link that bounces the user back to the dashboard.
 *
 * Entries are additionally filtered by the server's permission matrix: a
 * `broker_inspector` has `Clients:View = no`, so that link must not render at
 * all — following it would only produce a 403.
 */
const gestionItems: NavItem[] = [
  { to: "/", labelKey: "nav.dashboard", icon: LayoutGrid, end: true, module: "Dashboard" },
  { to: "/clients", labelKey: "nav.clients", icon: Users, module: "Clients" },
  { to: "/placements", labelKey: "nav.placements", icon: Briefcase, module: "Placements" },
  { to: "/quotes", labelKey: "nav.quotes", icon: FileText, module: "Quotes" },
  { to: "/proposals", labelKey: "nav.proposals", icon: Send, module: "Proposals" },
  { to: "/inspections", labelKey: "nav.inspections", icon: ClipboardCheck, module: "Inspections" },
  { to: "/insurers", labelKey: "nav.insurers", icon: Building2, module: "Insurers" },
  { to: "/offerings", labelKey: "nav.offerings", icon: Shield, module: "Offerings" },
];

const disabledItems: { labelKey: string; icon: LucideIcon }[] = [
  { labelKey: "nav.policies", icon: Shield },
  { labelKey: "nav.claims", icon: ShieldAlert },
  { labelKey: "nav.renewals", icon: RefreshCw },
  { labelKey: "nav.pipeline", icon: Workflow },
  { labelKey: "nav.billing", icon: Receipt },
  { labelKey: "nav.reports", icon: BarChart3 },
];

function SectionLabel({ children, className }: { children: React.ReactNode; className?: string }) {
  return (
    <div
      className={cn(
        "px-3 pb-2.5 text-[10.5px] font-semibold uppercase tracking-[0.11em] text-text-muted",
        className,
      )}
    >
      {children}
    </div>
  );
}

function initials(name?: string) {
  if (!name) return "R";
  return (
    name
      .replace(/[^A-Za-zÁÉÍÓÚÑáéíóúñ ]/g, "")
      .trim()
      .split(/\s+/)
      .slice(0, 2)
      .map((w) => w[0]?.toUpperCase())
      .join("") || "R"
  );
}

export function Sidebar() {
  const { t } = useTranslation("common");
  const { theme } = useTheme();
  const { user, organization } = useAuth();
  const { isAdmin } = useIsBrokerAdmin();
  const { data: perms, isLoading: permsLoading } = usePermissions();

  // While the matrix is in flight show nothing rather than a nav that pops
  // items away a moment later. Dashboard always stays as an anchor.
  const visibleItems = permsLoading
    ? gestionItems.filter((i) => i.module === "Dashboard")
    : gestionItems.filter((i) => can(perms, i.module, "View"));

  const logo = theme === "dark" ? "radal-mark-white" : "radal-mark-teal";
  const tenant =
    organization?.trade_name ?? organization?.legal_name ?? t("app.name");

  return (
    <aside className="sticky top-0 flex h-screen w-[266px] shrink-0 flex-col self-start border-r border-line bg-bg-sidebar px-4 pb-[18px] pt-[22px] transition-colors duration-300">
      {/* Brand */}
      <div className="flex items-center gap-[11px] px-2">
        <img src={`/brand/${logo}.svg`} alt="Radal" className="h-7 w-7" />
        <span className="wordmark font-display text-[21px] font-medium tracking-[-0.02em] text-text-primary">
          Radal.
        </span>
      </div>

      {/* Tenant pill */}
      <div className="mx-2 mb-5 mt-4 flex items-center gap-[9px] rounded-[10px] border border-line bg-[color-mix(in_srgb,var(--teal)_7%,transparent)] px-[11px] py-2">
        <span className="h-1.5 w-1.5 rounded-full bg-teal shadow-[0_0_0_3px_color-mix(in_srgb,var(--teal)_22%,transparent)]" />
        <span className="truncate text-[11px] font-semibold uppercase tracking-[0.14em] text-text-tertiary">
          {tenant}
        </span>
      </div>

      {/* Nav — Gestión */}
      <nav className="flex-1 overflow-y-auto">
        <SectionLabel>{t("nav.sections.management")}</SectionLabel>
        <ul className="flex flex-col gap-0.5">
          {visibleItems.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-[11px] rounded-[10px] px-3 py-[9px] text-label transition-colors duration-150",
                      isActive
                        ? "bg-[color-mix(in_srgb,var(--teal)_14%,transparent)] font-semibold text-teal-deep"
                        : "font-medium text-text-tertiary hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] hover:text-text-primary",
                    )
                  }
                >
                  <Icon className="h-[19px] w-[19px] shrink-0" strokeWidth={1.75} />
                  <span className="truncate">{t(item.labelKey)}</span>
                </NavLink>
              </li>
            );
          })}
        </ul>

        {/* Nav — Comercial y operaciones (disabled) */}
        <SectionLabel className="mt-6">
          {t("nav.sections.commercialOperations")}
        </SectionLabel>
        <ul className="flex flex-col gap-0.5">
          {disabledItems.map((item) => {
            const Icon = item.icon;
            return (
              <li key={item.labelKey}>
                <div
                  aria-disabled
                  className="flex cursor-not-allowed items-center gap-[11px] rounded-[10px] px-3 py-[9px] text-label font-medium text-text-muted opacity-70"
                >
                  <Icon className="h-[19px] w-[19px] shrink-0" strokeWidth={1.75} />
                  <span className="truncate">{t(item.labelKey)}</span>
                  <span className="ml-auto rounded-[5px] border border-line px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-[0.05em] text-text-muted">
                    {t("nav.soon")}
                  </span>
                </div>
              </li>
            );
          })}
        </ul>

        {/* Nav — Administración (admin_corredora only) */}
        {isAdmin ? (
          <>
            <SectionLabel className="mt-6">
              {t("nav.sections.administration")}
            </SectionLabel>
            <ul className="flex flex-col gap-0.5">
              <li>
                <NavLink
                  to="/settings"
                  className={({ isActive }) =>
                    cn(
                      "flex items-center gap-[11px] rounded-[10px] px-3 py-[9px] text-label transition-colors duration-150",
                      isActive
                        ? "bg-[color-mix(in_srgb,var(--teal)_14%,transparent)] font-semibold text-teal-deep"
                        : "font-medium text-text-tertiary hover:bg-[color-mix(in_srgb,var(--ink)_5%,transparent)] hover:text-text-primary",
                    )
                  }
                >
                  <Settings className="h-[19px] w-[19px] shrink-0" strokeWidth={1.75} />
                  <span className="truncate">{t("nav.settings")}</span>
                </NavLink>
              </li>
            </ul>
          </>
        ) : null}
      </nav>

      {/* Bottom: user card */}
      {user ? (
        <div className="mt-4 flex items-center gap-2.5 rounded-xl border border-line bg-bg-surface p-[9px]">
          <div className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[9px] bg-gradient-to-br from-teal to-blue font-display text-[13px] font-medium text-white">
            {initials(user.full_name)}
          </div>
          <div className="min-w-0 leading-[1.3]">
            <p className="truncate text-[13px] font-semibold text-text-primary">
              {user.full_name}
            </p>
            <p className="truncate text-[11.5px] text-text-muted">
              {user.job_title ?? user.role}
            </p>
          </div>
        </div>
      ) : null}
    </aside>
  );
}
