/**
 * The single context-switching sidebar (spec v4 §2, geometry v5).
 *
 * One rail with two context modes decided by the URL:
 *
 *  - **GLOBAL** (any route outside a group): `SidebarGlobal` — groups,
 *    Analítica, Agente, Compañías, the collapsed MÁS section, Administración.
 *  - **GROUP** (`/groups/:id/**`): `GroupSidebar` — slim since v5: the back
 *    affordance, the group name, Resumen, one row per vigencia, pinned
 *    Analítica. The tree itself lives in the central view now.
 *
 * **The no-remount guarantee lives HERE** (it used to live in `GroupShell`):
 * the `Sidebar` itself sits in `AppShell`, above the router's page swaps, so
 * it never remounts; and `GroupSidebar` is keyed by `groupId` only —
 * navigating *within* a group changes props, not identity, so its queries and
 * scroll position survive. Do not add a location-derived key to this
 * component or to the group-mode child.
 *
 * Mode detection is a pathname match, not `useParams` — the sidebar renders
 * outside the route tree, so params are not available here. `/groups` and
 * `/groups/new` stay GLOBAL.
 *
 * **Geometry (v5).** On desktop the rail is user-adjustable: its width is
 * dragged on a 3px hit area along the right edge (clamped 200–360, default
 * 264, persisted under `radal.sidebar.width` via `src/lib/sidebarState.ts`),
 * and a chevron in the header collapses it to a 56px icon rail (brand mark +
 * tooltipped icons, persisted under `radal.sidebar.collapsed`). The mobile
 * sheet (`variant="sheet"`) always renders full-width and expanded — width
 * and collapse are desktop affordances only.
 *
 * Below `lg` this same component renders inside the `AppShell` sheet, so the
 * mobile drawer is mode-aware too — one nav, one source of truth for what a
 * role may see (`GroupShell` no longer owns a rail or a sheet of its own).
 *
 * The shell carries only what both modes share: the brand row, the quiet
 * tenant row (the tenant is the constant, so it stays in group mode) and the
 * user card. The desktop rail is `sticky top-0 h-screen` (set by `AppShell`);
 * the scroll happens inside each mode's own content, never on the shell.
 */
import * as React from "react";
import { NavLink, useLocation } from "react-router-dom";
import { AnimatePresence, motion, useReducedMotion } from "framer-motion";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  BarChart3,
  Bot,
  Building2,
  Folder,
  FolderOpen,
  PanelLeftClose,
  PanelLeftOpen,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { useTheme } from "@/providers/ThemeProvider";
import { useAuth } from "@/providers/AuthProvider";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { can, useIsBrokerAdmin, usePermissions, type PermissionModule } from "@/lib/permissions";
import {
  SIDEBAR_COLLAPSED_WIDTH,
  SIDEBAR_MAX_WIDTH,
  SIDEBAR_MIN_WIDTH,
  useSidebarState,
} from "@/lib/sidebarState";
import { SidebarGlobal } from "./SidebarGlobal";
import { GroupSidebar } from "@/components/groups/GroupSidebar";
import { cn } from "@/lib/utils";

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

/** Signal icon-button recipe for the rail chrome (collapse/expand chevrons). */
const railIconBtn =
  "grid h-7 w-7 shrink-0 place-items-center rounded-sm text-ink-3 transition-[background-color,color,transform] duration-150 ease-out hover:bg-paper-2 hover:text-ink active:scale-[0.98]";

/** One tooltipped icon row of the 56px collapsed rail. */
function CollapsedRow({
  to,
  icon: Icon,
  label,
  end,
}: {
  to: string;
  icon: LucideIcon;
  label: string;
  end?: boolean;
}) {
  return (
    <li>
      <Tooltip>
        <TooltipTrigger asChild>
          <NavLink
            to={to}
            end={end}
            aria-label={label}
            className={({ isActive }) =>
              cn(
                "grid h-9 w-9 place-items-center rounded-sm transition-[background-color,color] duration-150 ease-out",
                isActive
                  ? "bg-brand-soft text-brand-deep"
                  : "text-ink-2 hover:bg-paper-2 hover:text-ink",
              )
            }
          >
            <Icon className="h-[17px] w-[17px]" strokeWidth={1.5} />
          </NavLink>
        </TooltipTrigger>
        <TooltipContent side="right">{label}</TooltipContent>
      </Tooltip>
    </li>
  );
}

/**
 * The collapsed rail's nav — mode-aware like the expanded one, gated by the
 * same server matrix (never hardcoded roles). Global mode shows the anchors;
 * group mode shows the way back, the group overview and Analítica.
 */
