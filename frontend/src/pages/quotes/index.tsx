/**
 * Quote requests — list.
 *
 * Everything here is wired to `/quotes` (list + create) and `/placements`
 * (the picker a new quote hangs off). The KPI strip is computed from the page
 * the server returned, so it never claims a number no endpoint can back.
 */
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { toast } from "sonner";
import type { ColumnDef } from "@tanstack/react-table";
import { FileText, Inbox, Plus, Send, Timer } from "lucide-react";

import { PageHeader } from "@/components/common/PageHeader";
import { KpiCard } from "@/components/common/KpiCard";
import { DataTable } from "@/components/common/DataTable";
import { FadeUp, Stagger } from "@/components/common/motion";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { useCan } from "@/lib/permissions";
import { formatDate } from "@/lib/format";
import { diasRestantes } from "@/lib/format";
import { useCreateQuote, useQuotes } from "@/api/quotes";
import { usePlacements } from "@/api/placements";
import {
  QUOTE_STATUSES,
  PRIORITIES,
  num,
  type Priority,
  type QuoteRequest,
  type QuoteRequestStatus,
} from "@/api/types";
import {
  DisabledHint,
  DueBadge,
  EmptyState,
  ErrorBanner,
  MonoChip,
  StatusBadge,
  apiError,
  differs,
  uf,
} from "@/pages/proposals/shared";

const PAGE_SIZE = 100;

