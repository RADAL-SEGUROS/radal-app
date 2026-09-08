/**
 * Expedientes — the list the broker actually navigates by.
 *
 * The board on top is `GET /case-files/summary` (counts by kind and stage); the
 * table below is `GET /case-files` with the same filters the server accepts, so
 * a filter chip is never a client-side illusion over a truncated page.
 */
import * as React from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { AlarmClock, FolderOpen, Layers, Search } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { FadeUp } from "@/components/common/motion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ErrorBanner, MonoChip, StatusBadge } from "@/pages/proposals/shared";
import { useCaseFiles, useCaseFilesSummary } from "@/api/caseFiles";
import { formatDate } from "@/lib/format";
import { CASE_FILE_KINDS, type CaseFile, type CaseFileKind } from "@/api/types";

export default function CasesPage() {
  const { t } = useTranslation("cases");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();

  const kindParam = params.get("kind") as CaseFileKind | null;
  const [query, setQuery] = React.useState(params.get("q") ?? "");

  const summary = useCaseFilesSummary();
  const list = useCaseFiles({
    kind: kindParam ? [kindParam] : undefined,
    q: params.get("q") ?? undefined,
    page_size: 50,
  });

  const applyQuery = () => {
    const next = new URLSearchParams(params);
    if (query.trim()) next.set("q", query.trim());
    else next.delete("q");
    setParams(next, { replace: true });
  };

  const setKind = (kind: CaseFileKind | null) => {
    const next = new URLSearchParams(params);
    if (kind) next.set("kind", kind);
    else next.delete("kind");
    setParams(next, { replace: true });
  };

  const columns = React.useMemo<ColumnDef<CaseFile>[]>(
    () => [
      {
        accessorKey: "reference",
        header: t("table.reference"),
        cell: ({ row }) => <MonoChip>{row.original.reference ?? `#${row.original.id}`}</MonoChip>,
      },
      {
        accessorKey: "title",
        header: t("table.title"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate font-medium text-text-primary">{row.original.title}</p>
            <p className="truncate text-caption text-text-muted">
              {row.original.client_legal_name ?? "—"}
            </p>
          </div>
        ),
      },
      {
        accessorKey: "kind",
        header: t("table.kind"),
        cell: ({ row }) => (
          <Badge variant="neutral">
            {t(`kinds.${row.original.kind}`, { defaultValue: row.original.kind })}
          </Badge>
        ),
      },
      {
        accessorKey: "stage",
        header: t("table.stage"),
        cell: ({ row }) => (
          <Badge variant="brand">
            {t(`stages.${row.original.stage}`, { defaultValue: row.original.stage })}
          </Badge>
        ),
      },
      {
        accessorKey: "status",
        header: t("table.status"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.status}
            label={t(`statuses.${row.original.status}`, { defaultValue: row.original.status })}
          />
        ),
      },
      {
        accessorKey: "insurance_line_name",
        header: t("table.line"),
        cell: ({ row }) => row.original.insurance_line_name ?? "—",
      },
      {
        accessorKey: "documents_count",
        header: t("table.documents"),
        cell: ({ row }) => (
          <span className="tabular-nums">{row.original.documents_count}</span>
        ),
      },
      {
        accessorKey: "due_at",
        header: t("table.due"),
        cell: ({ row }) => formatDate(row.original.due_at),
      },
    ],
    [t],
  );

  return (
    <div className="flex flex-col gap-5">
      <PageHeader title={t("list.title")} subtitle={t("list.subtitle")} />

      <FadeUp>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <KpiCard
            label={t("kpi.total")}
            countTo={summary.data?.total ?? 0}
            icon={<FolderOpen />}
          />
          <KpiCard
            label={t("kpi.open")}
            countTo={summary.data?.open ?? 0}
            tone="action"
            icon={<Layers />}
          />
          <KpiCard
            label={t("kpi.overdue")}
            countTo={summary.data?.overdue ?? 0}
            tone="danger"
            icon={<AlarmClock />}
          />
          <KpiCard
            label={t("kpi.accounts")}
            countTo={summary.data?.by_kind?.account ?? 0}
            tone="brand"
            icon={<FolderOpen />}
          />
        </div>
      </FadeUp>

      <FadeUp delay={0.04}>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant={kindParam ? "secondary" : "teal"}
            onClick={() => setKind(null)}
          >
            {t("filters.all")}
          </Button>
          {CASE_FILE_KINDS.map((kind) => (
            <Button
              key={kind}
              size="sm"
              variant={kindParam === kind ? "teal" : "secondary"}
              onClick={() => setKind(kind)}
            >
              {t(`kinds.${kind}`)}
              <Badge variant="muted" className="ml-1 px-1.5 py-0">
                {summary.data?.by_kind?.[kind] ?? 0}
              </Badge>
            </Button>
          ))}

          <div className="ml-auto flex items-center gap-2">
            <Input
              value={query}
              placeholder={t("filters.searchPlaceholder")}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") applyQuery();
              }}
              className="w-[240px]"
            />
            <Button size="sm" variant="secondary" onClick={applyQuery}>
              <Search className="h-4 w-4" />
              {tc("actions.search")}
            </Button>
          </div>
        </div>
      </FadeUp>

      {list.isError ? <ErrorBanner error={list.error} /> : null}

      <FadeUp delay={0.08}>
        <DataTable
          columns={columns}
          data={list.data?.items ?? []}
          isLoading={list.isLoading}
          emptyMessage={t("list.empty")}
          onRowClick={(row) => navigate(`/cases/${row.id}`)}
        />
      </FadeUp>
    </div>
  );
}
