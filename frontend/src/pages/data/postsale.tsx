/**
 * Analítica — Post-venta (spec v4 §4.2 last row).
 *
 * One tab, three sub-views behind a segmented toggle: Endosos / Cobranzas /
 * Siniestros. Each sub-view renders ONLY when its module grants `View`
 * (`Endorsements` / `Collections` / `Claims`) — the parent tab is shown when
 * any of the three is granted, and the toggle hides the others. The active
 * sub-view persists in `?sub=`; its status chip filters server-side.
 *
 * Money rules surfaced here:
 *  - endorsement deltas CARRY A SIGN (never an absolute value) — rendered with
 *    an explicit `+` so an increase and a refund read differently;
 *  - the collection ledger column is Σ `gross_amount_uf` over the plan's
 *    instalments — the figure the invariant compares against the policy gross.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import { FileEdit, Landmark, Siren } from "lucide-react";

import { DataTable } from "@/components/common/DataTable";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { EmptyState, ErrorBanner, MonoChip, StatusBadge, uf } from "@/pages/proposals/shared";
import { useEndorsements } from "@/api/endorsements";
import { useCollectionPlans } from "@/api/collections";
import { useClaims } from "@/api/claims";
import { usePermissions, can } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import {
  CLAIM_STATUSES,
  COLLECTION_PLAN_STATUSES,
  ENDORSEMENT_STATUSES,
  type Claim,
  type ClaimStatus,
  type CollectionPlan,
  type CollectionPlanStatus,
  type Endorsement,
  type EndorsementStatus,
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

type SubView = "endorsements" | "collections" | "claims";

/** A signed UF delta: `+UF 10,54` / `−UF 1,86` / `—`. The sign is the meaning. */
function signedUf(value: string | null): string {
  if (value === null || value === undefined) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return "—";
  return n > 0 ? `+${uf(value)}` : uf(value);
}

