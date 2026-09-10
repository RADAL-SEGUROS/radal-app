/**
 * GLOBAL-mode content of the single context-switching sidebar.
 *
 * Rendered by `Sidebar.tsx` whenever the route is outside a group. Sections,
 * top to bottom: GRUPOS (<=8 groups with open counts — no nested vigencias, the
 * vigencia level lives in group mode), then five plain rows: Datos, Analitica,
 * Agente, Companias, Configuracion. The user card and tenant chip belong to the
 * `Sidebar` shell, not to either mode.
 *
 * **MAS is gone, and so is the ADMINISTRACION header.** MAS held a flat list of
 * a dozen entity routes (Leads, Expedientes, Clientes, Colocaciones,
 * Cotizaciones, Propuestas, Polizas, Inspecciones, Siniestros, Ofertas) plus two
 * greyed "pronto" stubs. That list answered a question the app now answers
 * better in three places: **rows live in Datos**, **shape lives en Analitica**,
 * and **one account's detail lives inside its grupo**. The routes themselves
 * stay alive — deep links from group pages still resolve — they simply do not
 * need a rail entry each.
 *
 * Two rules still constrain every entry:
 *
 * 1. **The server decides what is visible.** Each item names its RBAC module
 *    and is filtered through `can(perms, module, "View")`. While the matrix is
 *    in flight only Dashboard-gated anchors render, so nothing pops away a
 *    moment later.
 * 2. **No dead buttons.** Every row here resolves to a real route. The two
 *    not-yet-built modules (Finanzas, Reporteria) no longer appear at all
 *    rather than as permanent "pronto" chips: Reporteria IS Analitica now, and
 *    Finanzas has no surface to point at.
 */
import * as React from "react";
import { Link, NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  Bot,
  Building2,
  Database,
  Folder,
  Plus,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { useNavigator } from "@/api/navigator";
import { Skeleton } from "@/components/ui/skeleton";
import {
  can,
  useIsBrokerAdmin,
  usePermissions,
  type PermissionModule,
} from "@/lib/permissions";
import { cn } from "@/lib/utils";

/**
 * Row anatomy (Signal). Quiet rows; the ACTIVE state is a brand-soft tint with
 * brand-deep text — the rail's single accent cue. Exported so the group-mode
 * rail (`GroupSidebar`) renders its rows with the exact same treatment.
 */
export const RAIL_ROW_BASE =
  "flex min-h-[32px] items-center gap-[9px] rounded-sm px-3 text-label transition-[background-color,color,box-shadow] duration-150 ease-out";
export const RAIL_ROW_IDLE =
  "text-ink-2 hover:bg-paper-2 hover:text-ink";
export const RAIL_ROW_ACTIVE = "bg-brand-soft font-medium text-brand-deep";

export function railRowClass(isActive: boolean, className?: string) {
  return cn(RAIL_ROW_BASE, isActive ? RAIL_ROW_ACTIVE : RAIL_ROW_IDLE, className);
}

export function RailSectionLabel({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "px-3 pb-1.5 pt-4 text-caption font-medium text-ink-3",
        className,
      )}
    >
      {children}
    </div>
  );
}

interface NavItem {
  to: string;
  labelKey: string;
  icon: LucideIcon;
  end?: boolean;
  /** RBAC module gating this entry; hidden unless the user has `View` on it. */
  module: PermissionModule;
}

/** Spec §2.2 — the rail shows at most 8 groups. */
const MAX_RAIL_GROUPS = 8;

/** A leaf link. `end` gives exact matching; without it a parent matches by prefix. */
function NavRow({ item, label }: { item: NavItem; label: string }) {
  const Icon = item.icon;
  return (
    <li>
      <NavLink to={item.to} end={item.end} className={({ isActive }) => railRowClass(isActive)}>
        <Icon className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
        <span className="truncate">{label}</span>
      </NavLink>
    </li>
  );
}

/** The open-accounts count: Inter tabular, muted (spec §2.2 row anatomy). */
function CountChip({ count, title }: { count: number; title?: string }) {
  return (
    <span
      title={title}
      className="ml-auto shrink-0 text-[11px] tabular-nums text-ink-3"
    >
      {count}
    </span>
  );
}

