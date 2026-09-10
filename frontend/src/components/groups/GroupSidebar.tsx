/**
 * GROUP-mode content of the single context-switching sidebar (v9).
 *
 * Header: the way back + the group's `GroupAvatar`. Then Resumen, then one row
 * per VIGENCIA. Each vigencia row is EXPANDABLE (chevron): expanding reveals the
 * account(s) under it — each with its ramo icon — and a submenu of the journey
 * parts (Resumen · Antecedentes · Bases Técnicas · Comparación · Propuesta ·
 * Pólizas), each deep-linking to `/groups/{id}/accounts/{caseId}?tab=<key>`.
 * The pinned Analítica anchor stays at the bottom.
 *
 * One request — `GET /account-groups/{id}/tree` — feeds it (name + periods +
 * lines), already narrowed to what the caller may see; the component renders
 * what arrives and never re-filters by tenant. Expansion state is kept in local
 * state; `Sidebar.tsx` keys this by `groupId` only, so it survives tab
 * navigation without a remount.
 */
import * as React from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  ArrowLeft,
  BarChart3,
  ChevronRight,
  FileText,
  GitCompareArrows,
  LayoutGrid,
  Layers,
  Paperclip,
  ScrollText,
  ShieldCheck,
  type LucideIcon,
} from "lucide-react";
import { useAccountGroup, useGroupTree } from "@/api/accountGroups";
import { GroupAvatar } from "@/components/groups/GroupAvatar";
import { can, usePermissions } from "@/lib/permissions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBanner } from "@/components/common/kit";
import {
  railRowClass,
  RailSectionLabel,
  RAIL_ROW_BASE,
  RAIL_ROW_IDLE,
} from "@/components/layout/SidebarGlobal";
import { cn } from "@/lib/utils";
import type { TreeLineNode, TreePeriodNode } from "@/api/types";

/** `period_start` desc, nulls last (spec §5.3). */
function sortedPeriods(periods: TreePeriodNode[]): TreePeriodNode[] {
  return [...periods].sort((a, b) => {
    if (!a.start && !b.start) return 0;
    if (!a.start) return 1;
    if (!b.start) return -1;
    return b.start.localeCompare(a.start);
  });
}

/** The journey parts of an account, in tab order — each a deep-link target. */
const JOURNEY_PARTS: { key: string; icon: LucideIcon }[] = [
  { key: "summary", icon: LayoutGrid },
  { key: "antecedentes", icon: Paperclip },
  { key: "bases-tecnicas", icon: FileText },
  { key: "comparison", icon: GitCompareArrows },
  { key: "propuesta", icon: ScrollText },
  { key: "policies", icon: ShieldCheck },
];

