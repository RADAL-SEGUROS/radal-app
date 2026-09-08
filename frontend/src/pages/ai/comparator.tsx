/**
 * Comparador (`/ai/comparator`) — spec v3 §5.4.
 *
 * The comparator itself already exists and lives where the data does:
 * `GET /quotes/{id}/comparison` rendered by `pages/proposals/compare.tsx` at
 * `/quotes/:quoteId/comparison`. This page is the *door* to it — the broker who
 * thinks "compare something" rather than "open quote COT-0004".
 *
 * It therefore only answers one question: which quote requests are actually
 * comparable? A comparison needs at least two proposals against the same quote
 * (one column is not a comparison), so the list is `GET /quotes` narrowed on
 * `proposal_count >= 2` and every row links to the existing comparator. Quotes
 * with exactly one proposal are counted, not listed — that count is the honest
 * explanation for a short list.
 *
 * No new endpoint, no second comparator implementation.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { ArrowRight, Columns3, Scale } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { DataTable } from "@/components/common/DataTable";
import { KpiCard } from "@/components/common/KpiCard";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { useCan } from "@/lib/permissions";
import { useQuotes } from "@/api/quotes";
import { type QuoteRequest } from "@/api/types";
import {
  DisabledHint,
  EmptyState,
  ErrorBanner,
  MonoChip,
  StatusBadge,
  uf,
} from "@/pages/proposals/shared";

const PAGE_SIZE = 100;
/** One column is not a comparison. */
const MIN_PROPOSALS = 2;

