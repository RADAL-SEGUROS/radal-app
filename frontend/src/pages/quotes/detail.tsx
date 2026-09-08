/**
 * Quote request — detail.
 *
 * The centrepiece is the line-item editor: the server enforces
 * `declared_value_uf == SUM(line_items.value_uf)` (±0.01 UF), so the sum is
 * recomputed on every keystroke and the mismatch is shown BEFORE the save is
 * attempted. When "sync" is on the declared value follows the sum; when it is
 * off, saving a mismatch is blocked here instead of being 422-ed by the API.
 */
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import {
  ArrowLeft,
  Check,
  FileUp,
  Layers,
  Plus,
  Send,
  Share2,
  Trash2,
  X,
} from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { FadeUp } from "@/components/common/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useCan } from "@/lib/permissions";
import { formatDate, formatDateTime, formatNumber } from "@/lib/format";
import { useQuote, useReplaceQuoteLineItems, useSendQuote } from "@/api/quotes";
import { useProposals } from "@/api/proposals";
import { useInsurers } from "@/api/insurers";
import { useCreateOffering, useOfferings } from "@/api/offerings";
import { num, type QuoteRequest } from "@/api/types";
import {
  ConfidenceBadge,
  DisabledHint,
  EmptyState,
  ErrorBanner,
  KeyValue,
  MonoChip,
  Section,
  StatusBadge,
  apiError,
  differs,
  permille,
  uf,
} from "@/pages/proposals/shared";

