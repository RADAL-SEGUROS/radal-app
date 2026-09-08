/**
 * GROUP-mode content of the single context-switching sidebar — SLIM since v5.
 *
 * The tree (vigencias → ramos → antecedentes / cotizaciones / propuestas /
 * pólizas → post-venta → renovación) moved into the central group view; this
 * rail is now pure orientation: the way back, the group's name, and a short
 * nav — Resumen, one row per vigencia, pinned Analítica. `Sidebar.tsx` renders
 * this in place of the global nav whenever the URL is inside a group, keyed by
 * `groupId` only — see the no-remount guarantee documented there.
 *
 * One request — `GET /account-groups/{id}/tree` — still feeds it (name +
 * periods), already narrowed to what the caller may see; the component renders
 * what arrives and never re-filters by tenant. A historic vigencia keeps its
 * "histórica" chip: the row is a link either way (a read-only folder is still
 * a folder), the central view owns the read-only affordances.
 */
import * as React from "react";
import { Link, NavLink, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { ArrowLeft, BarChart3, CalendarRange, FolderOpen, LayoutGrid } from "lucide-react";
import { useGroupTree } from "@/api/accountGroups";
import { can, usePermissions } from "@/lib/permissions";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBanner } from "@/components/common/kit";
import { railRowClass, RailSectionLabel } from "@/components/layout/SidebarGlobal";
import type { TreePeriodNode } from "@/api/types";

/**
 * `period_start` desc, nulls last (spec §5.3).
 *
 * `GET /account-groups/{id}/tree` already returns the periods in this order
 * and flags the first one `is_latest`; this only guarantees the rail reads as
 * a timeline even if a period arrives without a start date, and it never
 * re-filters anything — the server owns which periods exist.
 */
function sortedPeriods(periods: TreePeriodNode[]): TreePeriodNode[] {
  return [...periods].sort((a, b) => {
    if (!a.start && !b.start) return 0;
    if (!a.start) return 1;
    if (!b.start) return -1;
    return b.start.localeCompare(a.start);
  });
}

export function GroupSidebar({
  groupId,
  /** Called after any navigation — the `Sheet` below `lg` closes on it. */
  onNavigate,
  className,
}: {
  groupId: number;
  onNavigate?: () => void;
  className?: string;
}) {
  const { t } = useTranslation("common");
  const location = useLocation();
  const { data: perms } = usePermissions();
  const { data: tree, isLoading, error, refetch, isFetching } = useGroupTree(groupId);

  const periods = React.useMemo(() => sortedPeriods(tree?.periods ?? []), [tree]);
  const overviewPath = `/groups/${groupId}`;

  return (
    <div className={className}>
      {/* Header — the way back first, then identity. */}
      <div className="flex flex-col gap-1.5 px-3 pb-3 pt-2">
        {/* Back affordance: a ghost row out of group mode. */}
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
            className="flex items-start gap-2 rounded-sm px-1 py-0.5 transition-[background-color] duration-150 ease-out hover:bg-paper-2"
          >
            <FolderOpen className="mt-[3px] h-4 w-4 shrink-0 text-brand" strokeWidth={1.5} />
            <span className="block min-w-0 truncate text-[15px] font-semibold leading-tight tracking-tight text-ink">
              {tree.group.name}
            </span>
          </Link>
        ) : null}
      </div>

      {/* Nav — Resumen, then the vigencias. The rows are quiet links; the
          tree, the counts and every action live in the central view now. */}
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
              className={({ isActive }) =>
                // The overview also owns `?tab=…` sub-views of itself, but as a
                // plain `end` match; deeper pages (accounts, periods) unlight it.
                railRowClass(isActive && !location.search)
              }
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
                const periodPath = `/groups/${groupId}/periods/${encodeURIComponent(period.label)}`;
                return (
                  <li key={period.label}>
                    <NavLink
                      to={periodPath}
                      onClick={onNavigate}
                      className={({ isActive }) => railRowClass(isActive)}
                    >
                      <CalendarRange className="h-[16px] w-[16px] shrink-0" strokeWidth={1.5} />
                      <span className="truncate">{period.label}</span>
                      {!period.is_latest ? (
                        <Badge variant="muted" className="ml-auto shrink-0 px-1.5 py-0 text-[10px]">
                          {t("sidebar.historic")}
                        </Badge>
                      ) : null}
                    </NavLink>
                  </li>
                );
              })}
            </ul>
          </>
        ) : null}
      </nav>

      {/* Pinned bottom: the global anchor the broker still needs mid-group,
          above the shell's user card, below a hairline. Same row treatment as
          global mode; same Dashboard gate. */}
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