export default function AiComparatorPage() {
  const { t } = useTranslation("accounts");
  const { t: tq } = useTranslation("quotes");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const canViewQuotes = useCan("Quotes", "View");
  const canCompare = useCan("Proposals", "View");

  const [search, setSearch] = React.useState("");
  const [debounced, setDebounced] = React.useState("");

  React.useEffect(() => {
    const id = window.setTimeout(() => setDebounced(search.trim()), 300);
    return () => window.clearTimeout(id);
  }, [search]);

  const quotes = useQuotes(
    { search: debounced || undefined, limit: PAGE_SIZE },
    canViewQuotes.allowed,
  );

  const items = quotes.data?.items ?? [];
  const comparable = React.useMemo(
    () => items.filter((q) => (q.proposal_count ?? 0) >= MIN_PROPOSALS),
    [items],
  );
  const waiting = React.useMemo(
    () => items.filter((q) => (q.proposal_count ?? 0) === 1).length,
    [items],
  );
  const proposals = React.useMemo(
    () => comparable.reduce((acc, q) => acc + (q.proposal_count ?? 0), 0),
    [comparable],
  );

  const open = (quote: QuoteRequest) => {
    if (!canCompare.allowed) return;
    navigate(`/quotes/${quote.id}/comparison`);
  };

  const columns = React.useMemo<ColumnDef<QuoteRequest>[]>(
    () => [
      {
        accessorKey: "id",
        header: () => t("ai.comparator.table.quote"),
        cell: ({ row }) => <MonoChip>COT-{String(row.original.id).padStart(4, "0")}</MonoChip>,
      },
      {
        accessorKey: "insured_object",
        header: () => t("ai.comparator.table.object"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-text-primary">
              {row.original.insured_object || tq("table.noObject")}
            </div>
            <div className="truncate text-caption text-text-muted">
              {row.original.placement?.period ?? "—"}
            </div>
          </div>
        ),
      },
      {
        id: "declared",
        header: () => t("ai.comparator.table.declared"),
        accessorFn: (row) => row.declared_value_uf ?? "",
        cell: ({ row }) => (
          <span className="tabular-nums text-text-primary">
            {uf(row.original.declared_value_uf)}
          </span>
        ),
      },
      {
        id: "proposals",
        header: () => t("ai.comparator.table.proposals"),
        accessorFn: (row) => row.proposal_count,
        cell: ({ row }) => (
          <span className="tabular-nums text-text-primary">
            {t("ai.comparator.proposalsCount", { count: row.original.proposal_count })}
          </span>
        ),
      },
      {
        accessorKey: "status",
        header: () => t("ai.comparator.table.status"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.status}
            label={tq(`status.${row.original.status}`, { defaultValue: row.original.status })}
          />
        ),
      },
      {
        id: "action",
        header: () => "",
        cell: ({ row }) => (
          <DisabledHint
            hint={
              canCompare.isLoading
                ? tc("actions.loading")
                : canCompare.allowed
                  ? null
                  : t("ai.comparator.noPermission")
            }
          >
            <Button
              size="sm"
              variant="secondary"
              disabled={!canCompare.allowed}
              onClick={(e) => {
                e.stopPropagation();
                open(row.original);
              }}
            >
              {t("ai.comparator.open")}
              <ArrowRight className="h-4 w-4" />
            </Button>
          </DisabledHint>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [t, tq, tc, canCompare.allowed, canCompare.isLoading],
  );

  if (!canViewQuotes.isLoading && !canViewQuotes.allowed) {
    return (
      <>
        <PageHeader title={t("nav.comparator")} subtitle={t("ai.comparator.subtitle")} />
        <Card>
          <EmptyState
            icon={<Scale className="h-6 w-6" />}
            title={t("ai.comparator.noPermission")}
          />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader title={t("nav.comparator")} subtitle={t("ai.comparator.subtitle")} />

      <Stagger className="grid grid-cols-2 gap-[18px] lg:grid-cols-3">
        <KpiCard
          label={t("ai.comparator.kpi.comparable")}
          countTo={comparable.length}
          icon={<Columns3 />}
          tone="brand"
        />
        <KpiCard
          label={t("ai.comparator.kpi.proposals")}
          countTo={proposals}
          icon={<Scale />}
          tone="action"
        />
        <KpiCard
          label={t("ai.comparator.kpi.waiting")}
          countTo={waiting}
          icon={<ArrowRight />}
          tone="warn"
          hint={t("ai.comparator.waitingHint")}
        />
      </Stagger>

      <FadeUp delay={0.06}>
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("ai.comparator.search")}
            className="h-9 max-w-xs"
          />
          {debounced ? (
            <Button variant="ghost" size="sm" onClick={() => setSearch("")}>
              {tc("actions.clear")}
            </Button>
          ) : null}
          <span className="ml-auto text-caption text-text-muted">
            {t("ai.comparator.showing", {
              shown: comparable.length,
              total: quotes.data?.total ?? 0,
            })}
          </span>
          {/*
           * `proposal_count` is only known per row, so "comparable" is decided
           * client-side over the fetched page. Beyond `PAGE_SIZE` the KPIs and
           * the list are page-scoped — say so rather than imply the book is
           * empty.
           */}
          {(quotes.data?.total ?? 0) > PAGE_SIZE ? (
            <span className="basis-full text-caption text-signal-warn">
              {t("ai.comparator.truncated", { limit: PAGE_SIZE })}
            </span>
          ) : null}
        </Card>
      </FadeUp>

      {quotes.isError ? <ErrorBanner error={quotes.error} /> : null}

      <FadeUp delay={0.1}>
        {!quotes.isLoading && comparable.length === 0 ? (
          <Card>
            <EmptyState
              icon={<Scale className="h-6 w-6" />}
              title={t("ai.comparator.empty")}
              hint={
                waiting
                  ? t("ai.comparator.waiting", { count: waiting })
                  : t("ai.comparator.emptyHint")
              }
            />
          </Card>
        ) : (
          <DataTable
            columns={columns}
            data={comparable}
            isLoading={quotes.isLoading}
            onRowClick={canCompare.allowed ? open : undefined}
          />
        )}
      </FadeUp>
    </>
  );
}