export function SidebarGlobal() {
  const { t } = useTranslation(["common", "accounts"]);
  const { isAdmin } = useIsBrokerAdmin();
  const { data: perms, isLoading: permsLoading } = usePermissions();

  /**
   * While the matrix is in flight show nothing rather than a nav that pops
   * items away a moment later. Dashboard always stays as an anchor.
   */
  const gate = React.useCallback(
    (module: PermissionModule) =>
      permsLoading ? module === "Dashboard" : can(perms, module, "View"),
    [perms, permsLoading],
  );

  const canSeeGroups = gate("Groups");
  const canCreateGroup = !permsLoading && can(perms, "Groups", "Create");

  // The rail is the navigator's only consumer here; do not fetch it for a role
  // that cannot see groups at all. (`rail`, not `navigator` — the latter
  // shadows `window.navigator`.)
  const { data: rail, isLoading: railLoading, isError: railFailed } = useNavigator(canSeeGroups);
  const groups = (rail?.groups ?? []).slice(0, MAX_RAIL_GROUPS);


  return (
    <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
      {/* GRUPOS — the front door */}
      {canSeeGroups ? (
        <>
          <RailSectionLabel className="pt-1">{t("nav.groups")}</RailSectionLabel>
          {railLoading && groups.length === 0 ? (
            <div className="flex flex-col gap-1.5 px-3 py-1">
              <Skeleton className="h-5 w-full" />
              <Skeleton className="h-5 w-4/5" />
              <Skeleton className="h-5 w-3/5" />
            </div>
          ) : railFailed && groups.length === 0 ? (
            // Never claim "Sin grupos" when the truth is "we could not ask".
            <p className="px-3 pb-1 text-[11.5px] leading-snug text-text-muted">
              {t("accounts:nav.error")}
            </p>
          ) : groups.length === 0 ? (
            <div className="px-3 pb-1 pt-0.5">
              <p className="text-[12.5px] font-medium text-text-muted">
                {t("accounts:nav.emptyGroups")}
              </p>
              <p className="mt-0.5 text-[11.5px] leading-snug text-text-muted">
                {t("accounts:nav.emptyGroupsHint")}
              </p>
            </div>
          ) : (
            <ul className="flex flex-col gap-0.5">
              {groups.map((group) => (
                <li key={group.id}>
                  {/* A real link: Cmd/Ctrl-click opens the group in a tab. The
                      row can never render active — /groups/:id switches the
                      whole rail into group mode. */}
                  <Link
                    to={`/groups/${group.id}`}
                    title={group.name}
                    className={railRowClass(false)}
                  >
                    <Folder className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
                    <span className="truncate">{group.name}</span>
                    {group.open_count > 0 ? (
                      <CountChip
                        count={group.open_count}
                        title={t("accounts:nav.openAccounts", { count: group.open_count })}
                      />
                    ) : null}
                  </Link>
                </li>
              ))}
            </ul>
          )}

          <ul className="mt-0.5 flex flex-col gap-0.5">
            <li>
              <NavLink
                to="/groups"
                end
                className={({ isActive }) => railRowClass(isActive, "text-[12.5px]")}
              >
                {/* Spacer keeps this aligned with the "+ Nuevo grupo" row. */}
                <span className="h-[15px] w-[15px] shrink-0" aria-hidden />
                <span className="truncate">{t("nav.allGroups")}</span>
              </NavLink>
            </li>
            {canCreateGroup ? (
              <li>
                <NavLink
                  to="/groups/new"
                  className={({ isActive }) => railRowClass(isActive, "text-[12.5px]")}
                >
                  <Plus className="h-[15px] w-[15px] shrink-0" strokeWidth={1.75} />
                  <span className="truncate">{t("nav.newGroup")}</span>
                </NavLink>
              </li>
            ) : null}
          </ul>
        </>
      ) : null}

      {/* Datos · Analítica · Agente · Compañías · Configuración.
          Five plain rows, no section headers: with MÁS gone there is no longer
          a "rest of the app" to fold away. */}
      <ul className="mt-5 flex flex-col gap-0.5">
        {gate("Dashboard") ? (
          <NavRow
            item={{ to: "/data", labelKey: "nav.data", icon: Database, module: "Dashboard" }}
            label={t("nav.data")}
          />
        ) : null}
        {gate("Dashboard") ? (
          <NavRow
            item={{ to: "/analytics", labelKey: "nav.analytics", icon: BarChart3, module: "Dashboard" }}
            label={t("nav.analytics")}
          />
        ) : null}
        {gate("Dashboard") ? (
          <NavRow
            item={{ to: "/agent", labelKey: "nav.agent", icon: Bot, module: "Dashboard" }}
            label={t("nav.agent")}
          />
        ) : null}
        {gate("Insurers") ? (
          <NavRow
            item={{ to: "/insurers", labelKey: "nav.insurersAppetite", icon: Building2, module: "Insurers" }}
            label={t("nav.insurersAppetite")}
          />
        ) : null}
        {/* Configuración is broker_admin only, but it is a normal row now —
            a one-item ADMINISTRACIÓN header was more chrome than content. */}
        {isAdmin ? (
          <li>
            <NavLink to="/settings" className={({ isActive }) => railRowClass(isActive)}>
              <Settings className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
              <span className="truncate">{t("nav.settings")}</span>
            </NavLink>
          </li>
        ) : null}
      </ul>

    </nav>
  );
}
