/**
 * GLOBAL-mode content of the single context-switching sidebar (spec v4 §2.2).
 *
 * Rendered by `Sidebar.tsx` whenever the route is outside a group. Sections,
 * top to bottom: GRUPOS (≤8 groups with open counts — no nested vigencias, the
 * vigencia level lives in group mode now), Analítica, Agente, Compañías, the
 * collapsed MÁS section, and Administración. The user card and tenant chip
 * belong to the `Sidebar` shell, not to either mode.
 *
 * Two rules constrain every entry:
 *
 * 1. **The server decides what is visible.** Each item names its RBAC module
 *    and is filtered through `can(perms, module, "View")`. While the matrix is
 *    in flight only Dashboard-gated anchors render, so nothing pops away a
 *    moment later.
 * 2. **No dead buttons.** Routes that do not exist yet (Finanzas, Reportería)
 *    live INSIDE the collapsed MÁS section, greyed with the "pronto" chip —
 *    visible, never dead. The old COMERCIAL/Cartera/AI entries are replaced by
 *    Analítica and the single Agente entry (decisions 4 + 5); their routes keep
 *    working and stay one click away under MÁS.
 */
import * as React from "react";
import { Link, NavLink } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  BarChart3,
  Bot,
  Briefcase,
  Building2,
  ChevronRight,
  ClipboardCheck,
  FileText,
  Folder,
  Plus,
  Send,
  Settings,
  Share2,
  Shield,
  ShieldAlert,
  Sparkles,
  Users,
  Wallet,
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
import { NAV_EXPANDED_STORAGE_KEY, navKeys, useExpanded } from "@/lib/treeState";
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

/**
 * MÁS — collapsed by default, persisted under `navKeys.section("more")`. Every
 * route the old flat sections carried stays exactly one click away instead of
 * disappearing (rule 3: nothing is hidden without a path). The one exception,
 * decided in v5: the legacy v1 `/dashboard` is superseded by Analítica — its
 * route stays alive but it gets no nav row.
 */
const moreItems: NavItem[] = [
  { to: "/leads", labelKey: "nav.leads", icon: Sparkles, module: "Leads" },
  { to: "/cases", labelKey: "nav.cases", icon: Folder, module: "CaseFiles" },
  { to: "/clients", labelKey: "nav.clients", icon: Users, module: "Clients" },
  { to: "/placements", labelKey: "nav.placements", icon: Briefcase, module: "Placements" },
  { to: "/quotes", labelKey: "nav.quotes", icon: FileText, module: "Quotes" },
  { to: "/proposals", labelKey: "nav.proposals", icon: Send, module: "Proposals" },
  { to: "/policies", labelKey: "nav.policies", icon: Shield, module: "Policies" },
  { to: "/inspections", labelKey: "nav.inspections", icon: ClipboardCheck, module: "Inspections" },
  { to: "/claims", labelKey: "nav.claims", icon: ShieldAlert, module: "Claims" },
  { to: "/offerings", labelKey: "nav.offerings", icon: Share2, module: "Offerings" },
];

/** Not built yet — greyed inside MÁS with the "pronto" chip, never a dead link. */
const disabledItems: { labelKey: string; icon: LucideIcon }[] = [
  { labelKey: "nav.finance", icon: Wallet },
  { labelKey: "accounts:nav.reports", icon: BarChart3 },
];

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
  const nav = useExpanded(NAV_EXPANDED_STORAGE_KEY);

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

  const more = moreItems.filter((item) => gate(item.module));
  const moreKey = navKeys.section("more");
  const moreOpen = nav.isExpanded(moreKey);

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

      {/* Analítica · Agente · Compañías — the three global anchors */}
      <ul className="mt-5 flex flex-col gap-0.5">
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
      </ul>

      {/* MÁS — collapsed by default, persisted per user */}
      {more.length > 0 ? (
        <div className="mt-5">
          <button
            type="button"
            aria-expanded={moreOpen}
            onClick={() => nav.toggle(moreKey)}
            className="flex w-full items-center gap-1.5 rounded-sm px-3 pb-1.5 pt-1 text-caption font-medium text-ink-3 transition-[color] duration-150 ease-out hover:text-ink"
          >
            <ChevronRight
              className={cn(
                "h-3 w-3 shrink-0 transition-transform duration-150",
                moreOpen && "rotate-90",
              )}
              strokeWidth={2}
            />
            <span>{t("nav.more")}</span>
          </button>
          {moreOpen ? (
            <ul className="flex flex-col gap-0.5">
              {more.map((item) => (
                <NavRow key={item.to} item={item} label={t(item.labelKey)} />
              ))}
              {disabledItems.map((item) => {
                const Icon = item.icon;
                return (
                  <li key={item.labelKey}>
                    <div
                      aria-disabled
                      className="flex min-h-[32px] cursor-not-allowed items-center gap-[9px] rounded-sm px-3 text-label text-ink-3 opacity-70"
                    >
                      <Icon className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
                      <span className="truncate">{t(item.labelKey)}</span>
                      <span className="ml-auto rounded-md border border-line px-1.5 py-0.5 text-[10px] font-medium text-ink-3">
                        {t("nav.soon")}
                      </span>
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : null}
        </div>
      ) : null}

      {/* Administración (broker_admin only) */}
      {isAdmin ? (
        <>
          <RailSectionLabel className="mt-2">
            {t("nav.sections.administration")}
          </RailSectionLabel>
          <ul className="flex flex-col gap-0.5">
            <li>
              <NavLink to="/settings" className={({ isActive }) => railRowClass(isActive)}>
                <Settings className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
                <span className="truncate">{t("nav.settings")}</span>
              </NavLink>
            </li>
          </ul>
        </>
      ) : null}
    </nav>
  );
}
