/**
 * GLOBAL-mode content of the single context-switching sidebar.
 *
 * Rendered by `Sidebar.tsx` whenever the route is outside a group. Order, top to
 * bottom (v12, `docs/v12/07-frontend.md` §1):
 *
 *   Agente · Pólizas · Cobranza (overdue-cuotas chip) · ASEGURADOS (<=8 groups
 *   with open counts) · Portafolio · Analítica · Cotizar en portales (pronto
 *   while `GET /meta/features` says so) · Compañías · Configuración · Mantenedor
 *
 * **The copilot leads** (copilot pass): the broker's home is the agent chat
 * (`/agent`), which can drive most tasks. Pólizas follows — journeys are
 * partial and a broker often starts from a policy PDF — then Cobranza, because
 * the overdue count is the one number a broker checks every morning. The user card and tenant chip belong to the
 * `Sidebar` shell, not to either mode.
 *
 * Rules that constrain every entry:
 *
 * 1. **The server decides what is visible.** Each item names its RBAC module
 *    and is filtered through `can(perms, module, "View")`. While the matrix is
 *    in flight only Dashboard-gated anchors render, so nothing pops away a
 *    moment later.
 * 2. **No dead buttons.** Every row resolves to a real route — or, for a
 *    feature the server flags as not ready (portal search), renders visibly
 *    disabled with a "pronto" chip and the server's own reason.
 * 3. **A platform user has no broker workspace.** `user_type` comes from the
 *    same matrix response; for a platform user the broker rows — and the two
 *    broker-scoped fetches behind them (navigator, collections summary) — stay
 *    off, and the rail shows the maintainer console. Nothing 403s on render.
 * 4. **The overdue chip tolerates a missing endpoint.** `GET
 *    /collections/summary` may 404/422 or omit `overdue` on an older backend;
 *    the chip then simply does not render. It never shows a guessed number.
 */