export function GroupSidebar({
  groupId,
  onNavigate,
  className,
}: {
  groupId: number;
  onNavigate?: () => void;
  className?: string;
}) {
  const { t } = useTranslation("common");
  const { t: ta } = useTranslation("accounts");
  const location = useLocation();
  const { data: perms } = usePermissions();
  const { data: tree, isLoading, error, refetch, isFetching } = useGroupTree(groupId);
  const { data: group } = useAccountGroup(groupId);

  const periods = React.useMemo(() => sortedPeriods(tree?.periods ?? []), [tree]);
  const overviewPath = `/groups/${groupId}`;

  // Which vigencias are expanded — the latest opens by default, and expansion
  // survives tab navigation (the sidebar is not remounted per route).
  const [expanded, setExpanded] = React.useState<Record<string, boolean>>({});
  const isExpanded = (period: TreePeriodNode) => expanded[period.label] ?? period.is_latest;
  const toggle = (label: string, fallback: boolean) =>
    setExpanded((current) => ({ ...current, [label]: !(current[label] ?? fallback) }));

  const activeTab = new URLSearchParams(location.search).get("tab") ?? "summary";

  return (
    <div className={className}>
      {/* Header — the way back first, then identity. */}
      <div className="flex flex-col gap-1.5 px-3 pb-3 pt-2">
        <Link
          to="/groups"
          onClick={onNavigate}
          className="-mx-1 flex items-center gap-1.5 rounded-sm px-1 py-1 text-caption text-ink-3 transition-[color,background-color] duration-150 ease-out hover:bg-paper-2 hover:text-ink"
        >
          <ArrowLeft className="h-[15px] w-[15px] shrink-0" strokeWidth={1.5} />
          <span className="truncate">{t("nav.backToGroups")}</span>
        </Link>

        {isLoading && !tree ? (
          <Skeleton className="mx-1 h-5 w-4/5" />
        ) : tree ? (
          <Link
            to={overviewPath}
            onClick={onNavigate}
            className="flex items-center gap-2 rounded-sm px-1 py-0.5 transition-[background-color] duration-150 ease-out hover:bg-paper-2"
          >
            <GroupAvatar name={tree.group.name} icon={group?.icon ?? null} size="sm" />
            <span className="block min-w-0 truncate text-[15px] font-semibold leading-tight tracking-tight text-ink">
              {tree.group.name}
            </span>
          </Link>
        ) : null}
      </div>

      <nav className="flex min-h-0 flex-1 flex-col overflow-y-auto px-3 pb-2">
        {error ? (
          <div className="flex flex-col gap-2 px-1 py-2">
            <ErrorBanner error={error} />
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void refetch()}
              disabled={isFetching}
            >
              {t("actions.retry")}
            </Button>
          </div>
        ) : null}

        <ul className="flex flex-col gap-0.5">
          <li>
            <NavLink
              to={overviewPath}
              end
              onClick={onNavigate}
              className={({ isActive }) => railRowClass(isActive && !location.search)}
            >
              <LayoutGrid className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
              <span className="truncate">{t("sidebar.overview")}</span>
            </NavLink>
          </li>
        </ul>

        {isLoading && !tree ? (
          <div className="flex flex-col gap-1.5 px-3 py-2">
            <Skeleton className="h-5 w-full" />
            <Skeleton className="h-5 w-4/5" />
          </div>
        ) : periods.length > 0 ? (
          <>
            <RailSectionLabel>{t("sidebar.periods")}</RailSectionLabel>
            <ul className="flex flex-col gap-0.5">
              {periods.map((period) => {
                const open = isExpanded(period);
                return (
                  <li key={period.label}>
                    <button
                      type="button"
                      onClick={() => toggle(period.label, period.is_latest)}
                      aria-expanded={open}
                      className={cn(railRowClass(false), "w-full text-left")}
                    >
                      <ChevronRight
                        className={cn(
                          "h-[15px] w-[15px] shrink-0 text-ink-3 transition-transform duration-150 ease-out",
                          open && "rotate-90",
                        )}
                        strokeWidth={1.75}
                      />
                      <span className="truncate">{period.label}</span>
                      {!period.is_latest ? (
                        <Badge variant="muted" className="ml-auto shrink-0 px-1.5 py-0 text-[10px]">
                          {t("sidebar.historic")}
                        </Badge>
                      ) : null}
                    </button>

                    {open ? (
                      <div className="mb-1 ml-[7px] flex flex-col gap-1.5 border-l border-line pl-2 pt-1">
                        {period.lines.map((line) => (
                          <AccountBranch
                            key={line.account.case_file_id}
                            groupId={groupId}
                            line={line}
                            activeTab={activeTab}
                            currentPath={location.pathname}
                            onNavigate={onNavigate}
                            partLabel={(key) => ta(`account.tabs.${key}`)}
                          />
                        ))}
                      </div>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </>
        ) : null}
      </nav>

      {can(perms, "Dashboard", "View") ? (
        <nav
          aria-label={t("nav.sections.pinned")}
          className="flex shrink-0 flex-col gap-0.5 border-t border-line px-2 py-2"
        >
          <NavLink
            to="/analytics"
            onClick={onNavigate}
            className={({ isActive }) => railRowClass(isActive)}
          >
            <BarChart3 className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
            <span className="truncate">{t("nav.analytics")}</span>
          </NavLink>
        </nav>
      ) : null}
    </div>
  );
}

/**
 * One account under a vigencia: the ramo row (its ramo icon → the account) and
 * the journey-part deep-links beneath it. Active detection reads `?tab=` off the
 * URL because a `NavLink` matches on pathname alone.
 */
function AccountBranch({
  groupId,
  line,
  activeTab,
  currentPath,
  onNavigate,
  partLabel,
}: {
  groupId: number;
  line: TreeLineNode;
  activeTab: string;
  currentPath: string;
  onNavigate?: () => void;
  partLabel: (key: string) => string;
}) {
  const accountPath = `/groups/${groupId}/accounts/${line.account.case_file_id}`;
  const onAccount = currentPath === accountPath;

  return (
    <div className="flex flex-col gap-0.5">
      <Link
        to={accountPath}
        onClick={onNavigate}
        className={cn(RAIL_ROW_BASE, RAIL_ROW_IDLE, "min-h-[28px] font-medium text-ink")}
        title={line.name}
      >
        <Layers className="h-[15px] w-[15px] shrink-0 text-brand" strokeWidth={1.75} />
        <span className="truncate">{line.name}</span>
      </Link>
      <ul className="flex flex-col gap-0.5 pl-[6px]">
        {JOURNEY_PARTS.map((part) => {
          const active = onAccount && activeTab === part.key;
          const Icon = part.icon;
          return (
            <li key={part.key}>
              <Link
                to={`${accountPath}?tab=${part.key}`}
                onClick={onNavigate}
                className={railRowClass(active, "min-h-[28px] text-[12.5px]")}
              >
                <Icon className="h-[14px] w-[14px] shrink-0" strokeWidth={1.5} />
                <span className="truncate">{partLabel(part.key)}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
