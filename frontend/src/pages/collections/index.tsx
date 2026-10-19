/**
 * Cobranza — the cuota-level board (`GET /collections/installments`).
 *
 * Top to bottom, as the team spec draws it (Cobranza, clic a clic):
 *  - the cards (`GET /collections/summary`, broker-wide) — a click filters;
 *  - "Pólizas sin plan de pago (n)" and "Respuestas por confirmar (n)", only
 *    when there are any;
 *  - subtabs Próximas · Vencidas · Al día · Saldadas (server `bucket`, with the
 *    server's own `counts`);
 *  - filters (compañía, ramo, método, nivel, Mías, search) — all server-side;
 *  - the rows, already ordered by urgency BY THE SERVER (riesgo de término →
 *    vencidas → vence hoy → esta semana → próximas). The UI never re-sorts.
 *
 * Desktop renders a table, phones a card list; both open the same side sheet
 * with every action on the cuota. Every piece of view state lives in the URL.
 *
 * The summary hook is keyed `qk.collections.summary()` with NO params, so this
 * page and the rail's overdue chip share one cache entry and refresh together.
 */
import * as React from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import {
  AlertTriangle,
  CalendarClock,
  CalendarRange,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardList,
  Coins,
  Inbox,
  MailQuestion,
  Search,
  ShieldAlert,
  Wallet,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { Stagger } from "@/components/common/motion";
import { EmptyState, ErrorBanner, LoadingRows } from "@/components/common/kit";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { cn } from "@/lib/utils";
import { formatDate, formatUF } from "@/lib/format";
import { useSetUrlParams, useUrlParam } from "@/lib/urlParams";
import { FilterChip, FilterRow } from "@/pages/data/shared";
import { PAYMENT_MODES, num, type PaymentMode } from "@/api/types";
import { useCatalogLines } from "@/api/catalog";
import { usePaymentInsurers } from "@/api/payments";
import {
  COLLECTION_BUCKETS,
  COLLECTION_LEVELS,
  useBoardInstallments,
  useCollectionsBoardSummary,
  type BoardInstallmentRow,
  type CollectionBucket,
  type CobranzaLevel,
} from "@/api/collections";
import { LevelBadge } from "@/components/collections/LevelBadge";
import { InstallmentSheet } from "@/components/collections/InstallmentSheet";
import {
  BUCKET_EMPTY,
  BUCKET_LABEL,
  FLAG_LABEL,
  LEVEL_LABEL,
  METHOD_LABEL,
  labelOf,
} from "@/components/collections/labels";
import { InstallmentStateBadges, clp, debitKindOf, money } from "@/components/collections/shared";
import { formatRut } from "@/pages/clients/rut";

const PAGE = 50;
/** A client-side mark filter (the server has no flag param) reads a wider page. */
const PAGE_WITH_FLAG = 200;

function isBucket(v: string | null): v is CollectionBucket {
  return !!v && (COLLECTION_BUCKETS as readonly string[]).includes(v);
}
function isLevel(v: string | null): v is CobranzaLevel {
  return !!v && (COLLECTION_LEVELS as readonly string[]).includes(v);
}
function isMethod(v: string | null): v is PaymentMode {
  return !!v && (PAYMENT_MODES as readonly string[]).includes(v);
}
function isFlag(v: string | null): v is keyof typeof FLAG_LABEL {
  return !!v && Object.prototype.hasOwnProperty.call(FLAG_LABEL, v);
}

/** KpiCard is presentational; the click-to-filter wrapper lives here. */
function CardButton({
  onClick,
  active,
  label,
  children,
}: {
  onClick: () => void;
  active?: boolean;
  label: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      title={label}
      aria-pressed={active}
      className={cn(
        "h-full rounded-card text-left transition-[transform,box-shadow] duration-150 ease-out active:scale-[0.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        active && "ring-2 ring-brand-ring",
      )}
    >
      {children}
    </button>
  );
}

function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { t } = useTranslation("collections");
  const [draft, setDraft] = React.useState(value);
  React.useEffect(() => setDraft(value), [value]);
  React.useEffect(() => {
    if (draft === value) return;
    const id = window.setTimeout(() => onChange(draft), 300);
    return () => window.clearTimeout(id);
  }, [draft, value, onChange]);
  return (
    <div className="relative w-full sm:w-64">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-ink-3" />
      <Input
        type="search"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        placeholder={t("filters.searchPlaceholder")}
        aria-label={t("filters.search")}
        className="pl-9"
      />
    </div>
  );
}