function CollapsedNav({ groupId }: { groupId: number | null }) {
  const { t } = useTranslation("common");
  const { isAdmin } = useIsBrokerAdmin();
  const { data: perms, isLoading: permsLoading } = usePermissions();

  // Same rule as `SidebarGlobal`: while the matrix is in flight only
  // Dashboard-gated anchors render, so nothing pops away a moment later.
  const gate = (module: PermissionModule) =>
    permsLoading ? module === "Dashboard" : can(perms, module, "View");

  if (groupId !== null) {
    return (
      <ul className="flex flex-col items-center gap-1">
        <CollapsedRow to="/groups" end icon={ArrowLeft} label={t("nav.backToGroups")} />
        <CollapsedRow
          to={`/groups/${groupId}`}
          end
          icon={FolderOpen}
          label={t("sidebar.overview")}
        />
        {gate("Dashboard") ? (
          <CollapsedRow to="/analytics" icon={BarChart3} label={t("nav.analytics")} />
        ) : null}
      </ul>
    );
  }

  return (
    <ul className="flex flex-col items-center gap-1">
      {gate("Groups") ? (
        <CollapsedRow to="/groups" icon={Folder} label={t("nav.groups")} />
      ) : null}
      {gate("Dashboard") ? (
        <CollapsedRow to="/analytics" icon={BarChart3} label={t("nav.analytics")} />
      ) : null}
      {gate("Dashboard") ? (
        <CollapsedRow to="/agent" icon={Bot} label={t("nav.agent")} />
      ) : null}
      {gate("Insurers") ? (
        <CollapsedRow to="/insurers" icon={Building2} label={t("nav.insurersAppetite")} />
      ) : null}
      {isAdmin ? (
        <CollapsedRow to="/settings" icon={Settings} label={t("nav.settings")} />
      ) : null}
    </ul>
  );
}

export interface SidebarProps {
  /** Extra classes for the shell — how the desktop aside and the mobile sheet
      differ. Everything inside is identical, on purpose: one nav, one source of
      truth for what a role may see. Closing the sheet on navigation is handled
      by `AppShell` watching the location, so no `onNavigate` has to be threaded
      through every link in here. */
  className?: string;
  /** `"sheet"` = the mobile drawer: always full-width, always expanded, no
      resize handle. `"desktop"` (default) reads `useSidebarState`. */
  variant?: "desktop" | "sheet";
}

