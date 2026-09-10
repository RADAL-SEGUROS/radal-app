/**
 * Analítica — Cuentas (spec v4 §4.2 first row).
 *
 * `GET /case-files?kind=account&kind=renewal` with the server-side filters the
 * endpoint honours (`stage[]`, `status[]`, `insurance_line_id`). Two view
 * modes, persisted in `?view=`:
 *  - tabla — the DataTable;
 *  - tablero — one column per macro-phase (§3.2), the board the broker thinks
 *    in, grouped client-side from the SAME server page (the grouping is
 *    presentation; the filtering stays server-side).
 *
 * Row → the group account page when the folder belongs to a group, else the
 * flat expediente page. Old routes stay valid — nothing here replaces them.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { FolderOpen } from "lucide-react";

import { DataTable } from "@/components/common/DataTable";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorBanner, MonoChip } from "@/pages/proposals/shared";
import { StageBadge, CaseStatusBadge } from "@/pages/groups/shared";
import { useCaseFiles, useCaseFilesSummary } from "@/api/caseFiles";
import { formatDate } from "@/lib/format";
import {
  CASE_FILE_STATUSES,
  CASE_STAGE_FLOW,
  type CaseFile,
  type CaseFileStatus,
  type CaseStage,
} from "@/api/types";
import {
  FilterChip,
  FilterRow,
  PAGE_SIZE,
  ResultCount,
  usePageIndex,
  useSetUrlParams,
  useUrlParam,
} from "./shared";
import { useScopeParams } from "@/components/common/ScopeFilter";

// =============================================================================
// Macro-phase map — LOCAL copy of spec §3.2 for the board columns only.
//
// The authoritative map for the Journey visualization lives inside
// `components/common/Journey.tsx` (F3). This constant exists so the board can
// group rows without importing a component this package does not own; if the
// phase map ever changes, both copies change (they are the same spec table).
// =============================================================================

const ACCOUNT_PHASES = [
  "antecedentes",
  "mercado",
  "comparacion",
  "propuesta",
  "poliza",
  "vigente",
] as const;
type AccountPhase = (typeof ACCOUNT_PHASES)[number] | "renovacion";

const PHASE_BY_STAGE: Partial<Record<CaseStage, AccountPhase>> = {
  lead: "antecedentes",
  intake: "antecedentes",
  pre_underwriting: "antecedentes",
  technical_basis: "mercado",
  market_submission: "mercado",
  quotes_received: "mercado",
  comparison: "comparacion",
  insured_decision: "comparacion",
  proposal_issued: "propuesta",
  ratified: "propuesta",
  policy_issued: "poliza",
  mirror_validation: "poliza",
  active: "vigente",
  renewal_review: "renovacion",
};

/** Where an account/renewal row lives: its group page when it has one. */
function accountRoute(row: CaseFile): string {
  return row.account_group_id
    ? `/groups/${row.account_group_id}/accounts/${row.id}`
    : `/cases/${row.id}`;
}

