/**
 * `/policies` — Pólizas, the main view (v12, lane F1).
 *
 * Journeys are partial: a policy may arrive from a PDF, a hand entry or the
 * full market process, and this list is where the broker works them all.
 *
 * - KPI cards from `GET /policies/summary`; a card that has a matching list
 *   filter applies it on click (and clears it on a second click).
 * - Filters map 1:1 to `GET /policies` (`q`, `line_code`, `subtype_id`,
 *   `insurer_id`, `status`, `collection`, `missing`, `owner=me`) and live in the
 *   URL, so a filtered view is a shareable link and nothing is filtered on the
 *   client.
 * - ≥ md: the table; < md: a card list (brokers check progress from phones).
 * - Actions: "Subir póliza" (the one primary) and "Registrar a mano".
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ColumnDef } from "@tanstack/react-table";
import {
  AlertTriangle,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  FileWarning,
  FileX2,
  Lock,
  PencilLine,
  ShieldCheck,
  SlidersHorizontal,
  Upload,
  User,
  Wallet,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { DataTable } from "@/components/common/DataTable";
import { FadeUp, Stagger, CountUp } from "@/components/common/motion";
import { EmptyState, ErrorBanner, LoadingRows } from "@/components/common/kit";
import { TouchHint } from "@/components/policies/TouchHint";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { cn } from "@/lib/utils";
import { formatUF } from "@/lib/format";
import { useCan } from "@/lib/permissions";
import { useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import { useCatalogLines } from "@/api/catalog";
import { useInsurers } from "@/api/insurers";
import {
  POLICY_COLLECTION_FILTERS,
  POLICY_COMPUTED_STATUSES,
  POLICY_MISSING_KEYS,
  usePolicies,
  usePoliciesSummary,
  type PolicyCollectionFilter,
  type PolicyComputedStatus,
  type PolicyMissingKey,
  type PolicyRow,
} from "@/api/policies";
import { PolicyUploadDialog } from "@/components/policies/PolicyUploadDialog";
import {
  CollectionCell,
  InsuredCell,
  MissingChips,
  NumberCell,
  PeriodCell,
  PremiumCell,
  RamoCell,
  StatusCell,
} from "@/components/policies/PolicyCells";
import {
  COLLECTION_FILTER_LABEL,
  COMPUTED_STATUS_LABEL,
  MISSING_LABEL,
} from "@/components/policies/policyLabels";

const PAGE_SIZE = 25;
const ALL = "all";

function oneOf<T extends string>(values: readonly T[], value: string | null): T | null {
  return value && (values as readonly string[]).includes(value) ? (value as T) : null;
}

function positiveInt(value: string | null): number | undefined {
  const n = Number(value);
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

// =============================================================================
// KPI tile — a KpiCard look that can also be a filter toggle
// =============================================================================

function KpiTile({
  label,
  value,
  hint,
  icon,
  tone = "default",
  active,
  onClick,
}: {
  label: string;
  value: React.ReactNode;
  hint?: React.ReactNode;
  icon: React.ReactNode;
  tone?: "default" | "danger" | "warn";
  active?: boolean;
  onClick?: () => void;
}) {
  const body = (
    <>
      <div className="flex items-start justify-between gap-2">
        <div className="text-caption font-medium text-ink-3">{label}</div>
        <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm bg-brand-soft text-brand-deep [&_svg]:h-4 [&_svg]:w-4">
          {icon}
        </span>
      </div>
      <div className="mt-2 text-kpi font-semibold tabular-nums tracking-tight text-ink">{value}</div>
      {hint ? (
        <div
          className={cn(
            "mt-[5px] text-caption",
            tone === "danger" ? "text-neg-text" : tone === "warn" ? "text-warn-text" : "text-ink-3",
          )}
        >
          {hint}
        </div>
      ) : null}
    </>
  );
  const base =
    "h-full w-full rounded-card border bg-bone p-[18px] text-left text-ink shadow-elev";
  if (!onClick) {
    return (
      <FadeUp className="h-full">
        <div className={cn(base, "border-line")}>{body}</div>
      </FadeUp>
    );
  }
  return (
    <FadeUp className="h-full">
      <button
        type="button"
        aria-pressed={!!active}
        onClick={onClick}
        className={cn(
          base,
          "transition-[border-color,box-shadow,transform] duration-150 ease-out hover:border-line-strong hover:shadow-elev-hover active:scale-[0.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
          active ? "border-brand ring-1 ring-brand" : "border-line",
        )}
      >
        {body}
      </button>
    </FadeUp>
  );
}

// =============================================================================
// Mobile card
// =============================================================================

function PolicyCard({ policy, onOpen }: { policy: PolicyRow; onOpen: () => void }) {
  const { t } = useTranslation("policies");
  return (
    <button
      type="button"
      onClick={onOpen}
      className="flex w-full flex-col gap-2.5 rounded-card border border-line bg-bone p-4 text-left shadow-elev transition-[border-color,transform] duration-150 ease-out hover:border-line-strong active:scale-[0.98]"
    >
      <div className="flex items-start justify-between gap-2">
        <InsuredCell policy={policy} />
        <StatusCell policy={policy} />
      </div>
      <div className="grid grid-cols-2 gap-x-3 gap-y-2">
        <div className="min-w-0">
          <div className="text-[11px] font-medium text-ink-3">{t("columns.line")}</div>
          <RamoCell policy={policy} />
        </div>
        <div className="min-w-0">
          <div className="text-[11px] font-medium text-ink-3">{t("columns.insurer")}</div>
          <div className="truncate text-ink-2">{policy.insurer_name ?? "—"}</div>
        </div>
        {/* Number and premium share ONE flex row: the premium keeps its
            width on the right, the number takes the rest and truncates only
            past it (full text in the tooltip). In two grid halves a long
            number ("SURA-PYME-71830") ran into "UF 29,75" at 375 px. */}
        <div className="col-span-2 flex min-w-0 items-end justify-between gap-3">
          <div className="min-w-0 flex-1">
            <div className="text-[11px] font-medium text-ink-3">{t("columns.number")}</div>
            <div className="truncate" title={policy.policy_number ?? undefined}>
              <NumberCell policy={policy} />
            </div>
          </div>
          <div className="shrink-0 text-right">
            <div className="text-[11px] font-medium text-ink-3">{t("columns.premium")}</div>
            <PremiumCell policy={policy} />
          </div>
        </div>
        <div className="col-span-2 min-w-0">
          <div className="text-[11px] font-medium text-ink-3">{t("columns.period")}</div>
          <PeriodCell policy={policy} />
        </div>
      </div>
      <div className="flex flex-col gap-2 border-t border-line pt-2.5">
        <div className="min-w-0">
          <div className="mb-1 text-[11px] font-medium text-ink-3">{t("columns.collection")}</div>
          <CollectionCell policy={policy} />
        </div>
        {policy.missing.length > 0 ? (
          <div className="min-w-0">
            <div className="mb-1 text-[11px] font-medium text-ink-3">{t("columns.missing")}</div>
            <MissingChips policy={policy} max={4} />
          </div>
        ) : null}
      </div>
    </button>
  );
}