export function Sidebar({ className, variant = "desktop" }: SidebarProps = {}) {
  const { t } = useTranslation("common");
  const { theme } = useTheme();
  const { user, organization } = useAuth();
  const { pathname } = useLocation();
  const reduce = useReducedMotion();
  const rail = useSidebarState();

  // Live only during an edge drag; kills the width transition so the rail
  // tracks the pointer 1:1 instead of chasing it.
  const [dragging, setDragging] = React.useState(false);
  const dragOrigin = React.useRef({ x: 0, width: 0 });

  const isSheet = variant === "sheet";
  const collapsed = !isSheet && rail.collapsed;

  // "/groups" and "/groups/new" stay GLOBAL; only a numeric id enters group mode.
  const match = pathname.match(/^\/groups\/(\d+)(\/|$)/);
  const groupId = match ? Number(match[1]) : null;
  const modeKey = groupId === null ? "global" : `group-${groupId}`;

  const logo = theme === "dark" ? "radal-mark-white" : "radal-mark-teal";
  const tenant = organization?.trade_name ?? organization?.legal_name ?? t("app.name");

  const onHandlePointerDown = (event: React.PointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    dragOrigin.current = { x: event.clientX, width: rail.width };
    setDragging(true);
    event.currentTarget.setPointerCapture(event.pointerId);
  };
  const onHandlePointerMove = (event: React.PointerEvent<HTMLDivElement>) => {
    if (!dragging) return;
    rail.setWidth(dragOrigin.current.width + (event.clientX - dragOrigin.current.x));
  };
  const onHandlePointerUp = (event: React.PointerEvent<HTMLDivElement>) => {
    setDragging(false);
    event.currentTarget.releasePointerCapture(event.pointerId);
  };

  return (
    <aside
      className={cn(
        "relative flex shrink-0 flex-col border-r border-line bg-bg-sidebar pb-[18px]",
        !isSheet && !dragging && "transition-[width] duration-150 ease-out",
        isSheet && "w-full",
        className,
      )}
      style={
        isSheet
          ? undefined
          : { width: collapsed ? SIDEBAR_COLLAPSED_WIDTH : rail.width }
      }
    >
      {/* Brand — mark only when collapsed; the chevron lives in this header. */}
      {collapsed ? (
        <div className="flex flex-col items-center gap-2 pt-[22px]">
          <img src={`/brand/${logo}.svg`} alt="Radal" className="h-7 w-7" />
          <Tooltip>
            <TooltipTrigger asChild>
              <button
                type="button"
                aria-label={t("sidebar.expand")}
                onClick={() => rail.setCollapsed(false)}
                className={railIconBtn}
              >
                <PanelLeftOpen className="h-4 w-4" strokeWidth={1.5} />
              </button>
            </TooltipTrigger>
            <TooltipContent side="right">{t("sidebar.expand")}</TooltipContent>
          </Tooltip>
        </div>
      ) : (
        <div className="flex items-center gap-[11px] pl-5 pr-3 pt-[22px]">
          <img src={`/brand/${logo}.svg`} alt="Radal" className="h-7 w-7" />
          <span className="wordmark text-[21px] font-medium tracking-[-0.02em] text-text-primary">
            Radal.
          </span>
          {!isSheet ? (
            <button
              type="button"
              aria-label={t("sidebar.collapse")}
              title={t("sidebar.collapse")}
              onClick={() => rail.setCollapsed(true)}
              className={cn(railIconBtn, "ml-auto")}
            >
              <PanelLeftClose className="h-4 w-4" strokeWidth={1.5} />
            </button>
          ) : null}
        </div>
      )}

      {/* Tenant — a quiet one-line row: brand dot + trade name, nothing boxed. */}
      {!collapsed ? (
        <div className="mb-3 mt-2.5 flex items-center gap-1.5 px-5">
          <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-brand" aria-hidden />
          <span className="truncate text-caption text-ink-3">{tenant}</span>
        </div>
      ) : null}

      {/* Mode switch — cross-fade + 8px slide, 150ms ease-out, opacity/transform
          only (a high-frequency interaction, never a staged entrance).
          `initial={false}` keeps the very first render static. The collapsed
          icon rail is mode-aware through the same `modeKey`. */}
      <div className={cn("relative flex min-h-0 flex-1 flex-col", collapsed && "pt-4")}>
        <AnimatePresence initial={false} mode="popLayout">
          <motion.div
            key={collapsed ? `${modeKey}-collapsed` : modeKey}
            initial={reduce ? { opacity: 1, x: 0 } : { opacity: 0, x: 8 }}
            animate={{ opacity: 1, x: 0 }}
            exit={reduce ? { opacity: 0 } : { opacity: 0, x: -8 }}
            transition={{ duration: 0.15, ease: "easeOut" }}
            className="flex min-h-0 flex-1 flex-col"
          >
            {collapsed ? (
              <nav className="min-h-0 flex-1 overflow-y-auto px-2">
                <CollapsedNav groupId={groupId} />
              </nav>
            ) : groupId === null ? (
              <SidebarGlobal />
            ) : (
              <GroupSidebar
                key={groupId}
                groupId={groupId}
                className="flex min-h-0 flex-1 flex-col"
              />
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* Bottom: user card — hairline border does the ring, shadow whispers.
          Collapsed: just the avatar, tooltipped. */}
      {user ? (
        collapsed ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <div className="mx-auto mt-3 flex h-[34px] w-[34px] items-center justify-center rounded-sm bg-brand-soft text-[13px] font-medium text-brand-deep">
                {initials(user.full_name)}
              </div>
            </TooltipTrigger>
            <TooltipContent side="right">{user.full_name}</TooltipContent>
          </Tooltip>
        ) : (
          <div className="mx-3 mt-3 flex items-center gap-2.5 rounded-lg border border-line bg-bone p-[9px] shadow-elev">
            <div className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-sm bg-brand-soft text-[13px] font-medium text-brand-deep">
              {initials(user.full_name)}
            </div>
            <div className="min-w-0 leading-[1.3]">
              <p className="truncate text-[13px] font-semibold text-ink">
                {user.full_name}
              </p>
              <p className="truncate text-[11.5px] text-ink-3">
                {user.job_title ?? user.role}
              </p>
            </div>
          </div>
        )
      ) : null}

      {/* Resize handle — a 3px hit area on the right edge, desktop + expanded
          only. Brand tint on hover/drag; double-click snaps to the default. */}
      {!isSheet && !collapsed ? (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label={t("sidebar.resize")}
          aria-valuenow={rail.width}
          aria-valuemin={SIDEBAR_MIN_WIDTH}
          aria-valuemax={SIDEBAR_MAX_WIDTH}
          title={t("sidebar.resize")}
          onPointerDown={onHandlePointerDown}
          onPointerMove={onHandlePointerMove}
          onPointerUp={onHandlePointerUp}
          onDoubleClick={rail.resetWidth}
          className={cn(
            "absolute inset-y-0 right-0 z-10 w-[3px] cursor-col-resize touch-none select-none transition-[background-color] duration-150 ease-out hover:bg-brand/40",
            dragging && "bg-brand",
          )}
        />
      ) : null}
    </aside>
  );
}