function LastNoteCell({ row }: { row: BoardInstallmentRow }) {
  const { t } = useTranslation("collections");
  if (row.promise_until) {
    return (
      <span className="text-caption text-brand-deep">
        {t("board.promiseUntil", { date: formatDate(row.promise_until) })}
      </span>
    );
  }
  if (!row.last_note) return <span className="text-ink-3">—</span>;
  return (
    <span className="line-clamp-2 max-w-[220px] text-caption text-ink-2" title={row.last_note.body}>
      {row.last_note.created_at ? (
        <span className="text-ink-3">{formatDate(row.last_note.created_at, "d MMM")} · </span>
      ) : null}
      {row.last_note.body}
    </span>
  );
}

/**
 * Gross on top; under it the open balance (partial) or the balance's CLP.
 * Every line is `nowrap` — a partial payment used to wrap into five lines.
 */
function AmountCell({ row }: { row: BoardInstallmentRow }) {
  const { t } = useTranslation("collections");
  const paid = num(row.paid_uf) ?? 0;
  const gross = num(row.gross_amount_uf) ?? 0;
  const partial = paid > 0 && paid < gross;
  const open = Number(row.balance_uf) > 0 && row.state !== "voided" && row.state !== "paid";
  return (
    <div className="flex flex-col items-end whitespace-nowrap tabular-nums">
      <span className="font-medium text-ink">{money(row.gross_amount_uf, row.currency)}</span>
      {partial && open ? (
        <>
          <span className="text-caption text-warn-text">
            {t("board.balanceShort", { amount: money(row.balance_uf, row.currency) })}
          </span>
          <span className="text-caption text-ink-3">
            {t("board.paidShort", { amount: money(row.paid_uf, row.currency) })}
          </span>
        </>
      ) : open && row.balance_clp !== null ? (
        <span className="text-caption text-ink-3">{clp(row.balance_clp)}</span>
      ) : null}
    </div>
  );
}

function InstallmentCard({ row, onOpen }: { row: BoardInstallmentRow; onOpen: () => void }) {
  const { t } = useTranslation("collections");
  return (
    <li>
      <button
        type="button"
        onClick={onOpen}
        className="flex w-full flex-col gap-2.5 rounded-card border border-line bg-bone p-4 text-left shadow-elev transition-[border-color,box-shadow,transform] duration-150 ease-out hover:border-line-strong active:scale-[0.96]"
      >
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-body font-medium text-ink">
              {row.insured_name ?? row.policy_number}
            </div>
            <div className="truncate text-caption text-ink-3">
              {[row.insurer_name, `${t("board.policyShort")} ${row.policy_number}`, row.line_name]
                .filter(Boolean)
                .join(" · ")}
            </div>
          </div>
          {/* No tooltip inside a tappable card: the sheet shows the reason in full. */}
          <LevelBadge level={row.level} />
        </div>
        <InstallmentStateBadges state={row.state} flags={row.flags} daysLate={row.days_late} debitKind={debitKindOf(row)} />
        <div className="flex items-end justify-between gap-3">
          <div className="text-caption text-ink-3">
            <div>
              {t("board.installmentOf", {
                number: row.number,
                count: row.installment_count ?? row.number,
              })}
            </div>
            <div>
              {t("board.columns.due")}: <span className="text-ink-2">{formatDate(row.due_date)}</span>
            </div>
          </div>
          <AmountCell row={row} />
        </div>
        {row.promise_until || row.last_note ? (
          <div className="border-t border-line pt-2">
            <LastNoteCell row={row} />
          </div>
        ) : null}
      </button>
    </li>
  );
}

