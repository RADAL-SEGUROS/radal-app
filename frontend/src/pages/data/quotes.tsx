/**
 * Portafolio — **Cotizaciones**: the market conversation, one row per
 * colocación.
 *
 * This tab used to list `quote_request` rows. The broker who filed issue #2
 * does not think in quote requests: he thinks in **colocaciones** — one
 * *asegurado × vigencia × ramo*, i.e. a `case_file(kind=account)` — and asks
 * three questions about them, in this order:
 *
 *  - **En mercado** (`stage=technical_basis, market_submission`) — sent out,
 *    nothing back yet. The follow-up list.
 *  - **Ofertas recibidas** (`stage=quotes_received, comparison`) — cotizaciones
 *    are in and comparable. Each row links straight into its Comparación.
 *  - **Ofertas presentadas** (`stage=insured_decision, proposal_issued`) — the
 *    comparison is in front of the insured; the ball is in his court.
 *
 * The sub-view is the URL's `?sub=`, the stage set is a SERVER-side filter, and
 * the hint under the toggle spells out what the list means — three lists that
 * look alike are only useful if each says what it is.
 *
 * ⚠ `stage` is a REPEATABLE query param. It reaches FastAPI as
 * `stage=technical_basis&stage=market_submission` only through the
 * `paramsSerializer` in `src/lib/api.ts`; axios's default `stage[]=` form
 * matches nothing, and the failure is SILENT — the filter stops applying and
 * the table fills with plausible, wrong rows.
 */
import * as React from "react";
import { Link, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { FileSearch } from "lucide-react";

import { DataTable } from "@/components/common/DataTable";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorBanner, MonoChip } from "@/pages/proposals/shared";
import { StageBadge } from "@/pages/groups/shared";
import { useCaseFiles } from "@/api/caseFiles";
import { formatDate } from "@/lib/format";
import type { CaseFile, CaseStage } from "@/api/types";

import { accountRoute } from "./accounts";
import { PAGE_SIZE, ResultCount, usePageIndex, useSetUrlParams, useUrlParam } from "./shared";
import { useScopeParams } from "@/components/common/ScopeFilter";

const SUB_VIEWS = ["inMarket", "received", "presented"] as const;
type SubView = (typeof SUB_VIEWS)[number];

/**
 * Sub-view → the stages it means. These are the account stages between "the
 * bases técnicas left the desk" and "the insured has the comparison"; anything
 * earlier is Antecedentes work and anything later is a Propuesta or a Póliza,
 * which have their own tabs.
 */
const SUB_STAGES: Record<SubView, CaseStage[]> = {
  inMarket: ["technical_basis", "market_submission"],
  received: ["quotes_received", "comparison"],
  presented: ["insured_decision", "proposal_issued"],
};

/** Where the Comparación of this account lives. */
function comparisonRoute(row: CaseFile): string {
  // Inside a group the comparison is a TAB of the account page, so the broker
  // keeps the tree and the journey around it. A group-less folder (legacy /
  // imported) has no such page — it gets the standalone worktable, which is
  // the same screen the account tab itself links to.
  return row.account_group_id
    ? `${accountRoute(row)}?tab=comparison`
    : `/comparisons/${row.id}`;
}