export default function AccountsTab() {
  const { t } = useTranslation("analytics");
  const { t: tCases } = useTranslation("cases");
  const navigate = useNavigate();

  const [stage] = useUrlParam("stage");
  const [status] = useUrlParam("status");
  const [line] = useUrlParam("line");
  const [view, setView] = useUrlParam("view");
  const [pageIndex, setPageIndex] = usePageIndex();
  const setParams = useSetUrlParams();
  const mode = view === "board" ? "board" : "table";

  // Changing a filter re-anchors to the first page in the SAME update.
  const setStage = (value: string | null) => setParams({ stage: value, page: null });
  const setStatus = (value: string | null) => setParams({ status: value, page: null });
  const setLine = (value: string | null) => setParams({ line: value, page: null });

  const scope = useScopeParams();
  const list = useCaseFiles({
    // The page-level scope (grupo · grupo-cuenta · fechas) is applied
    // SERVER-side, exactly like the export, so the table and the file the
    // broker downloads can never disagree about what was filtered.
    ...scope,
    kind: ["account", "renewal"],
    stage: stage ? [stage as CaseStage] : undefined,
    status: status ? [status as CaseFileStatus] : undefined,
    insurance_line_id: line ? Number(line) : undefined,
    page: pageIndex + 1,
    page_size: PAGE_SIZE,
  });
  const summary = useCaseFilesSummary();
  const items = list.data?.items ?? [];

  // Ramo options come from the rows in view (there is no /insurance-lines list
  // endpoint); the FILTERING itself is the server's `insurance_line_id` param.
  const lineOptions = React.useMemo(() => {
    const seen = new Map<number, string>();
    for (const row of items) {
      if (row.insurance_line_id && row.insurance_line_name) {
        seen.set(row.insurance_line_id, row.insurance_line_name);
      }
    }
    return [...seen.entries()].map(([id, name]) => ({ value: String(id), label: name }));
  }, [items]);

  const stageOptions = React.useMemo(
    () =>
      [...CASE_STAGE_FLOW.renewal, "closed" as const].map((s) => ({
        value: s,
        label: tCases(`stages.${s}`, { defaultValue: s }),
      })),
    [tCases],
  );
  const statusOptions = React.useMemo(
    () =>
      CASE_FILE_STATUSES.map((s) => ({
        value: s,
        label: tCases(`statuses.${s}`, { defaultValue: s }),
      })),
    [tCases],
  );

  const columns = React.useMemo<ColumnDef<CaseFile>[]>(
    () => [
      {
        accessorKey: "title",
        header: t("columns.group"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate font-medium text-text-primary">
              {row.original.account_group_name ?? row.original.title}
            </p>
            <p className="truncate text-caption text-text-muted">
              {row.original.client_legal_name ?? "—"}
            </p>
          </div>
        ),
      },
      {
        accessorKey: "insurance_line_name",
        header: t("columns.line"),
        cell: ({ row }) => row.original.insurance_line_name ?? "—",
      },
      {
        accessorKey: "period_label",
        header: t("columns.period"),
        cell: ({ row }) => (
          <span className="tabular-nums text-text-secondary">
            {row.original.period_label ??
              (row.original.period_start
                ? `${formatDate(row.original.period_start)} – ${formatDate(row.original.period_end)}`
                : "—")}
          </span>
        ),
      },
      {
        accessorKey: "stage",
        header: t("columns.stage"),
        cell: ({ row }) => <StageBadge stage={row.original.stage} />,
      },
      {
        accessorKey: "status",
        header: t("columns.status"),
        cell: ({ row }) => <CaseStatusBadge status={row.original.status} />,
      },
      {
        accessorKey: "reference",
        header: t("columns.reference"),
        cell: ({ row }) => (
          <MonoChip>{row.original.reference ?? `#${row.original.id}`}</MonoChip>
        ),
      },
    ],
    [t],
  );

  const isFiltered = !!(stage || status || line);
  // One batched update — three sequential setters would each read the same
  // stale URL and only the last change would survive.
  const clear = () => setParams({ stage: null, status: null, line: null, page: null });

  // Post-sale swim count for the board footer (spec §4.3): the open post-sale
  // cases across the book, from the summary the header already fetched.
  const postsaleCount = React.useMemo(() => {
    const byKind = summary.data?.by_kind ?? {};
    return (
      (byKind["endorsement"] ?? 0) + (byKind["collection"] ?? 0) + (byKind["claim"] ?? 0)
    );
  }, [summary.data]);

  return (
    <div className="flex flex-col gap-3">
      <FilterRow
        isFiltered={isFiltered}
        onClear={clear}
        trailing={
          <div className="flex items-center gap-3">
            <ResultCount total={list.data?.total} />
            <Tabs value={mode} onValueChange={(v) => setView(v === "board" ? "board" : null)}>
              <TabsList>
                <TabsTrigger value="table">{t("views.table")}</TabsTrigger>
                <TabsTrigger value="board">{t("views.board")}</TabsTrigger>
              </TabsList>
            </Tabs>
          </div>
        }
      >
        <FilterChip
          label={t("filters.stage")}
          value={stage}
          options={stageOptions}
          onChange={setStage}
        />
        <FilterChip
          label={t("filters.status")}
          value={status}
          options={statusOptions}
          onChange={setStatus}
        />
        {lineOptions.length > 0 || line ? (
          <FilterChip
            label={t("filters.line")}
            value={line}
            options={lineOptions}
            onChange={setLine}
          />
        ) : null}
      </FilterRow>

      <ErrorBanner error={list.error} />

      {mode === "table" ? (
        <DataTable
          columns={columns}
          data={items}
          isLoading={list.isLoading}
          pageIndex={pageIndex}
          pageSize={PAGE_SIZE}
          total={list.data?.total}
          onPageChange={setPageIndex}
          onRowClick={(row) => navigate(accountRoute(row))}
          emptyMessage={
            <EmptyState
              icon={<FolderOpen className="h-6 w-6" />}
              title={t("empty.accounts")}
              hint={t("emptyHint.accounts")}
            />
          }
        />
      ) : (
        <AccountsBoard
          items={items}
          isLoading={list.isLoading}
          postsaleCount={postsaleCount}
          onOpen={(row) => navigate(accountRoute(row))}
        />
      )}
    </div>
  );
}

