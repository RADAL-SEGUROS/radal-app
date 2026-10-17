/**
 * "Búsquedas" — the broker's portal-search runs (`GET /portal-searches`),
 * newest first. A table from `md`, a card list on a phone. The list POLLS
 * while any run is queued or running (`usePortalRuns`) and stops the moment
 * nothing is in flight; a row opens the run's detail sheet (`?run=ID`).
 */
import * as React from "react";
import { useTranslation } from "react-i18next";
import { ChevronRight, Loader2, Plus, Search } from "lucide-react";

import {
  PORTAL_RUN_STATUSES,
  runIsLive,
  usePortalRuns,
  type PortalRunStatus,
  type PortalRunSummary,
} from "@/api/rpa";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { EmptyState, ErrorBanner, LoadingRows } from "@/components/common/kit";
import { formatDateTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { RUN_STATUS_LABEL_KEY } from "@/pages/portal-search/labels";
import { RunStatusBadge, useInputFormatter, useSubtypeName } from "@/pages/portal-search/RunDetail";

const PAGE = 50;
const ALL = "all";

/** A one-line description of the risk: "ABCD12 · Toyota Yaris 2022" for VEH-01. */
export function useRiskSummary() {
  const formatInput = useInputFormatter();
  return React.useCallback(
    (input: Record<string, unknown>): string => {
      const parts = ["brand", "model", "year"]
        .map((name) => input[name])
        .filter((v) => v !== null && v !== undefined && v !== "")
        .map(String);
      if (input.plate || parts.length) {
        return [input.plate ? String(input.plate) : null, parts.join(" ") || null]
          .filter(Boolean)
          .join(" · ");
      }
      // Another connector's fields: the first few values, verbatim.
      return Object.entries(input)
        .slice(0, 3)
        .map(([name, value]) => formatInput(name, value))
        .join(" · ");
    },
    [formatInput],
  );
}

export function RunsList({
  onOpen,
  onNewSearch,
}: {
  onOpen: (id: number) => void;
  onNewSearch: () => void;
}) {
  const { t } = useTranslation("rpa");
  const [status, setStatus] = React.useState<PortalRunStatus | null>(null);
  const [limit, setLimit] = React.useState(PAGE);
  const runs = usePortalRuns({ status: status ?? undefined, limit });
  const risk = useRiskSummary();

  const items = runs.data?.items ?? [];
  const total = runs.data?.total ?? 0;
  const inFlight = items.filter((run) => runIsLive(run)).length;
  const subtypeName = useSubtypeName();

  return (
    <Card className="flex flex-col gap-3 p-3 sm:p-4">
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-h3 tracking-tight text-ink">{t("runs.title")}</h2>
          {inFlight > 0 ? (
            <span className="inline-flex items-center gap-1 text-caption text-brand-deep">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
              {t("runs.inFlight", { count: inFlight })}
            </span>
          ) : runs.isFetching && !runs.isLoading ? (
            <span className="text-caption text-ink-3">{t("runs.updating")}</span>
          ) : null}
        </div>
        <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
          <Select
            value={status ?? ALL}
            onValueChange={(value) => {
              setStatus(value === ALL ? null : (value as PortalRunStatus));
              setLimit(PAGE);
            }}
          >
            <SelectTrigger aria-label={t("runs.filterStatus")} className="h-10 w-full sm:h-9 sm:w-48">
              <SelectValue placeholder={t("runs.filterStatus")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={ALL}>{t("runs.allStatuses")}</SelectItem>
              {PORTAL_RUN_STATUSES.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(RUN_STATUS_LABEL_KEY[value])}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {runs.isLoading ? (
        <LoadingRows rows={4} />
      ) : runs.isError ? (
        <ErrorBanner error={runs.error} />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<Search />}
          title={t("runs.empty")}
          hint={t("runs.emptyHint")}
          action={
            <Button size="sm" variant="secondary" className="min-h-10 sm:min-h-8" onClick={onNewSearch}>
              <Plus className="h-4 w-4" aria-hidden />
              {t("runs.newSearch")}
            </Button>
          }
        />
      ) : (
        <>
          {/* Phone: cards */}
          <ul className="flex flex-col divide-y divide-line rounded-lg border border-line md:hidden">
            {items.map((run) => (
              <li key={run.id}>
                <button
                  type="button"
                  onClick={() => onOpen(run.id)}
                  className="flex min-h-[64px] w-full items-center gap-3 px-3 py-2.5 text-left transition-[background-color,scale] duration-150 ease-out hover:bg-paper-2/60 active:scale-[0.99]"
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="font-mono text-[11px] text-ink-3">#{run.id}</span>
                      <span className="text-label font-medium text-ink">{run.portal_name}</span>
                      <RunStatusBadge run={run} />
                    </div>
                    <p className="mt-0.5 truncate text-caption text-ink-2">{risk(run.input ?? {})}</p>
                    <p className="text-[11px] text-ink-3">
                      {[subtypeName(run), formatDateTime(run.created_at)]
                        .filter(Boolean)
                        .join(" · ")}
                      {run.quote_count > 0 ? ` · ${t("runs.plans", { count: run.quote_count })}` : ""}
                    </p>
                  </div>
                  <ChevronRight className="h-4 w-4 shrink-0 text-ink-3" strokeWidth={1.5} aria-hidden />
                </button>
              </li>
            ))}
          </ul>

          {/* md+: table */}
          <div className="hidden overflow-x-auto rounded-lg border border-line md:block">
            <table className="w-full min-w-[720px] text-left text-caption">
              <thead className="border-b border-line bg-paper-2/60 text-ink-3">
                <tr>
                  <th className="px-3 py-2 font-medium">{t("runs.columns.id")}</th>
                  <th className="px-3 py-2 font-medium">{t("runs.columns.portal")}</th>
                  <th className="px-3 py-2 font-medium">{t("runs.columns.subtype")}</th>
                  <th className="px-3 py-2 font-medium">{t("runs.columns.risk")}</th>
                  <th className="px-3 py-2 font-medium">{t("runs.columns.status")}</th>
                  <th className="px-3 py-2 text-right font-medium">{t("runs.columns.quotes")}</th>
                  <th className="px-3 py-2 font-medium">{t("runs.columns.created")}</th>
                  <th className="px-3 py-2">
                    <span className="sr-only">{t("runs.view")}</span>
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {items.map((run) => (
                  <RunRow
                    key={run.id}
                    run={run}
                    risk={risk(run.input ?? {})}
                    subtype={subtypeName(run)}
                    onOpen={onOpen}
                  />
                ))}
              </tbody>
            </table>
          </div>

          {total > items.length ? (
            <div className="flex justify-center">
              <Button
                size="sm"
                variant="ghost"
                className="min-h-10 sm:min-h-8"
                disabled={runs.isFetching || limit >= 200}
                onClick={() => setLimit((n) => Math.min(200, n + PAGE))}
              >
                {t("runs.more", { shown: items.length, total })}
              </Button>
            </div>
          ) : null}
        </>
      )}
    </Card>
  );
}

function RunRow({
  run,
  risk,
  subtype,
  onOpen,
}: {
  run: PortalRunSummary;
  risk: string;
  subtype: string | null;
  onOpen: (id: number) => void;
}) {
  const { t } = useTranslation("rpa");
  return (
    <tr
      className={cn(
        "cursor-pointer align-middle transition-[background-color] duration-150 ease-out hover:bg-paper-2/60",
      )}
      onClick={() => onOpen(run.id)}
    >
      <td className="px-3 py-2 font-mono text-[11px] text-ink-3">#{run.id}</td>
      <td className="px-3 py-2 text-ink">{run.portal_name}</td>
      <td className="max-w-[200px] truncate px-3 py-2 text-ink-2">{subtype ?? "—"}</td>
      <td className="max-w-[240px] truncate px-3 py-2 text-ink-2">{risk || "—"}</td>
      <td className="px-3 py-2">
        <RunStatusBadge run={run} />
      </td>
      <td className="px-3 py-2 text-right tabular-nums text-ink">{run.quote_count}</td>
      <td className="whitespace-nowrap px-3 py-2 tabular-nums text-ink-3">{formatDateTime(run.created_at)}</td>
      <td className="px-3 py-2 text-right">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="min-h-8"
          onClick={(e) => {
            e.stopPropagation();
            onOpen(run.id);
          }}
        >
          {t("runs.view")}
        </Button>
      </td>
    </tr>
  );
}