import * as React from "react";
import { Link, NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart3,
  Bot,
  Building2,
  Database,
  Folder,
  Globe,
  Plus,
  Settings,
  ShieldCheck,
  Wallet,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import api from "@/lib/api";
import { qk } from "@/api/keys";
import { useNavigator } from "@/api/navigator";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { DisabledHint } from "@/components/common/kit";
import { useFeature } from "@/api/meta";
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
function NavRow({
  item,
  label,
  trailing,
  onClick,
}: {
  item: NavItem;
  label: string;
  /** Right-aligned extra, e.g. the Cobranza overdue chip. */
  trailing?: React.ReactNode;
  onClick?: () => void;
}) {
  const Icon = item.icon;
  return (
    <li>
      <NavLink
        to={item.to}
        end={item.end}
        onClick={onClick}
        className={({ isActive }) => railRowClass(isActive)}
      >
        <Icon className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
        <span className="truncate">{label}</span>
        {trailing}
      </NavLink>
    </li>
  );
}

/**
 * The slice of `GET /collections/summary` the rail reads. Every field optional
 * on purpose: the endpoint lands with lane B2, and until then (or on an older
 * backend) the response may be absent or shaped differently.
 */
interface CollectionsSummaryLike {
  overdue?: { count?: number | null } | null;
}

/** Overdue-installments count for the Cobranza row, or `null` when unknown. */
function useOverdueCount(enabled: boolean): number | null {
  const { data } = useQuery({
    queryKey: qk.collections.summary(),
    enabled,
    staleTime: 60 * 1000,
    retry: false,
    queryFn: async () => {
      const { data } = await api.get<CollectionsSummaryLike>("/collections/summary");
      return data;
    },
  });
  const count = data?.overdue?.count;
  return typeof count === "number" && Number.isFinite(count) ? count : null;
}

/** Overdue cuotas: a negative-tone pill — the rail's only alarm colour. */
function OverdueChip({ count, title }: { count: number; title: string }) {
  return (
    <span
      title={title}
      aria-label={title}
      className="ml-auto shrink-0 rounded-full bg-neg-soft px-1.5 text-[11px] font-medium tabular-nums leading-[18px] text-neg-text"
    >
      {count}
    </span>
  );
}

/**
 * A row for a feature the server says is not ready: muted, not a link, a
 * "pronto" chip, and the server's reason in the tooltip (focusable, so it is
 * reachable by keyboard and by a tap on touch).
 */
function SoonRow({
  icon: Icon,
  label,
  reason,
  soon,
}: {
  icon: LucideIcon;
  label: string;
  reason: string;
  soon: string;
}) {
  return (
    <li>
      <DisabledHint hint={reason} className="w-full">
        <span
          role="link"
          aria-disabled="true"
          aria-label={`${label} · ${reason}`}
          className={cn(RAIL_ROW_BASE, "w-full cursor-not-allowed text-ink-3")}
        >
          <Icon className="h-[16px] w-[16px] shrink-0 opacity-60" strokeWidth={1.5} />
          <span className="truncate opacity-70">{label}</span>
          <Badge variant="muted" className="ml-auto shrink-0 px-1.5 py-0 text-[10.5px]">
            {soon}
          </Badge>
        </span>
      </DisabledHint>
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

export function SidebarGlobal({
  onEnterAgent,
}: {
  /** The Agente row: tells the shell to return to agent mode (the user may be
      ON `/agent` already, having asked for the main menu). */
  onEnterAgent?: () => void;
} = {}) {
  const { t } = useTranslation(["common", "accounts"]);
  const portal = useFeature("portal_search");
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

  // A platform user carries no broker: every workspace row (and its fetch)
  // stays off, and only the maintainer console is offered. `user_type` is the
  // server's own answer, not a role guess.
  const isPlatformUser = perms?.user_type === "platform";
  const ws = (module: PermissionModule) => !isPlatformUser && gate(module);

  const canSeeGroups = ws("Groups");
  const canCreateGroup = canSeeGroups && can(perms, "Groups", "Create");
  const canSeeCollections = ws("Collections");
  const overdue = useOverdueCount(canSeeCollections);

  // The rail is the navigator's only consumer here; do not fetch it for a role
  // that cannot see groups at all. (`rail`, not `navigator` — the latter
  // shadows `window.navigator`.)
  const { data: rail, isLoading: railLoading, isError: railFailed } = useNavigator(canSeeGroups);
  const groups = (rail?.groups ?? []).slice(0, MAX_RAIL_GROUPS);


  return (
    <nav className="min-h-0 flex-1 overflow-y-auto px-3 pb-3">
      {/* Agente (the home) · Pólizas · Cobranza. */}
      {ws("Dashboard") || ws("Policies") || canSeeCollections ? (
        <ul className="flex flex-col gap-0.5 pt-1">
          {ws("Dashboard") ? (
            <NavRow
              item={{ to: "/agent", labelKey: "nav.agent", icon: Bot, module: "Dashboard" }}
              label={t("nav.agent")}
              onClick={onEnterAgent}
            />
          ) : null}
          {ws("Policies") ? (
            <NavRow
              item={{ to: "/policies", labelKey: "nav.policies", icon: ShieldCheck, module: "Policies" }}
              label={t("nav.policies")}
            />
          ) : null}
          {canSeeCollections ? (
            <NavRow
              item={{ to: "/collections", labelKey: "nav.collections", icon: Wallet, module: "Collections" }}
              label={t("nav.collections")}
              trailing={
                overdue !== null && overdue > 0 ? (
                  <OverdueChip
                    count={overdue}
                    title={t("nav.overdueInstallments", { count: overdue })}
                  />
                ) : null
              }
            />
          ) : null}
        </ul>
      ) : null}

      {/* ASEGURADOS — the groups block, unchanged */}
      {canSeeGroups ? (
        <>
          <RailSectionLabel>{t("nav.groups")}</RailSectionLabel>
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

      {/* Portafolio · Analítica · Cotizar en portales · Compañías ·
          Configuración · Mantenedor. Plain rows, no section headers: with MÁS
          gone there is no longer a "rest of the app" to fold away. */}
      <ul className="mt-5 flex flex-col gap-0.5">
        {ws("Dashboard") ? (
          <NavRow
            item={{ to: "/data", labelKey: "nav.data", icon: Database, module: "Dashboard" }}
            label={t("nav.data")}
          />
        ) : null}
        {ws("Dashboard") ? (
          <NavRow
            item={{ to: "/analytics", labelKey: "nav.analytics", icon: BarChart3, module: "Dashboard" }}
            label={t("nav.analytics")}
          />
        ) : null}
        {ws("Integrations") ? (
          portal.enabled ? (
            <NavRow
              item={{ to: "/portal-search", labelKey: "nav.portalSearch", icon: Globe, module: "Integrations" }}
              label={t("nav.portalSearch")}
            />
          ) : (
            <SoonRow
              icon={Globe}
              label={t("nav.portalSearch")}
              reason={portal.reason ?? t("nav.portalSearchSoonReason")}
              soon={t("nav.soon")}
            />
          )
        ) : null}
        {ws("Insurers") ? (
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
        {/* Mantenedor — the Radal console. No broker role holds Platform.View,
            so this row exists only for platform users. */}
        {!permsLoading && can(perms, "Platform", "View") ? (
          <NavRow
            item={{ to: "/platform", labelKey: "nav.platform", icon: Wrench, module: "Platform" }}
            label={t("nav.platform")}
          />
        ) : null}
      </ul>

    </nav>
  );
}