export default function PostsaleTab() {
  const { t } = useTranslation("analytics");
  const perms = usePermissions();

  const granted = React.useMemo<SubView[]>(() => {
    const out: SubView[] = [];
    if (can(perms.data, "Endorsements", "View")) out.push("endorsements");
    if (can(perms.data, "Collections", "View")) out.push("collections");
    if (can(perms.data, "Claims", "View")) out.push("claims");
    return out;
  }, [perms.data]);

  const [subParam] = useUrlParam("sub");
  const [status] = useUrlParam("status");
  const setParams = useSetUrlParams();
  const sub: SubView =
    subParam && granted.includes(subParam as SubView)
      ? (subParam as SubView)
      : (granted[0] ?? "endorsements");

  // One batched update: status vocabularies differ per sub-view and a page
  // index is meaningless on another table, so both reset with the switch.
  const switchSub = (next: string) => setParams({ sub: next, status: null, page: null });
  const setStatus = (value: string | null) => setParams({ status: value, page: null });

  if (perms.isLoading) return null;
  if (granted.length === 0) return null; // the parent tab is already hidden

  return (
    <div className="flex flex-col gap-3">
      {granted.length > 1 ? (
        <Tabs value={sub} onValueChange={switchSub}>
          <TabsList>
            {granted.map((view) => (
              <TabsTrigger key={view} value={view}>
                {t(`postsale.${view}`)}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
      ) : null}

      {sub === "endorsements" ? (
        <EndorsementsView status={status} setStatus={setStatus} />
      ) : sub === "collections" ? (
        <CollectionsView status={status} setStatus={setStatus} />
      ) : (
        <ClaimsView status={status} setStatus={setStatus} />
      )}
    </div>
  );
}

interface SubViewProps {
  status: string | null;
  setStatus: (value: string | null) => void;
}

// =============================================================================
// Endosos
// =============================================================================

function EndorsementsView({ status, setStatus }: SubViewProps) {
  const { t } = useTranslation("analytics");
  const { t: tPostsale } = useTranslation("postsale");
  const navigate = useNavigate();

  const [pageIndex, setPageIndex] = usePageIndex();
  const scope = useScopeParams();
  const list = useEndorsements({
    // The page-level scope (grupo · grupo-cuenta · fechas) is applied
    // SERVER-side, exactly like the export, so the table and the file the
    // broker downloads can never disagree about what was filtered.
    ...scope,
    status: (status as EndorsementStatus) || undefined,
    limit: PAGE_SIZE,
    offset: pageIndex * PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  const statusOptions = React.useMemo(
    () =>
      ENDORSEMENT_STATUSES.map((s) => ({
        value: s,
        label: tPostsale(`endorsement.status.${s}`, { defaultValue: s }),
      })),
    [tPostsale],
  );

  const columns = React.useMemo<ColumnDef<Endorsement>[]>(
    () => [
      {
        accessorKey: "endorsement_number",
        header: t("columns.reference"),
        cell: ({ row }) => (
          <MonoChip>
            {row.original.endorsement_number ?? `E${row.original.sequence_no}`}
          </MonoChip>
        ),
      },
      {
        accessorKey: "kind",
        header: t("columns.kind"),
        cell: ({ row }) => (
          <Badge variant="neutral">
            {tPostsale(`endorsement.kind.${row.original.kind}`, {
              defaultValue: row.original.kind,
            })}
          </Badge>
        ),
      },
      {
        id: "delta",
        header: () => <span className="block text-right">{t("columns.delta")}</span>,
        accessorFn: (row) => Number(row.total_premium_delta_uf ?? 0),
        cell: ({ row }) => (
          <span className="cell-num block">
            {signedUf(row.original.total_premium_delta_uf)}
          </span>
        ),
      },
      {
        accessorKey: "effective_at",
        header: t("columns.effectiveAt"),
        cell: ({ row }) => formatDate(row.original.effective_at),
      },
      {
        accessorKey: "status",
        header: t("columns.status"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.status}
            label={tPostsale(`endorsement.status.${row.original.status}`, {
              defaultValue: row.original.status,
            })}
          />
        ),
      },
    ],
    [t, tPostsale],
  );

  return (
    <>
      <FilterRow
        isFiltered={!!status}
        onClear={() => setStatus(null)}
        trailing={<ResultCount total={list.data?.total} />}
      >
        <FilterChip
          label={t("filters.status")}
          value={status}
          options={statusOptions}
          onChange={setStatus}
        />
      </FilterRow>
      <ErrorBanner error={list.error} />
      <DataTable
        columns={columns}
        data={items}
        isLoading={list.isLoading}
        pageIndex={pageIndex}
        pageSize={PAGE_SIZE}
        total={list.data?.total}
        onPageChange={setPageIndex}
        onRowClick={(row) => navigate(`/endorsements/${row.id}`)}
        emptyMessage={
          <EmptyState
            icon={<FileEdit className="h-6 w-6" />}
            title={t("empty.postsale")}
            hint={t("emptyHint.postsale")}
          />
        }
      />
    </>
  );
}

// =============================================================================
// Cobranzas
// =============================================================================

function CollectionsView({ status, setStatus }: SubViewProps) {
  const { t } = useTranslation("analytics");
  const { t: tPostsale } = useTranslation("postsale");
  const navigate = useNavigate();

  const [pageIndex, setPageIndex] = usePageIndex();
  const scope = useScopeParams();
  const list = useCollectionPlans({
    // The page-level scope (grupo · grupo-cuenta · fechas) is applied
    // SERVER-side, exactly like the export, so the table and the file the
    // broker downloads can never disagree about what was filtered.
    ...scope,
    status: (status as CollectionPlanStatus) || undefined,
    limit: PAGE_SIZE,
    offset: pageIndex * PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  const statusOptions = React.useMemo(
    () =>
      COLLECTION_PLAN_STATUSES.map((s) => ({
        value: s,
        label: tPostsale(`collection.status.${s}`, { defaultValue: s }),
      })),
    [tPostsale],
  );

  const columns = React.useMemo<ColumnDef<CollectionPlan>[]>(
    () => [
      {
        accessorKey: "plan_number",
        header: t("columns.plan"),
        cell: ({ row }) => (
          <MonoChip>{row.original.plan_number ?? `#${row.original.id}`}</MonoChip>
        ),
      },
      {
        accessorKey: "payment_mode",
        header: t("columns.paymentMode"),
        cell: ({ row }) =>
          tPostsale(`collection.paymentMode.${row.original.payment_mode}`, {
            defaultValue: row.original.payment_mode,
          }),
      },
      {
        id: "installments",
        header: () => <span className="block text-right">{t("columns.installments")}</span>,
        accessorFn: (row) => row.installments.length,
        cell: ({ row }) => (
          <span className="cell-num block">{row.original.installments.length}</span>
        ),
      },
      {
        id: "ledger",
        header: () => <span className="block text-right">{t("columns.ledgerTotal")}</span>,
        accessorFn: (row) =>
          row.installments.reduce((acc, i) => acc + Number(i.gross_amount_uf ?? 0), 0),
        cell: ({ row }) => {
          const total = row.original.installments.reduce(
            (acc, i) => acc + Number(i.gross_amount_uf ?? 0),
            0,
          );
          return <span className="cell-num block">{uf(total)}</span>;
        },
      },
      {
        accessorKey: "status",
        header: t("columns.status"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.status}
            label={tPostsale(`collection.status.${row.original.status}`, {
              defaultValue: row.original.status,
            })}
          />
        ),
      },
    ],
    [t, tPostsale],
  );

  return (
    <>
      <FilterRow
        isFiltered={!!status}
        onClear={() => setStatus(null)}
        trailing={<ResultCount total={list.data?.total} />}
      >
        <FilterChip
          label={t("filters.status")}
          value={status}
          options={statusOptions}
          onChange={setStatus}
        />
      </FilterRow>
      <ErrorBanner error={list.error} />
      <DataTable
        columns={columns}
        data={items}
        isLoading={list.isLoading}
        pageIndex={pageIndex}
        pageSize={PAGE_SIZE}
        total={list.data?.total}
        onPageChange={setPageIndex}
        onRowClick={(row) => navigate(`/collections/${row.id}`)}
        emptyMessage={
          <EmptyState
            icon={<Landmark className="h-6 w-6" />}
            title={t("empty.postsale")}
            hint={t("emptyHint.postsale")}
          />
        }
      />
    </>
  );
}

// =============================================================================
// Siniestros
// =============================================================================

function ClaimsView({ status, setStatus }: SubViewProps) {
  const { t } = useTranslation("analytics");
  const { t: tPostsale } = useTranslation("postsale");
  const navigate = useNavigate();

  const [pageIndex, setPageIndex] = usePageIndex();
  const scope = useScopeParams();
  const list = useClaims({
    // The page-level scope (grupo · grupo-cuenta · fechas) is applied
    // SERVER-side, exactly like the export, so the table and the file the
    // broker downloads can never disagree about what was filtered.
    ...scope,
    status: (status as ClaimStatus) || undefined,
    limit: PAGE_SIZE,
    offset: pageIndex * PAGE_SIZE,
  });
  const items = list.data?.items ?? [];

  const statusOptions = React.useMemo(
    () =>
      CLAIM_STATUSES.map((s) => ({
        value: s,
        label: tPostsale(`claim.status.${s}`, { defaultValue: s }),
      })),
    [tPostsale],
  );

  const columns = React.useMemo<ColumnDef<Claim>[]>(
    () => [
      {
        accessorKey: "claim_number",
        header: t("columns.claimNumber"),
        cell: ({ row }) => (
          <MonoChip>{row.original.claim_number ?? `#${row.original.id}`}</MonoChip>
        ),
      },
      {
        accessorKey: "policy_number",
        header: t("columns.policyNumber"),
        cell: ({ row }) =>
          row.original.policy_number ? (
            <MonoChip>{row.original.policy_number}</MonoChip>
          ) : (
            "—"
          ),
      },
      {
        accessorKey: "kind",
        header: t("columns.kind"),
        cell: ({ row }) => (
          <span className="truncate text-text-secondary">{row.original.kind ?? "—"}</span>
        ),
      },
      {
        accessorKey: "event_date",
        header: t("columns.eventDate"),
        cell: ({ row }) => formatDate(row.original.event_date),
      },
      {
        id: "paid",
        header: () => <span className="block text-right">{t("columns.paid")}</span>,
        accessorFn: (row) => Number(row.paid_amount_uf ?? 0),
        cell: ({ row }) => (
          <span className="cell-num block">{uf(row.original.paid_amount_uf)}</span>
        ),
      },
      {
        accessorKey: "coverage_ruling",
        header: t("columns.ruling"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.coverage_ruling}
            label={tPostsale(`claim.ruling.${row.original.coverage_ruling}`, {
              defaultValue: row.original.coverage_ruling,
            })}
          />
        ),
      },
      {
        accessorKey: "status",
        header: t("columns.status"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.status}
            label={tPostsale(`claim.status.${row.original.status}`, {
              defaultValue: row.original.status,
            })}
          />
        ),
      },
    ],
    [t, tPostsale],
  );

  return (
    <>
      <FilterRow
        isFiltered={!!status}
        onClear={() => setStatus(null)}
        trailing={<ResultCount total={list.data?.total} />}
      >
        <FilterChip
          label={t("filters.status")}
          value={status}
          options={statusOptions}
          onChange={setStatus}
        />
      </FilterRow>
      <ErrorBanner error={list.error} />
      <DataTable
        columns={columns}
        data={items}
        isLoading={list.isLoading}
        pageIndex={pageIndex}
        pageSize={PAGE_SIZE}
        total={list.data?.total}
        onPageChange={setPageIndex}
        onRowClick={(row) => navigate(`/claims/${row.id}`)}
        emptyMessage={
          <EmptyState
            icon={<Siren className="h-6 w-6" />}
            title={t("empty.postsale")}
            hint={t("emptyHint.postsale")}
          />
        }
      />
    </>
  );
}