export default function QuotesTab() {
  const { t } = useTranslation("analytics");
  const navigate = useNavigate();

  const [subParam] = useUrlParam("sub");
  const [pageIndex, setPageIndex] = usePageIndex();
  const setParams = useSetUrlParams();

  const sub: SubView = SUB_VIEWS.includes(subParam as SubView)
    ? (subParam as SubView)
    : "inMarket";

  // A page index on one stage set is meaningless on another.
  const switchSub = (next: string) => setParams({ sub: next, page: null });

  const scope = useScopeParams();
  const list = useCaseFiles({
    // The page-level scope (grupo · grupo-cuenta · fechas) is applied
    // SERVER-side, exactly like the export, so the table and the file the
    // broker downloads can never disagree about what was filtered.
    ...scope,
    kind: ["account"],
    stage: SUB_STAGES[sub],
    page: pageIndex + 1,
    page_size: PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  const showInsurers = sub !== "inMarket";
  const showCompare = sub === "received";

  const columns = React.useMemo<ColumnDef<CaseFile>[]>(() => {
    const base: ColumnDef<CaseFile>[] = [
      {
        accessorKey: "client_legal_name",
        header: t("columns.client"),
        cell: ({ row }) => (
          <div className="flex min-w-0 flex-col gap-1">
            <span className="truncate font-medium text-text-primary">
              {row.original.client_legal_name ?? row.original.title}
            </span>
            {row.original.client_rut ? (
              <MonoChip>{row.original.client_rut}</MonoChip>
            ) : null}
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
        accessorKey: "account_group_name",
        // The group IS the asegurado in the broker's vocabulary — `scope.group`
        // is the one label for that entity, and the scope chip above the table
        // already uses it.
        header: t("scope.group"),
        cell: ({ row }) => (
          <span className="truncate text-text-secondary">
            {row.original.account_group_name ?? "—"}
          </span>
        ),
      },
    ];

    if (showInsurers) {
      base.push({
        id: "quoting_insurers",
        header: t("quotesSub.insurers"),
        cell: ({ row }) => <QuotingInsurers row={row.original} />,
      });
    }

    base.push(
      {
        accessorKey: "stage",
        header: t("columns.stage"),
        cell: ({ row }) => <StageBadge stage={row.original.stage} />,
      },
      {
        accessorKey: "updated_at",
        header: t("columns.updatedAt", { defaultValue: "Actualizado" }),
        cell: ({ row }) => formatDate(row.original.updated_at),
      },
    );

    if (showCompare) {
      base.push({
        id: "compare",
        header: "",
        cell: ({ row }) => (
          <div className="flex justify-end">
            <Button
              asChild
              variant="ghost"
              size="sm"
              // The row itself navigates to the account; this link goes one
              // level deeper, so it must not fire both.
              onClick={(event) => event.stopPropagation()}
            >
              <Link to={comparisonRoute(row.original)}>{t("quotesSub.compare")}</Link>
            </Button>
          </div>
        ),
      });
    }

    return base;
  }, [t, showInsurers, showCompare]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-3">
          <Tabs value={sub} onValueChange={switchSub}>
            <TabsList>
              {SUB_VIEWS.map((view) => (
                <TabsTrigger key={view} value={view}>
                  {t(`quotesSub.${view}`)}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <div className="ms-auto">
            <ResultCount total={list.data?.total} />
          </div>
        </div>
        {/* Three lists of the same shape are only useful if each says what it
            is — the hint is the difference between them. */}
        <p className="text-caption text-text-muted">{t(`quotesSub.${sub}Hint`)}</p>
      </div>

      <ErrorBanner error={list.error} />

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
            icon={<FileSearch className="h-6 w-6" />}
            title={t("empty.quotes")}
            hint={t("emptyHint.quotes")}
          />
        }
      />
    </div>
  );
}

/**
 * The insurers that answered, as badges.
 *
 * `quoting_insurers` / `proposals_count` are OPTIONAL on the list item: read
 * them defensively so a server that has not (yet) got the fields renders
 * "ninguna todavía" instead of crashing the table.
 */
function QuotingInsurers({ row }: { row: CaseFile }) {
  const { t } = useTranslation("analytics");
  const insurers = row.quoting_insurers ?? [];
  const count = row.proposals_count ?? 0;

  if (insurers.length === 0) {
    // No named insurers but cotizaciones on file: say how many rather than
    // claiming the market is silent.
    if (count > 0) {
      return (
        <Badge variant="neutral">
          {count} · {t("quotesSub.offers")}
        </Badge>
      );
    }
    return <span className="text-text-muted">{t("quotesSub.noInsurers")}</span>;
  }

  const shown = insurers.slice(0, 3);
  const rest = insurers.length - shown.length;

  return (
    <div className="flex flex-wrap items-center gap-1">
      {shown.map((insurer) => (
        <Badge key={insurer.id} variant="neutral">
          {insurer.name}
        </Badge>
      ))}
      {rest > 0 ? <span className="text-caption text-text-muted">+{rest}</span> : null}
    </div>
  );
}