export default function QuoteDetailPage() {
  const { quoteId } = useParams<{ quoteId: string }>();
  const id = Number(quoteId);
  const { t } = useTranslation("quotes");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const quote = useQuote(Number.isFinite(id) ? id : undefined);
  const proposals = useProposals({ quote_request_id: id, limit: 50 }, Number.isFinite(id));
  const offerings = useOfferings({ quote_request_id: id }, Number.isFinite(id));
  const createOffering = useCreateOffering();
  const canCreateOffering = useCan("Offerings", "Create");
  const canSubmit = useCan("Quotes", "Submit");

  const [sendOpen, setSendOpen] = React.useState(false);

  if (quote.isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-9 w-72" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (quote.isError || !quote.data) {
    return (
      <>
        <PageHeader title={t("detail.title")} />
        <ErrorBanner error={quote.error ?? t("detail.notFound")} />
        <Button variant="secondary" size="sm" onClick={() => navigate("/quotes")}>
          <ArrowLeft className="h-4 w-4" />
          {tc("actions.back")}
        </Button>
      </>
    );
  }

  const q = quote.data;
  const proposalCount = proposals.data?.total ?? q.proposal_count;
  const sendable = q.status === "draft" || q.status === "sent";

  const buildOffering = () => {
    createOffering.mutate(
      { quote_request_id: q.id },
      {
        onSuccess: (offering) => {
          toast.success(t("detail.offeringCreated"));
          navigate(`/offerings/${offering.id}`);
        },
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );
  };

  return (
    <>
      <PageHeader
        eyebrow={
          <Link to="/quotes" className="inline-flex items-center gap-1.5 no-underline">
            <ArrowLeft className="h-3.5 w-3.5" />
            {t("title")}
          </Link>
        }
        title={q.insured_object || t("table.noObject")}
        subtitle={
          <span className="flex flex-wrap items-center gap-2">
            <MonoChip>COT-{String(q.id).padStart(4, "0")}</MonoChip>
            <StatusBadge value={q.status} label={t(`status.${q.status}`)} />
            <StatusBadge value={q.priority} label={t(`priority.${q.priority}`)} />
            {q.placement?.period ? (
              <span className="text-caption text-text-muted">{q.placement.period}</span>
            ) : null}
          </span>
        }
        actions={
          <>
            <DisabledHint hint={sendable ? null : t("send.notSendable")}>
              <Button
                variant="secondary"
                size="sm"
                disabled={!sendable || !canSubmit.allowed}
                onClick={() => setSendOpen(true)}
              >
                <Send className="h-4 w-4" />
                {t("send.action")}
              </Button>
            </DisabledHint>

            <Button variant="secondary" size="sm" asChild>
              <Link to={`/proposals/upload?quote=${q.id}`}>
                <FileUp className="h-4 w-4" />
                {t("detail.uploadProposal")}
              </Link>
            </Button>

            {proposalCount > 0 ? (
              <Button variant="secondary" size="sm" asChild>
                <Link to={`/quotes/${q.id}/comparison`}>
                  <Layers className="h-4 w-4" />
                  {t("detail.compare")}
                </Link>
              </Button>
            ) : (
              <DisabledHint hint={t("detail.noProposalsYet")}>
                <Button variant="secondary" size="sm" disabled>
                  <Layers className="h-4 w-4" />
                  {t("detail.compare")}
                </Button>
              </DisabledHint>
            )}

            <DisabledHint
              hint={
                canCreateOffering.allowed
                  ? proposalCount > 0
                    ? null
                    : t("detail.offeringNeedsProposal")
                  : t("detail.offeringNoPermission")
              }
            >
              <Button
                size="sm"
                disabled={
                  proposalCount === 0 || !canCreateOffering.allowed || createOffering.isPending
                }
                onClick={buildOffering}
              >
                <Share2 className="h-4 w-4" />
                {t("detail.createOffering")}
              </Button>
            </DisabledHint>
          </>
        }
      />

      <SummaryStrip quote={q} />

      <LineItemsEditor quote={q} />

      <FadeUp delay={0.16}>
        <Section
          title={t("detail.proposalsTitle")}
          description={t("detail.proposalsDescription")}
          actions={
            proposalCount > 1 ? (
              <Button variant="secondary" size="sm" asChild>
                <Link to={`/quotes/${q.id}/comparison`}>
                  <Layers className="h-4 w-4" />
                  {t("detail.compare")}
                </Link>
              </Button>
            ) : null
          }
          bodyClassName="p-0"
        >
          {proposals.isLoading ? (
            <div className="p-5">
              <Skeleton className="h-24 w-full" />
            </div>
          ) : (proposals.data?.items.length ?? 0) === 0 ? (
            <EmptyState
              title={t("detail.noProposalsTitle")}
              hint={t("detail.noProposalsHint")}
              action={
                <Button size="sm" asChild>
                  <Link to={`/proposals/upload?quote=${q.id}`}>
                    <FileUp className="h-4 w-4" />
                    {t("detail.uploadProposal")}
                  </Link>
                </Button>
              }
            />
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead>{t("detail.proposalInsurer")}</TableHead>
                  <TableHead>{t("detail.proposalTotal")}</TableHead>
                  <TableHead>{t("detail.proposalRate")}</TableHead>
                  <TableHead>{t("detail.proposalStatus")}</TableHead>
                  <TableHead>{t("detail.proposalConfirmed")}</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {(proposals.data?.items ?? []).map((p) => (
                  <TableRow
                    key={p.id}
                    className="cursor-pointer"
                    onClick={() => navigate(`/proposals/${p.id}`)}
                  >
                    <TableCell>
                      <div className="font-medium text-text-primary">
                        {p.insurer?.trade_name || p.insurer?.legal_name || `#${p.insurer_id}`}
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5">
                        <MonoChip>{p.insurer?.cmf_code ?? "—"}</MonoChip>
                        <Badge variant={p.origin === "native" ? "brand" : "neutral"}>
                          {t(`origin.${p.origin}`)}
                        </Badge>
                      </div>
                    </TableCell>
                    <TableCell className="tabular-nums">{uf(p.total_premium_uf)}</TableCell>
                    <TableCell className="tabular-nums">
                      {permille(p.comprehensive_rate_permille)}
                    </TableCell>
                    <TableCell>
                      <StatusBadge value={p.status} label={t(`proposalStatus.${p.status}`)} />
                    </TableCell>
                    <TableCell>
                      {p.is_confirmed ? (
                        <Badge variant="success" className="gap-1">
                          <Check className="h-3 w-3" />
                          {t("detail.confirmed")}
                        </Badge>
                      ) : (
                        <div className="flex items-center gap-1.5">
                          <Badge variant="warn">{t("detail.unconfirmed")}</Badge>
                          <ConfidenceBadge value={p.extraction_confidence} />
                        </div>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </Section>
      </FadeUp>

      <FadeUp delay={0.2}>
        <Section title={t("detail.offeringsTitle")} description={t("detail.offeringsDescription")}>
          {offerings.isLoading ? (
            <Skeleton className="h-16 w-full" />
          ) : (offerings.data?.items.length ?? 0) === 0 ? (
            <p className="text-body text-text-muted">{t("detail.noOfferings")}</p>
          ) : (
            <ul className="flex flex-col gap-2">
              {(offerings.data?.items ?? []).map((o) => (
                <li key={o.id}>
                  <Link
                    to={`/offerings/${o.id}`}
                    className="flex items-center justify-between gap-3 rounded-card border border-line px-3.5 py-2.5 no-underline transition-colors hover:border-brand-line hover:bg-brand-soft"
                  >
                    <span className="flex items-center gap-2">
                      <MonoChip>OFR-{String(o.id).padStart(4, "0")}</MonoChip>
                      <StatusBadge value={o.status} label={t(`offeringStatus.${o.status}`)} />
                    </span>
                    <span className="text-caption text-text-muted">
                      {o.sent_at ? formatDateTime(o.sent_at) : t("detail.offeringNotSent")}
                    </span>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </Section>
      </FadeUp>

      <SendDialog quote={q} open={sendOpen} onOpenChange={setSendOpen} />
    </>
  );
}

// =============================================================================
// Summary
// =============================================================================

function SummaryStrip({ quote }: { quote: QuoteRequest }) {
  const { t } = useTranslation("quotes");
  const declared = num(quote.declared_value_uf);
  const sum = num(quote.line_items_total_uf);
  const mismatch = differs(declared, sum);

  return (
    <FadeUp delay={0.06}>
      <Card className="grid grid-cols-2 gap-5 p-5 md:grid-cols-3 lg:grid-cols-6">
        <KeyValue
          label={t("summary.declared")}
          value={uf(quote.declared_value_uf)}
          tone={mismatch ? "danger" : "default"}
        />
        <KeyValue
          label={t("summary.lineItemsTotal")}
          value={uf(quote.line_items_total_uf)}
          tone={mismatch ? "danger" : "success"}
        />
        <KeyValue
          label={t("summary.proposals")}
          value={formatNumber(quote.proposal_count)}
        />
        <KeyValue
          label={t("summary.desiredStart")}
          value={formatDate(quote.desired_start)}
        />
        <KeyValue label={t("summary.due")} value={formatDate(quote.due_at)} />
        <KeyValue
          label={t("summary.sentAt")}
          value={quote.sent_at ? formatDateTime(quote.sent_at) : "—"}
        />
        {quote.requested_coverages ? (
          <div className="col-span-2 md:col-span-3 lg:col-span-6">
            <KeyValue
              label={t("summary.requestedCoverages")}
              value={<span className="whitespace-pre-wrap">{quote.requested_coverages}</span>}
            />
          </div>
        ) : null}
      </Card>
    </FadeUp>
  );
}

// =============================================================================
// Line items
// =============================================================================

interface Row {
  key: string;
  name: string;
  value: string;
  detail: string;
}

let rowSeq = 0;
const nextKey = () => `row-${++rowSeq}`;

function toRows(quote: QuoteRequest): Row[] {
  return quote.line_items
    .slice()
    .sort((a, b) => a.sort_order - b.sort_order)
    .map((item) => ({
      key: `item-${item.id}`,
      name: item.name,
      value: String(num(item.value_uf) ?? ""),
      detail: item.detail ?? "",
    }));
}

function LineItemsEditor({ quote }: { quote: QuoteRequest }) {
  const { t } = useTranslation("quotes");
  const { t: tc } = useTranslation("common");
  const canEdit = useCan("Quotes", "Edit");

  const [rows, setRows] = React.useState<Row[]>(() => toRows(quote));
  const [sync, setSync] = React.useState(true);
  const [dirty, setDirty] = React.useState(false);

  // A stable signature of the saved items — a background refetch hands us a new
  // array object every time, and re-seeding on that would wipe edits mid-typing.
  const signature = React.useMemo(
    () =>
      JSON.stringify(
        quote.line_items.map((i) => [i.id, i.name, i.value_uf, i.detail, i.sort_order]),
      ),
    [quote.line_items],
  );
  const dirtyRef = React.useRef(false);
  dirtyRef.current = dirty;
  const quoteRef = React.useRef(quote);
  quoteRef.current = quote;

  // Re-seed from the server only when the user has nothing unsaved.
  React.useEffect(() => {
    if (dirtyRef.current) return;
    setRows(toRows(quoteRef.current));
  }, [signature, quote.id]);

  const replace = useReplaceQuoteLineItems(quote.id, sync);

  const sum = rows.reduce((acc, r) => acc + (Number(r.value) || 0), 0);
  const declared = num(quote.declared_value_uf) ?? 0;
  const delta = sum - declared;
  const mismatch = Math.abs(delta) > 0.01;

  const patch = (key: string, field: keyof Row, value: string) => {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, [field]: value } : r)));
    setDirty(true);
  };

  const addRow = () => {
    setRows((prev) => [...prev, { key: nextKey(), name: "", value: "", detail: "" }]);
    setDirty(true);
  };

  const removeRow = (key: string) => {
    setRows((prev) => prev.filter((r) => r.key !== key));
    setDirty(true);
  };

  const invalidRows = rows.filter((r) => !r.name.trim() || r.value === "" || Number.isNaN(Number(r.value)));

  const blockedReason = !canEdit.allowed
    ? t("lineItems.noPermission")
    : invalidRows.length > 0
      ? t("lineItems.incomplete")
      : !sync && mismatch
        ? t("lineItems.mismatchBlocks")
        : !dirty
          ? t("lineItems.noChanges")
          : null;

  const save = () => {
    replace.mutate(
      rows.map((r, index) => ({
        name: r.name.trim(),
        value_uf: Number(r.value),
        detail: r.detail.trim() || null,
        sort_order: index,
      })),
      {
        onSuccess: () => {
          setDirty(false);
          toast.success(t("lineItems.saved"));
        },
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );
  };

  return (
    <FadeUp delay={0.1}>
      <Section
        title={t("lineItems.title")}
        description={t("lineItems.description")}
        actions={
          <>
            <DisabledHint hint={canEdit.allowed ? null : t("lineItems.noPermission")}>
              <Button variant="secondary" size="sm" onClick={addRow} disabled={!canEdit.allowed}>
                <Plus className="h-4 w-4" />
                {t("lineItems.add")}
              </Button>
            </DisabledHint>
            {dirty ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  setRows(toRows(quote));
                  setDirty(false);
                }}
              >
                <X className="h-4 w-4" />
                {t("lineItems.discard")}
              </Button>
            ) : null}
            <DisabledHint hint={blockedReason}>
              <Button size="sm" onClick={save} disabled={!!blockedReason || replace.isPending}>
                <Check className="h-4 w-4" />
                {replace.isPending ? tc("actions.loading") : t("lineItems.save")}
              </Button>
            </DisabledHint>
          </>
        }
        bodyClassName="p-0"
      >
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="w-[34%]">{t("lineItems.name")}</TableHead>
              <TableHead className="w-[18%]">{t("lineItems.value")}</TableHead>
              <TableHead>{t("lineItems.detail")}</TableHead>
              <TableHead className="w-[52px]" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={4} className="h-20 text-center text-body text-text-muted">
                  {t("lineItems.empty")}
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row) => (
                <TableRow key={row.key} className="hover:bg-transparent">
                  <TableCell>
                    <Input
                      value={row.name}
                      onChange={(e) => patch(row.key, "name", e.target.value)}
                      placeholder={t("lineItems.namePlaceholder")}
                      className="h-9"
                      disabled={!canEdit.allowed}
                    />
                  </TableCell>
                  <TableCell>
                    <Input
                      type="number"
                      step="0.01"
                      inputMode="decimal"
                      value={row.value}
                      onChange={(e) => patch(row.key, "value", e.target.value)}
                      className="h-9 text-right tabular-nums"
                      disabled={!canEdit.allowed}
                    />
                  </TableCell>
                  <TableCell>
                    <Input
                      value={row.detail}
                      onChange={(e) => patch(row.key, "detail", e.target.value)}
                      placeholder={t("lineItems.detailPlaceholder")}
                      className="h-9"
                      disabled={!canEdit.allowed}
                    />
                  </TableCell>
                  <TableCell>
                    <DisabledHint hint={canEdit.allowed ? null : t("lineItems.noPermission")}>
                      <Button
                        variant="ghost"
                        size="icon"
                        className="h-9 w-9"
                        onClick={() => removeRow(row.key)}
                        disabled={!canEdit.allowed}
                        aria-label={tc("actions.delete")}
                      >
                        <Trash2 className="h-4 w-4 text-signal-danger" />
                      </Button>
                    </DisabledHint>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>

        {/* Live validation footer — the sum against the declared value. */}
        <div className="flex flex-wrap items-center justify-between gap-4 border-t border-line bg-bg-recessed px-5 py-4">
          <div className="flex flex-wrap items-center gap-6">
            <KeyValue label={t("lineItems.sum")} value={uf(sum)} />
            <KeyValue label={t("lineItems.declared")} value={uf(declared)} />
            <KeyValue
              label={t("lineItems.delta")}
              value={
                <span className="flex items-center gap-2">
                  {uf(delta)}
                  {mismatch ? (
                    <Badge variant={sync ? "warn" : "danger"}>
                      {sync ? t("lineItems.willSync") : t("lineItems.mismatch")}
                    </Badge>
                  ) : (
                    <Badge variant="success" className="gap-1">
                      <Check className="h-3 w-3" />
                      {t("lineItems.balanced")}
                    </Badge>
                  )}
                </span>
              }
              tone={mismatch && !sync ? "danger" : mismatch ? "warn" : "success"}
            />
          </div>
          <label className="flex cursor-pointer items-center gap-2 text-caption text-text-secondary">
            <input
              type="checkbox"
              checked={sync}
              onChange={(e) => setSync(e.target.checked)}
              disabled={!canEdit.allowed}
              className="h-4 w-4 accent-brand"
            />
            {t("lineItems.syncDeclared")}
          </label>
        </div>

        {replace.isError ? (
          <div className="px-5 pb-5">
            <ErrorBanner error={replace.error} />
          </div>
        ) : null}
      </Section>
    </FadeUp>
  );
}

// =============================================================================
// Send to insurers
// =============================================================================

function SendDialog({
  quote,
  open,
  onOpenChange,
}: {
  quote: QuoteRequest;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("quotes");
  const { t: tc } = useTranslation("common");
  const insurers = useInsurers({ limit: 200, status: "active" }, open);
  const send = useSendQuote(quote.id);

  const [selected, setSelected] = React.useState<number[]>(quote.recipient_insurer_ids ?? []);
  const [dueAt, setDueAt] = React.useState("");
  const [search, setSearch] = React.useState("");

  React.useEffect(() => {
    if (open) {
      setSelected(quote.recipient_insurer_ids ?? []);
      setDueAt("");
      setSearch("");
    }
  }, [open, quote.recipient_insurer_ids]);

  const toggle = (id: number) =>
    setSelected((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]));

  const list = (insurers.data?.items ?? []).filter((i) => {
    if (!search.trim()) return true;
    const needle = search.toLowerCase();
    return (
      i.legal_name.toLowerCase().includes(needle) ||
      (i.trade_name ?? "").toLowerCase().includes(needle) ||
      i.cmf_code.toLowerCase().includes(needle) ||
      i.rut.toLowerCase().includes(needle)
    );
  });

  const submit = () => {
    send.mutate(
      {
        recipient_insurer_ids: selected,
        // The server stores an ISO datetime; a date input yields a plain date.
        due_at: dueAt ? new Date(`${dueAt}T23:59:00`).toISOString() : null,
      },
      {
        onSuccess: () => {
          toast.success(t("send.recorded"));
          onOpenChange(false);
        },
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>{t("send.title")}</DialogTitle>
          <DialogDescription>{t("send.description")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("send.searchInsurers")}
            className="h-9"
          />

          <div className="max-h-[280px] overflow-y-auto rounded-card border border-line">
            {insurers.isLoading ? (
              <div className="p-4">
                <Skeleton className="h-24 w-full" />
              </div>
            ) : list.length === 0 ? (
              <p className="p-4 text-body text-text-muted">{t("send.noInsurers")}</p>
            ) : (
              <ul className="divide-y divide-line">
                {list.map((i) => (
                  <li key={i.id}>
                    <label className="flex cursor-pointer items-center gap-3 px-3.5 py-2.5 transition-colors hover:bg-bg-recessed">
                      <input
                        type="checkbox"
                        checked={selected.includes(i.id)}
                        onChange={() => toggle(i.id)}
                        className="h-4 w-4 accent-brand"
                      />
                      <span className="min-w-0 flex-1">
                        <span className="block truncate text-body font-medium text-text-primary">
                          {i.trade_name || i.legal_name}
                        </span>
                        <span className="mt-0.5 flex items-center gap-1.5">
                          <MonoChip>{i.cmf_code}</MonoChip>
                          <Badge variant={i.is_native ? "brand" : "neutral"}>
                            {t(`origin.${i.is_native ? "native" : "external"}`)}
                          </Badge>
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="due">{t("send.dueAt")}</Label>
            <Input
              id="due"
              type="date"
              value={dueAt}
              onChange={(e) => setDueAt(e.target.value)}
              className="max-w-[220px]"
            />
          </div>

          <p className="text-caption text-text-muted">{t("send.manualNote")}</p>

          {send.isError ? <ErrorBanner error={send.error} /> : null}
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <DisabledHint hint={selected.length ? null : t("send.pickInsurer")}>
            <Button onClick={submit} disabled={selected.length === 0 || send.isPending}>
              <Send className="h-4 w-4" />
              {send.isPending ? tc("actions.loading") : t("send.confirm")}
            </Button>
          </DisabledHint>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