// =============================================================================
// Page
// =============================================================================

export default function PoliciesListPage() {
  const { t } = useTranslation("policies");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  // ---- URL state -------------------------------------------------------------
  const [qParam] = useUrlParam("q");
  const [lineParam] = useUrlParam("line");
  const [subtypeParam] = useUrlParam("subtype");
  const [insurerParam] = useUrlParam("insurer");
  const [statusParam] = useUrlParam("status");
  const [collectionParam] = useUrlParam("collection");
  const [missingParam] = useUrlParam("missing");
  const [mineParam] = useUrlParam("mine");
  const [pageParam] = useUrlParam("page");
  const setParams = useSetUrlParams();

  const status = oneOf<PolicyComputedStatus>(POLICY_COMPUTED_STATUSES, statusParam);
  const collection = oneOf<PolicyCollectionFilter>(POLICY_COLLECTION_FILTERS, collectionParam);
  const missing = oneOf<PolicyMissingKey>(POLICY_MISSING_KEYS, missingParam);
  const subtypeId = positiveInt(subtypeParam);
  const insurerId = positiveInt(insurerParam);
  const mine = mineParam === "1";
  const page = Math.max(0, Number(pageParam) || 0);

  const setFilter = (updates: Record<string, string | null>) =>
    setParams({ ...updates, page: null });

  // Debounced search box → `?q=`.
  const [term, setTerm] = React.useState(qParam ?? "");
  React.useEffect(() => setTerm(qParam ?? ""), [qParam]);
  React.useEffect(() => {
    const next = term.trim();
    if (next === (qParam ?? "")) return;
    const id = window.setTimeout(() => setFilter({ q: next || null }), 350);
    return () => window.clearTimeout(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [term]);

  const [filtersOpen, setFiltersOpen] = React.useState(false);
  const [uploadOpen, setUploadOpen] = React.useState(false);

  // ---- Permissions -----------------------------------------------------------
  const canView = useCan("Policies", "View");
  const canCreate = useCan("Policies", "Create");
  const canUploadDoc = useCan("Documents", "Upload");
  const canInsurers = useCan("Insurers", "View");

  // ---- Data ------------------------------------------------------------------
  const catalog = useCatalogLines();
  const lines = React.useMemo(
    () => (catalog.data ?? []).filter((line) => !!line.code),
    [catalog.data],
  );
  const selectedLine = lines.find((line) => line.code === lineParam) ?? null;
  const insurers = useInsurers({ limit: 200 }, canInsurers.allowed);
  const summary = usePoliciesSummary(canView.allowed);

  const list = usePolicies(
    {
    q: qParam || undefined,
    line_code: selectedLine?.code ?? undefined,
    subtype_id: subtypeId,
    insurer_id: insurerId,
    status: status ?? undefined,
    collection: collection ? [collection] : undefined,
    missing: missing ? [missing] : undefined,
    owner: mine ? "me" : undefined,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    },
    // The server 403s without Policies.View: never fire a request we know fails.
    canView.allowed,
  );
  const rows = React.useMemo(() => list.data?.items ?? [], [list.data?.items]);
  const total = list.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const activeFilters =
    [selectedLine, subtypeId, insurerId, status, collection, missing].filter(Boolean).length +
    (mine ? 1 : 0);
  const anyFilter = activeFilters > 0 || !!qParam;

  const clearAll = () => {
    setTerm("");
    setParams({
      q: null,
      line: null,
      subtype: null,
      insurer: null,
      status: null,
      collection: null,
      missing: null,
      mine: null,
      page: null,
    });
  };

  // ---- Columns ---------------------------------------------------------------
  const columns = React.useMemo<ColumnDef<PolicyRow>[]>(
    () => [
      {
        id: "insured",
        header: () => t("columns.insured"),
        accessorFn: (row) => row.insured_name ?? row.client_legal_name ?? "",
        cell: ({ row }) => <InsuredCell policy={row.original} />,
      },
      {
        id: "line",
        header: () => t("columns.line"),
        accessorFn: (row) => row.line?.name ?? "",
        cell: ({ row }) => <RamoCell policy={row.original} />,
      },
      {
        id: "period",
        header: () => t("columns.period"),
        accessorFn: (row) => row.start_date ?? "",
        cell: ({ row }) => <PeriodCell policy={row.original} />,
      },
      {
        id: "insurer",
        header: () => t("columns.insurer"),
        accessorFn: (row) => row.insurer_name ?? "",
        cell: ({ row }) => (
          <span className="block max-w-[180px] truncate">{row.original.insurer_name ?? "—"}</span>
        ),
      },
      {
        id: "number",
        header: () => t("columns.number"),
        accessorFn: (row) => row.policy_number,
        cell: ({ row }) => <NumberCell policy={row.original} />,
      },
      {
        id: "premium",
        header: () => t("columns.premium"),
        accessorFn: (row) => Number(row.total_premium_uf ?? 0),
        cell: ({ row }) => <PremiumCell policy={row.original} />,
      },
      {
        id: "collection",
        header: () => t("columns.collection"),
        enableSorting: false,
        cell: ({ row }) => <CollectionCell policy={row.original} />,
      },
      {
        id: "missing",
        header: () => t("columns.missing"),
        enableSorting: false,
        cell: ({ row }) => <MissingChips policy={row.original} />,
      },
      {
        id: "status",
        header: () => t("columns.status"),
        accessorFn: (row) => row.computed_status ?? row.status,
        cell: ({ row }) => <StatusCell policy={row.original} />,
      },
    ],
    [t],
  );

  // ---- KPI toggles -----------------------------------------------------------
  const s = summary.data;
  /** A KPI number, or a quiet dash while the summary loads (never a fake 0). */
  const kpi = (value: number | undefined, format?: (n: number) => string) =>
    summary.isLoading || value === undefined ? "—" : <CountUp value={value} format={format} />;
  // A card is ONE filter: clicking it REPLACES whatever card filter was on
  // (they used to stack — `?collection=no_plan&missing=source_document` → 0
  // rows under cards that said 2, review v12 #10). Clicking the active card
  // clears it. The dropdowns below still combine freely.
  const toggle = (key: "status" | "collection" | "missing", value: string, current: string | null) =>
    setFilter({
      status: null,
      collection: null,
      missing: null,
      [key]: current === value ? null : value,
    });

  const uploadHint = !canCreate.allowed
    ? t("noPermission.create")
    : !canUploadDoc.allowed
      ? t("noPermission.upload")
      : null;

  if (!canView.isLoading && !canView.allowed) {
    return (
      <>
        <PageHeader title={t("title")} subtitle={t("list.subtitle")} />
        <Card>
          <EmptyState
            title={tc("state.noAccess")}
            hint={tc("state.noAccessHint")}
            icon={<Lock className="h-5 w-5" />}
          />
        </Card>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("list.subtitle")}
        actions={
          <>
            <TouchHint hint={canCreate.allowed ? null : t("noPermission.create")}>
              <Button
                variant="secondary"
                disabled={!canCreate.allowed}
                onClick={() => navigate("/policies/new")}
              >
                <PencilLine className="h-4 w-4" />
                {t("actions.manual")}
              </Button>
            </TouchHint>
            <TouchHint hint={uploadHint}>
              <Button disabled={!!uploadHint} onClick={() => setUploadOpen(true)}>
                <Upload className="h-4 w-4" />
                {t("actions.upload")}
              </Button>
            </TouchHint>
          </>
        }
      />

      <PolicyUploadDialog
        open={uploadOpen}
        onOpenChange={setUploadOpen}
        onUploaded={(result) => navigate(`/policies/${result.policy.id}`)}
      />

      {canView.allowed ? (
        <Stagger className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-[18px] xl:grid-cols-6">
          <KpiTile
            label={t("kpi.active")}
            value={kpi(s?.active_count)}
            hint={t("kpi.activeHint")}
            icon={<ShieldCheck />}
            active={status === "active"}
            onClick={() => toggle("status", "active", status)}
          />
          <KpiTile
            label={t("kpi.premium")}
            value={kpi(
              s ? Number(s.active_premium_uf ?? 0) : undefined,
              (n) => formatUF(n, { decimals: 0 }),
            )}
            hint={t("kpi.premiumHint")}
            icon={<Wallet />}
            active={status === "active"}
            onClick={() => toggle("status", "active", status)}
          />
          <KpiTile
            label={t("kpi.overdue")}
            value={kpi(s?.overdue_installments)}
            hint={
              Number(s?.overdue_uf ?? 0) > 0
                ? formatUF(Number(s?.overdue_uf ?? 0), { decimals: 0 })
                : t("kpi.overdueHint")
            }
            tone={(s?.overdue_installments ?? 0) > 0 ? "danger" : "default"}
            icon={<AlertTriangle />}
            active={collection === "overdue"}
            onClick={() => toggle("collection", "overdue", collection)}
          />
          <KpiTile
            label={t("kpi.noPlan")}
            value={kpi(s?.no_plan)}
            hint={t("kpi.noPlanHint")}
            tone={(s?.no_plan ?? 0) > 0 ? "warn" : "default"}
            icon={<FileWarning />}
            active={collection === "no_plan"}
            onClick={() => toggle("collection", "no_plan", collection)}
          />
          <KpiTile
            label={t("kpi.noSource")}
            value={kpi(s?.without_source_document)}
            hint={
              (s?.provisional ?? 0) > 0
                ? t("kpi.provisionalHint", { count: s?.provisional ?? 0 })
                : t("kpi.noSourceHint")
            }
            icon={<FileX2 />}
            active={missing === "source_document"}
            onClick={() => toggle("missing", "source_document", missing)}
          />
          {/* No list filter matches "vence en 90 días" — an honest, inert tile. */}
          <KpiTile
            label={t("kpi.expiring")}
            value={kpi(s?.expiring_90)}
            hint={t("kpi.expiringHint")}
            icon={<CalendarClock />}
          />
        </Stagger>
      ) : null}
      <ErrorBanner error={summary.error} />

      {/* ---- Filters ---- */}
      <FadeUp delay={0.06}>
        <Card className="flex flex-col gap-3 p-3 md:p-4">
          <div className="flex items-center gap-2">
            <Input
              value={term}
              onChange={(e) => setTerm(e.target.value)}
              placeholder={t("filters.search")}
              aria-label={t("filters.search")}
              className="h-10 min-w-0 flex-1 md:h-9 md:max-w-sm"
            />
            <Button
              variant="secondary"
              className="h-10 shrink-0 md:hidden"
              aria-expanded={filtersOpen}
              onClick={() => setFiltersOpen((v) => !v)}
            >
              <SlidersHorizontal className="h-4 w-4" />
              {t("filters.toggle")}
              {activeFilters > 0 ? (
                <Badge variant="brand" className="px-1.5 py-0">
                  {activeFilters}
                </Badge>
              ) : null}
            </Button>
            <span className="ml-auto hidden text-caption text-ink-3 md:inline">
              {t("list.resultCount", { count: total })}
            </span>
          </div>

          <div
            className={cn(
              "grid-cols-1 gap-2.5 sm:grid-cols-2 md:grid md:grid-cols-3 lg:grid-cols-4 xl:flex xl:flex-wrap xl:items-center",
              filtersOpen ? "grid" : "hidden",
            )}
          >
            <Select
              value={selectedLine?.code ?? ALL}
              onValueChange={(v) => setFilter({ line: v === ALL ? null : v, subtype: null })}
            >
              <SelectTrigger className="h-10 md:h-9 xl:w-[180px]" aria-label={t("filters.line")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("filters.allLines")}</SelectItem>
                {lines.map((line) => (
                  <SelectItem key={line.id} value={line.code as string}>
                    {line.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={subtypeId ? String(subtypeId) : ALL}
              onValueChange={(v) => setFilter({ subtype: v === ALL ? null : v })}
              disabled={!selectedLine}
            >
              <SelectTrigger className="h-10 md:h-9 xl:w-[200px]" aria-label={t("filters.subtype")}>
                <SelectValue placeholder={t("filters.allSubtypes")} />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>
                  {selectedLine ? t("filters.allSubtypes") : t("filters.pickLineFirst")}
                </SelectItem>
                {(selectedLine?.subtypes ?? []).map((sub) => (
                  <SelectItem key={sub.id} value={String(sub.id)}>
                    {sub.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <TouchHint
              hint={canInsurers.allowed ? null : t("noPermission.insurers")}
              className="w-full xl:w-auto"
            >
              <Select
                value={insurerId ? String(insurerId) : ALL}
                onValueChange={(v) => setFilter({ insurer: v === ALL ? null : v })}
                disabled={!canInsurers.allowed}
              >
                <SelectTrigger className="h-10 w-full md:h-9 xl:w-[200px]" aria-label={t("filters.insurer")}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL}>{t("filters.allInsurers")}</SelectItem>
                  {(insurers.data?.items ?? []).map((ins) => (
                    <SelectItem key={ins.id} value={String(ins.id)}>
                      {ins.trade_name ?? ins.legal_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </TouchHint>

            <Select
              value={status ?? ALL}
              onValueChange={(v) => setFilter({ status: v === ALL ? null : v })}
            >
              <SelectTrigger className="h-10 md:h-9 xl:w-[160px]" aria-label={t("filters.status")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("filters.allStatuses")}</SelectItem>
                {POLICY_COMPUTED_STATUSES.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(COMPUTED_STATUS_LABEL[value])}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={collection ?? ALL}
              onValueChange={(v) => setFilter({ collection: v === ALL ? null : v })}
            >
              <SelectTrigger className="h-10 md:h-9 xl:w-[170px]" aria-label={t("filters.collection")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("filters.allCollection")}</SelectItem>
                {POLICY_COLLECTION_FILTERS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t(COLLECTION_FILTER_LABEL[value])}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Select
              value={missing ?? ALL}
              onValueChange={(v) => setFilter({ missing: v === ALL ? null : v })}
            >
              <SelectTrigger className="h-10 md:h-9 xl:w-[190px]" aria-label={t("filters.missing")}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={ALL}>{t("filters.allMissing")}</SelectItem>
                {POLICY_MISSING_KEYS.map((value) => (
                  <SelectItem key={value} value={value}>
                    {t("filters.missingOption", { label: t(MISSING_LABEL[value]) })}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>

            <Button
              variant={mine ? "accent-soft" : "secondary"}
              aria-pressed={mine}
              className="h-10 md:h-9"
              onClick={() => setFilter({ mine: mine ? null : "1" })}
            >
              <User className="h-4 w-4" />
              {t("filters.mine")}
            </Button>

            {anyFilter ? (
              <Button variant="ghost" className="h-10 md:h-9" onClick={clearAll}>
                {t("filters.clear")}
              </Button>
            ) : null}
          </div>
          <span className="text-caption text-ink-3 md:hidden">
            {t("list.resultCount", { count: total })}
          </span>
        </Card>
      </FadeUp>

      <ErrorBanner error={list.error} />

      {/* ---- Results ---- */}
      {list.isError ? null : !list.isLoading && rows.length === 0 ? (
        <Card>
          <EmptyState
            title={anyFilter ? t("list.emptyFiltered") : t("list.empty")}
            hint={anyFilter ? t("list.emptyFilteredHint") : t("list.emptyHint")}
            icon={<ShieldCheck className="h-5 w-5" />}
            action={
              anyFilter ? (
                <Button variant="secondary" size="sm" onClick={clearAll}>
                  {t("filters.clear")}
                </Button>
              ) : null
            }
          />
        </Card>
      ) : (
        <>
          <FadeUp delay={0.1} className="hidden md:block">
            <div className="overflow-x-auto">
              <DataTable
                columns={columns}
                data={rows}
                isLoading={list.isLoading}
                emptyMessage={t("list.empty")}
                onRowClick={(row) => navigate(`/policies/${row.id}`)}
                pageIndex={page}
                pageSize={PAGE_SIZE}
                total={total}
                onPageChange={(next) => setParams({ page: next > 0 ? String(next) : null })}
              />
            </div>
          </FadeUp>

          <div className="flex flex-col gap-2.5 md:hidden">
            {list.isLoading ? (
              <LoadingRows rows={4} />
            ) : (
              rows.map((row) => (
                <PolicyCard key={row.id} policy={row} onOpen={() => navigate(`/policies/${row.id}`)} />
              ))
            )}
            {total > PAGE_SIZE ? (
              <div className="flex items-center justify-between gap-2 pt-1">
                <Button
                  variant="secondary"
                  className="h-10"
                  disabled={page === 0}
                  onClick={() => setParams({ page: page - 1 > 0 ? String(page - 1) : null })}
                  aria-label={t("pager.previous")}
                >
                  <ChevronLeft className="h-4 w-4" />
                </Button>
                <span className="text-caption tabular-nums text-ink-3">
                  {t("pager.page", { page: page + 1, pages })}
                </span>
                <Button
                  variant="secondary"
                  className="h-10"
                  disabled={page + 1 >= pages}
                  onClick={() => setParams({ page: String(page + 1) })}
                  aria-label={t("pager.next")}
                >
                  <ChevronRight className="h-4 w-4" />
                </Button>
              </div>
            ) : null}
          </div>
        </>
      )}
    </>
  );
}