export default function CollectionsPage() {
  const { t } = useTranslation(["collections", "common"]);
  const setParams = useSetUrlParams();
  const [tabRaw] = useUrlParam("tab");
  const [insurerRaw] = useUrlParam("insurer");
  const [lineRaw] = useUrlParam("ramo");
  const [methodRaw] = useUrlParam("method");
  const [levelRaw] = useUrlParam("level");
  const [ownerRaw] = useUrlParam("owner");
  const [qRaw] = useUrlParam("q");
  const [flagRaw] = useUrlParam("flag");
  const [pageRaw] = useUrlParam("page");

  const tab: CollectionBucket = isBucket(tabRaw) ? tabRaw : "upcoming";
  const level = isLevel(levelRaw) ? levelRaw : undefined;
  const method = isMethod(methodRaw) ? methodRaw : undefined;
  const flag = isFlag(flagRaw) ? flagRaw : null;
  const insurerId = insurerRaw && Number(insurerRaw) > 0 ? Number(insurerRaw) : undefined;
  const mine = ownerRaw === "me";
  const pageIndex = pageRaw && Number(pageRaw) > 1 ? Number(pageRaw) - 1 : 0;
  const limit = flag ? PAGE_WITH_FLAG : PAGE;

  const summary = useCollectionsBoardSummary(true, mine ? "me" : undefined);
  const insurers = usePaymentInsurers({ scope: "mine" });
  const lines = useCatalogLines();
  const board = useBoardInstallments({
    bucket: tab,
    insurer_id: insurerId,
    line_code: lineRaw || undefined,
    method,
    level,
    owner: mine ? "me" : undefined,
    q: qRaw || undefined,
    limit,
    offset: pageIndex * limit,
  });

  const allItems = board.data?.items ?? [];
  const items = flag ? allItems.filter((r) => r.flags.includes(flag)) : allItems;
  const total = board.data?.total ?? 0;
  const counts = board.data?.counts ?? {};
  const ufRate = num(board.data?.uf_rate ?? null);
  const pageCount = Math.max(1, Math.ceil(total / limit));

  const [selectedId, setSelectedId] = React.useState<number | null>(null);
  const [selectedRow, setSelectedRow] = React.useState<BoardInstallmentRow | null>(null);
  // Keep the sheet on the LIVE row: after a payment the board refetches and
  // the sheet must show the new state, not the snapshot taken at click time.
  const liveRow = (selectedId !== null && allItems.find((r) => r.id === selectedId)) || selectedRow;
  const openRow = (row: BoardInstallmentRow) => {
    setSelectedId(row.id);
    setSelectedRow(row);
  };

  const set = (updates: Record<string, string | null>) => setParams({ ...updates, page: null });
  const isFiltered = !!(insurerRaw || lineRaw || methodRaw || levelRaw || ownerRaw || qRaw || flagRaw);

  const s = summary.data;
  const insurerOptions = (insurers.data ?? []).map((c) => ({
    value: String(c.insurer.id),
    label: c.insurer.name,
  }));
  const lineOptions = (lines.data ?? [])
    .filter((l) => !!l.code)
    .map((l) => ({ value: l.code as string, label: l.name }));
  const methodOptions = PAYMENT_MODES.map((m) => ({ value: m, label: labelOf(t, METHOD_LABEL, m, m) }));
  const levelOptions = COLLECTION_LEVELS.map((l) => ({ value: l, label: labelOf(t, LEVEL_LABEL, l, l) }));

  const ufFmt = (n: number) => formatUF(n, { decimals: 2 });

  return (
    <>
      <PageHeader
        title={t("collections:title")}
        subtitle={t("collections:description")}
        actions={
          <>
            <Button asChild variant="secondary">
              <Link to="/collections/evidence">
                <Inbox className="h-4 w-4" />
                {t("collections:evidence.title")}
                {s && s.pending_evidence > 0 ? (
                  <Badge variant="warn" className="px-1.5 py-0 tabular-nums">
                    {s.pending_evidence}
                  </Badge>
                ) : null}
              </Link>
            </Button>
            <Button asChild variant="ghost">
              <Link to="/collections/inquiries">
                <MailQuestion className="h-4 w-4" />
                {t("collections:inquiries.title")}
              </Link>
            </Button>
          </>
        }
      />

      <ErrorBanner error={summary.error} />

      <Stagger className="grid grid-cols-2 gap-3 md:grid-cols-3 md:gap-[18px] xl:grid-cols-6">
        <CardButton label={t("collections:cards.due7d")} onClick={() => set({ tab: "upcoming", flag: null })}>
          <KpiCard
            label={t("collections:cards.due7d")}
            countTo={s?.due_7d.count ?? 0}
            hint={s ? formatUF(num(s.due_7d.uf) ?? 0, { decimals: 2 }) : undefined}
            icon={<CalendarClock />}
            tone="warn"
          />
        </CardButton>
        <CardButton label={t("collections:cards.due30d")} onClick={() => set({ tab: "upcoming", flag: null })}>
          <KpiCard
            label={t("collections:cards.due30d")}
            countTo={num(s?.due_30d.uf ?? null) ?? 0}
            format={ufFmt}
            hint={s ? t("collections:cards.installments", { count: s.due_30d.count }) : undefined}
            icon={<CalendarRange />}
          />
        </CardButton>
        <CardButton
          label={t("collections:cards.overdueCount")}
          active={tab === "overdue"}
          onClick={() => set({ tab: "overdue", flag: null })}
        >
          <KpiCard
            label={t("collections:cards.overdueCount")}
            countTo={s?.overdue.count ?? 0}
            hint={
              s && s.termination_risk.count > 0
                ? t("collections:cards.terminationRisk", { count: s.termination_risk.count })
                : undefined
            }
            icon={<AlertTriangle />}
            tone="danger"
          />
        </CardButton>
        <CardButton label={t("collections:cards.overdueAmount")} onClick={() => set({ tab: "overdue", flag: null })}>
          <KpiCard
            label={t("collections:cards.overdueAmount")}
            countTo={num(s?.overdue.uf ?? null) ?? 0}
            format={ufFmt}
            icon={<ShieldAlert />}
            tone="danger"
          />
        </CardButton>
        <CardButton label={t("collections:cards.collectedMonth")} onClick={() => set({ tab: "settled", flag: null })}>
          <KpiCard
            label={t("collections:cards.collectedMonth")}
            countTo={num(s?.collected_month_uf ?? null) ?? 0}
            format={ufFmt}
            icon={<Coins />}
            tone="success"
          />
        </CardButton>
        <CardButton
          label={t("collections:cards.unconfirmed")}
          active={flag === "unconfirmed"}
          onClick={() => set({ tab: "settled", flag: "unconfirmed" })}
        >
          <KpiCard
            label={t("collections:cards.unconfirmed")}
            countTo={s?.unconfirmed_payments ?? 0}
            hint={t("collections:cards.unconfirmedHint")}
            icon={<CheckCircle2 />}
          />
        </CardButton>
      </Stagger>

      {s && (s.plans_missing > 0 || s.pending_evidence > 0) ? (
        <div className="flex flex-col gap-2 sm:flex-row">
          {s.plans_missing > 0 ? (
            <Card className="flex flex-1 flex-wrap items-center gap-3 px-4 py-3">
              <ClipboardList className="h-4 w-4 shrink-0 text-warn" />
              <span className="min-w-0 flex-1 text-body text-ink-2">
                {t("collections:banner.plansMissing", { count: s.plans_missing })}
              </span>
              <Button asChild variant="secondary" size="sm">
                <Link to="/policies?collection=no_plan">{t("collections:banner.plansMissingCta")}</Link>
              </Button>
            </Card>
          ) : null}
          {s.pending_evidence > 0 ? (
            <Card className="flex flex-1 flex-wrap items-center gap-3 px-4 py-3">
              <Inbox className="h-4 w-4 shrink-0 text-brand" />
              <span className="min-w-0 flex-1 text-body text-ink-2">
                {t("collections:banner.pendingEvidence", { count: s.pending_evidence })}
              </span>
              <Button asChild variant="secondary" size="sm">
                <Link to="/collections/evidence">{t("collections:banner.pendingEvidenceCta")}</Link>
              </Button>
            </Card>
          ) : null}
        </div>
      ) : null}

      <Tabs value={tab} onValueChange={(v) => set({ tab: v === "upcoming" ? null : v, flag: null })}>
        <div className="-mx-4 overflow-x-auto px-4 sm:mx-0 sm:px-0">
          <TabsList variant="underline" className="min-w-max">
            {/* "Por revisar" (D1) only appears while something waits for it. */}
            {COLLECTION_BUCKETS.filter(
              (b) => b !== "review" || tab === "review" || (counts.review ?? 0) > 0,
            ).map((b) => (
              <TabsTrigger key={b} value={b} className="gap-1.5">
                {labelOf(t, BUCKET_LABEL, b)}
                {counts[b] !== undefined ? (
                  <span
                    className={cn(
                      "rounded-full px-1.5 text-[11px] tabular-nums",
                      b === "overdue" && (counts[b] ?? 0) > 0
                        ? "bg-neg-soft text-neg-text"
                        : "bg-paper-2 text-ink-3",
                    )}
                  >
                    {counts[b]}
                  </span>
                ) : null}
              </TabsTrigger>
            ))}
          </TabsList>
        </div>
        {/* The tabs overlap by design (team spec): two read cuotas, two read
            plans — so their counts do not add up. Say so. */}
        <p className="mt-2 text-caption text-ink-3">{t("collections:board.tabsHelp")}</p>
      </Tabs>

      <div className="flex flex-col gap-3 sm:flex-row sm:flex-wrap sm:items-center">
        <SearchBox value={qRaw ?? ""} onChange={(v) => set({ q: v || null })} />
        <FilterRow
          isFiltered={isFiltered}
          onClear={() =>
            set({ insurer: null, ramo: null, method: null, level: null, owner: null, q: null, flag: null })
          }
        >
          <FilterChip
            label={t("collections:filters.insurer")}
            value={insurerRaw}
            options={insurerOptions}
            onChange={(v) => set({ insurer: v })}
          />
          <FilterChip
            label={t("collections:filters.line")}
            value={lineRaw}
            options={lineOptions}
            onChange={(v) => set({ ramo: v })}
          />
          <FilterChip
            label={t("collections:filters.method")}
            value={methodRaw}
            options={methodOptions}
            onChange={(v) => set({ method: v })}
          />
          <FilterChip
            label={t("collections:filters.level")}
            value={levelRaw}
            options={levelOptions}
            onChange={(v) => set({ level: v })}
          />
          <button
            type="button"
            aria-pressed={mine}
            onClick={() => set({ owner: mine ? null : "me" })}
            className={cn(
              "inline-flex min-h-8 items-center gap-1 rounded-full px-3 py-1 text-caption transition-[background-color,color,transform] duration-150 ease-out active:scale-[0.96]",
              mine
                ? "bg-brand-soft font-medium text-brand-deep"
                : "border border-dashed border-line text-text-secondary hover:text-text-primary",
            )}
          >
            {t("collections:filters.mine")}
          </button>
          {flag ? (
            <Badge variant="brand" className="gap-1">
              {t("collections:filters.flag")}: {labelOf(t, FLAG_LABEL, flag)}
            </Badge>
          ) : null}
        </FilterRow>
        <span className="text-caption tabular-nums text-ink-3 sm:ms-auto">
          {board.data ? t("collections:board.count", { count: flag ? items.length : total }) : null}
        </span>
      </div>

      <ErrorBanner error={board.error} />

      {board.isLoading ? (
        <Card className="p-5">
          <LoadingRows rows={6} />
        </Card>
      ) : items.length === 0 ? (
        <Card>
          <EmptyState
            icon={<Wallet />}
            title={labelOf(t, BUCKET_EMPTY, tab)}
            hint={isFiltered ? t("collections:board.emptyFiltered") : t("collections:board.emptyHint")}
          />
        </Card>
      ) : (
        <>
          {/* Phones: cards. */}
          <ul className="flex flex-col gap-2.5 md:hidden">
            {items.map((row) => (
              <InstallmentCard key={row.id} row={row} onOpen={() => openRow(row)} />
            ))}
          </ul>

          {/* ≥ md: the table, scrolled horizontally inside its card when narrow. */}
          <Card className={cn("hidden overflow-hidden md:block", board.isFetching && "opacity-80")}>
            <div className="overflow-x-auto">
              {/* Seven columns fit 1440 px; the action column is sticky so
                  «Gestionar» is reachable even when the table scrolls, and the
                  whole row opens the same sheet. */}
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>{t("collections:board.columns.insured")}</TableHead>
                    <TableHead>{t("collections:board.columns.policy")}</TableHead>
                    <TableHead>{t("collections:board.columns.installment")}</TableHead>
                    <TableHead className="text-right">{t("collections:board.columns.amount")}</TableHead>
                    <TableHead>{t("collections:board.columns.state")}</TableHead>
                    <TableHead>{t("collections:board.columns.lastNote")}</TableHead>
                    <TableHead className="sticky right-0 z-[1] bg-bone text-right">
                      <span className="sr-only">{t("collections:board.columns.actions")}</span>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((row) => (
                    <TableRow
                      key={row.id}
                      className="group cursor-pointer"
                      onClick={() => openRow(row)}
                    >
                      <TableCell className="max-w-[220px]">
                        <div className="truncate font-medium text-ink">{row.insured_name ?? "—"}</div>
                        {row.insured_rut ? (
                          <div className="whitespace-nowrap text-caption tabular-nums text-ink-3">
                            {formatRut(row.insured_rut)}
                          </div>
                        ) : null}
                      </TableCell>
                      <TableCell className="max-w-[200px]">
                        <div className="truncate tabular-nums text-ink-2">{row.policy_number}</div>
                        <div className="truncate text-caption text-ink-3">
                          {[row.insurer_name, row.line_name].filter(Boolean).join(" · ") || "—"}
                        </div>
                      </TableCell>
                      <TableCell className="whitespace-nowrap">
                        <div className="tabular-nums text-ink-2">
                          {row.number}/{row.installment_count ?? "—"}
                        </div>
                        <div className="text-caption text-ink-3">{formatDate(row.due_date)}</div>
                      </TableCell>
                      <TableCell className="text-right">
                        <AmountCell row={row} />
                      </TableCell>
                      <TableCell className="min-w-[150px]">
                        <div className="flex flex-col items-start gap-1">
                          <InstallmentStateBadges state={row.state} flags={row.flags} daysLate={row.days_late} debitKind={debitKindOf(row)} />
                          <LevelBadge level={row.level} reason={row.level_reason} />
                        </div>
                      </TableCell>
                      <TableCell className="min-w-[140px]">
                        <LastNoteCell row={row} />
                      </TableCell>
                      <TableCell className="sticky right-0 z-[1] bg-bone text-right shadow-[-8px_0_8px_-8px_rgb(0_0_0/0.12)] transition-[background-color] duration-150 group-hover:bg-paper-2">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            openRow(row);
                          }}
                        >
                          {t("collections:board.manage")}
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </Card>

          {pageCount > 1 ? (
            <div className="flex items-center justify-end gap-2">
              <span className="text-caption tabular-nums text-ink-3">
                {t("collections:board.page", { page: pageIndex + 1, pages: pageCount })}
              </span>
              <Button
                variant="secondary"
                size="icon"
                aria-label={t("common:actions.previous")}
                disabled={pageIndex === 0}
                onClick={() => setParams({ page: pageIndex <= 1 ? null : String(pageIndex) })}
              >
                <ChevronLeft className="h-4 w-4" />
              </Button>
              <Button
                variant="secondary"
                size="icon"
                aria-label={t("common:actions.next")}
                disabled={pageIndex + 1 >= pageCount}
                onClick={() => setParams({ page: String(pageIndex + 2) })}
              >
                <ChevronRight className="h-4 w-4" />
              </Button>
            </div>
          ) : null}
        </>
      )}

      <InstallmentSheet
        row={liveRow}
        ufRate={ufRate}
        open={selectedId !== null}
        onOpenChange={(open) => {
          if (!open) setSelectedId(null);
        }}
      />
    </>
  );
}
