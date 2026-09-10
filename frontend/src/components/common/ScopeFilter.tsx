/**
 * The **scope filter** — grupo · grupo-cuenta · rango de fechas.
 *
 * One control shared by both analysis destinations, Datos (the tables) and
 * Analítica (the visuals), because the broker asks the same question of both:
 * *"show me this, but only for this account"*. Comparing groups side by side is
 * deliberately NOT here — that is the agent's job; this narrows to ONE group,
 * and optionally to one grupo-cuenta inside it.
 *
 * The scope lives entirely in the URL (`?group=`, `?account=`, `?from=`,
 * `?to=`), so a scoped dashboard is a link you can send someone. Picking a
 * different grupo clears the grupo-cuenta — an account id from another group
 * would silently return nothing, which reads as "no data" rather than as the
 * mistake it is.
 *
 * The grupo-cuenta options come from the group's tree, so they carry the real
 * vigencia and ramo the broker recognises ("Incendio · 2026-2027"), not a bare
 * case-file id.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { CalendarRange, FolderOpen, Layers, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { useAccountGroups, useGroupTree } from "@/api/accountGroups";
import { useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import { FilterChip, type FilterOption } from "@/pages/data/shared";

export interface Scope {
  /** `account_group_id` — the broker-private grupo. */
  groupId?: number;
  /** `case_file_id` — one grupo-cuenta inside that grupo. */
  caseFileId?: number;
  dateFrom?: string;
  dateTo?: string;
}

/** Reads the scope out of the URL. Both pages call this to build their query. */
export function useScope(): Scope {
  const [group] = useUrlParam("group");
  const [account] = useUrlParam("account");
  const [from] = useUrlParam("from");
  const [to] = useUrlParam("to");

  return React.useMemo(() => {
    const groupId = group ? Number(group) : NaN;
    const caseFileId = account ? Number(account) : NaN;
    return {
      groupId: Number.isInteger(groupId) && groupId > 0 ? groupId : undefined,
      // An account without a group is not a scope we offer — the picker can
      // only produce one from inside a group.
      caseFileId:
        Number.isInteger(caseFileId) && caseFileId > 0 && group
          ? caseFileId
          : undefined,
      dateFrom: from || undefined,
      dateTo: to || undefined,
    };
  }, [group, account, from, to]);
}

/** The scope as query params for a list/summary/export call. */
export function scopeParams(scope: Scope): Record<string, number | string | undefined> {
  return {
    account_group_id: scope.groupId,
    case_file_id: scope.caseFileId,
    date_from: scope.dateFrom,
    date_to: scope.dateTo,
  };
}

export function isScoped(scope: Scope): boolean {
  return Boolean(scope.groupId || scope.caseFileId || scope.dateFrom || scope.dateTo);
}

function DateRangeChip({
  from,
  to,
  onChange,
}: {
  from: string | undefined;
  to: string | undefined;
  onChange: (next: { from: string | null; to: string | null }) => void;
}) {
  const { t } = useTranslation("analytics");
  const active = Boolean(from || to);

  return (
    <Popover>
      <PopoverTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-caption transition-[background-color,color,box-shadow,transform] duration-150 ease-out active:scale-[0.98]",
            active
              ? "bg-brand-soft font-medium text-brand-deep"
              : "border border-dashed border-line text-text-secondary hover:text-text-primary",
          )}
        >
          <CalendarRange className="h-3 w-3 opacity-70" />
          <span>
            {active
              ? `${from || "…"} → ${to || "…"}`
              : t("scope.dateRange")}
          </span>
        </button>
      </PopoverTrigger>
      <PopoverContent align="start" className="w-[260px]">
        <div className="flex flex-col gap-2.5">
          <label className="flex flex-col gap-1 text-caption text-ink-3">
            {t("scope.from")}
            <Input
              type="date"
              value={from ?? ""}
              onChange={(e) => onChange({ from: e.target.value || null, to: to ?? null })}
            />
          </label>
          <label className="flex flex-col gap-1 text-caption text-ink-3">
            {t("scope.to")}
            <Input
              type="date"
              value={to ?? ""}
              onChange={(e) => onChange({ from: from ?? null, to: e.target.value || null })}
            />
          </label>
          {active ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => onChange({ from: null, to: null })}
            >
              <X className="h-3.5 w-3.5" />
              {t("scope.clearDates")}
            </Button>
          ) : null}
        </div>
      </PopoverContent>
    </Popover>
  );
}