// =============================================================================
// Tablero — one recessed column per macro-phase.
// =============================================================================

function AccountsBoard({
  items,
  isLoading,
  postsaleCount,
  onOpen,
}: {
  items: CaseFile[];
  isLoading: boolean;
  postsaleCount: number;
  onOpen: (row: CaseFile) => void;
}) {
  const { t } = useTranslation("analytics");
  const { t: tCases } = useTranslation("cases");

  const byPhase = React.useMemo(() => {
    const map = new Map<AccountPhase, CaseFile[]>();
    for (const row of items) {
      const phase = PHASE_BY_STAGE[row.stage];
      if (!phase) continue; // `closed` and post-sale stages are not board columns
      const bucket = map.get(phase) ?? [];
      bucket.push(row);
      map.set(phase, bucket);
    }
    return map;
  }, [items]);

  const renewals = byPhase.get("renovacion") ?? [];
  const phases: { phase: AccountPhase; rows: CaseFile[] }[] = [
    // Renovación prepends only when it has rows (§3.2 — a renewal re-enters
    // the account phases, so an always-empty seventh column is noise).
    ...(renewals.length > 0 ? [{ phase: "renovacion" as AccountPhase, rows: renewals }] : []),
    ...ACCOUNT_PHASES.map((phase) => ({ phase, rows: byPhase.get(phase) ?? [] })),
  ];

  return (
    <div className="flex flex-col gap-2">
      <div className="overflow-x-auto pb-1">
        <div className="grid min-w-[900px] auto-cols-fr grid-flow-col gap-2">
          {phases.map(({ phase, rows }) => (
            <div key={phase} className="flex flex-col gap-2 rounded-lg bg-bg-recessed p-2">
              <div className="flex items-baseline justify-between px-1 pt-0.5">
                <span className="text-caption font-medium text-text-muted">
                  {tCases(`journey.phases.${phase}`, { defaultValue: phase })}
                </span>
                <span className="text-caption tabular-nums text-text-muted">{rows.length}</span>
              </div>
              {isLoading ? (
                <div className="h-20 animate-pulse rounded-sm bg-bg-surface/60" />
              ) : rows.length === 0 ? (
                <p className="px-1 pb-1 text-caption text-text-muted opacity-70">
                  {t("board.empty")}
                </p>
              ) : (
                rows.map((row) => (
                  <button
                    key={row.id}
                    type="button"
                    onClick={() => onOpen(row)}
                    className="w-full rounded-sm border border-line bg-bg-surface p-2.5 text-left shadow-elev transition-[border-color,box-shadow,transform] duration-150 ease-out hover:border-line-strong hover:shadow-elev-hover active:scale-[0.98]"
                  >
                    <p className="truncate text-caption font-medium text-text-primary">
                      {row.account_group_name ?? row.title}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                      {row.insurance_line_name ? (
                        <Badge variant="neutral">{row.insurance_line_name}</Badge>
                      ) : null}
                      <StageBadge stage={row.stage} />
                    </div>
                  </button>
                ))
              )}
            </div>
          ))}
        </div>
      </div>
      {postsaleCount > 0 ? (
        <p className="text-caption text-text-muted">
          {t("board.postsaleFooter", { count: postsaleCount })}
        </p>
      ) : null}
    </div>
  );
}