export default function QuotesListPage() {
  const { t } = useTranslation("quotes");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const [search, setSearch] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  const [status, setStatus] = React.useState<QuoteRequestStatus | "all">("all");
  const [createOpen, setCreateOpen] = React.useState(false);

  React.useEffect(() => {
    const id = window.setTimeout(() => setDebounced(search.trim()), 300);
    return () => window.clearTimeout(id);
  }, [search]);

  const quotes = useQuotes({
    search: debounced || undefined,
    status: status === "all" ? undefined : status,
    limit: PAGE_SIZE,
  });

  const items = quotes.data?.items ?? [];

  const kpis = React.useMemo(() => {
    const drafts = items.filter((q) => q.status === "draft").length;
    const inMarket = items.filter((q) => q.status === "sent" || q.status === "receiving").length;
    const proposals = items.reduce((acc, q) => acc + (q.proposal_count ?? 0), 0);
    return { total: quotes.data?.total ?? items.length, drafts, inMarket, proposals };
  }, [items, quotes.data?.total]);

  const columns = React.useMemo<ColumnDef<QuoteRequest>[]>(
    () => [
      {
        accessorKey: "id",
        header: () => t("table.id"),
        cell: ({ row }) => <MonoChip>COT-{String(row.original.id).padStart(4, "0")}</MonoChip>,
      },
      {
        accessorKey: "insured_object",
        header: () => t("table.object"),
        cell: ({ row }) => (
          <div className="min-w-0">
            <div className="truncate font-medium text-text-primary">
              {row.original.insured_object || t("table.noObject")}
            </div>
            <div className="truncate text-caption text-text-muted">
              {row.original.placement?.period ?? "—"}
            </div>
          </div>
        ),
      },
      {
        id: "declared",
        header: () => t("table.declared"),
        accessorFn: (row) => num(row.declared_value_uf) ?? 0,
        cell: ({ row }) => {
          const declared = num(row.original.declared_value_uf);
          const sum = num(row.original.line_items_total_uf);
          const mismatch = differs(declared, sum);
          return (
            <div className="tabular-nums">
              <div className={mismatch ? "text-signal-danger" : "text-text-primary"}>
                {uf(row.original.declared_value_uf)}
              </div>
              <div className="text-caption text-text-muted">
                {t("table.itemsCount", { count: row.original.line_items.length })}
              </div>
            </div>
          );
        },
      },
      {
        id: "proposals",
        header: () => t("table.proposals"),
        accessorFn: (row) => row.proposal_count,
        cell: ({ row }) => (
          <span className="tabular-nums text-text-primary">{row.original.proposal_count}</span>
        ),
      },
      {
        accessorKey: "priority",
        header: () => t("table.priority"),
        cell: ({ row }) => (
          <StatusBadge
            value={row.original.priority}
            label={t(`priority.${row.original.priority}`)}
          />
        ),
      },
      {
        accessorKey: "status",
        header: () => t("table.status"),
        cell: ({ row }) => (
          <StatusBadge value={row.original.status} label={t(`status.${row.original.status}`)} />
        ),
      },
      {
        id: "due",
        header: () => t("table.due"),
        accessorFn: (row) => row.due_at ?? "",
        cell: ({ row }) =>
          row.original.due_at ? (
            <div className="flex flex-col gap-1">
              <DueBadge days={diasRestantes(row.original.due_at)} />
              <span className="text-caption text-text-muted">
                {formatDate(row.original.due_at)}
              </span>
            </div>
          ) : (
            <span className="text-text-muted">—</span>
          ),
      },
    ],
    [t],
  );

  const canCreate = useCan("Quotes", "Create");

  return (
    <>
      <PageHeader
        title={t("title")}
        subtitle={t("subtitle")}
        actions={
          <DisabledHint
            hint={
              canCreate.isLoading
                ? tc("actions.loading")
                : canCreate.allowed
                  ? null
                  : t("create.noPermission")
            }
          >
            <Button
              onClick={() => setCreateOpen(true)}
              disabled={!canCreate.allowed || canCreate.isLoading}
            >
              <Plus className="h-4 w-4" />
              {t("create.action")}
            </Button>
          </DisabledHint>
        }
      />

      <Stagger className="grid grid-cols-2 gap-[18px] lg:grid-cols-4">
        <KpiCard
          label={t("kpi.total")}
          countTo={kpis.total}
          icon={<FileText />}
          tone="brand"
        />
        <KpiCard label={t("kpi.draft")} countTo={kpis.drafts} icon={<Timer />} tone="warn" />
        <KpiCard
          label={t("kpi.inMarket")}
          countTo={kpis.inMarket}
          icon={<Send />}
          tone="action"
        />
        <KpiCard
          label={t("kpi.proposals")}
          countTo={kpis.proposals}
          icon={<Inbox />}
          tone="success"
        />
      </Stagger>

      <FadeUp delay={0.08}>
        <Card className="flex flex-wrap items-center gap-3 p-4">
          <Input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder={t("filters.search")}
            className="h-9 max-w-xs"
          />
          <Select
            value={status}
            onValueChange={(v) => setStatus(v as QuoteRequestStatus | "all")}
          >
            <SelectTrigger className="h-9 w-[190px]">
              <SelectValue placeholder={t("filters.status")} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="all">{t("filters.allStatuses")}</SelectItem>
              {QUOTE_STATUSES.map((s) => (
                <SelectItem key={s} value={s}>
                  {t(`status.${s}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {debounced || status !== "all" ? (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSearch("");
                setStatus("all");
              }}
            >
              {tc("actions.clear")}
            </Button>
          ) : null}
          <span className="ml-auto text-caption text-text-muted">
            {t("filters.showing", {
              shown: items.length,
              total: quotes.data?.total ?? 0,
            })}
          </span>
        </Card>
      </FadeUp>

      {quotes.isError ? <ErrorBanner error={quotes.error} /> : null}

      <FadeUp delay={0.12}>
        {!quotes.isLoading && items.length === 0 ? (
          <Card>
            <EmptyState
              title={t("empty.title")}
              hint={t("empty.hint")}
              action={
                canCreate.allowed ? (
                  <Button size="sm" onClick={() => setCreateOpen(true)}>
                    <Plus className="h-4 w-4" />
                    {t("create.action")}
                  </Button>
                ) : undefined
              }
            />
          </Card>
        ) : (
          <DataTable
            columns={columns}
            data={items}
            isLoading={quotes.isLoading}
            onRowClick={(row) => navigate(`/quotes/${row.id}`)}
          />
        )}
      </FadeUp>

      <CreateQuoteDialog open={createOpen} onOpenChange={setCreateOpen} />
    </>
  );
}

// =============================================================================
// Create
// =============================================================================

function CreateQuoteDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { t } = useTranslation("quotes");
  const { t: tc } = useTranslation("common");
  const navigate = useNavigate();

  const placements = usePlacements({ open_only: true, page_size: 100 }, open);
  const create = useCreateQuote();

  const [placementId, setPlacementId] = React.useState<string>("");
  const [insuredObject, setInsuredObject] = React.useState("");
  const [declaredValue, setDeclaredValue] = React.useState("");
  const [desiredStart, setDesiredStart] = React.useState("");
  const [priority, setPriority] = React.useState<Priority>("normal");
  const [coverages, setCoverages] = React.useState("");

  React.useEffect(() => {
    if (!open) {
      setPlacementId("");
      setInsuredObject("");
      setDeclaredValue("");
      setDesiredStart("");
      setPriority("normal");
      setCoverages("");
      create.reset();
    }
    // `create` is a stable mutation object from react-query.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  const options = placements.data?.items ?? [];
  const canSubmit = !!placementId && !create.isPending;

  const submit = () => {
    if (!placementId) return;
    create.mutate(
      {
        placement_id: Number(placementId),
        insured_object: insuredObject || null,
        declared_value_uf: declaredValue === "" ? null : Number(declaredValue),
        desired_start: desiredStart || null,
        priority,
        requested_coverages: coverages || null,
      },
      {
        onSuccess: (quote) => {
          toast.success(t("create.created"));
          onOpenChange(false);
          navigate(`/quotes/${quote.id}`);
        },
        onError: (error) => toast.error(apiError(error, tc("toast.error"))),
      },
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>{t("create.title")}</DialogTitle>
          <DialogDescription>{t("create.description")}</DialogDescription>
        </DialogHeader>

        <div className="flex flex-col gap-4">
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="placement">{t("create.placement")}</Label>
            <Select value={placementId} onValueChange={setPlacementId}>
              <SelectTrigger id="placement">
                <SelectValue
                  placeholder={
                    placements.isLoading ? tc("actions.loading") : t("create.placementPlaceholder")
                  }
                />
              </SelectTrigger>
              <SelectContent>
                {options.map((p) => (
                  <SelectItem key={p.id} value={String(p.id)}>
                    {[p.client?.legal_name, p.asset?.name, p.insurance_line?.name, p.period]
                      .filter(Boolean)
                      .join(" · ")}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            {!placements.isLoading && options.length === 0 ? (
              <p className="text-caption text-amber-deep">{t("create.noPlacements")}</p>
            ) : null}
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="object">{t("create.insuredObject")}</Label>
            <Input
              id="object"
              value={insuredObject}
              onChange={(e) => setInsuredObject(e.target.value)}
              placeholder={t("create.insuredObjectPlaceholder")}
            />
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="declared">{t("create.declaredValue")}</Label>
              <Input
                id="declared"
                type="number"
                inputMode="decimal"
                step="0.01"
                value={declaredValue}
                onChange={(e) => setDeclaredValue(e.target.value)}
                placeholder="0,00"
              />
              <p className="text-caption text-text-muted">{t("create.declaredHint")}</p>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="start">{t("create.desiredStart")}</Label>
              <Input
                id="start"
                type="date"
                value={desiredStart}
                onChange={(e) => setDesiredStart(e.target.value)}
              />
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="priority">{t("create.priority")}</Label>
            <Select value={priority} onValueChange={(v) => setPriority(v as Priority)}>
              <SelectTrigger id="priority">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {PRIORITIES.map((p) => (
                  <SelectItem key={p} value={p}>
                    {t(`priority.${p}`)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex flex-col gap-1.5">
            <Label htmlFor="coverages">{t("create.requestedCoverages")}</Label>
            <Input
              id="coverages"
              value={coverages}
              onChange={(e) => setCoverages(e.target.value)}
              placeholder={t("create.requestedCoveragesPlaceholder")}
            />
          </div>

          {create.isError ? <ErrorBanner error={create.error} /> : null}
        </div>

        <DialogFooter>
          <Button variant="secondary" onClick={() => onOpenChange(false)}>
            {tc("actions.cancel")}
          </Button>
          <DisabledHint hint={placementId ? null : t("create.pickPlacement")}>
            <Button onClick={submit} disabled={!canSubmit}>
              {create.isPending ? tc("actions.loading") : tc("actions.create")}
            </Button>
          </DisabledHint>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