export function ScopeFilter({
  /** Rendered at the end of the row — the export menu, a group-by chip, … */
  trailing,
  className,
}: {
  trailing?: React.ReactNode;
  className?: string;
}) {
  const { t } = useTranslation("analytics");
  const scope = useScope();
  const setParams = useSetUrlParams();

  const groups = useAccountGroups({ page_size: 100 });
  // Only fetch the tree once a group is actually chosen.
  const tree = useGroupTree(scope.groupId);

  const groupOptions: FilterOption[] = React.useMemo(
    () =>
      (groups.data?.items ?? []).map((group) => ({
        value: String(group.id),
        label: group.name,
      })),
    [groups.data],
  );

  /** Every grupo-cuenta of the chosen group, labelled "Ramo · Vigencia". */
  const accountOptions: FilterOption[] = React.useMemo(() => {
    const out: FilterOption[] = [];
    for (const period of tree.data?.periods ?? []) {
      for (const line of period.lines) {
        out.push({
          value: String(line.account.case_file_id),
          label: `${line.account.line_name || line.name} · ${period.label}`,
        });
      }
    }
    return out;
  }, [tree.data]);

  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>
      <FilterChip
        label={t("scope.group")}
        value={scope.groupId ? String(scope.groupId) : null}
        options={groupOptions}
        // Changing the grupo invalidates the grupo-cuenta AND the page index.
        onChange={(value) =>
          setParams({ group: value, account: null, page: null })
        }
      />

      {/* The account chip only exists once a group narrows it — an account
          picker over every group in the tenant is a list nobody can read. */}
      {scope.groupId ? (
        <FilterChip
          label={t("scope.account")}
          value={scope.caseFileId ? String(scope.caseFileId) : null}
          options={accountOptions}
          onChange={(value) => setParams({ account: value, page: null })}
        />
      ) : null}

      <DateRangeChip
        from={scope.dateFrom}
        to={scope.dateTo}
        onChange={({ from, to }) => setParams({ from, to, page: null })}
      />

      {isScoped(scope) ? (
        <button
          type="button"
          onClick={() =>
            setParams({ group: null, account: null, from: null, to: null, page: null })
          }
          className="text-caption text-text-muted underline-offset-2 transition-[color] duration-150 hover:text-text-primary hover:underline"
        >
          {t("filters.clear")}
        </button>
      ) : null}

      {trailing ? <div className="ms-auto">{trailing}</div> : null}
    </div>
  );
}

/** A quiet summary of the active scope, for a PDF/PNG export header. */
export function useScopeLabel(): string | null {
  const { t } = useTranslation("analytics");
  const scope = useScope();
  const groups = useAccountGroups({ page_size: 100 }, Boolean(scope.groupId));
  const tree = useGroupTree(scope.groupId);

  if (!isScoped(scope)) return null;

  const parts: string[] = [];
  const group = (groups.data?.items ?? []).find((g) => g.id === scope.groupId);
  if (group) parts.push(group.name);

  if (scope.caseFileId) {
    for (const period of tree.data?.periods ?? []) {
      for (const line of period.lines) {
        if (line.account.case_file_id === scope.caseFileId) {
          parts.push(`${line.account.line_name || line.name} · ${period.label}`);
        }
      }
    }
  }
  if (scope.dateFrom || scope.dateTo) {
    parts.push(`${scope.dateFrom || "…"} → ${scope.dateTo || "…"}`);
  }
  return parts.length ? parts.join(" · ") : t("scope.filtered");
}

/** Icons re-exported so the pages can label their own sections consistently. */
export const ScopeIcons = { group: FolderOpen, account: Layers };

/**
 * The scope as ready-to-spread query params, memoised.
 *
 * Every Datos table spreads this into its own list call so the ROWS are
 * narrowed by the same grupo / grupo-cuenta / fechas the export uses. Without
 * it the chips would filter the downloaded file while the table on screen kept
 * showing everything — the two disagreeing is worse than neither filtering.
 */
export function useScopeParams(): Record<string, number | string | undefined> {
  const scope = useScope();
  return React.useMemo(() => scopeParams(scope), [scope]);
}
