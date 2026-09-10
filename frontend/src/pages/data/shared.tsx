/**
 * Datos shared bits — the filter chip row, pagination and the URL-param
 * re-exports every table tab uses.
 *
 * Rules encoded here, once:
 *  - a filter chip exists ONLY for a parameter the list endpoint honours
 *    server-side (no client-side pretend-filtering) — each tab passes exactly
 *    the options its endpoint accepts;
 *  - an inactive chip is the dashed `.mk-filter` pill; an active one renders
 *    solid brand-soft;
 *  - every piece of tab state lives in the URL (`?tab=`, `?view=`, filter
 *    params), so a filtered view is a shareable link.
 *
 * The URL helpers themselves now live in `@/lib/urlParams` — Analítica needs
 * the same ones, and it must not have to import out of the Datos page to get
 * them. They are re-exported here so the tab call sites keep compiling.
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { Check, ChevronDown } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { useUrlParam, useSetUrlParams } from "@/lib/urlParams";

export { useUrlParam, useSetUrlParams } from "@/lib/urlParams";

// =============================================================================
// Pagination — every consolidated table pages by 50, position in `?page=`
// =============================================================================

export const PAGE_SIZE = 50;

/**
 * The table page as URL state (`?page=`, 1-based in the URL, 0-based for
 * `DataTable`). Absent/garbage -> page 0; page 0 removes the key. Changing a
 * filter or the tab must reset it — pass `{ page: null }` through
 * {@link useSetUrlParams} alongside the filter change.
 */
export function usePageIndex(): [number, (pageIndex: number) => void] {
  const [raw, setRaw] = useUrlParam("page");
  const parsed = raw ? Number(raw) : NaN;
  const pageIndex = Number.isInteger(parsed) && parsed > 1 ? parsed - 1 : 0;
  const setPageIndex = React.useCallback(
    (next: number) => setRaw(next <= 0 ? null : String(next + 1)),
    [setRaw],
  );
  return [pageIndex, setPageIndex];
}

// =============================================================================
// Filter chips
// =============================================================================

export interface FilterOption {
  value: string;
  label: string;
}

/**
 * One server-side filter as a chip. Dashed pill when inactive, brand-soft
 * solid when active; the popover is a single-select list with a leading
 * "Todas/Todos" row that clears the filter.
 */
export function FilterChip({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string | null;
  options: FilterOption[];
  onChange: (value: string | null) => void;
}) {
  const { t } = useTranslation("analytics");
  const active = value !== null && value !== "";
  const current = options.find((o) => o.value === value);

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <button
          type="button"
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-3 py-1 text-caption transition-[background-color,color,box-shadow,transform] duration-150 ease-out active:scale-[0.98]",
            active
              ? "bg-brand-soft font-medium text-brand-deep"
              : "border border-dashed border-line text-text-secondary hover:text-text-primary",
          )}
        >
          <span>{active && current ? `${label}: ${current.label}` : label}</span>
          <ChevronDown className="h-3 w-3 opacity-60" />
        </button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="min-w-[180px]">
        <DropdownMenuItem onSelect={() => onChange(null)}>
          <span className="flex-1">{t("filters.all")}</span>
          {!active ? <Check className="h-3.5 w-3.5 text-brand" /> : null}
        </DropdownMenuItem>
        {options.map((option) => (
          <DropdownMenuItem key={option.value} onSelect={() => onChange(option.value)}>
            <span className="flex-1">{option.label}</span>
            {option.value === value ? <Check className="h-3.5 w-3.5 text-brand" /> : null}
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

/** The chip row above a table, with a trailing clear affordance when filtered. */
export function FilterRow({
  children,
  isFiltered,
  onClear,
  trailing,
}: {
  children: React.ReactNode;
  isFiltered: boolean;
  onClear: () => void;
  trailing?: React.ReactNode;
}) {
  const { t } = useTranslation("analytics");
  return (
    <div className="flex flex-wrap items-center gap-2">
      {children}
      {isFiltered ? (
        <button
          type="button"
          onClick={onClear}
          className="text-caption text-text-muted underline-offset-2 transition-[color] duration-150 hover:text-text-primary hover:underline"
        >
          {t("filters.clear")}
        </button>
      ) : null}
      {trailing ? <div className="ms-auto">{trailing}</div> : null}
    </div>
  );
}

/** "N resultados" — right of the chips, from the server's `total`. */
export function ResultCount({ total }: { total: number | undefined }) {
  const { t } = useTranslation("analytics");
  if (total === undefined) return null;
  return (
    <span className="text-caption tabular-nums text-text-muted">
      {t("count", { count: total })}
    </span>
  );
}
